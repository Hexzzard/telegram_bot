"""AgendaAI: configurar reglas de disponibilidad (no genera reservas ni slots físicos)."""
import argparse
import os
from pathlib import Path
from dotenv import load_dotenv
import psycopg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resource-id', type=int, default=1)
    parser.add_argument('--weekdays', default='1,2,3,4,5', help='ISO: lunes=1, domingo=7')
    parser.add_argument('--start', default='09:00')
    parser.add_argument('--end', default='17:00')
    parser.add_argument('--apply', action='store_true')
    a = parser.parse_args()
    from datetime import time
    opening, closing = time.fromisoformat(a.start), time.fromisoformat(a.end)
    days = sorted({int(x.strip()) for x in a.weekdays.split(',')})
    if a.resource_id < 1 or not days or any(d < 1 or d > 7 for d in days) or opening >= closing:
        parser.error('Revisa resource-id, weekdays 1..7, start/end')
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    url = os.environ.get('DATABASE_URL')
    if not url: parser.error('Falta DATABASE_URL')
    with psycopg.connect(url, connect_timeout=15) as conn:
        with conn.transaction():
            resource = conn.execute('SELECT name,duration_minutes,timezone FROM public.booking_resources WHERE id=%s', (a.resource_id,)).fetchone()
            if not resource: raise RuntimeError('Recurso inexistente; no se modificó nada')
            current = conn.execute('SELECT weekday,start_time,end_time FROM public.availability_rules WHERE resource_id=%s ORDER BY weekday,start_time', (a.resource_id,)).fetchall()
            print('Recurso:', resource)
            print('Reglas actuales:', current)
            print('Reglas propuestas:', [(d,opening,closing) for d in days])
            proposed = [(d, opening, closing) for d in days]
            if current == proposed:
                print('SIN CAMBIOS: la jornada ya coincide con la configuración solicitada.')
                return
            if a.apply:
                if input('Para aplicar escribe JORNADA: ').strip() != 'JORNADA':
                    raise RuntimeError('No confirmado')
                conn.execute('DELETE FROM public.availability_rules WHERE resource_id=%s', (a.resource_id,))
                for d in days:
                    conn.execute('INSERT INTO public.availability_rules(resource_id,weekday,start_time,end_time) VALUES (%s,%s,%s,%s)',(a.resource_id,d,opening,closing))
                print('Reglas cambiadas en transacción.')
            else:
                print('SIMULACIÓN. Ejecuta --apply tras revisar.')


if __name__ == '__main__': main()
