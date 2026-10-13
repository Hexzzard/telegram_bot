from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool

from agendaai.db_connection import open_connection


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class ConversationRepository:
    def __init__(self, database_url: str | AsyncConnectionPool):
        self._database_url = database_url

    async def check_connection(self) -> None:
        async with (
            open_connection(self._database_url) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute("SELECT 1")
            await cursor.fetchone()

    async def get_history(
        self,
        user_id: int,
        limit: int = 20,
    ) -> list[ChatMessage]:
        if limit < 1:
            raise ValueError("limit must be positive")

        async with (
            open_connection(self._database_url) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                    SELECT role, content
                    FROM (
                        SELECT id, role, content
                        FROM public.messages
                        WHERE telegram_user_id = %s
                        ORDER BY id DESC
                        LIMIT %s
                    ) AS recent
                    ORDER BY id ASC
                    """,
                (user_id, limit),
            )

            rows = await cursor.fetchall()

        return [ChatMessage(role=row[0], content=row[1]) for row in rows]

    async def save_exchange(
        self,
        user_id: int,
        user_message: str,
        model_message: str,
    ) -> None:
        async with (
            open_connection(self._database_url) as conn,
            conn.transaction(),
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                        INSERT INTO public.bot_users (
                            telegram_user_id
                        )
                        VALUES (%s)
                        ON CONFLICT (telegram_user_id)
                        DO UPDATE SET
                            updated_at = NOW()
                        """,
                (user_id,),
            )

            await cursor.executemany(
                """
                        INSERT INTO public.messages (
                            telegram_user_id,
                            role,
                            content
                        )
                        VALUES (%s, %s, %s)
                        """,
                [
                    (
                        user_id,
                        "user",
                        user_message,
                    ),
                    (
                        user_id,
                        "model",
                        model_message,
                    ),
                ],
            )

    async def delete_history(
        self,
        user_id: int,
    ) -> None:
        async with (
            open_connection(self._database_url) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                    DELETE FROM public.messages
                    WHERE telegram_user_id = %s
                    """,
                (user_id,),
            )
