"""Actualiza bloques públicos AgendaAI sin exponer nombres ni motivos.
Usa EXACTAMENTE los IDs generados por scripts/publicar_disponibilidad.py.
"""
from __future__ import annotations
import asyncio
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

TZ = ZoneInfo('America/Santiago')
MARKER = 'agendaai_public_availability_v1'
SCOPE = ['https://www.googleapis.com/auth/calendar.events']

class PublicCalendarService:
    def __init__(self, *, credentials_file: str | Path, calendar_id: str, slot_minutes: int = 30):
        self.credentials_file = Path(credentials_file)
        self.calendar_id = calendar_id.strip()
        self.slot_minutes = slot_minutes
        if not self.credentials_file.is_file() or not self.calendar_id:
            raise ValueError('Credenciales o GOOGLE_PUBLIC_CALENDAR_ID incorrectos')
        if slot_minutes < 5 or 60 % slot_minutes:
            raise ValueError('slot_minutes debe dividir 60 y ser >= 5')

    def _client(self):
        creds = service_account.Credentials.from_service_account_file(str(self.credentials_file), scopes=SCOPE)
        return build('calendar', 'v3', credentials=creds, cache_discovery=False)

    def _event_id(self, start: datetime) -> str:
        value = f'{self.calendar_id}|{start.astimezone(TZ).isoformat()}|{MARKER}'
        return 'av' + hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]

    def _block(self, start: datetime, busy: bool) -> dict:
        start = start.astimezone(TZ)
        end = start + timedelta(minutes=self.slot_minutes)
        return {
            'id': self._event_id(start),
            'summary': '🔴 Reservado' if busy else '🟢 Disponible',
            'description': ('Horario ocupado.' if busy else 'Horario disponible. Reserva mediante AgendaAI.'),
            'start': {'dateTime': start.isoformat(), 'timeZone': 'America/Santiago'},
            'end': {'dateTime': end.isoformat(), 'timeZone': 'America/Santiago'},
            'colorId': '11' if busy else '10',
            'transparency': 'opaque' if busy else 'transparent',
            'extendedProperties': {'private': {'agendaai_kind': MARKER, 'agendaai_slot_start': start.isoformat()}},
        }

    def _sync_block(self, start: datetime, busy: bool) -> None:
        service = self._client()
        body = self._block(start, busy)
        event_id = body['id']
        try:
            current = service.events().get(calendarId=self.calendar_id, eventId=event_id).execute(num_retries=0)
        except HttpError as e:
            if e.resp.status != 404:
                raise
            try:
                service.events().insert(calendarId=self.calendar_id, body=body, sendUpdates='none').execute(num_retries=0)
                return
            except HttpError as conflict:
                if conflict.resp.status != 409:
                    raise
                current = service.events().get(calendarId=self.calendar_id, eventId=event_id).execute(num_retries=0)
        metadata = current.get('extendedProperties', {}).get('private', {})
        if metadata.get('agendaai_kind') != MARKER:
            raise RuntimeError('No se puede modificar un evento público ajeno a AgendaAI')
        if all(current.get(k) == body[k] for k in ('summary','description','colorId','transparency')):
            return
        service.events().patch(calendarId=self.calendar_id, eventId=event_id,
            body={k:body[k] for k in ('summary','description','colorId','transparency')},
            sendUpdates='none').execute(num_retries=0)

    async def sync_block(self, start: datetime, busy: bool) -> None:
        await asyncio.to_thread(self._sync_block, start, busy)
