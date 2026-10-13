import asyncio
from types import SimpleNamespace

from agendaai.gemini_service import GeminiService


class FakeRepository:
    def __init__(self):
        self.messages = []

    async def get_history(self, user_id, limit=20):
        return []

    async def save_exchange(
        self,
        user_id,
        user_message,
        model_message,
    ):
        self.messages.append((user_id, user_message, model_message))

    async def delete_history(self, user_id):
        self.messages.clear()


class FakeModels:
    def __init__(self, response):
        self.response = response

    def generate_content(self, **kwargs):
        return self.response


class FakeBookingExecutor:
    def __init__(self):
        self.received = None

    async def execute(
        self,
        arguments,
        *,
        user_id,
        telegram_update_id,
    ):
        self.received = (
            arguments,
            user_id,
            telegram_update_id,
        )
        return "RESERVA CONFIRMADA\nCódigo: AG-000001"


def test_booking_uses_real_telegram_identity():
    async def scenario():
        executor = FakeBookingExecutor()
        repository = FakeRepository()

        response = SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    name="create_booking",
                    args={
                        "title": "Asesoría general",
                        "start_at": ("2030-10-15T15:00:00-03:00"),
                    },
                )
            ],
            text=None,
        )

        service = GeminiService(
            api_key="fake-key",
            model="fake-model",
            repository=repository,
            booking_executor=executor,
        )

        service._client = SimpleNamespace(models=FakeModels(response))

        result = await service.answer(
            user_id=12345,
            message="Reserva una hora a las 15",
            request_id=98765,
        )

        assert "RESERVA CONFIRMADA" in result
        assert executor.received[1] == 12345
        assert executor.received[2] == 98765

    asyncio.run(scenario())


def test_booking_rejects_missing_request_id():
    async def scenario():
        executor = FakeBookingExecutor()

        response = SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    name="create_booking",
                    args={
                        "title": "Asesoría",
                        "start_at": ("2030-10-15T15:00:00-03:00"),
                    },
                )
            ],
            text=None,
        )

        service = GeminiService(
            api_key="fake-key",
            model="fake-model",
            repository=FakeRepository(),
            booking_executor=executor,
        )

        service._client = SimpleNamespace(models=FakeModels(response))

        result = await service.answer(
            user_id=12345,
            message="Reserva una hora",
        )

        assert "No puedo identificar" in result
        assert executor.received is None

    asyncio.run(scenario())
