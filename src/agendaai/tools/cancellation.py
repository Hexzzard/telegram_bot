"""Cancelación autenticada de reservas, con bitácora y restricciones en PostgreSQL.

El ID de usuario SIEMPRE proviene de update.effective_user.id (Telegram).
El LLM no puede proporcionar ni reemplazar esa identidad.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from google.genai import types
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)
TIMEZONE = ZoneInfo("America/Santiago")

CANCELLATION_TOOL = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="list_my_bookings",
        description=(
            "Lista hasta cinco reservas futuras CONFIRMADAS del usuario que escribe. "
            "Usa esta herramienta si desea cancelar pero no conoce su código AG-. "
            "Nunca muestra citas de otros usuarios."
        ),
        parameters_json_schema={"type": "object", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="cancel_my_booking",
        description=(
            "Cancela una reserva del propio usuario identificado por su código AG- numérico. "
            "Úsala SOLO ante una petición explícita de cancelación en el mensaje ACTUAL "
            "y cuando el mensaje ACTUAL incluya ese mismo código. "
            "Nunca canceles al consultar políticas, disponibilidad o al mencionar un código pasado. "
            "Exige 12 horas de anticipación según la política de la veterinaria."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {"booking_id": {"type": "integer", "description": "Número del código AG-000123"}},
            "required": ["booking_id"],
        },
    ),
])


class CancellationToolExecutor:
    def __init__(self, *, pool: AsyncConnectionPool, minimum_notice_hours: int = 12):
        self._pool = pool
        self._notice_hours = minimum_notice_hours

    async def list_my_bookings(self, *, user_id: int) -> str:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT id, title, start_at FROM public.bookings
                       WHERE telegram_user_id=%s AND status='confirmed'
                         AND start_at > now()
                       ORDER BY start_at, id LIMIT 5""",
                    (user_id,),
                )
                rows = await cur.fetchall()
        if not rows:
            return "No encuentro reservas futuras confirmadas asociadas a tu cuenta de Telegram."
        lines = ["Estas son tus próximas reservas:"]
        for booking_id, title, start in rows:
            lines.append(f"• AG-{booking_id:06d} — {start.astimezone(TIMEZONE):%d/%m/%Y a las %H:%M} — {title}")
        lines.append("Para cancelar, escribe explícitamente «Cancela AG-000123» con el código que corresponda.")
        return "\n".join(lines)

    @staticmethod
    def explicit_confirmation(message: str, booking_id: int) -> bool:
        """Nunca operar sobre un código inferido del historial o inventado por Gemini."""
        if booking_id < 1:
            return False
        code = re.search(rf"(?<![A-Za-z0-9])AG\s*-\s*0*{booking_id}(?!\d)", message, re.I)
        # Distinguir órdenes de preguntas como «¿Puedo cancelar AG-123?».
        intent = re.search(
            r"(?:^|[.!;]\s*)(?:por favor[, ]*)?"
            r"(?:cancela|cancélame|anula|elimina|borra)\b"
            r"|\b(?:quiero|deseo|necesito|solicito)\s+"
            r"(?:cancelar|anular|eliminar|borrar)\b",
            message, re.I,
        )
        return bool(code and intent)

    async def cancel_my_booking(self, *, user_id: int, booking_id: int, current_message: str) -> str:
        if not isinstance(booking_id, int) or isinstance(booking_id, bool) or booking_id < 1:
            return "Indica un código de reserva válido, por ejemplo AG-000123."
        if not self.explicit_confirmation(current_message, booking_id):
            return (
                "Para asegurarme de cancelar la cita correcta, escríbeme "
                f"«Cancela AG-{booking_id:06d}». Tu hora sigue reservada por ahora."
            )
        # Una sola transacción, bloqueo del registro y validación de propiedad en SQL.
        async with self._pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        """SELECT status, start_at FROM public.bookings
                           WHERE id=%s AND telegram_user_id=%s FOR UPDATE""",
                        (booking_id, user_id),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        # Mensaje indistinguible entre no existente y ajeno (evita enumeración).
                        return "No encuentro una reserva con ese código asociada a tu cuenta de Telegram."
                    status, start_at = row
                    if status == "cancelled":
                        return f"La reserva AG-{booking_id:06d} ya estaba cancelada."
                    if status != "confirmed":
                        return "Esta reserva no está confirmada y no puedo cancelarla."
                    await cur.execute(
                        """UPDATE public.bookings SET status='cancelled'
                           WHERE id=%s AND telegram_user_id=%s
                             AND status='confirmed'
                             AND start_at > now() + (%s * interval '1 hour')
                           RETURNING start_at""",
                        (booking_id, user_id, self._notice_hours),
                    )
                    updated = await cur.fetchone()
                    if updated is None:
                        return (
                            f"La reserva AG-{booking_id:06d} está demasiado próxima. "
                            f"Según la política se solicitan al menos {self._notice_hours} horas "
                            "de anticipación. Contacta a recepción para revisar el caso. "
                            "No he cancelado la reserva."
                        )
        logger.info("Reserva cancelada por propietario: id=%s user=%s", booking_id, user_id)
        start_local = updated[0].astimezone(TIMEZONE)
        return (
            f"✅ Tu reserva AG-{booking_id:06d} del {start_local:%d/%m/%Y a las %H:%M} "
            "quedó cancelada. 🐾 La hora vuelve a estar disponible. "
            "¿Te gustaría que busquemos otro horario?"
        )
