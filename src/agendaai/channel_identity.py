"""Identidad privada de WhatsApp; aislamiento por número empresarial y cliente.

Se reservan IDs internos negativos para que nunca colisionen con Telegram.
No es vinculación entre cuentas: una persona en ambos canales tiene
dos identidades separadas hasta realizar una vinculación explícita.
"""
from psycopg_pool import AsyncConnectionPool


async def whatsapp_principal(pool: AsyncConnectionPool, phone_id: str, wa_id: str) -> int:
    if not phone_id or not wa_id or not wa_id.isdigit():
        raise ValueError("Identidad WhatsApp no válida")
    async with pool.connection() as conn:
        async with conn.transaction():
            row = await (await conn.execute(
                """SELECT principal_id FROM public.wa_channel_identities
                   WHERE phone_number_id=%s AND wa_id=%s""",
                (phone_id, wa_id),
            )).fetchone()
            if row:
                return int(row[0])
            # El ON CONFLICT protege carreras entre workers.
            row = await (await conn.execute(
                """INSERT INTO public.wa_channel_identities
                       (phone_number_id,wa_id,principal_id)
                   VALUES (%s,%s,nextval('public.wa_principal_id_seq'))
                   ON CONFLICT (phone_number_id,wa_id) DO UPDATE
                   SET wa_id=EXCLUDED.wa_id
                   RETURNING principal_id""", (phone_id,wa_id),
            )).fetchone()
            principal = int(row[0])
            await conn.execute(
                """INSERT INTO public.bot_users (telegram_user_id) VALUES (%s)
                   ON CONFLICT (telegram_user_id) DO NOTHING""",(principal,),
            )
            return principal
