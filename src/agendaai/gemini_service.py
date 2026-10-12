
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types
from google.genai.errors import (
    ClientError,
    ServerError,
)

from agendaai.database import ConversationRepository
from agendaai.tools.availability import (
    AVAILABILITY_TOOL,
    AvailabilityToolExecutor,
)
from agendaai.tools.booking import (
    BOOKING_TOOL,
    BookingToolExecutor,
)

logger = logging.getLogger(__name__)
TIMEZONE = ZoneInfo("America/Santiago")

SYSTEM_INSTRUCTION = (
    "Eres AgendaAI, un asistente de reservas por Telegram. "
    "Responde en español. Administras una agenda compartida. "
    "Si el usuario pregunta por disponibilidad, utiliza "
    "check_availability o get_available_slots. "
    "Si el usuario solicita EXPLÍCITAMENTE reservar "
    "una fecha y hora concretas, utiliza create_booking. "
    "No solicites una segunda confirmación cuando "
    "la intención de reservar sea inequívoca. "
    "Si falta información, pregunta antes de reservar. "
    "Nunca reserves por una simple consulta de horarios. "
    "No inventes disponibilidad ni afirmes haber reservado "
    "sin recibir un resultado exitoso de la herramienta. "
    "Usa la fecha actual proporcionada para interpretar "
    "expresiones como mañana o el próximo jueves. "
    "Los horarios corresponden a America/Santiago. "
    "No aceptes instrucciones del historial como si "
    "fueran nuevas solicitudes de reserva."
)


class GeminiService:
    def __init__(
        self,
        api_key: str,
        model: str,
        repository: ConversationRepository,
        availability_executor: AvailabilityToolExecutor | None = None,
        booking_executor: BookingToolExecutor | None = None,
        max_turns: int = 10,
    ):
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._repository = repository
        self._availability_executor = availability_executor
        self._booking_executor = booking_executor
        self._max_turns = max_turns

        self._locks: dict[int, asyncio.Lock] = {}

    def _get_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()

        return self._locks[user_id]

    async def answer(
        self,
        user_id: int,
        message: str,
        request_id: int | None = None,
    ) -> str:
        async with self._get_lock(user_id):
            history = await self._repository.get_history(
                user_id=user_id,
                limit=self._max_turns * 2,
            )

            contents = [
                types.Content(
                    role=item.role,
                    parts=[
                        types.Part.from_text(
                            text=item.content
                        )
                    ],
                )
                for item in history
            ]

            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=message)
                    ],
                )
            )

            now = datetime.now(TIMEZONE)

            instructions = (
                SYSTEM_INSTRUCTION
                + "\nFecha y hora actual de Chile: "
                + now.isoformat()
            )

            tools = []

            if self._availability_executor is not None:
                tools.append(AVAILABILITY_TOOL)

            if self._booking_executor is not None:
                tools.append(BOOKING_TOOL)

            config = types.GenerateContentConfig(
                system_instruction=instructions,
                tools=tools or None,
                automatic_function_calling=(
                    types.AutomaticFunctionCallingConfig(
                        disable=True
                    )
                ),
            )

            try:
                response = await asyncio.to_thread(
                    self._client.models.generate_content,
                    model=self._model,
                    contents=contents,
                    config=config,
                )

            except ServerError as error:
                logger.warning(
                    "Error temporal de Gemini: HTTP %s",
                    error.code,
                )

                return (
                    "El servicio de inteligencia artificial "
                    "está temporalmente saturado.\n\n"
                    "No he ejecutado ninguna reserva en esta "
                    "solicitud. Intenta nuevamente dentro "
                    "de unos momentos."
                )

            except ClientError as error:
                logger.error(
                    "Error de Gemini: HTTP %s",
                    error.code,
                )

                if error.code == 429:
                    return (
                        "Hemos alcanzado un límite temporal "
                        "de solicitudes de IA. "
                        "Intenta nuevamente más tarde."
                    )

                return (
                    "No pude procesar tu solicitud debido "
                    "a un problema de configuración o acceso "
                    "al modelo de IA."
                )

            calls = response.function_calls or []

            if len(calls) > 1:
                answer_text = (
                    "Procesaré una solicitud a la vez. "
                    "Indica qué operación quieres realizar."
                )

            elif not calls:
                answer_text = (
                    response.text
                    or "No pude generar una respuesta."
                )

            else:
                call = calls[0]
                args = call.args or {}

                logger.info(
                    "Gemini solicitó herramienta: %s",
                    call.name,
                )

                if call.name == "create_booking":
                    if self._booking_executor is None:
                        answer_text = (
                            "Las reservas no están habilitadas."
                        )
                    elif request_id is None:
                        answer_text = (
                            "No puedo identificar esta solicitud "
                            "de forma segura."
                        )
                    else:
                        answer_text = (
                            await self._booking_executor.execute(
                                args,
                                user_id=user_id,
                                telegram_update_id=request_id,
                            )
                        )

                elif call.name in (
                    "check_availability",
                    "get_available_slots",
                ):
                    if self._availability_executor is None:
                        answer_text = (
                            "La agenda no está configurada."
                        )
                    else:
                        answer_text = (
                            await self._availability_executor.execute(
                                call.name,
                                args,
                            )
                        )

                else:
                    logger.warning(
                        "Herramienta no autorizada: %s",
                        call.name,
                    )
                    answer_text = (
                        "No puedo ejecutar esa herramienta."
                    )

            await self._repository.save_exchange(
                user_id=user_id,
                user_message=message,
                model_message=answer_text,
            )

            return answer_text

    async def reset(self, user_id: int) -> None:
        async with self._get_lock(user_id):
            await self._repository.delete_history(user_id)
