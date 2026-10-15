"""Worker WhatsApp con GeminiService compartido, límite mensual y envío seguro."""
import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from whatsapp_gateway.utils import month_key
from whatsapp_gateway.brain import WhatsAppBrain

import httpx
from dotenv import load_dotenv
from pathlib import Path
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)



# Presentación por canal: Gemini redacta el texto y WhatsApp adjunta un botón.
# Mostrar el botón solo cuando la persona pide el calendario expresamente,
# o cuando el agente realmente enumera varios horarios disponibles.
# No activar ante palabras genéricas: "hora", "agendar", "reserva", etc.
_EXPLICIT_CALENDAR = re.compile(
    r"(?:calendario|(?:link|enlace|bot[oó]n|url).{0,35}(?:horarios?|horas?|disponib|calendario)|"
    r"(?:horarios?|horas?|disponib).{0,35}(?:link|enlace|bot[oó]n|url))",
    re.IGNORECASE,
)
_AVAILABILITY_REQUEST = re.compile(
    r"(?:horarios?|horas?)\s+disponibles?|disponibilidad|"
    r"(?:lista|listado|mostrar|mu[eé]strame|ver|cu[aá]les).{0,55}(?:horarios?|horas?)|"
    r"(?:horarios?|horas?).{0,35}(?:libres|disponibles?)",
    re.IGNORECASE,
)
_SLOT_TIME = re.compile(r"(?<!\d)(?:0?[89]|1[0-6]):(?:00|30)(?!\d)")
_BOOKING_RESULT = re.compile(
    r"(?:reserva|cita|hora)\s+(?:qued[oó]|ha sido|est[aá])\s+(?:confirmada|agendada|registrada)|"
    r"(?:reserva|cita)\s+(?:confirmada|realizada)|c[oó]digo:\s*AG[-\d]",
    re.IGNORECASE,
)


def should_attach_calendar(incoming_text: str, answer: str) -> bool:
    """CTA contextual: pedido explícito o listado real; nunca confirmaciones."""
    if _BOOKING_RESULT.search(answer):
        return False
    if _EXPLICIT_CALENDAR.search(incoming_text):
        return True
    if not _AVAILABILITY_REQUEST.search(incoming_text):
        return False
    # Si Gemini no enumeró horas, no mostrar un calendario por adivinación.
    return len(set(_SLOT_TIME.findall(answer))) >= 2


def calendar_url() -> str | None:
    """Solo se permite el calendario público explícitamente configurado."""
    url = os.getenv("GOOGLE_PUBLIC_CALENDAR_URL", "").strip()
    return url if url.startswith("https://calendar.google.com/") else None


def strip_legacy_calendar_footer(body: str, public_url: str) -> str:
    """Retira el enlace añadido por parches antiguos, conservando el texto de Gemini."""
    escaped = re.escape(public_url)
    body = re.sub(
        r"\n*📅\s*Haz clic aquí para ver el calendario:\s*\n*" + escaped + r"\s*$",
        "", body, flags=re.IGNORECASE,
    )
    # Pie añadido por GeminiService al listar horarios (otra versión del parche).
    body = re.sub(
        r"\n*📅\s*Calendario público de disponibilidad:\s*\n*" + escaped + r"\s*$",
        "", body, flags=re.IGNORECASE,
    )
    # Si un parche anterior insertó solo el enlace, evita exhibirlo como texto.
    body = re.sub(r"\n*" + escaped + r"\s*$", "", body)
    return body.strip()


def outgoing_message(body: str, incoming_text: str) -> tuple[str, str | None]:
    """El texto es de Gemini; la decisión de mostrar el botón es del canal."""
    url = calendar_url()
    if not url:
        return body, None
    body = strip_legacy_calendar_footer(body, url)
    if not should_attach_calendar(incoming_text, body):
        return body, None
    # Límite de Meta para el cuerpo de mensajes interactivos CTA URL.
    if len(body) > 1024:
        log.warning("Respuesta demasiado larga para botón CTA (%s caracteres)", len(body))
        return body, None
    return body, url


async def claim_inbound(pool):
    async with pool.connection() as conn:
        async with conn.transaction():
            result = await conn.execute('''
              SELECT id, phone_number_id, wa_id, message_text
              FROM public.wa_inbound_messages
              WHERE status='pending' OR (status='processing' AND processing_at < now() - interval '5 minutes'
                AND NOT EXISTS (SELECT 1 FROM public.wa_outbound_messages o WHERE o.inbound_id = wa_inbound_messages.id))
              ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
            ''')
            row = await result.fetchone()
            if not row:
                return None
            await conn.execute('''
              UPDATE public.wa_inbound_messages
              SET status='processing', processing_at=now() WHERE id=%s
            ''', (row[0],))
            return row


async def reserve_send(pool, inbound_id: int, phone_id: str, wa_id: str, body: str,
                       limit: int, tz_name: str):
    """Reserva un cupo de forma transaccional: cuenta intentos, no solo entregas.

    Una respuesta ya reservada nunca se envía dos veces automáticamente.
    """
    period = month_key(datetime.now(timezone.utc), tz_name)
    async with pool.connection() as conn:
        async with conn.transaction():
            # Serializar por número y período frente a múltiples workers concurrentes.
            await conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))', (phone_id, period))
            found = await conn.execute('''
              SELECT id FROM public.wa_outbound_messages WHERE inbound_id=%s
            ''', (inbound_id,))
            if await found.fetchone():
                return None, 'already_reserved'
            cursor = await conn.execute('''
              SELECT COUNT(*) FROM public.wa_outbound_messages
              WHERE phone_number_id=%s AND month_key=%s
            ''', (phone_id, period))
            used = (await cursor.fetchone())[0]
            if used >= limit:
                return None, 'monthly_limit'
            window = await conn.execute('''
              SELECT last_inbound_at FROM public.wa_customer_windows
              WHERE phone_number_id=%s AND wa_id=%s
            ''', (phone_id, wa_id))
            last = await window.fetchone()
            if not last or datetime.now(timezone.utc) >= last[0] + timedelta(hours=24):
                return None, 'window_closed'
            result = await conn.execute('''
              INSERT INTO public.wa_outbound_messages
                (inbound_id, phone_number_id, wa_id, month_key, body, status)
              VALUES (%s,%s,%s,%s,%s,'reserved') RETURNING id
            ''', (inbound_id, phone_id, wa_id, period, body))
            return (await result.fetchone())[0], 'reserved'


async def send_message(phone_id: str, wa_id: str, body: str, button_url: str | None = None) -> str:
    """Un solo envío facturable: texto normal o texto de Gemini con botón CTA."""
    version = os.getenv('WHATSAPP_GRAPH_VERSION', 'v23.0')
    token = os.environ['WHATSAPP_ACCESS_TOKEN']
    url = f'https://graph.facebook.com/{version}/{phone_id}/messages'
    if button_url:
        payload = {
            'messaging_product': 'whatsapp',
            'to': wa_id,
            'type': 'interactive',
            'interactive': {
                'type': 'cta_url',
                'body': {'text': body},
                'action': {
                    'name': 'cta_url',
                    'parameters': {
                        'display_text': '📅 Ver calendario',
                        'url': button_url,
                    },
                },
            },
        }
    else:
        payload = {
            'messaging_product': 'whatsapp',
            'to': wa_id,
            'type': 'text',
            'text': {'preview_url': False, 'body': body},
        }
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.post(
            url,
            headers={'Authorization': f'Bearer {token}'},
            json=payload,
        )
    if response.is_error:
        log.error('Meta rechazó envío WhatsApp (HTTP %s): %s',
                  response.status_code, response.text[:700])
    response.raise_for_status()
    return response.json()['messages'][0]['id']


async def can_respond(pool, phone_id: str, wa_id: str, limit: int, tz_name: str):
    # Verificación previa para no confirmar reservas sin capacidad de responder.
    period = month_key(datetime.now(timezone.utc), tz_name)
    async with pool.connection() as conn:
        count = await (await conn.execute(
            'SELECT COUNT(*) FROM public.wa_outbound_messages WHERE phone_number_id=%s AND month_key=%s',
            (phone_id, period))).fetchone()
        if count[0] >= limit:
            return False, 'monthly_limit'
        window = await (await conn.execute(
            'SELECT last_inbound_at FROM public.wa_customer_windows WHERE phone_number_id=%s AND wa_id=%s',
            (phone_id, wa_id))).fetchone()
        if not window or datetime.now(timezone.utc) >= window[0] + timedelta(hours=24):
            return False, 'window_closed'
    return True, 'ok'


async def process_one(pool, limit: int, tz_name: str, brain: WhatsAppBrain | None = None):
    incoming = await claim_inbound(pool)
    if not incoming:
        return False
    inbound_id, phone_id, wa_id, text = incoming
    allowed, reason = await can_respond(pool, phone_id, wa_id, limit, tz_name)
    if not allowed:
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE public.wa_inbound_messages SET status='done', error=%s WHERE id=%s",
                (reason, inbound_id))
        log.warning('Mensaje WhatsApp sin procesar: %s', reason)
        return True
    # El adaptador resuelve una identidad interna exclusiva antes de Gemini.
    mode = os.getenv('WHATSAPP_AGENT_MODE', 'disabled')
    if mode == 'demo':
        body = '🐾 ¡Hola! WhatsApp está conectado a AgendaAI en modo de prueba. Para reservar, usa nuestro bot de Telegram mientras activamos la identidad multicanal.'
    elif mode in ('gemini', 'shared') and brain is not None:
        body = await brain.answer(phone_id, wa_id, text, inbound_id)
    else:
        body = None
    if not body:
        async with pool.connection() as conn:
            await conn.execute('''UPDATE public.wa_inbound_messages
               SET status='done', error='Agente multicanal aún no conectado' WHERE id=%s''', (inbound_id,))
        return True
    # El contenido sigue siendo de Gemini; el canal añade un botón real de Meta.
    button_url = None
    if mode in ('gemini', 'shared'):
        body, button_url = outgoing_message(body, text)
    send_id, outcome = await reserve_send(pool, inbound_id, phone_id, wa_id, body, limit, tz_name)
    if send_id is None:
        log.warning('WhatsApp sin envío: %s (inbound=%s)', outcome, inbound_id)
        async with pool.connection() as conn:
            await conn.execute('''UPDATE public.wa_inbound_messages SET status='done', error=%s
                                  WHERE id=%s''', (outcome, inbound_id))
        return True
    try:
        provider_id = await send_message(phone_id, wa_id, body, button_url)
        state, err = 'accepted', None
    except Exception as exc:
        # Estado desconocido: un timeout podría haber entregado el mensaje.
        # No reintentamos automáticamente para evitar duplicación y sobrecuota.
        log.exception('Error envío WhatsApp: inbound=%s', inbound_id)
        provider_id = None
        state, err = 'unknown', str(exc)[:1200]
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute('''UPDATE public.wa_outbound_messages
                SET status=%s, provider_message_id=%s, last_error=%s, updated_at=now()
                WHERE id=%s''', (state, provider_id, err, send_id))
            await conn.execute('''UPDATE public.wa_inbound_messages
                SET status='done', error=%s WHERE id=%s''', (err, inbound_id))
    return True


async def main():
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    logging.basicConfig(level=logging.INFO)
    limit = int(os.getenv('WHATSAPP_SERVICE_MONTHLY_CAP','900'))
    if not 1 <= limit <= 900:
        raise ValueError('Se permite un tope de seguridad entre 1 y 900')
    tz_name = os.getenv('WHATSAPP_BILLING_TIMEZONE','America/Santiago')
    ZoneInfo(tz_name)
    pool = AsyncConnectionPool(os.environ['DATABASE_URL'], min_size=1, max_size=3, open=False)
    await pool.open()
    await pool.wait()
    mode = os.getenv('WHATSAPP_AGENT_MODE','disabled')
    brain = WhatsAppBrain(pool) if mode in ('gemini', 'shared') else None
    log.info('Worker iniciado (cap=%s, tz=%s, modo=%s)',limit,tz_name,mode)
    try:
        while True:
            try:
                if not await process_one(pool, limit, tz_name, brain):
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('Fallo al procesar webhook')
                await asyncio.sleep(5)
    finally:
        if brain is not None:
            await brain.close()
        await pool.close()


if __name__ == '__main__':
    import sys
    import selectors
    if sys.platform == 'win32':
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(main())
