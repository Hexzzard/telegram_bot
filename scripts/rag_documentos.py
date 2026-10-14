"""Laboratorio documental para AgendaAI.

Ejecutar desde la raíz del proyecto original:
  python scripts/rag_documentos.py stats
  python scripts/rag_documentos.py index --file knowledge/chunks_huellitas_del_sur.jsonl
  python scripts/rag_documentos.py search --text "¿Cuándo puedo cancelar?"

Requiere sql/009_company_knowledge.sql aplicado en Supabase.
Por defecto index --dry-run NO llama Gemini ni modifica Supabase.
Usar index --apply para almacenar realmente los embeddings.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import re
import random
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types
from google.genai.errors import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from agendaai.config import Settings  # noqa: E402
from agendaai.db_pool import DatabasePool  # noqa: E402

MODEL = 'gemini-embedding-001'
DIMS = 768
DOCUMENT = 'huellitas-del-sur-manual-2026-v1'
log = logging.getLogger('rag_documentos')


def vector_string(values):
    if len(values) != DIMS:
        raise ValueError(f'Dimensión incorrecta: {len(values)} != {DIMS}')
    return '[' + ','.join(format(float(v), '.9g') for v in values) + ']'


def load_chunks(filename):
    data = [json.loads(line) for line in filename.read_text(encoding='utf-8').splitlines() if line.strip()]
    seen=set()
    for item in data:
        if item['document_id'] != DOCUMENT:
            raise ValueError('document_id inesperado en el corpus')
        if not 20 <= len(item['content']) <= 4000:
            raise ValueError(f"Longitud incorrecta: {item['chunk_id']}")
        key=(item['document_id'],item['chunk_id'])
        if key in seen:
            raise ValueError(f'Fragmento duplicado: {key}')
        seen.add(key)
    return data



def retry_delay(error: ClientError, attempt: int) -> float:
    """Respeta RetryInfo cuando existe; si no, retroceso exponencial."""
    message = str(error)
    # El cuerpo de Google suele incluir: "Please retry in 40.486s".
    match = re.search(r"retry\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*s", message, re.I)
    if not match:
        # Algunas respuestas ofrecen un campo RetryInfo: retryDelay: '40s'.
        match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?([0-9]+(?:\.[0-9]+)?)s", message, re.I)
    if match:
        return min(300.0, float(match.group(1)) + 2.0)
    return min(300.0, 15.0 * (2 ** (attempt - 1)) + random.uniform(0, 3))


async def embed_with_retry(client, *, contents, task_type, max_attempts=8):
    """Reintenta exclusivamente errores 429; el resto se propaga."""
    for attempt in range(1, max_attempts + 1):
        try:
            return await client.aio.models.embed_content(
                model=MODEL,
                contents=contents,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=DIMS,
                ),
            )
        except ClientError as error:
            if error.code != 429 or attempt == max_attempts:
                raise
            delay = retry_delay(error, attempt)
            print(
                f"Cuota de embeddings agotada (429). "
                f"Esperando {delay:.1f}s antes del intento "
                f"{attempt + 1}/{max_attempts}...",
                flush=True,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("No se logró obtener el embedding")


async def get_embedding(client, text, task_type):
    r = await embed_with_retry(client, contents=text, task_type=task_type)
    if not r.embeddings or not r.embeddings[0].values:
        raise RuntimeError('Respuesta de embeddings vacía')
    return vector_string(r.embeddings[0].values)


async def run(args):
    if args.action == 'index':
        input_file=Path(args.file)
        chunks=load_chunks(input_file)
        print(f'Fragmentos en archivo: {len(chunks)}')
        if not args.apply:
            print('SIMULACIÓN: usa --apply para generar embeddings y escribir en PostgreSQL.')
            return
    settings=Settings()
    pool=DatabasePool(settings.database_url.get_secret_value(), min_size=1, max_size=2)
    client=genai.Client(api_key=settings.gemini_api_key.get_secret_value())
    try:
        await pool.open()
        if args.action=='stats':
            async with pool.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT document_id, COUNT(*), COUNT(DISTINCT section_id) FROM public.company_knowledge_chunks GROUP BY document_id')
                    rows=await cur.fetchall()
            print('Documentos:',rows)
        elif args.action=='index':
            # Solo generar embeddings para fragmentos nuevos/modificados.
            async with pool.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT chunk_id, content_sha256 FROM public.company_knowledge_chunks WHERE document_id=%s',(DOCUMENT,))
                    existing=dict(await cur.fetchall())
            pending=[]
            for item in chunks:
                digest=hashlib.sha256(item['content'].encode('utf-8')).hexdigest()
                if existing.get(item['chunk_id'])!=digest:
                    pending.append((item,digest))
            print(f'Fragmentos nuevos o modificados: {len(pending)} / {len(chunks)}')
            start=time.perf_counter()
            # Lotes pequeños reducen viajes de red sin sobrecargar la API.
            BATCH_SIZE = 8
            for offset in range(0,len(pending),BATCH_SIZE):
                batch=pending[offset:offset+BATCH_SIZE]
                response = await embed_with_retry(
                    client,
                    contents=[item['content'] for item, _ in batch],
                    task_type='RETRIEVAL_DOCUMENT',
                )
                if not response.embeddings or len(response.embeddings)!=len(batch):
                    raise RuntimeError('La API no devolvió un embedding por fragmento')
                rows=[]
                for (item,digest),emb in zip(batch,response.embeddings):
                    if not emb.values: raise RuntimeError('Embedding de documento vacío')
                    rows.append((item['document_id'],item['chunk_id'],item['version'],
                        item['section_id'],item['section_title'],item['source'],
                        item['content'],digest,MODEL,vector_string(emb.values)))
                async with pool.pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.executemany('''
                            INSERT INTO public.company_knowledge_chunks
                              (document_id,chunk_id,version,section_id,section_title,source,content,content_sha256,embedding_model,embedding)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::extensions.vector)
                            ON CONFLICT (document_id,chunk_id) DO UPDATE SET
                              version=EXCLUDED.version, section_id=EXCLUDED.section_id,
                              section_title=EXCLUDED.section_title, source=EXCLUDED.source,
                              content=EXCLUDED.content, content_sha256=EXCLUDED.content_sha256,
                              embedding_model=EXCLUDED.embedding_model,embedding=EXCLUDED.embedding,
                              updated_at=now()
                        ''',rows)
                done=offset+len(batch)
                print(f'Indexados {done}/{len(pending)}; transcurrido {time.perf_counter()-start:.1f}s',flush=True)
            print('Indexación completada. No se eliminan automáticamente fragmentos retirados de una versión anterior.')
        elif args.action=='search':
            t0=time.perf_counter()
            emb=await get_embedding(client,args.text,'RETRIEVAL_QUERY')
            et=time.perf_counter()-t0
            t1=time.perf_counter()
            async with pool.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('''
                        SELECT section_id,section_title,content,source,
                               1-(embedding <=> %s::extensions.vector) AS similarity
                        FROM public.company_knowledge_chunks
                        WHERE document_id=%s AND embedding_model=%s
                        ORDER BY embedding <=> %s::extensions.vector
                        LIMIT %s
                    ''',(emb,DOCUMENT,MODEL,emb,args.limit))
                    rows=await cur.fetchall()
            print(f'Embedding: {et:.3f}s | SQL: {time.perf_counter()-t1:.3f}s')
            if not rows: print('Sin resultados: primero ejecutar index --apply.')
            for i,(sid,title,content,source,score) in enumerate(rows,1):
                print(f'\n{i}. [{sid}] {title} — similitud {float(score):.3f}')
                print(f'Fuente: {source}\n{content}')
            print('\nAVISO: las similitudes no son certezas; esta es documentación ficticia.')
    finally:
        await pool.close()
        await client.aio.aclose()


def main():
    p=argparse.ArgumentParser(description='RAG documental veterinaria de demostración')
    sub=p.add_subparsers(dest='action',required=True)
    i=sub.add_parser('index');i.add_argument('--file',default=str(ROOT/'knowledge'/'chunks_huellitas_del_sur.jsonl'));i.add_argument('--apply',action='store_true')
    sub.add_parser('stats')
    s=sub.add_parser('search');s.add_argument('--text',required=True);s.add_argument('--limit',type=int,default=3)
    args=p.parse_args()
    if args.action=='search' and not 1<=args.limit<=8: p.error('--limit entre 1 y 8')
    if sys.platform=='win32':
        asyncio.run(run(args),loop_factory=lambda:asyncio.SelectorEventLoop())
    else: asyncio.run(run(args))

if __name__=='__main__':main()
