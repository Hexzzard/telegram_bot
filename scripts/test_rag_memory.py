"""Prueba aislada de RAG: python scripts/test_rag_memory.py --help.

Ejecutar desde la raíz del proyecto AgendaAI, con .env existente.
No integra RAG en GeminiService ni en handlers.py.
"""

import argparse
import asyncio
import hashlib
import logging
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types

# Permite ejecutar el script sin instalación editable del paquete.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agendaai.config import Settings  # noqa: E402
from agendaai.db_pool import DatabasePool  # noqa: E402

MODEL = "gemini-embedding-001"
DIMENSIONS = 768
logger = logging.getLogger(__name__)


def as_vector(values: list[float]) -> str:
    if len(values) != DIMENSIONS:
        raise ValueError(f"Dimensión incorrecta: {len(values)} != {DIMENSIONS}")
    return "[" + ",".join(format(float(x), ".9g") for x in values) + "]"


async def embed(client: genai.Client, text: str, task_type: str) -> tuple[str, float]:
    start = time.perf_counter()
    response = await client.aio.models.embed_content(
        model=MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=DIMENSIONS,
        ),
    )
    elapsed = time.perf_counter() - start
    if not response.embeddings or not response.embeddings[0].values:
        raise RuntimeError("Google no devolvió ningún embedding")
    return as_vector(response.embeddings[0].values), elapsed


async def run(args: argparse.Namespace) -> None:
    settings = Settings()
    pool = DatabasePool(
        settings.database_url.get_secret_value(), min_size=1, max_size=2
    )
    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
    try:
        await pool.open()
        if args.action == "add":
            content = args.text.strip()
            if not 5 <= len(content) <= 1000:
                raise ValueError("El recuerdo debe tener entre 5 y 1000 caracteres")
            embedding, embed_time = await embed(client, content, "RETRIEVAL_DOCUMENT")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            t0 = time.perf_counter()
            async with pool.pool.connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO public.bot_users (telegram_user_id)
                        VALUES (%s)
                        ON CONFLICT (telegram_user_id) DO NOTHING
                        """,
                        (args.user_id,),
                    )
                    await cursor.execute(
                        """
                        INSERT INTO public.agent_memories
                            (telegram_user_id, content, content_sha256,
                             embedding_model, embedding)
                        VALUES (%s, %s, %s, %s, %s::extensions.vector)
                        ON CONFLICT (telegram_user_id, content_sha256)
                        DO UPDATE SET content = EXCLUDED.content,
                                      embedding = EXCLUDED.embedding,
                                      embedding_model = EXCLUDED.embedding_model
                        RETURNING id
                        """,
                        (args.user_id, content, digest, MODEL, embedding),
                    )
                    memory_id = (await cursor.fetchone())[0]
            print(f"Recuerdo #{memory_id} guardado")
            print(f"Embedding: {embed_time:.3f}s | Escritura: {time.perf_counter()-t0:.3f}s")
        elif args.action == "search":
            embedding, embed_time = await embed(client, args.text, "RETRIEVAL_QUERY")
            t0 = time.perf_counter()
            async with pool.pool.connection() as conn:
                async with conn.cursor() as cursor:
                    # Subconsulta por usuario: no se usan recuerdos de otros usuarios.
                    await cursor.execute(
                        """
                        SELECT id, content,
                               1 - (embedding <=> %s::extensions.vector) AS similarity
                        FROM public.agent_memories
                        WHERE telegram_user_id = %s AND embedding_model = %s
                        ORDER BY embedding <=> %s::extensions.vector
                        LIMIT %s
                        """,
                        (embedding, args.user_id, MODEL, embedding, args.limit),
                    )
                    rows = await cursor.fetchall()
            print(f"Embedding consulta: {embed_time:.3f}s | Búsqueda SQL: {time.perf_counter()-t0:.3f}s")
            if not rows:
                print("Sin recuerdos. Primero ejecuta el comando add.")
            for rank, (memory_id, content, similarity) in enumerate(rows, 1):
                print(f"{rank}. id={memory_id} similitud={similarity:.3f}: {content}")
            print("AVISO: similitud alta no equivale a certeza; valida manualmente relevancia.")
        elif args.action == "list":
            async with pool.pool.connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT id, content
                        FROM public.agent_memories
                        WHERE telegram_user_id = %s
                        ORDER BY id
                        """,
                        (args.user_id,),
                    )
                    for memory_id, content in await cursor.fetchall():
                        print(f"#{memory_id}: {content}")
    finally:
        await pool.close()
        # El SDK administra su propia sesión HTTP asíncrona.
        await client.aio.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Laboratorio RAG de AgendaAI")
    parser.add_argument("action", choices=["add", "search", "list"])
    parser.add_argument("--user-id", required=True, type=int, help="ID de Telegram de prueba")
    parser.add_argument("--text", default="", help="Recuerdo o pregunta")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    if args.action in {"add", "search"} and not args.text.strip():
        parser.error("add y search requieren --text")
    if not 1 <= args.limit <= 10:
        parser.error("--limit debe estar entre 1 y 10")
    asyncio.run(run(args))


if __name__ == "__main__":
    import asyncio
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()
        )

    main()
