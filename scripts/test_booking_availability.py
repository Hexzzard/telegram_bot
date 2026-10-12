import asyncio
import sys
from datetime import date

from agendaai.availability_service import (
    AvailabilityService,
)
from agendaai.booking_repository import (
    BookingRepository,
)
from agendaai.config import Settings

RESOURCE_ID = 1


async def main() -> None:
    settings = Settings()

    repository = BookingRepository(settings.database_url.get_secret_value())

    service = AvailabilityService(repository)

    target_date = date(2026, 10, 15)

    resource = await repository.get_resource(RESOURCE_ID)

    if resource is None:
        print("No se encontró el servicio.")
        return

    print(f"Servicio: {resource.name}")
    print(f"Fecha: {target_date}")
    print("\nHorarios disponibles:\n")

    slots = await service.get_available_slots(
        resource_id=RESOURCE_ID,
        target_date=target_date,
    )

    if not slots:
        print("No hay horarios disponibles.")
        return

    for slot in slots:
        print(slot.strftime("%H:%M"))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
