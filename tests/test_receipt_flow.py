"""Pruebas del flujo de comprobantes sin OTP.

No se conectan a Supabase ni envían correos: sustituyen ambos servicios por dobles.
"""
import asyncio

from agendaai.conversation_state import ConversationStage, ConversationState
from agendaai.receipt_flow import ReceiptFlow
from agendaai.receipt_service import ReceiptResult


class FakeStates:
    def __init__(self, stage):
        self.stage = stage
        self.booking_id = 25
        self.clear_calls = 0

    async def get_state(self, user_id):
        return ConversationState(
            telegram_user_id=user_id,
            state=self.stage,
            booking_id=self.booking_id,
            pending_email=None,
        )

    async def clear_state(self, *, user_id):
        self.clear_calls += 1
        self.stage = ConversationStage.IDLE

    async def move_to_email_input(self, *, user_id):
        if self.stage != ConversationStage.AWAITING_RECEIPT_CHOICE:
            return False
        self.stage = ConversationStage.AWAITING_EMAIL
        return True


class FakeReceipts:
    def __init__(self, previous_email=None):
        self.last_email = None
        self.last_user_id = None
        self.previous_email = previous_email
        self.lookup_user_id = None
        self.enqueue_count = 0

    async def enqueue_receipt(self, *, user_id, email):
        self.enqueue_count += 1
        self.last_user_id = user_id
        self.last_email = email
        return ReceiptResult(
            status="queued",
            message="¡Perfecto! Dejé preparado el envío del comprobante.",
        )

    async def last_receipt_email(self, *, user_id):
        self.lookup_user_id = user_id
        return self.previous_email


def test_normal_conversation_goes_to_gemini():
    async def scenario():
        states = FakeStates(ConversationStage.IDLE)
        receipts = FakeReceipts()
        response = await ReceiptFlow(states=states, receipts=receipts).handle(
            user_id=123, message="¿Qué horarios tienes mañana?"
        )
        assert response.handled is False
        assert receipts.enqueue_count == 0
    asyncio.run(scenario())


def test_user_accepts_receipt():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_RECEIPT_CHOICE)
        response = await ReceiptFlow(states=states, receipts=FakeReceipts()).handle(
            user_id=123, message="Sí, por favor"
        )
        assert response.handled is True
        assert "correo" in response.text.lower()
        assert states.stage == ConversationStage.AWAITING_EMAIL
    asyncio.run(scenario())


def test_user_rejects_receipt():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_RECEIPT_CHOICE)
        response = await ReceiptFlow(states=states, receipts=FakeReceipts()).handle(
            user_id=123, message="No, gracias"
        )
        assert response.handled is True
        assert states.stage == ConversationStage.IDLE
        assert "reserva" in response.text.lower()
    asyncio.run(scenario())


def test_user_provides_email_without_otp():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_EMAIL)
        receipts = FakeReceipts()
        response = await ReceiptFlow(states=states, receipts=receipts).handle(
            user_id=123, message="persona@example.com"
        )
        assert response.handled is True
        assert receipts.last_email == "persona@example.com"
        assert receipts.last_user_id == 123
        assert receipts.enqueue_count == 1
        assert "código" not in response.text.lower()
    asyncio.run(scenario())


def test_can_provide_email_directly_when_offered_receipt():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_RECEIPT_CHOICE)
        receipts = FakeReceipts()
        response = await ReceiptFlow(states=states, receipts=receipts).handle(
            user_id=123, message="persona@example.com"
        )
        assert response.handled is True
        assert states.stage == ConversationStage.AWAITING_EMAIL
        assert receipts.last_email == "persona@example.com"
    asyncio.run(scenario())


def test_legacy_verification_stage_is_closed_without_validating_otp():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_VERIFICATION)
        receipts = FakeReceipts()
        response = await ReceiptFlow(states=states, receipts=receipts).handle(
            user_id=123, message="123456"
        )
        assert response.handled is True
        assert states.stage == ConversationStage.IDLE
        assert receipts.enqueue_count == 0
        assert "otp" in response.text.lower()
    asyncio.run(scenario())


def test_cancel_receipt_does_not_cancel_booking():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_EMAIL)
        receipts = FakeReceipts()
        response = await ReceiptFlow(states=states, receipts=receipts).handle(
            user_id=123, message="cancelar comprobante"
        )
        assert response.handled is True
        assert states.stage == ConversationStage.IDLE
        assert states.booking_id == 25
        assert receipts.enqueue_count == 0
        assert "reserva" in response.text.lower()
    asyncio.run(scenario())


def test_reuse_last_receipt_email_only_on_explicit_request():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_EMAIL)
        receipts = FakeReceipts(previous_email="antes@example.com")
        response = await ReceiptFlow(states=states, receipts=receipts).handle(
            user_id=123, message="al último correo que te envié"
        )
        assert response.handled is True
        assert receipts.lookup_user_id == 123
        assert receipts.last_email == "antes@example.com"
        assert receipts.enqueue_count == 1
    asyncio.run(scenario())


def test_missing_previous_email_asks_for_address():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_EMAIL)
        receipts = FakeReceipts(previous_email=None)
        response = await ReceiptFlow(states=states, receipts=receipts).handle(
            user_id=123, message="al correo anterior"
        )
        assert response.handled is True
        assert "correo" in response.text.lower()
        assert receipts.enqueue_count == 0
    asyncio.run(scenario())
