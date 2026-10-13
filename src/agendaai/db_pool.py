"""Ciclo de vida del pool PostgreSQL de AgendaAI."""

import logging

from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class DatabasePool:
    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 3,
    ) -> None:
        if min_size < 0 or max_size < 1 or min_size > max_size:
            raise ValueError("Tamaño de pool inválido")
        self.pool = AsyncConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            timeout=25.0,
            max_waiting=20,
            max_lifetime=1800.0,
            max_idle=300.0,
            reconnect_timeout=30.0,
            kwargs={"connect_timeout": 15},
            open=False,
        )

    async def open(self) -> None:
        logger.info("Abriendo pool PostgreSQL...")
        await self.pool.open()
        try:
            await self.pool.wait(timeout=60.0)
        except Exception:
            await self.pool.close()
            raise
        logger.info("Pool PostgreSQL listo")

    async def check_connection(self) -> None:
        async with self.pool.connection() as conn, conn.cursor() as cursor:
            await cursor.execute("SELECT 1")
            row = await cursor.fetchone()
        if row != (1,):
            raise RuntimeError("La consulta de prueba no devolvió 1")

    async def close(self) -> None:
        await self.pool.close()
        logger.info("Pool PostgreSQL cerrado")
