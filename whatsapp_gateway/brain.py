"""Adaptador WhatsApp al mismo GeminiService y ReceiptFlow de Telegram."""
from __future__ import annotations
import os
from psycopg_pool import AsyncConnectionPool
from agendaai.agent_factory import AgentRuntime
from agendaai.channel_identity import whatsapp_principal


class WhatsAppBrain:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool
        self.runtime = AgentRuntime(
            pool=pool, gemini_key=os.environ['GEMINI_API_KEY'],
            gemini_model=os.getenv('GEMINI_MODEL', 'gemini-2.5-flash'),
            resource_id=int(os.getenv('BOOKING_RESOURCE_ID','1')),
            resend_key=os.environ['RESEND_API_KEY'],
            email_from=os.environ['EMAIL_FROM'], debug_sources=False,
        )

    async def close(self):
        await self.runtime.close()

    async def answer(self, phone_id: str, wa_id: str, message: str,
                     inbound_id: int) -> str:
        principal_id = await whatsapp_principal(self.pool,phone_id,wa_id)
        flow = await self.runtime.receipt_flow.handle(
            user_id=principal_id, message=message)
        if flow.handled:
            return flow.text
        return await self.runtime.agent.answer(
            user_id=principal_id, message=message, request_id=inbound_id)
