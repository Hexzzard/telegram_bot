"""Adaptador Google Calendar. Uso exclusivo desde el trabajador de sincronización."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


@dataclass(frozen=True)
class GoogleEvent:
    event_id: str
    html_link: str | None


class GoogleCalendarService:
    def __init__(self, *, credentials_file: str | Path, calendar_id: str) -> None:
        self._credentials_file = Path(credentials_file)
        self._calendar_id = calendar_id.strip()
        if not self._credentials_file.is_file():
            raise FileNotFoundError(f"No existe el JSON de la cuenta de servicio: {self._credentials_file}")
        if not self._calendar_id:
            raise ValueError("GOOGLE_CALENDAR_ID no puede estar vacío")

    @staticmethod
    def event_id_for_booking(booking_id: int) -> str:
        """ID permitido por Calendar, estable para todos los reintentos."""
        if booking_id < 1:
            raise ValueError("booking_id inválido")
        return f"ag{booking_id:020d}"

    def _build_client(self):
        credentials = service_account.Credentials.from_service_account_file(
            str(self._credentials_file), scopes=SCOPES
        )
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def _ensure_event_blocking(
        self, *, booking_id: int, title: str, client_name: str, start_at: datetime, end_at: datetime
    ) -> GoogleEvent:
        event_id = self.event_id_for_booking(booking_id)
        service = self._build_client()
        payload = {
            "id": event_id,
            "summary": client_name[:120],
            "description": f"Motivo de consulta: {title}\nReserva: AG-{booking_id:06d}.",
            "start": {"dateTime": start_at.isoformat(), "timeZone": "America/Santiago"},
            "end": {"dateTime": end_at.isoformat(), "timeZone": "America/Santiago"},
            "extendedProperties": {"private": {"agendaai_booking_id": str(booking_id)}},
        }
        try:
            event = service.events().insert(
                calendarId=self._calendar_id, body=payload, sendUpdates="none"
            ).execute(num_retries=0)
        except HttpError as error:
            if error.resp.status != 409:
                raise
            # Un 409 significa que ya existe ese ID: comprobar que sea nuestro evento.
            event = service.events().get(
                calendarId=self._calendar_id, eventId=event_id
            ).execute(num_retries=0)
            private = event.get("extendedProperties", {}).get("private", {})
            if private.get("agendaai_booking_id") != str(booking_id):
                raise RuntimeError("Conflicto de identificador con un evento ajeno") from error
            # Ya existe: aplicar título/motivo nuevos sin duplicar el evento.
            event = service.events().patch(calendarId=self._calendar_id, eventId=event_id, body={"summary": payload["summary"], "description": payload["description"]}, sendUpdates="none").execute(num_retries=0)
        return GoogleEvent(event_id=event["id"], html_link=event.get("htmlLink"))

    def _remove_event_blocking(self, *, booking_id: int) -> None:
        event_id = self.event_id_for_booking(booking_id)
        service = self._build_client()
        try:
            event = service.events().get(
                calendarId=self._calendar_id, eventId=event_id
            ).execute(num_retries=0)
        except HttpError as error:
            if error.resp.status == 404:
                return  # Nunca llegó a crearse, o fue eliminado previamente.
            raise
        private = event.get("extendedProperties", {}).get("private", {})
        if private.get("agendaai_booking_id") != str(booking_id):
            raise RuntimeError("Se rechaza eliminar un evento sin propiedad verificable de AgendaAI")
        try:
            service.events().delete(
                calendarId=self._calendar_id,
                eventId=event_id,
                sendUpdates="none",
            ).execute(num_retries=0)
        except HttpError as error:
            if error.resp.status != 404:
                raise

    async def remove_event(self, *, booking_id: int) -> None:
        await asyncio.to_thread(self._remove_event_blocking, booking_id=booking_id)

    async def ensure_event(
        self, *, booking_id: int, title: str, client_name: str, start_at: datetime, end_at: datetime
    ) -> GoogleEvent:
        return await asyncio.to_thread(
            self._ensure_event_blocking,
            booking_id=booking_id,
            title=title,
            client_name=client_name,
            start_at=start_at,
            end_at=end_at,
        )
