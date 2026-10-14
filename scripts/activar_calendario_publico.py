"""Aplica cambios mínimos sobre main.py existente y crea respaldo.
Ejecutar tras copiar public_calendar_service.py y calendar_sync.py.
"""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
p = root / 'src/agendaai/main.py'
s = p.read_text(encoding='utf-8')
if 'public_calendar=public_calendar' in s:
    print('main.py ya estaba integrado. No se modificó.')
    sys.exit(0)
old_import = 'from agendaai.calendar_sync import CalendarSyncService'
if old_import not in s:
    raise RuntimeError('No se encontró el import esperado; no se modificó main.py')
old_call = 'calendar_sync = CalendarSyncService(\n            pool=shared_pool,'
if old_call not in s:
    raise RuntimeError('No se encontró CalendarSyncService esperado; no se modificó main.py')
new_call = '''public_id = os.getenv("GOOGLE_PUBLIC_CALENDAR_ID", "").strip()
        if public_id and public_id == calendar_id:
            raise RuntimeError("El calendario público y el privado tienen el mismo ID")
        public_calendar = PublicCalendarService(
            credentials_file=credentials_path, calendar_id=public_id,
        ) if public_id else None
        calendar_sync = CalendarSyncService(
            pool=shared_pool,
            public_calendar=public_calendar,
            resource_id=settings.booking_resource_id,'''
s = s.replace(old_import, old_import + '\nfrom agendaai.public_calendar_service import PublicCalendarService', 1)
s = s.replace(old_call, new_call, 1)
backup = p.with_name('main.py.bak_antes_cal_publico')
backup.write_text(p.read_text(encoding='utf-8'), encoding='utf-8')
p.write_text(s, encoding='utf-8')
print('Actualizado main.py; respaldo creado:', backup)
