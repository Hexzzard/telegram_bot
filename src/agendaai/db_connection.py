"""Acceso homogéneo a PostgreSQL: pool en el bot, URL en scripts aislados."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from psycopg_pool import AsyncConnectionPool


@asynccontextmanager
async def open_connection(
    database: str | AsyncConnectionPool,
) -> AsyncIterator[psycopg.AsyncConnection]:
    """Presta una conexión del pool o abre una temporal en scripts antiguos."""
    if isinstance(database, AsyncConnectionPool):
        async with database.connection() as conn:
            yield conn
    else:
        async with await psycopg.AsyncConnection.connect(database, connect_timeout=15) as conn:
            yield conn
