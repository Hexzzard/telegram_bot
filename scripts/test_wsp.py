import os
import json
import logging

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agendaai.whatsapp")

app = FastAPI()

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")


# 1. Verificar el webhook con Meta
@app.get("/api/webhook/whatsapp")
async def verify_webhook(request: Request):

    params = request.query_params

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if (
        mode == "subscribe"
        and VERIFY_TOKEN
        and token == VERIFY_TOKEN
        and challenge is not None
    ):
        logger.info("Webhook verificado correctamente")
        return PlainTextResponse(challenge)

    raise HTTPException(
        status_code=403,
        detail="Token de verificación incorrecto"
    )


# 2. Recibir mensajes de WhatsApp
@app.post("/api/webhook/whatsapp")
async def receive_whatsapp(request: Request):

    payload = await request.json()

    logger.info("Evento recibido desde WhatsApp")

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):

            if change.get("field") != "messages":
                continue

            value = change.get("value", {})

            for message in value.get("messages", []):

                sender = message.get("from", "")
                message_type = message.get("type", "")

                if message_type == "text":
                    text = message.get("text", {}).get("body", "")

                    logger.info(
                        "Mensaje de %s: %s",
                        sender[-4:].rjust(len(sender), "*"),
                        text
                    )