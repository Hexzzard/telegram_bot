# AgendaAI — Bot inteligente de reservas para Telegram

AgendaAI es un asistente conversacional para Telegram que permite consultar disponibilidad y realizar reservas automáticamente mediante inteligencia artificial.

El sistema utiliza **Google Gemini** para interpretar las solicitudes en lenguaje natural y **Supabase PostgreSQL** para administrar los horarios, usuarios y reservas.

Su objetivo es facilitar la gestión de una agenda compartida, evitando conflictos de horario y permitiendo que múltiples usuarios realicen reservas desde Telegram.

## Características principales

- **Reservas automáticas:** los usuarios pueden solicitar una cita mediante mensajes de texto, sin formularios ni confirmaciones adicionales.
- **Consultas de disponibilidad:** permite comprobar si una fecha y hora están disponibles.
- **Horarios alternativos:** muestra los espacios libres según la configuración de atención.
- **Protección contra reservas duplicadas:** PostgreSQL impide que dos reservas confirmadas ocupen horarios superpuestos.
- **Control de concurrencia:** utiliza transacciones y bloqueos para coordinar solicitudes simultáneas.
- **Idempotencia:** evita crear una nueva reserva cuando se procesa nuevamente una misma operación.
- **Historial persistente:** conserva conversaciones en Supabase para proporcionar contexto al asistente.
- **Soporte de zona horaria:** utiliza `America/Santiago` para la agenda configurada.

## Tecnologías utilizadas

| Tecnología | Función |
|---|---|
| Python 3.12 | Lenguaje principal |
| python-telegram-bot | Comunicación con Telegram |
| Google Gemini API | Interpretación del lenguaje natural |
| Supabase PostgreSQL | Persistencia de usuarios, conversaciones y reservas |
| Psycopg 3 | Acceso asíncrono a PostgreSQL |
| Pydantic | Validación de datos y configuración |
| Pytest | Pruebas automatizadas |
| Ruff | Análisis y formato del código |

## Requisitos previos

Para ejecutar AgendaAI necesitas:

- Python 3.12 o una versión compatible con las dependencias del proyecto.
- Git.
- Una cuenta de Telegram.
- Un token de bot obtenido mediante [@BotFather](https://t.me/BotFather).
- Una API Key de [Google AI Studio](https://aistudio.google.com/).
- Un proyecto de [Supabase](https://supabase.com/) con PostgreSQL.

## Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/TU_USUARIO/Test_telegram_bot.git
cd Test_telegram_bot
```

Reemplaza `TU_USUARIO` por el propietario del repositorio en GitHub.

### 2. Crear un entorno virtual

En Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Si PowerShell bloquea la activación, también puedes ejecutar directamente el Python del entorno virtual.

### 3. Instalar las dependencias

Desde la raíz del proyecto:

```powershell
python -m pip install --upgrade pip
python -m pip install -e .
```

Para ejecutar pruebas y herramientas de desarrollo, instala también `pytest` y `ruff` si no forman parte de las dependencias del proyecto:

```powershell
python -m pip install pytest ruff
```

## Configuración

### 1. Variables de entorno

Crea un archivo `.env` en la raíz del proyecto:

```dotenv
TELEGRAM_BOT_TOKEN=tu_token_de_telegram
GEMINI_API_KEY=tu_api_key_de_gemini
GEMINI_MODEL=nombre_del_modelo_disponible
DATABASE_URL=postgresql://usuario:contraseña@servidor:5432/base_de_datos
BOOKING_RESOURCE_ID=1
ALLOWED_TELEGRAM_USER_IDS=
```

**Descripción de las variables:**

| Variable | Descripción |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token proporcionado por BotFather |
| `GEMINI_API_KEY` | Credencial de acceso a Gemini |
| `GEMINI_MODEL` | Modelo habilitado para la clave utilizada |
| `DATABASE_URL` | Cadena de conexión PostgreSQL de Supabase |
| `BOOKING_RESOURCE_ID` | Identificador de la agenda que administra el bot |
| `ALLOWED_TELEGRAM_USER_IDS` | IDs de Telegram autorizados, separados por comas |

Si `ALLOWED_TELEGRAM_USER_IDS` está vacío, el bot permite el acceso de cualquier usuario que lo encuentre.

**Seguridad:** nunca publiques `.env` ni compartas las credenciales en GitHub.

### 2. Configurar la base de datos

AgendaAI requiere las siguientes tablas:

| Tabla | Función |
|---|---|
| `bot_users` | Identificar usuarios de Telegram |
| `messages` | Almacenar historial conversacional |
| `booking_resources` | Definir agendas o servicios disponibles |
| `availability_rules` | Configurar días y horarios de atención |
| `bookings` | Registrar reservas confirmadas o canceladas |
| `pending_bookings` | Estructura preparada para solicitudes pendientes |

Desde **Supabase → SQL Editor**, ejecuta las migraciones SQL del proyecto en su orden correspondiente.

La base de datos debe incluir una restricción de exclusión que impida reservas confirmadas superpuestas y un índice único de claves de idempotencia.

### 3. Configurar los horarios de atención

Los servicios se encuentran almacenados en `booking_resources` y los horarios en `availability_rules`.

Para visualizar la configuración actual:

```sql
SELECT
    r.id,
    r.name,
    r.duration_minutes,
    a.weekday,
    a.start_time,
    a.end_time
FROM public.booking_resources AS r
JOIN public.availability_rules AS a
    ON r.id = a.resource_id
ORDER BY r.id, a.weekday;
```

El campo `weekday` utiliza los valores del 1 al 7, donde el lunes corresponde a 1 y el domingo a 7.

Asegúrate de que `BOOKING_RESOURCE_ID` corresponda a un servicio activo.

## Ejecutar el bot

Desde la raíz del proyecto, con el entorno virtual activado:

```powershell
python -m agendaai.main
```

Si la configuración es correcta, aparecerán mensajes similares a:

```text
INFO Iniciando AgendaAI...
INFO Supabase conectado. Agenda: Asesoría general
INFO Application started
```

El bot permanecerá ejecutándose mientras el proceso esté activo.

Para detenerlo, presiona `Ctrl + C` en la terminal.

## Cómo utilizar AgendaAI

Abre Telegram e inicia una conversación con tu bot.

### Consultar disponibilidad

Envía un mensaje como:

> ¿Está disponible el 15 de octubre a las 15:00?

AgendaAI consultará la información almacenada en PostgreSQL antes de responder.

### Consultar horarios libres

> ¿Qué horarios tienes disponibles el jueves?

El bot obtendrá los espacios que no estén ocupados por reservas confirmadas y que se encuentren dentro del horario de atención.

### Crear una reserva

> Quiero reservar una asesoría para el jueves a las 15:00.

Si la solicitud contiene los datos necesarios y el horario está disponible, AgendaAI registrará automáticamente la reserva.

Una respuesta exitosa tendrá un formato similar al siguiente:

```text
RESERVA CONFIRMADA

Código: AG-000125
Servicio: Asesoría general
Motivo: Asesoría
Fecha: 15/10/2026
Horario: 15:00–15:30
Zona horaria: America/Santiago
Estado: Confirmada
```

El ejemplo es ilustrativo. Los datos y el código dependerán de la reserva creada.

### ¿Qué ocurre si la hora está ocupada?

La operación será rechazada y el usuario recibirá un mensaje indicando que el horario no está disponible.

Luego podrá consultar otros espacios libres y solicitar una nueva reserva.

## Comandos disponibles

| Comando | Descripción |
|---|---|
| `/start` | Muestra información inicial del bot |
| `/whoami` | Muestra el identificador de Telegram |
| `/reset` | Elimina el historial conversacional del usuario |

**Importante:** `/reset` no elimina ni cancela las reservas realizadas.

Las funciones para listar y cancelar reservas directamente desde Telegram forman parte de las ampliaciones previstas.

## Protección contra reservas simultáneas

AgendaAI utiliza PostgreSQL como fuente de verdad para determinar si una cita puede confirmarse.

Cuando dos usuarios solicitan horarios superpuestos:

1. El backend inicia una transacción de reserva.
2. PostgreSQL coordina el acceso al recurso mediante un bloqueo transaccional.
3. La operación comprueba las reglas de atención y las reservas existentes.
4. Si el intervalo está disponible, se inserta la reserva.
5. El siguiente intento encuentra el horario ocupado y es rechazado.

Además, una restricción `EXCLUDE USING gist` impide que existan reservas confirmadas superpuestas para un mismo recurso.

De esta manera, la protección no depende de las respuestas generadas por Gemini.

La prioridad corresponde al orden efectivo de procesamiento en PostgreSQL, que no garantiza necesariamente el mismo orden cronológico de envío de mensajes en Telegram.

## Ejecutar pruebas

Para comprobar el funcionamiento general:

```powershell
python -m pytest -q
```

Para revisar el estilo del código:

```powershell
python -m ruff check .
python -m ruff format --check .
```

### Prueba de reservas simultáneas

El proyecto incluye un script para comprobar que dos solicitudes concurrentes no generen reservas superpuestas:

```powershell
python scripts/test_booking_race.py
```

La prueba utiliza conexiones independientes a PostgreSQL e intenta crear dos reservas para el mismo horario.

El resultado esperado es una operación aceptada y otra rechazada, con una sola reserva confirmada en la base de datos.

El script crea datos temporales y los elimina al finalizar. Debe ejecutarse únicamente sobre una base de datos destinada a desarrollo o pruebas.

## Consultar reservas desde Supabase

Para revisar los últimos registros:

```sql
SELECT
    id,
    telegram_user_id,
    title,
    start_at AT TIME ZONE 'America/Santiago'
        AS inicio,
    end_at AT TIME ZONE 'America/Santiago'
        AS termino,
    status
FROM public.bookings
ORDER BY created_at DESC
LIMIT 20;
```

Esta consulta permite verificar qué reservas se han guardado y cuáles continúan confirmadas.

## Seguridad y privacidad

AgendaAI procesa identificadores de Telegram, contenido de mensajes y datos relacionados con las reservas.

Se recomienda:

- Mantener las claves y contraseñas en variables de entorno.
- No publicar archivos con credenciales.
- Restringir el acceso a Supabase.
- Utilizar una API de Gemini con condiciones de tratamiento de datos apropiadas cuando se procesen datos personales.
- Definir políticas de conservación y eliminación de datos.
- Controlar los permisos administrativos antes de habilitar el bot públicamente.

La seguridad de la base de datos y la protección de los secretos son independientes de la selección del modelo de inteligencia artificial.

## Limitaciones actuales

El sistema se encuentra en desarrollo y actualmente administra una agenda compartida configurada mediante `BOOKING_RESOURCE_ID`.

Todavía no incluye una interfaz web de administración, recordatorios automáticos ni comandos completos para que los usuarios consulten, modifiquen o cancelen sus reservas.

El bot también depende de la disponibilidad de la API de Gemini para interpretar solicitudes en lenguaje natural.

## Próximas mejoras

- Consulta de reservas por usuario mediante `/misreservas`.
- Cancelación y reprogramación de citas.
- Administración de múltiples servicios y profesionales.
- Notificaciones y recordatorios automáticos.
- Panel web de administración.
- Registro y monitoreo de consumo de tokens.
- Mejor manejo de errores y reintentos.
- Validación reforzada de fechas y zonas horarias.
- Despliegue permanente en la nube.

## Licencia

La licencia del proyecto queda pendiente de definición.

---

**AgendaAI** — Sistema de reservas conversacional desarrollado con Python, Telegram, Google Gemini y Supabase PostgreSQL.


## Ejecutar

```powershell
python -m pytest -q
python -m ruff check .
python -m agendaai.main
```

En Telegram abre tu bot y ejecuta `/start`, `/whoami` y envía un mensaje.

**Importante**: la lista de usuarios permitidos comienza vacía para facilitar el primer inicio, por lo que cualquier usuario que encuentre el bot puede usar tu cuota de Gemini. Tras ejecutar `/whoami`, detén el bot, define `ALLOWED_TELEGRAM_USER_IDS=TU_ID_NUMERICO` en `.env` y reinícialo. Idealmente usa un bot de pruebas que no compartas públicamente hasta entonces. Nunca hagas commit de `.env`.

## Diseño

- `config.py`: configuración y allowlist
- `gemini_service.py`: adaptador para la API Gemini
- `handlers.py`: controladores Telegram + errores
- `main.py`: composición e inicio con long polling
- `tests/`: pruebas que no consumen tokens

## Limitaciones intencionales

- Sin historia de conversación entre mensajes ni memoria persistente.
- Sin Google Calendar ni OAuth.
- Long polling local, no apto para Cloud Run con escalado a cero: migrar a FastAPI + webhook en la semana 4.
- Sin rate limiting, gestión multiusuario ni traza distribuida todavía.

## Próximo laboratorio

Function calling y contratos Pydantic: herramientas simuladas antes de conectar Google Calendar con OAuth y confirmación explícita.
