import asyncio
from types import SimpleNamespace

from agendaai.database import ChatMessage
from agendaai.gemini_service import GeminiService


class FakeRepository:
    def __init__(self):
        self.messages = {}

    async def get_history(self, user_id, limit=20):
        return self.messages.get(user_id, [])[-limit:]

    async def save_exchange(
        self,
        user_id,
        user_message,
        model_message,
    ):
        history = self.messages.setdefault(user_id, [])

        history.extend(
            [
                ChatMessage("user", user_message),
                ChatMessage("model", model_message),
            ]
        )

    async def delete_history(self, user_id):
        self.messages.pop(user_id, None)


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(
        self,
        *,
        model,
        contents,
        config,
    ):
        self.calls.append(list(contents))

        return SimpleNamespace(
            text="Respuesta de prueba",
            function_calls=[],
        )


def make_service():
    repository = FakeRepository()

    service = GeminiService(
        api_key="test-key",
        model="test-model",
        repository=repository,
        max_turns=2,
    )

    service._client = SimpleNamespace(models=FakeModels())

    return service, repository


def test_user_histories_are_independent():
    async def scenario():
        service, repository = make_service()

        await service.answer(101, "Hola A")
        await service.answer(202, "Hola B")

        assert len(repository.messages[101]) == 2
        assert len(repository.messages[202]) == 2

        calls = service._client.models.calls

        assert len(calls[0]) == 1
        assert len(calls[1]) == 1

        await service.answer(101, "Segundo mensaje A")

        assert len(calls[2]) == 3

    asyncio.run(scenario())


def test_history_window():
    async def scenario():
        service, repository = make_service()

        for number in range(5):
            await service.answer(
                101,
                f"Mensaje {number}",
            )

        assert len(repository.messages[101]) == 10

        calls = service._client.models.calls

        # Última solicitud: 4 mensajes previos
        # y el mensaje actual.
        assert len(calls[-1]) == 5

    asyncio.run(scenario())


def test_reset_only_deletes_selected_user():
    async def scenario():
        service, repository = make_service()

        await service.answer(101, "Hola A")
        await service.answer(202, "Hola B")

        await service.reset(101)

        assert 101 not in repository.messages
        assert len(repository.messages[202]) == 2

    asyncio.run(scenario())
