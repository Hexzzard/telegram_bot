import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from email_validator import EmailNotValidError, validate_email
from psycopg_pool import AsyncConnectionPool

from agendaai.db_connection import open_connection

logger = logging.getLogger(__name__)

CODE_LIFETIME = timedelta(minutes=10)
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class ReceiptResult:
    status: str
    message: str


def make_code() -> str:
    """Genera un código criptográficamente seguro."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(
    secret: str,
    user_id: int,
    email: str,
    code: str,
) -> str:
    """Vincula el código al usuario y su correo."""
    payload = f"{user_id}:{email}:{code}".encode()

    return hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()


class ReceiptService:
    def __init__(
        self,
        *,
        database_url: str | AsyncConnectionPool,
        resend_api_key: str,
        email_from: str,
        verification_secret: str,
    ):
        if not verification_secret:
            raise ValueError("EMAIL_VERIFICATION_SECRET es obligatorio")

        self._database_url = database_url
        self._resend_api_key = resend_api_key
        self._email_from = email_from
        self._secret = verification_secret

    async def _send_verification_email(
        self,
        *,
        recipient: str,
        code: str,
    ) -> None:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": (f"Bearer {self._resend_api_key}"),
                    "Content-Type": "application/json",
                },
                json={
                    "from": self._email_from,
                    "to": [recipient],
                    "subject": ("Código de verificación de AgendaAI"),
                    "text": (
                        "Tu código para verificar el correo "
                        f"en AgendaAI es: {code}\n\n"
                        "Vence en 10 minutos.\n"
                        "Si no lo solicitaste, ignora "
                        "este mensaje."
                    ),
                },
            )

            response.raise_for_status()

    async def request_verification(
        self,
        *,
        user_id: int,
        email: str,
    ) -> ReceiptResult:
        """Guarda un desafío y solicita su envío."""
        try:
            normalized = validate_email(
                email,
                check_deliverability=False,
            ).normalized
        except EmailNotValidError:
            return ReceiptResult(
                "invalid_email",
                "La dirección de correo no es válida.",
            )

        code = make_code()
        digest = hash_code(
            self._secret,
            user_id,
            normalized,
            code,
        )

        expires_at = datetime.now(UTC) + CODE_LIFETIME

        async with (
            open_connection(self._database_url) as conn,
            conn.transaction(),
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                        SELECT state, booking_id
                        FROM public.user_conversation_state
                        WHERE telegram_user_id = %s
                        FOR UPDATE
                        """,
                (user_id,),
            )
            state = await cursor.fetchone()

            if state is None or state[0] != "awaiting_email":
                return ReceiptResult(
                    "invalid_state",
                    "No hay una solicitud de correo "
                    "pendiente. Primero acepta "
                    "recibir un comprobante.",
                )

            booking_id = state[1]

            await cursor.execute(
                """
                        SELECT EXISTS (
                            SELECT 1
                            FROM public.bookings
                            WHERE id = %s
                              AND telegram_user_id = %s
                              AND status = 'confirmed'
                        )
                        """,
                (booking_id, user_id),
            )

            owns_booking = (await cursor.fetchone())[0]

            if not owns_booking:
                return ReceiptResult(
                    "invalid_booking",
                    "La reserva no está disponible para generar un comprobante.",
                )

            await cursor.execute(
                """
                        UPDATE public.user_conversation_state
                        SET state = 'awaiting_verification',
                            pending_email = %s,
                            verification_hash = %s,
                            verification_expires_at = %s,
                            verification_attempts = 0,
                            last_code_sent_at = NOW(),
                            verification_delivery_status =
                                'preparing',
                            verification_delivery_error = NULL,
                            updated_at = NOW()
                        WHERE telegram_user_id = %s
                        """,
                (
                    normalized,
                    digest,
                    expires_at,
                    user_id,
                ),
            )

        try:
            await self._send_verification_email(
                recipient=normalized,
                code=code,
            )

        except httpx.HTTPError as error:
            logger.warning(
                "Resend rechazó o no completó la solicitud de verificación para el usuario %s: %s",
                user_id,
                type(error).__name__,
            )

            async with open_connection(self._database_url) as conn:
                await conn.execute(
                    """
                    UPDATE public.user_conversation_state
                    SET verification_delivery_status = 'failed',
                        verification_delivery_error = %s,
                        updated_at = NOW()
                    WHERE telegram_user_id = %s
                      AND verification_hash = %s
                      AND state = 'awaiting_verification'
                    """,
                    (
                        type(error).__name__,
                        user_id,
                        digest,
                    ),
                )

            return ReceiptResult(
                "send_failed",
                "No pude solicitar el envío del código. "
                "Tu reserva sigue confirmada. "
                "Puedes intentar más tarde.",
            )

        async with open_connection(self._database_url) as conn:
            await conn.execute(
                """
                UPDATE public.user_conversation_state
                SET verification_delivery_status = 'sent',
                    verification_delivery_error = NULL,
                    updated_at = NOW()
                WHERE telegram_user_id = %s
                  AND verification_hash = %s
                  AND state = 'awaiting_verification'
                """,
                (user_id, digest),
            )

        return ReceiptResult(
            "verification_sent",
            "Solicité el envío de un código de seis "
            "dígitos a tu correo. Escríbelo aquí "
            "cuando lo recibas. Vence en 10 minutos.",
        )

    async def verify_code(
        self,
        *,
        user_id: int,
        code: str,
    ) -> ReceiptResult:
        """Verifica el código y encola el comprobante."""
        if not (len(code) == 6 and code.isascii() and code.isdigit()):
            return ReceiptResult(
                "invalid_code",
                "El código debe contener seis dígitos.",
            )

        async with (
            open_connection(self._database_url) as conn,
            conn.transaction(),
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                        SELECT
                            state,
                            booking_id,
                            pending_email,
                            verification_hash,
                            verification_expires_at,
                            verification_attempts,
                            verification_delivery_status
                        FROM public.user_conversation_state
                        WHERE telegram_user_id = %s
                        FOR UPDATE
                        """,
                (user_id,),
            )

            row = await cursor.fetchone()

            if row is None or row[0] != "awaiting_verification":
                return ReceiptResult(
                    "invalid_state",
                    "No tienes un código pendiente de verificación.",
                )

            (
                _,
                booking_id,
                email,
                stored_hash,
                expires_at,
                attempts,
                delivery_status,
            ) = row

            if delivery_status != "sent":
                return ReceiptResult(
                    "not_sent",
                    "El envío del código no se ha confirmado. No puedo verificarlo todavía.",
                )

            if not email or not stored_hash or not expires_at:
                return ReceiptResult(
                    "invalid_state",
                    "Los datos de verificación están incompletos.",
                )

            now = datetime.now(UTC)

            if expires_at <= now:
                return ReceiptResult(
                    "expired",
                    "El código venció. Solicita uno nuevo.",
                )

            if attempts >= MAX_ATTEMPTS:
                return ReceiptResult(
                    "blocked",
                    "Se agotaron los intentos de verificación.",
                )

            expected_hash = hash_code(
                self._secret,
                user_id,
                email,
                code,
            )

            if not hmac.compare_digest(
                stored_hash,
                expected_hash,
            ):
                await cursor.execute(
                    """
                            UPDATE public.user_conversation_state
                            SET verification_attempts =
                                    verification_attempts + 1,
                                updated_at = NOW()
                            WHERE telegram_user_id = %s
                            """,
                    (user_id,),
                )

                return ReceiptResult(
                    "incorrect",
                    "El código no es correcto. Comprueba el mensaje recibido.",
                )

            # Verificar nuevamente la propiedad
            # de la reserva antes de enviar datos.
            await cursor.execute(
                """
                        SELECT
                            b.id,
                            b.title,
                            b.start_at,
                            b.end_at,
                            r.name
                        FROM public.bookings AS b
                        JOIN public.booking_resources AS r
                          ON r.id = b.resource_id
                        WHERE b.id = %s
                          AND b.telegram_user_id = %s
                          AND b.status = 'confirmed'
                        """,
                (booking_id, user_id),
            )

            booking = await cursor.fetchone()

            if booking is None:
                return ReceiptResult(
                    "invalid_booking",
                    "La reserva ya no está confirmada.",
                )

            (
                confirmed_id,
                title,
                start_at,
                end_at,
                service_name,
            ) = booking

            # Guardar el correo verificado.
            await cursor.execute(
                """
                        INSERT INTO public.verified_email_contacts (
                            telegram_user_id,
                            email,
                            verified_at,
                            updated_at
                        )
                        VALUES (%s, %s, NOW(), NOW())
                        ON CONFLICT (telegram_user_id)
                        DO UPDATE SET
                            email = EXCLUDED.email,
                            verified_at = NOW(),
                            updated_at = NOW()
                        """,
                (user_id, email),
            )

            body = (
                "Tu reserva fue confirmada.\n\n"
                f"Código: AG-{confirmed_id:06d}\n"
                f"Servicio: {service_name}\n"
                f"Motivo: {title}\n"
                f"Inicio (UTC): {start_at.isoformat()}\n"
                f"Término (UTC): {end_at.isoformat()}\n"
                "Estado: Confirmada\n\n"
                "Este es un comprobante generado "
                "por AgendaAI."
            )

            await cursor.execute(
                """
                        INSERT INTO public.email_notifications (
                            booking_id,
                            recipient,
                            subject,
                            body,
                            status
                        )
                        VALUES (
                            %s, %s, %s, %s, 'pending'
                        )
                        ON CONFLICT (booking_id)
                        DO NOTHING
                        RETURNING id
                        """,
                (
                    confirmed_id,
                    email,
                    (f"Confirmación de reserva AG-{confirmed_id:06d}"),
                    body,
                ),
            )

            notification = await cursor.fetchone()

            await cursor.execute(
                """
                        UPDATE public.user_conversation_state
                        SET state = 'idle',
                            booking_id = NULL,
                            pending_email = NULL,
                            verification_hash = NULL,
                            verification_expires_at = NULL,
                            verification_attempts = 0,
                            last_code_sent_at = NULL,
                            verification_delivery_status = 'none',
                            verification_delivery_error = NULL,
                            updated_at = NOW()
                        WHERE telegram_user_id = %s
                        """,
                (user_id,),
            )

            if notification is None:
                return ReceiptResult(
                    "already_queued",
                    "Este comprobante ya había sido "
                    "registrado para envío. "
                    "No generé otro duplicado.",
                )

            return ReceiptResult(
                "queued",
                "Correo verificado correctamente. El comprobante fue agregado a la cola de envío.",
            )

    async def enqueue_verified_receipt(
        self,
        *,
        user_id: int,
    ) -> ReceiptResult:
        """
        Genera el comprobante utilizando el correo
        previamente verificado del usuario.

        No solicita un nuevo código.

        Comprueba:
        - Que exista una oferta de comprobante.
        - Que la reserva pertenezca al usuario.
        - Que la reserva siga confirmada.
        - Que el correo esté verificado.
        - Que no exista un comprobante duplicado.
        """
        from zoneinfo import ZoneInfo

        async with (
            open_connection(self._database_url) as conn,
            conn.transaction(),
            conn.cursor() as cursor,
        ):
            # Bloquear el estado para evitar
            # procesar dos respuestas a la vez.
            await cursor.execute(
                """
                        SELECT booking_id
                        FROM public.user_conversation_state
                        WHERE telegram_user_id = %s
                          AND state = 'awaiting_receipt_choice'
                        FOR UPDATE
                        """,
                (user_id,),
            )

            state_row = await cursor.fetchone()

            if state_row is None:
                return ReceiptResult(
                    status="invalid_state",
                    message=("No tienes una oferta de comprobante pendiente."),
                )

            booking_id = state_row[0]

            # Consultar exclusivamente la dirección
            # que este usuario ya verificó.
            await cursor.execute(
                """
                        SELECT email
                        FROM public.verified_email_contacts
                        WHERE telegram_user_id = %s
                        """,
                (user_id,),
            )

            contact = await cursor.fetchone()

            if contact is None:
                return ReceiptResult(
                    status="no_verified_email",
                    message=("Todavía no tienes un correo verificado."),
                )

            email = contact[0]

            # Nunca confiar solamente en el ID
            # de reserva guardado en el estado.
            await cursor.execute(
                """
                        SELECT
                            b.id,
                            b.title,
                            b.start_at,
                            b.end_at,
                            r.name
                        FROM public.bookings AS b
                        JOIN public.booking_resources AS r
                            ON r.id = b.resource_id
                        WHERE b.id = %s
                          AND b.telegram_user_id = %s
                          AND b.status = 'confirmed'
                        """,
                (booking_id, user_id),
            )

            booking = await cursor.fetchone()

            if booking is None:
                return ReceiptResult(
                    status="invalid_booking",
                    message=("No pude encontrar una reserva confirmada asociada a esta solicitud."),
                )

            (
                confirmed_id,
                title,
                start_at,
                end_at,
                service_name,
            ) = booking

            timezone_chile = ZoneInfo("America/Santiago")

            local_start = start_at.astimezone(timezone_chile)
            local_end = end_at.astimezone(timezone_chile)

            subject = f"Confirmación de reserva AG-{confirmed_id:06d}"

            body = (
                "Tu reserva fue confirmada.\n\n"
                f"Código: AG-{confirmed_id:06d}\n"
                f"Servicio: {service_name}\n"
                f"Motivo: {title}\n"
                "Fecha: "
                f"{local_start:%d/%m/%Y}\n"
                "Horario: "
                f"{local_start:%H:%M} - "
                f"{local_end:%H:%M}\n"
                "Zona horaria: America/Santiago\n"
                "Estado: Confirmada\n\n"
                "Comprobante generado por AgendaAI."
            )

            # La restricción UNIQUE(booking_id)
            # impide generar otro envío para
            # la misma reserva.
            await cursor.execute(
                """
                        INSERT INTO public.email_notifications (
                            booking_id,
                            recipient,
                            subject,
                            body,
                            status
                        )
                        VALUES (%s, %s, %s, %s, 'pending')
                        ON CONFLICT (booking_id)
                        DO NOTHING
                        RETURNING id
                        """,
                (
                    confirmed_id,
                    email,
                    subject,
                    body,
                ),
            )

            notification = await cursor.fetchone()

            # Finalizar la conversación sobre
            # este comprobante.
            await cursor.execute(
                """
                        UPDATE public.user_conversation_state
                        SET state = 'idle',
                            booking_id = NULL,
                            pending_email = NULL,
                            verification_hash = NULL,
                            verification_expires_at = NULL,
                            verification_attempts = 0,
                            last_code_sent_at = NULL,
                            verification_delivery_status = 'none',
                            verification_delivery_error = NULL,
                            updated_at = NOW()
                        WHERE telegram_user_id = %s
                        """,
                (user_id,),
            )

            if notification is None:
                return ReceiptResult(
                    status="already_queued",
                    message=(
                        "El comprobante de esta "
                        "reserva ya estaba registrado "
                        "para su envío. No generaré "
                        "un duplicado."
                    ),
                )

            # Mostrar parcialmente el correo,
            # sin exponerlo completo en el chat.
            local_part, domain = email.rsplit("@", 1)

            masked_email = (
                local_part[0]
                + "*"
                * min(
                    max(len(local_part) - 1, 1),
                    8,
                )
                + "@"
                + domain
            )

            return ReceiptResult(
                status="queued",
                message=(
                    "Perfecto. Utilizaré tu correo "
                    "verificado: "
                    f"{masked_email}.\n\n"
                    "El comprobante quedó preparado "
                    "para su envío. No necesitas "
                    "verificar tu correo nuevamente."
                ),
            )
