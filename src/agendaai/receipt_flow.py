import re
import unicodedata
from dataclasses import dataclass

from agendaai.conversation_state import (
    ConversationStage,
    ConversationStateRepository,
)
from agendaai.receipt_service import ReceiptService


@dataclass(frozen=True)
class FlowResponse:
    handled: bool
    text: str = ""


def normalize(text: str) -> str:
    """
    Normaliza mayúsculas, tildes, puntuación
    y espacios repetidos.
    """
    value = unicodedata.normalize(
        "NFKD",
        text.casefold(),
    )

    value = "".join(char for char in value if not unicodedata.combining(char))

    value = re.sub(
        r"[^\w\s]",
        " ",
        value,
    )

    return " ".join(value.split())


YES = {
    "si",
    "si por favor",
    "claro",
    "dale",
    "ok",
    "acepto",
    "por supuesto",
    "enviamelo",
    "quiero el comprobante",
}

NO = {
    "no",
    "no gracias",
    "ahora no",
    "prefiero que no",
}

CANCEL = {
    "cancelar comprobante",
    "olvida el comprobante",
    "salir del comprobante",
}

CHANGE_EMAIL = {
    "cambiar correo",
    "cambiar mi correo",
    "quiero cambiar mi correo",
    "usar otro correo",
    "otro correo",
    "enviar a otro correo",
    "enviarlo a otro correo",
}


class ReceiptFlow:
    def __init__(
        self,
        states: ConversationStateRepository,
        receipts: ReceiptService,
    ):
        self._states = states
        self._receipts = receipts

    async def handle(
        self,
        *,
        user_id: int,
        message: str,
    ) -> FlowResponse:

        current = await self._states.get_state(user_id)

        if current.state == ConversationStage.IDLE:
            return FlowResponse(handled=False)

        value = normalize(message)

        # Cancelar solo el flujo de comprobante,
        # nunca la reserva.
        if value in CANCEL:
            await self._states.clear_state(user_id=user_id)

            return FlowResponse(
                handled=True,
                text=(
                    "He cancelado solamente el envío del comprobante. Tu reserva sigue confirmada."
                ),
            )

        # -------------------------------------
        # ESTADO 1: ¿Desea un comprobante?
        # -------------------------------------
        if current.state == ConversationStage.AWAITING_RECEIPT_CHOICE:
            if value in NO:
                await self._states.clear_state(user_id=user_id)

                return FlowResponse(
                    handled=True,
                    text=("Entendido. No enviaré un comprobante. Tu reserva continúa confirmada."),
                )

            # El usuario quiere utilizar
            # una dirección diferente.
            if value in CHANGE_EMAIL:
                changed = await self._states.move_to_email_input(user_id=user_id)

                if not changed:
                    return FlowResponse(
                        handled=True,
                        text=("No pude actualizar tu solicitud. Intenta nuevamente."),
                    )

                return FlowResponse(
                    handled=True,
                    text=(
                        "De acuerdo. ¿Cuál es tu "
                        "nueva dirección de correo? "
                        "Tendremos que verificarla."
                    ),
                )

            if value in YES:
                # Primero intentamos reutilizar
                # el correo previamente verificado.
                result = await self._receipts.enqueue_verified_receipt(user_id=user_id)

                if result.status == "no_verified_email":
                    # Usuario nuevo: solicitar correo.
                    changed = await self._states.move_to_email_input(user_id=user_id)

                    if not changed:
                        return FlowResponse(
                            handled=True,
                            text=("No pude actualizar tu solicitud. Intenta nuevamente."),
                        )

                    return FlowResponse(
                        handled=True,
                        text=(
                            "Perfecto. ¿A qué dirección de correo quieres que envíe el comprobante?"
                        ),
                    )

                # Usuario verificado: comprobante
                # encolado sin pedir otro código.
                return FlowResponse(
                    handled=True,
                    text=result.message,
                )

            return FlowResponse(
                handled=True,
                text=(
                    "¿Quieres recibir el comprobante "
                    "por correo? Responde «sí» o «no».\n"
                    "También puedes escribir "
                    "«quiero cambiar mi correo»."
                ),
            )

        # -------------------------------------
        # ESTADO 2: Solicitar correo
        # -------------------------------------
        if current.state == ConversationStage.AWAITING_EMAIL:
            candidate = message.strip()

            if len(candidate) > 254:
                return FlowResponse(
                    handled=True,
                    text=("La dirección de correo es demasiado larga."),
                )

            result = await self._receipts.request_verification(
                user_id=user_id,
                email=candidate,
            )

            return FlowResponse(
                handled=True,
                text=result.message,
            )

        # -------------------------------------
        # ESTADO 3: Verificar código
        # -------------------------------------
        if current.state == ConversationStage.AWAITING_VERIFICATION:
            code = message.strip()

            if re.fullmatch(r"[0-9]{6}", code):
                result = await self._receipts.verify_code(
                    user_id=user_id,
                    code=code,
                )

                return FlowResponse(
                    handled=True,
                    text=result.message,
                )

            return FlowResponse(
                handled=True,
                text=(
                    "Estoy esperando el código de "
                    "seis dígitos que llegó a tu "
                    "correo.\n\n"
                    "Si no puedes continuar, escribe "
                    "«cancelar comprobante». "
                    "Tu reserva no se cancelará."
                ),
            )

        return FlowResponse(handled=False)
