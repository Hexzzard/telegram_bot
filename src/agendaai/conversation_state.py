from dataclasses import dataclass
from enum import StrEnum

from psycopg_pool import AsyncConnectionPool

from agendaai.db_connection import open_connection


class ConversationStage(StrEnum):
    IDLE = "idle"
    AWAITING_RECEIPT_CHOICE = "awaiting_receipt_choice"
    AWAITING_EMAIL = "awaiting_email"
    AWAITING_VERIFICATION = "awaiting_verification"


@dataclass(frozen=True)
class ConversationState:
    telegram_user_id: int
    state: ConversationStage
    booking_id: int | None
    pending_email: str | None


class ConversationStateRepository:
    def __init__(self, database_url: str | AsyncConnectionPool):
        self._database_url = database_url

    async def get_state(
        self,
        user_id: int,
    ) -> ConversationState:
        """
        Recupera la etapa conversacional del usuario.

        Cuando no existe una fila, se interpreta
        como conversación normal (idle).
        """
        async with (
            open_connection(self._database_url) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                    SELECT
                        telegram_user_id,
                        state,
                        booking_id,
                        pending_email
                    FROM public.user_conversation_state
                    WHERE telegram_user_id = %s
                    """,
                (user_id,),
            )

            row = await cursor.fetchone()

        if row is None:
            return ConversationState(
                telegram_user_id=user_id,
                state=ConversationStage.IDLE,
                booking_id=None,
                pending_email=None,
            )

        return ConversationState(
            telegram_user_id=row[0],
            state=ConversationStage(row[1]),
            booking_id=row[2],
            pending_email=row[3],
        )

    async def begin_receipt_offer(
        self,
        *,
        user_id: int,
        booking_id: int,
    ) -> bool:
        """
        Inicia una oferta de comprobante únicamente
        si la reserva confirmada pertenece al usuario.

        La comprobación y la actualización se hacen
        en una única sentencia SQL.
        """
        async with (
            open_connection(self._database_url) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                    INSERT INTO public.user_conversation_state (
                        telegram_user_id,
                        state,
                        booking_id,
                        pending_email,
                        verification_hash,
                        verification_expires_at,
                        verification_attempts,
                        last_code_sent_at,
                        updated_at
                    )
                    SELECT
                        %s,
                        'awaiting_receipt_choice',
                        b.id,
                        NULL,
                        NULL,
                        NULL,
                        0,
                        NULL,
                        NOW()
                    FROM public.bookings AS b
                    WHERE b.id = %s
                      AND b.telegram_user_id = %s
                      AND b.status = 'confirmed'
                    ON CONFLICT (telegram_user_id)
                    DO UPDATE SET
                        state = EXCLUDED.state,
                        booking_id = EXCLUDED.booking_id,
                        pending_email = NULL,
                        verification_hash = NULL,
                        verification_expires_at = NULL,
                        verification_attempts = 0,
                        last_code_sent_at = NULL,
                        updated_at = NOW()
                    RETURNING booking_id
                    """,
                (user_id, booking_id, user_id),
            )

            row = await cursor.fetchone()

        return row is not None

    async def move_to_email_input(
        self,
        *,
        user_id: int,
    ) -> bool:
        """
        Pasa de la pregunta de comprobante
        a la solicitud de correo.

        Solo admite la transición esperada.
        """
        async with (
            open_connection(self._database_url) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                    UPDATE public.user_conversation_state
                    SET state = 'awaiting_email',
                        pending_email = NULL,
                        verification_hash = NULL,
                        verification_expires_at = NULL,
                        verification_attempts = 0,
                        last_code_sent_at = NULL,
                        updated_at = NOW()
                    WHERE telegram_user_id = %s
                      AND state = 'awaiting_receipt_choice'
                    RETURNING telegram_user_id
                    """,
                (user_id,),
            )

            row = await cursor.fetchone()

        return row is not None

    async def return_to_receipt_choice(
        self,
        *,
        user_id: int,
    ) -> bool:
        """
        Permite volver desde la solicitud de
        correo a la pregunta anterior.
        """
        async with (
            open_connection(self._database_url) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                    UPDATE public.user_conversation_state
                    SET state = 'awaiting_receipt_choice',
                        pending_email = NULL,
                        verification_hash = NULL,
                        verification_expires_at = NULL,
                        verification_attempts = 0,
                        last_code_sent_at = NULL,
                        updated_at = NOW()
                    WHERE telegram_user_id = %s
                      AND state = 'awaiting_email'
                    RETURNING telegram_user_id
                    """,
                (user_id,),
            )

            row = await cursor.fetchone()

        return row is not None

    async def clear_state(
        self,
        *,
        user_id: int,
    ) -> None:
        """
        Limpia el flujo conversacional.

        No elimina reservas, historial general
        ni direcciones verificadas.
        """
        async with open_connection(self._database_url) as conn:
            await conn.execute(
                """
                UPDATE public.user_conversation_state
                SET state = 'idle',
                    booking_id = NULL,
                    pending_email = NULL,
                    verification_hash = NULL,
                    verification_expires_at = NULL,
                    verification_attempts = 0,
                    last_code_sent_at = NULL,
                    updated_at = NOW()
                WHERE telegram_user_id = %s
                """,
                (user_id,),
            )
