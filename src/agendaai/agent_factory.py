"""Construye un único núcleo AgendaAI para ambos canales.

La identidad `user_id` es un ID interno asignado por el adaptador de canal.
El esquema legado usa columnas `telegram_user_id`, que se migrarán
en una fase posterior sin romper las reservas existentes.
"""
from google import genai
from psycopg_pool import AsyncConnectionPool
from agendaai.database import ConversationRepository
from agendaai.booking_repository import BookingRepository
from agendaai.booking_service import BookingService
from agendaai.availability_service import AvailabilityService
from agendaai.conversation_state import ConversationStateRepository
from agendaai.receipt_service import ReceiptService
from agendaai.receipt_flow import ReceiptFlow
from agendaai.gemini_service import GeminiService
from agendaai.tools.availability import AvailabilityToolExecutor
from agendaai.tools.booking import BookingToolExecutor
from agendaai.tools.cancellation import CancellationToolExecutor
from agendaai.tools.knowledge import KnowledgeToolExecutor


class AgentRuntime:
    def __init__(self, *, pool: AsyncConnectionPool, gemini_key: str,
                 gemini_model: str, resource_id: int, resend_key: str,
                 email_from: str, debug_sources: bool = False):
        self.embedding_client = genai.Client(api_key=gemini_key)
        states = ConversationStateRepository(pool)
        booking_repo = BookingRepository(pool)
        self.receipt_flow = ReceiptFlow(
            states=states,
            receipts=ReceiptService(database_url=pool, resend_api_key=resend_key,
                                    email_from=email_from, verification_secret=''),
        )
        self.agent = GeminiService(
            api_key=gemini_key, model=gemini_model,
            repository=ConversationRepository(pool),
            availability_executor=AvailabilityToolExecutor(
                service=AvailabilityService(booking_repo), resource_id=resource_id),
            booking_executor=BookingToolExecutor(
                service=BookingService(booking_repo, resource_id=resource_id),
                conversation_states=states),
            knowledge_executor=KnowledgeToolExecutor(
                pool=pool, client=self.embedding_client, limit=5),
            cancellation_executor=CancellationToolExecutor(
                pool=pool, minimum_notice_hours=12),
            max_turns=10, debug_sources=debug_sources,
        )

    async def close(self):
        await self.embedding_client.aio.aclose()
        await self.agent._client.aio.aclose()
