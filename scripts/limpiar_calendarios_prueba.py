"""Borra exclusivamente eventos etiquetados por AgendaAI en calendarios de prueba.

Por defecto solo muestra conteos y muestras. --apply exige confirmación manual.
No borra calendarios completos ni eventos sin etiquetas de propiedad.
"""
import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ['https://www.googleapis.com/auth/calendar.events']
PUBLIC_MARKER = 'agendaai_public_availability_v1'


def events(service, calendar_id, lower, upper):
    result = []
    page = None
    while True:
        data = service.events().list(
            calendarId=calendar_id, timeMin=lower, timeMax=upper,
            maxResults=2500, pageToken=page, singleEvents=True,
            showDeleted=False,
        ).execute(num_retries=3)
        result.extend(data.get('items', []))
        page = data.get('nextPageToken')
        if not page:
            return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days-back', type=int, default=7)
    p.add_argument('--days-forward', type=int, default=60)
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    if not 0 <= a.days_back <= 365 or not 1 <= a.days_forward <= 365:
        p.error('Intervalo no válido')
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root/'.env')
    private_id = os.getenv('GOOGLE_CALENDAR_ID','').strip()
    public_id = os.getenv('GOOGLE_PUBLIC_CALENDAR_ID','').strip()
    cred = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE','').strip()
    if not private_id or not public_id or not cred or private_id == public_id:
        p.error('Configura calendarios público/privado distintos y archivo de credenciales')
    credpath = Path(cred)
    if not credpath.is_absolute(): credpath=root/credpath
    if not credpath.is_file(): p.error('Credenciales inexistentes')
    creds = service_account.Credentials.from_service_account_file(str(credpath),scopes=SCOPES)
    service = build('calendar','v3',credentials=creds,cache_discovery=False)
    now = datetime.now(timezone.utc)
    lower = (now-timedelta(days=a.days_back)).isoformat()
    upper = (now+timedelta(days=a.days_forward)).isoformat()
    found = []
    for label, cal_id in [('privado',private_id),('publico',public_id)]:
        items = events(service,cal_id,lower,upper)
        matching = []
        for item in items:
            marker = item.get('extendedProperties',{}).get('private',{})
            owned = bool(marker.get('agendaai_booking_id')) if label=='privado' else marker.get('agendaai_kind') == PUBLIC_MARKER
            if owned:
                matching.append(item)
        print(f'Calendario {label}: {len(items)} eventos; {len(matching)} propiedad AgendaAI.')
        for item in matching[:5]:
            print('   ',item['id'],item.get('start',{}).get('dateTime', item.get('start',{}).get('date')),
                  item.get('summary','')[:60])
        found.extend((cal_id,item['id']) for item in matching)
    print('CANDIDATOS TOTALES A ELIMINAR:',len(found))
    if not a.apply:
        print('SIMULACIÓN. Nada eliminado. Para aplicar: --apply')
        return
    if input('Escribe BORRAR EVENTOS AGENDAAI: ').strip() != 'BORRAR EVENTOS AGENDAAI':
        print('Cancelado.')
        return
    done=0
    for calendar_id,event_id in found:
        try:
            service.events().delete(calendarId=calendar_id,eventId=event_id,sendUpdates='none').execute(num_retries=3)
            done+=1
        except HttpError as exc:
            if exc.resp.status!=404: raise
    print(f'Eliminados {done} eventos etiquetados. Revisa ambos calendarios.')


if __name__=='__main__':main()
