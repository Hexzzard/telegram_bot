# AgendaAI — Laboratorio 01

Primer bot Telegram + Gemini, desarrollado en Python. **No crea eventos aún** y **no guarda memoria**.

## Requisitos

- Python 3.12 o superior
- Bot de Telegram creado con [BotFather](https://t.me/BotFather)
- API key en [Google AI Studio](https://aistudio.google.com/api-keys)

## Instalación (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
Copy-Item .env.example .env
```

Edita `.env` y configura `TELEGRAM_BOT_TOKEN` y `GEMINI_API_KEY`. El modelo por defecto es `gemini-2.5-flash-lite`; cámbialo en `.env` si tu cuenta/proyecto no lo tiene disponible. Verifica cuotas y precio en Google AI Studio.

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
