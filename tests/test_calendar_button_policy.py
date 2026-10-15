import importlib.util
from pathlib import Path

# Pruebas unitarias ligeras de la decisión del CTA, sin Meta ni Supabase.
import ast

path = Path(__file__).resolve().parents[1] / 'whatsapp_gateway' / 'worker.py'
module = ast.parse(path.read_text(encoding='utf-8'))
selected = [node for node in module.body if isinstance(node, (ast.Assign, ast.FunctionDef)) and (not isinstance(node, ast.FunctionDef) or node.name == 'should_attach_calendar')]
# Capturar solo constantes regex relacionadas y la función pura.
selected = [node for node in selected if isinstance(node, ast.FunctionDef) or any(isinstance(target, ast.Name) and target.id in {'_EXPLICIT_CALENDAR','_AVAILABILITY_REQUEST','_SLOT_TIME','_BOOKING_RESULT'} for target in getattr(node,'targets',[]))]
namespace={'re':__import__('re')}
exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),namespace)
should = namespace['should_attach_calendar']

def test_no_button_when_asking_to_book():
    assert not should('quiero agendar una hora','¡Con gusto! ¿Para qué fecha y hora?')

def test_no_button_during_data_collection():
    assert not should('quiero la del viernes 16 a las 11','Ese horario está disponible. ¿A qué nombre registramos la reserva?')

def test_no_button_after_confirmation():
    assert not should('me llamo Alan','¡Listo! Tu reserva quedó agendada. Código: AG-000018')

def test_calendar_link_requested():
    assert should('puedes mostrarme el link del calendario','Claro, revisa el calendario.')

def test_calendar_button_requested():
    assert should('¿tienes un link con las horas?','Puedes ver la agenda.')

def test_listed_slots():
    assert should('muéstrame el listado de horas disponibles','09:00, 09:30, 10:00')

def test_no_button_when_no_real_slots():
    assert not should('muéstrame el listado de horas disponibles','¿Para qué día te gustaría revisar?')

def test_no_button_unrelated_chat():
    assert not should('¿cuánto es 1+1?','2')
