
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from agendaai.booking_repository import (
    BookingRepository,
    BookingResult,
)


class BookingService:
    def __init__(
        self,
        repository: BookingRepository,
        resource_id: int,
    ):
        self._repository = repository
        self._resource_id = resource_id

    async def create_booking(
        self,
        *,
        user_id: int,
        telegram_update_id: int,
        title: str,
        start_at: datetime,
    ) -> BookingResult:
        key = uuid5(
            NAMESPACE_URL,
            (
                "agendaai:booking:"
                f"{user_id}:{telegram_update_id}"
            ),
        )

        return await self._repository.create_booking(
            resource_id=self._resource_id,
            user_id=user_id,
            title=title,
            start_at=start_at,
            idempotency_key=key,
        )
