"""Webhook HTTPS de Meta: valida firma, guarda el mensaje y responde inmediatamente.

Ejecutar: uvicorn whatsapp_gateway.app:app --host 0.0.0.0 --port 8000
"""
import asyncio
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from whatsapp_gateway.utils import verify_signature

from fastapi import FastAPI, HTTPException, Query, Request
from dotenv import load_dotenv
from pathlib import Path
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)
_pool: AsyncConnectionPool | None = None

from whatsapp_gateway.worker import main as worker_main


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool

    load_dotenv(
        Path(__file__).resolve().parents[1] / ".env"
    )

    database_url = os.environ["DATABASE_URL"]

    _pool = AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=3,
        open=False,
    )

    worker_task = None

    try:
        await _pool.open()
        await _pool.wait()

        log.info("WhatsApp: conexión con Supabase OK")

        # Iniciar el trabajador en segundo plano.
        worker_task = asyncio.create_task(
            worker_main(),
            name="agendaai-whatsapp-worker",
        )

        log.info("WhatsApp: trabajador iniciado")

        yield

    finally:
        # Detener el trabajador antes de cerrar Supabase.
        if worker_task is not None:
            worker_task.cancel()

            results = await asyncio.gather(
                worker_task,
                return_exceptions=True,
            )

            for result in results:
                if (
                    isinstance(result, BaseException)
                    and not isinstance(
                        result, asyncio.CancelledError
                    )
                ):
                    log.error(
                        "El trabajador terminó con error: %r",
                        result,
                    )

        await _pool.close()
        _pool = None

        log.info("WhatsApp: servidor detenido")



app = FastAPI(lifespan=lifespan)


@app.get('/health')
async def health():
    return {'status': 'ok'}


@app.get('/webhook')
async def meta_verify(
    hub_mode: str | None = Query(None, alias='hub.mode'),
    hub_challenge: str | None = Query(None, alias='hub.challenge'),
    hub_verify_token: str | None = Query(None, alias='hub.verify_token'),
):
    from fastapi.responses import PlainTextResponse
    expected = os.environ['WHATSAPP_VERIFY_TOKEN']
    if hub_mode != 'subscribe' or not hmac.compare_digest(hub_verify_token or '', expected):
        raise HTTPException(status_code=403, detail='Token no válido')
    return PlainTextResponse(hub_challenge or '')


@app.post('/webhook')
async def receive(request: Request):
    body = await request.body()
    if not verify_signature(body, request.headers.get('X-Hub-Signature-256'), os.environ['META_APP_SECRET']):
        raise HTTPException(status_code=403, detail='Firma no válida')
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail='JSON no válido')
    if payload.get('object') != 'whatsapp_business_account':
        return {'ok': True}
    phone_id_expected = os.environ['WHATSAPP_PHONE_NUMBER_ID']
    async with _pool.connection() as conn:
        async with conn.transaction():
            for entry in payload.get('entry', []):
                for change in entry.get('changes', []):
                    value = change.get('value', {})
                    phone_id = str(value.get('metadata', {}).get('phone_number_id', ''))
                    if phone_id != phone_id_expected:
                        continue
                    for msg in value.get('messages', []):
                        if msg.get('type') != 'text':
                            continue  # otros tipos requieren una implementación separada
                        mid = str(msg.get('id', ''))
                        wa_id = str(msg.get('from', ''))
                        content = str(msg.get('text', {}).get('body', '')).strip()
                        try:
                            sent_at = datetime.fromtimestamp(int(msg['timestamp']), tz=timezone.utc)
                        except (KeyError, TypeError, ValueError, OverflowError):
                            continue
                        if not mid or not wa_id or not content:
                            continue
                        inserted = await conn.execute('''
                            INSERT INTO public.wa_inbound_messages
                            (provider_message_id, phone_number_id, wa_id, message_text, provider_timestamp)
                            VALUES (%s,%s,%s,%s,%s)
                            ON CONFLICT (provider_message_id) DO NOTHING
                            RETURNING id
                        ''', (mid, phone_id, wa_id, content, sent_at))
                        new_row = await inserted.fetchone()
                        if new_row:
                            await conn.execute('''
                              INSERT INTO public.wa_customer_windows(phone_number_id, wa_id, last_inbound_at)
                              VALUES (%s,%s,%s)
                              ON CONFLICT(phone_number_id,wa_id) DO UPDATE
                              SET last_inbound_at = GREATEST(
                                public.wa_customer_windows.last_inbound_at, EXCLUDED.last_inbound_at)
                            ''', (phone_id, wa_id, sent_at))
                    for status in value.get('statuses', []):
                        message_id = status.get('id')
                        state = status.get('status')
                        if state in ('delivered', 'read', 'failed') and message_id:
                            await conn.execute('''
                              UPDATE public.wa_outbound_messages
                              SET status = %s, updated_at = now(),
                                  last_error = CASE WHEN %s='failed' THEN %s ELSE last_error END
                              WHERE provider_message_id=%s
                            ''', (state, state, json.dumps(status.get('errors', []))[:1200], message_id))
    return {'ok': True}
