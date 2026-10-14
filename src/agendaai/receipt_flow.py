"""Oferta de comprobante sin OTP. No se verifica propiedad del correo."""
from dataclasses import dataclass
import re
import unicodedata
from agendaai.conversation_state import ConversationStage, ConversationStateRepository
from agendaai.receipt_service import ReceiptService

@dataclass(frozen=True)
class FlowResponse:
    handled: bool
    text: str = ""

def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"[^\w\s]", " ", value)
    return " ".join(value.split())

class ReceiptFlow:
    def __init__(self, states: ConversationStateRepository, receipts: ReceiptService):
        self._states = states
        self._receipts = receipts

    async def handle(self, *, user_id: int, message: str) -> FlowResponse:
        current = await self._states.get_state(user_id)
        if current.state == ConversationStage.IDLE:
            return FlowResponse(False)
        value = normalize(message).strip(" .!¡¿?")
        if value in {"cancelar comprobante", "olvida el comprobante", "no", "no gracias", "ahora no"}:
            await self._states.clear_state(user_id=user_id)
            return FlowResponse(True, "Entendido. No enviaré el comprobante; tu reserva sigue confirmada. 🐾")
        if current.state == ConversationStage.AWAITING_RECEIPT_CHOICE:
            if value in {"si", "si por favor", "dale", "claro", "ok", "acepto", "por supuesto", "quiero el comprobante", "enviamelo"}:
                changed = await self._states.move_to_email_input(user_id=user_id)
                return FlowResponse(True, "¡Con gusto! ¿A qué correo electrónico lo enviamos?" if changed else "No pude continuar con el comprobante. Intenta nuevamente.")
            if "@" in message:
                await self._states.move_to_email_input(user_id=user_id)
                result = await self._receipts.enqueue_receipt(user_id=user_id, email=message.strip())
                return FlowResponse(True, result.message)
            return FlowResponse(True, "¿Quieres recibir un comprobante por correo? Responde «sí» o «no». 🐾")
        if current.state == ConversationStage.AWAITING_EMAIL:
            # "Al último que te envié" expresa autorización para reutilizar
            # el destinatario de un comprobante de este mismo usuario.
            reuse_phrases = (
                "al ultimo", "al mismo correo", "al correo anterior",
                "al correo de antes", "al que te envie", "al que use",
                "el mismo de antes", "usa el anterior", "al ultimo que te envie",
            )
            if any(phrase in value for phrase in reuse_phrases):
                previous = await self._receipts.last_receipt_email(user_id=user_id)
                if previous is None:
                    return FlowResponse(
                        True,
                        "Todavía no encuentro un correo usado anteriormente "
                        "para tus comprobantes. ¿Me lo escribes, por favor?",
                    )
                result = await self._receipts.enqueue_receipt(
                    user_id=user_id, email=previous,
                )
                return FlowResponse(True, result.message)
            result = await self._receipts.enqueue_receipt(user_id=user_id, email=message.strip())
            return FlowResponse(True, result.message)
        if current.state == ConversationStage.AWAITING_VERIFICATION:
            await self._states.clear_state(user_id=user_id)
            return FlowResponse(True, "Se cerró un proceso antiguo de verificación. No utilizamos códigos OTP. Solicita un nuevo comprobante después de tu próxima reserva.")
        return FlowResponse(False)
