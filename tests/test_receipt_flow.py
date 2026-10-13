import asyncio

from agendaai.conversation_state import (
    ConversationStage,
    ConversationState,
)
from agendaai.receipt_flow import ReceiptFlow
from agendaai.receipt_service import ReceiptResult


class FakeStates:
    def __init__(self, stage):
        self.stage = stage
        self.booking_id = 25

    async def get_state(self, user_id):
        return ConversationState(
            telegram_user_id=user_id,
            state=self.stage,
            booking_id=self.booking_id,
            pending_email=None,
        )

    async def clear_state(self, *, user_id):
        self.stage = ConversationStage.IDLE

    async def move_to_email_input(self, *, user_id):
        if self.stage != (ConversationStage.AWAITING_RECEIPT_CHOICE):
            return False

        self.stage = ConversationStage.AWAITING_EMAIL
        return True


class FakeReceipts:
    def __init__(self):
        self.last_email = None
        self.last_code = None

    async def request_verification(
        self,
        *,
        user_id,
        email,
    ):
        self.last_email = email

        return ReceiptResult(
            status="verification_sent",
            message="Código de verificación enviado.",
        )

    async def verify_code(
        self,
        *,
        user_id,
        code,
    ):
        self.last_code = code

        return ReceiptResult(
            status="queued",
            message="Comprobante encolado.",
        )

    async def enqueue_verified_receipt(
        self,
        *,
        user_id,
    ):
        return ReceiptResult(
            status="no_verified_email",
            message="No existe correo verificado.",
        )


def test_normal_conversation_goes_to_gemini():
    async def scenario():
        states = FakeStates(ConversationStage.IDLE)

        flow = ReceiptFlow(
            states=states,
            receipts=FakeReceipts(),
        )

        response = await flow.handle(
            user_id=123,
            message="¿Qué horarios tienes mañana?",
        )

        assert response.handled is False

    asyncio.run(scenario())


def test_user_accepts_receipt():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_RECEIPT_CHOICE)

        flow = ReceiptFlow(
            states=states,
            receipts=FakeReceipts(),
        )

        response = await flow.handle(
            user_id=123,
            message="Sí, por favor",
        )

        assert response.handled is True
        assert "dirección de correo" in response.text
        assert states.stage == ConversationStage.AWAITING_EMAIL

    asyncio.run(scenario())


def test_user_rejects_receipt():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_RECEIPT_CHOICE)

        flow = ReceiptFlow(
            states=states,
            receipts=FakeReceipts(),
        )

        response = await flow.handle(
            user_id=123,
            message="No, gracias",
        )

        assert response.handled is True
        assert states.stage == ConversationStage.IDLE

    asyncio.run(scenario())


def test_user_provides_email():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_EMAIL)
        receipts = FakeReceipts()

        flow = ReceiptFlow(
            states=states,
            receipts=receipts,
        )

        response = await flow.handle(
            user_id=123,
            message="persona@example.com",
        )

        assert response.handled is True
        assert receipts.last_email == "persona@example.com"

    asyncio.run(scenario())


def test_user_provides_verification_code():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_VERIFICATION)
        receipts = FakeReceipts()

        flow = ReceiptFlow(
            states=states,
            receipts=receipts,
        )

        response = await flow.handle(
            user_id=123,
            message="123456",
        )

        assert response.handled is True
        assert receipts.last_code == "123456"

    asyncio.run(scenario())


def test_cancel_receipt_does_not_cancel_booking():
    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_VERIFICATION)

        flow = ReceiptFlow(
            states=states,
            receipts=FakeReceipts(),
        )

        response = await flow.handle(
            user_id=123,
            message="cancelar comprobante",
        )

        assert response.handled is True
        assert states.stage == ConversationStage.IDLE
        assert "reserva" in response.text.lower()

    asyncio.run(scenario())


def test_user_with_verified_email_does_not_enter_email_input():
    class VerifiedReceipts(FakeReceipts):
        async def enqueue_verified_receipt(
            self,
            *,
            user_id,
        ):
            return ReceiptResult(
                status="queued",
                message=("Comprobante preparado para h********@gmail.com"),
            )

    async def scenario():
        states = FakeStates(ConversationStage.AWAITING_RECEIPT_CHOICE)

        flow = ReceiptFlow(
            states=states,
            receipts=VerifiedReceipts(),
        )

        response = await flow.handle(
            user_id=123,
            message="Sí, por favor",
        )

        assert response.handled is True
        assert "h********@gmail.com" in response.text

        # Es fundamental que NO avance
        # a solicitar la dirección nuevamente.
        assert states.stage == ConversationStage.AWAITING_RECEIPT_CHOICE

    asyncio.run(scenario())
