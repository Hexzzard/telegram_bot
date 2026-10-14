"""AgendaAI: materializar bloques 09:00-17:00 en calendario público.

Lee eventos OCUPADOS del calendario PRIVADO para pintar los bloques ROJOS.
No modifica el calendario privado ni la base de datos Supabase.

Por defecto SIMULA. Usar --apply para escribir en Google Calendar.
Ejemplos:
    python scripts/publicar_disponibilidad.py --days 30
    python scripts/publicar_disponibilidad.py --days 30 --apply
    python scripts/publicar_disponibilidad.py --days 30 --weekdays 0,1,2,3,4,5 --apply
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

TZ = ZoneInfo("America/Santiago")
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
GREEN = "10"  # Basil
RED = "11"    # Tomato
MARKER = "agendaai_public_availability_v1"


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    occupied: bool


def local_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Usa HH:MM, por ejemplo 09:00") from exc


def weekdays(value: str) -> set[int]:
    try:
        result = {int(x.strip()) for x in value.split(",")}
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Usa números 0..6 separados por comas") from exc
    if not result or any(x not in range(7) for x in result):
        raise argparse.ArgumentTypeError("Días válidos: 0=lunes ... 6=domingo")
    return result


def event_id(calendar_id: str, when: datetime) -> str:
    # Identificador estable: mismo bloque => mismo evento. Solo a-f y 0-9.
    digest = hashlib.sha256(
        f"{calendar_id}|{when.isoformat()}|{MARKER}".encode("utf-8")
    ).hexdigest()[:32]
    return "av" + digest


def is_busy(event: dict) -> bool:
    if event.get("status") == "cancelled":
        return False
    return event.get("transparency", "opaque") != "transparent"


def bounds(event: dict) -> tuple[datetime, datetime]:
    start = event["start"]
    end = event["end"]
    if "dateTime" in start:
        a = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
        if a.tzinfo is None or b.tzinfo is None:
            raise ValueError("Evento con dateTime sin zona horaria")
        return a.astimezone(TZ), b.astimezone(TZ)
    # Google trata los eventos de día completo como rangos [inicio, fin).
    a = datetime.combine(date.fromisoformat(start["date"]), time.min, TZ)
    b = datetime.combine(date.fromisoformat(end["date"]), time.min, TZ)
    return a, b


def make_slots(start_date: date, days: int, eligible: set[int],
               open_at: time, close_at: time, slot_minutes: int,
               private_events: list[dict]) -> list[Slot]:
    busy_intervals = [bounds(item) for item in private_events if is_busy(item)]
    output = []
    for offset in range(days):
        current = start_date + timedelta(days=offset)
        if current.weekday() not in eligible:
            continue
        cursor = datetime.combine(current, open_at, TZ)
        close = datetime.combine(current, close_at, TZ)
        while cursor + timedelta(minutes=slot_minutes) <= close:
            end = cursor + timedelta(minutes=slot_minutes)
            overlap = any(a < end and cursor < b for a, b in busy_intervals)
            output.append(Slot(cursor, end, overlap))
            cursor = end
    return output


def event_body(slot: Slot, public_id: str) -> dict:
    key = slot.start.isoformat()
    occupied = slot.occupied
    return {
        "id": event_id(public_id, slot.start),
        "summary": "🔴 Reservado" if occupied else "🟢 Disponible",
        "description": (
            "Horario ocupado. Para consultar otras horas, escribe a AgendaAI."
            if occupied else "Horario orientativo. Reserva mediante AgendaAI; la disponibilidad final se valida al confirmar."
        ),
        "start": {"dateTime": slot.start.isoformat(), "timeZone": "America/Santiago"},
        "end": {"dateTime": slot.end.isoformat(), "timeZone": "America/Santiago"},
        "colorId": RED if occupied else GREEN,
        "transparency": "opaque" if occupied else "transparent",
        "extendedProperties": {"private": {"agendaai_kind": MARKER, "agendaai_slot_start": key}},
    }


def list_events(service, calendar_id: str, start: datetime, end: datetime) -> list[dict]:
    results = []
    page = None
    while True:
        reply = service.events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            maxResults=2500,
            singleEvents=True,
            pageToken=page,
            showDeleted=False,
        ).execute(num_retries=3)
        results.extend(reply.get("items", []))
        page = reply.get("nextPageToken")
        if not page:
            return results


def same_display(current: dict, desired: dict) -> bool:
    return all(current.get(key) == desired.get(key)
               for key in ("summary", "description", "colorId", "transparency")) and (
        current.get("start", {}).get("dateTime") == desired["start"]["dateTime"]
        and current.get("end", {}).get("dateTime") == desired["end"]["dateTime"]
    )


def sync(service, public_id: str, slots: list[Slot], existing: list[dict], apply: bool) -> None:
    by_id = {e["id"]: e for e in existing if e.get("id")}
    planned = {"create": 0, "update": 0, "same": 0, "busy": 0, "free": 0}
    for n, slot in enumerate(slots, 1):
        payload = event_body(slot, public_id)
        current = by_id.get(payload["id"])
        planned["busy" if slot.occupied else "free"] += 1
        if current is not None:
            props = current.get("extendedProperties", {}).get("private", {})
            if props.get("agendaai_kind") != MARKER:
                raise RuntimeError(f"Conflicto de ID con evento ajeno: {payload['id']}")
            if same_display(current, payload):
                planned["same"] += 1
                continue
            planned["update"] += 1
            if apply:
                service.events().patch(
                    calendarId=public_id, eventId=payload["id"],
                    body={k: payload[k] for k in (
                        "summary", "description", "colorId", "transparency", "start", "end")},
                    sendUpdates="none",
                ).execute(num_retries=3)
        else:
            planned["create"] += 1
            if apply:
                try:
                    service.events().insert(
                        calendarId=public_id, body=payload, sendUpdates="none"
                    ).execute(num_retries=3)
                except HttpError as exc:
                    if exc.resp.status != 409:
                        raise
                    # Otra ejecución creó el mismo bloque: verificar propiedad.
                    found = service.events().get(
                        calendarId=public_id, eventId=payload["id"]
                    ).execute(num_retries=3)
                    props = found.get("extendedProperties", {}).get("private", {})
                    if props.get("agendaai_kind") != MARKER:
                        raise RuntimeError("Conflicto con evento externo") from exc
        if apply and n % 40 == 0:
            print(f"Procesados {n}/{len(slots)} bloques...", flush=True)
    print(f"Bloques: {len(slots)} | Verdes: {planned['free']} | Rojos: {planned['busy']}")
    print(f"A crear: {planned['create']} | A actualizar: {planned['update']} | Sin cambios: {planned['same']}")
    print("Escritura completada." if apply else "SIMULACIÓN: no se escribió ningún evento. Usa --apply.")


def main() -> None:
    parser = argparse.ArgumentParser(description="AgendaAI: publicar disponibilidad en Google Calendar")
    parser.add_argument("--days", type=int, default=30, help="Días desde --start-date (1..60)")
    parser.add_argument("--start-date", type=date.fromisoformat, default=datetime.now(TZ).date())
    parser.add_argument("--weekdays", type=weekdays, default={0,1,2,3,4},
                        help="0=lunes, ..., 6=domingo. Predeterminado 0,1,2,3,4")
    parser.add_argument("--from-time", type=local_time, default=time(9, 0))
    parser.add_argument("--to-time", type=local_time, default=time(17, 0))
    parser.add_argument("--slot-minutes", type=int, default=30)
    parser.add_argument("--apply", action="store_true", help="Escribir en Google; sin esto solo simula")
    args = parser.parse_args()
    if not (1 <= args.days <= 60):
        parser.error("--days debe estar entre 1 y 60")
    if not (1 <= args.slot_minutes <= 120) or args.slot_minutes % 5:
        parser.error("--slot-minutes debe ser múltiplo de 5 entre 5 y 120")
    if args.to_time <= args.from_time:
        parser.error("--to-time debe ser posterior a --from-time")
    if args.start_date < datetime.now(TZ).date():
        parser.error("No crees bloques nuevos en fechas pasadas")

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    public_id = os.getenv("GOOGLE_PUBLIC_CALENDAR_ID", "").strip()
    private_id = os.getenv("GOOGLE_CALENDAR_ID", "").strip()
    if not credentials_path or not public_id or not private_id:
        parser.error("Faltan GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_CALENDAR_ID (privado), GOOGLE_PUBLIC_CALENDAR_ID")
    if private_id == public_id:
        parser.error("Los calendarios público y privado deben ser diferentes")
    path = Path(credentials_path)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        parser.error(f"No se encontró el archivo de credenciales: {path}")

    credentials = service_account.Credentials.from_service_account_file(str(path), scopes=SCOPES)
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    window_start = datetime.combine(args.start_date, time.min, TZ)
    window_end = datetime.combine(args.start_date + timedelta(days=args.days), time.min, TZ)
    print("Consultando eventos del calendario privado (solo lectura)...", flush=True)
    private_events = list_events(service, private_id, window_start, window_end)
    print("Consultando bloques existentes del calendario público...", flush=True)
    existing = list_events(service, public_id, window_start, window_end)
    slots = make_slots(args.start_date, args.days, args.weekdays,
                       args.from_time, args.to_time, args.slot_minutes, private_events)
    if not slots:
        print("No hay bloques para crear con los días y horarios seleccionados.")
        return
    sync(service, public_id, slots, existing, args.apply)
    print("Nota: los cambios posteriores en reservas/cancelaciones requieren volver a ejecutar este script")
    print("o integrar la actualización al trabajador Calendar de AgendaAI.")


if __name__ == "__main__":
    try:
        main()
    except HttpError as error:
        print(f"Google Calendar devolvió HTTP {error.resp.status}: {error}", file=sys.stderr)
        sys.exit(1)
