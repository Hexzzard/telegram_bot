"""Encola comprobantes de reservas existentes sin verificación OTP.
IMPORTANTE: correo no verificado; no usarlo como identidad ni reutilizarlo automáticamente.
"""
import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from email_validator import EmailNotValidError, validate_email
from psycopg_pool import AsyncConnectionPool
from agendaai.db_connection import open_connection

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class ReceiptResult:
    status: str
    message: str

class ReceiptService:
    def __init__(self, *, database_url: str | AsyncConnectionPool,
                 resend_api_key: str = "", email_from: str = "",
                 verification_secret: str = ""):
        # Conservamos la firma del constructor para compatibilidad con main.py.
        # No almacenamos ni utilizamos verification_secret.
        self._database_url = database_url

    async def last_receipt_email(self, *, user_id: int) -> str | None:
        """Destinatario usado previamente por ESTE usuario de Telegram.

        La dirección NO queda verificada. Solo se reutiliza si el usuario lo
        solicita de forma explícita mientras está en el flujo de comprobante.
        """
        async with (open_connection(self._database_url) as conn,
                    conn.cursor() as cursor):
            await cursor.execute(
                """SELECT n.recipient
                   FROM public.email_notifications AS n
                   JOIN public.bookings AS b ON b.id = n.booking_id
                   WHERE b.telegram_user_id = %s
                     AND n.recipient IS NOT NULL
                   ORDER BY n.id DESC LIMIT 1""",
                (user_id,),
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def enqueue_receipt(self, *, user_id: int, email: str) -> ReceiptResult:
        try:
            address = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError:
            return ReceiptResult("invalid_email", "Ese correo no parece válido. ¿Puedes revisarlo?")
        tz = ZoneInfo("America/Santiago")
        async with (open_connection(self._database_url) as conn,
                    conn.transaction(), conn.cursor() as cursor):
            await cursor.execute("""SELECT booking_id FROM public.user_conversation_state
                                WHERE telegram_user_id=%s AND state='awaiting_email'
                                FOR UPDATE""", (user_id,))
            row = await cursor.fetchone()
            if row is None:
                return ReceiptResult("invalid_state", "No tengo una solicitud de comprobante activa.")
            booking_id = row[0]
            await cursor.execute("""SELECT b.id,b.title,b.client_name,b.start_at,b.end_at,r.name
                                FROM public.bookings b JOIN public.booking_resources r
                                ON r.id=b.resource_id
                                WHERE b.id=%s AND b.telegram_user_id=%s AND b.status='confirmed'""",
                                 (booking_id,user_id))
            booking = await cursor.fetchone()
            if booking is None:
                return ReceiptResult("invalid_booking", "La reserva no está confirmada o no pertenece a esta cuenta.")
            booking_id,reason,client_name,start,end,service_name=booking
            local_start,local_end=start.astimezone(tz),end.astimezone(tz)
            subject=f"Confirmación de reserva AG-{booking_id:06d}"
            body=("Tu reserva fue confirmada.\n\n"
                  f"Código: AG-{booking_id:06d}\nCliente: {client_name}\n"
                  f"Servicio: {service_name}\nMotivo: {reason}\n"
                  f"Fecha: {local_start:%d/%m/%Y}\nHorario: {local_start:%H:%M} - {local_end:%H:%M}\n"
                  "Zona horaria: America/Santiago\nEstado: Confirmada\n\n"
                  "Mensaje generado por AgendaAI. El correo destinatario no ha sido verificado.")
            await cursor.execute("""INSERT INTO public.email_notifications
                    (booking_id, recipient, subject, body, status)
                    VALUES (%s,%s,%s,%s,'pending')
                    ON CONFLICT (booking_id) DO NOTHING RETURNING id""",
                    (booking_id,address,subject,body))
            notification=await cursor.fetchone()
            await cursor.execute("""UPDATE public.user_conversation_state SET state='idle',
                     booking_id=NULL,pending_email=NULL,verification_hash=NULL,
                     verification_expires_at=NULL,verification_attempts=0,
                     last_code_sent_at=NULL,verification_delivery_status='none',
                     verification_delivery_error=NULL,updated_at=NOW()
                     WHERE telegram_user_id=%s""",(user_id,))
        if notification is None:
            return ReceiptResult("already_queued", "El comprobante de esa reserva ya estaba registrado para envío.")
        logger.info("Comprobante encolado para reserva %s",booking_id)
        return ReceiptResult("queued", "¡Perfecto! 🐾 Dejé preparado el envío del comprobante al correo que elegiste.")
