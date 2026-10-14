
import os
import sys
import uuid

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

TIMEZONE = ZoneInfo("America/Santiago")


def main():
    # Obtenemos las configuraciones del proyecto.
    credentials_file = os.environ[
        "GOOGLE_SERVICE_ACCOUNT_FILE"
    ]

    calendar_id = os.environ[
        "GOOGLE_CALENDAR_ID"
    ]

    key_path = Path(credentials_file)

    if not key_path.is_absolute():
        key_path = ROOT / key_path

    # Autenticación mediante Service Account.
    credentials = (
        service_account.Credentials.from_service_account_file(
            str(key_path),
            scopes=SCOPES,
        )
    )

    service = build(
        "calendar",
        "v3",
        credentials=credentials,
    )

    # Comprobamos que el calendario sea accesible.
    calendar = (
        service.calendars()
        .get(calendarId=calendar_id)
        .execute()
    )

    print(
        "Calendario encontrado:",
        calendar.get("summary"),
    )

    # Evento de prueba para dentro de dos días.
    now = datetime.now(TIMEZONE)
    target_date = now.date() + timedelta(days=2)

    start = datetime.combine(
        target_date,
        datetime.min.time().replace(hour=16),
        tzinfo=TIMEZONE,
    )

    end = start + timedelta(minutes=30)

    # ID estable para no duplicar la prueba
    # al volver a ejecutar el script el mismo día.
    event_id = "ag" + uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"agendaai-test:{calendar_id}:{target_date}",
    ).hex

    event_body = {
        "id": event_id,
        "summary": "AgendaAI - Reserva de prueba",
        "description": (
            "Evento creado para comprobar "
            "la integración con Google Calendar."
        ),
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": "America/Santiago",
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": "America/Santiago",
        },
    }

    try:
        event = (
            service.events()
            .insert(
                calendarId=calendar_id,
                body=event_body,
            )
            .execute()
        )
        print("Evento creado correctamente.")

    except HttpError as error:
        if error.resp.status != 409:
            raise

        # Ya existe el evento de esta prueba.
        event = (
            service.events()
            .get(
                calendarId=calendar_id,
                eventId=event_id,
            )
            .execute()
        )
        print("Evento de prueba ya existente.")

    print("Evento:", event.get("summary"))
    print("ID:", event["id"])
    print("Inicio:", event["start"]["dateTime"])
    print("Fin:", event["end"]["dateTime"])
    print("Estado:", event.get("status"))

    if event.get("htmlLink"):
        print("Enlace:", event["htmlLink"])


if __name__ == "__main__":
    try:
        main()
    except HttpError as error:
        print(
            "Error de Google Calendar:",
            error.resp.status,
            file=sys.stderr,
        )
        print(error, file=sys.stderr)
        sys.exit(1)
