
import asyncio
import selectors
import sys

import uvicorn



def create_event_loop():
    """Crea un bucle compatible con Psycopg en Windows."""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(
            selectors.SelectSelector()
        )

    return asyncio.new_event_loop()


def main():
    config = uvicorn.Config(
        "whatsapp_gateway.app:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
        reload=False,
        workers=1,
    )

    server = uvicorn.Server(config)

    # Python 3.12: controlar explícitamente
    # qué bucle ejecuta Uvicorn.
    with asyncio.Runner(
        loop_factory=create_event_loop
    ) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    main()
