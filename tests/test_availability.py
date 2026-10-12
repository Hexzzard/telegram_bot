import asyncio
from datetime import (
    date,
    datetime,
    time,
)
from zoneinfo import ZoneInfo

from agendaai.availability_service import (
    AvailabilityService,
)
from agendaai.booking_repository import (
    AvailabilityRule,
    BookedInterval,
    BookingResource,
)

CHILE = ZoneInfo("America/Santiago")

TEST_NOW = datetime(2026, 10, 12, 10, 0, tzinfo=CHILE)


class FakeBookingRepository:
    def __init__(self):
        self.resource = BookingResource(
            id=1,
            name="Asesoría general",
            duration_minutes=30,
            timezone="America/Santiago",
        )

        self.rules = [
            AvailabilityRule(
                weekday=day,
                start_time=time(9, 0),
                end_time=time(17, 0),
            )
            for day in range(1, 6)
        ]

        self.bookings = [
            BookedInterval(
                start_at=datetime(
                    2026,
                    10,
                    15,
                    15,
                    0,
                    tzinfo=CHILE,
                ),
                end_at=datetime(
                    2026,
                    10,
                    15,
                    15,
                    30,
                    tzinfo=CHILE,
                ),
            )
        ]

    async def get_resource(self, resource_id):
        if resource_id == 1:
            return self.resource
        return None

    async def get_rules(self, resource_id):
        return self.rules

    async def get_bookings(
        self,
        resource_id,
        start_at,
        end_at,
    ):
        return [
            booking
            for booking in self.bookings
            if (booking.start_at < end_at and booking.end_at > start_at)
        ]


def make_service():
    return AvailabilityService(FakeBookingRepository())


def test_available_hour():
    async def scenario():
        service = make_service()

        result = await service.check_availability(
            resource_id=1,
            start_at=datetime(
                2026,
                10,
                15,
                14,
                0,
                tzinfo=CHILE,
            ),
            now=TEST_NOW,
        )

        assert result is True

    asyncio.run(scenario())


def test_occupied_hour():
    async def scenario():
        service = make_service()

        result = await service.check_availability(
            resource_id=1,
            start_at=datetime(
                2026,
                10,
                15,
                15,
                0,
                tzinfo=CHILE,
            ),
            now=TEST_NOW,
        )

        assert result is False

    asyncio.run(scenario())


def test_outside_working_hours():
    async def scenario():
        service = make_service()

        result = await service.check_availability(
            resource_id=1,
            start_at=datetime(
                2026,
                10,
                15,
                18,
                0,
                tzinfo=CHILE,
            ),
            now=TEST_NOW,
        )

        assert result is False

    asyncio.run(scenario())


def test_weekend_is_unavailable():
    async def scenario():
        service = make_service()

        result = await service.check_availability(
            resource_id=1,
            start_at=datetime(
                2026,
                10,
                17,
                10,
                0,
                tzinfo=CHILE,
            ),
            now=TEST_NOW,
        )

        assert result is False

    asyncio.run(scenario())


def test_available_slots_exclude_bookings():
    async def scenario():
        service = make_service()

        slots = await service.get_available_slots(
            resource_id=1,
            target_date=date(2026, 10, 15),
            now=TEST_NOW,
        )

        assert len(slots) == 15

        assert (
            datetime(
                2026,
                10,
                15,
                15,
                0,
                tzinfo=CHILE,
            )
            not in slots
        )

        assert (
            datetime(
                2026,
                10,
                15,
                15,
                30,
                tzinfo=CHILE,
            )
            in slots
        )

    asyncio.run(scenario())


def test_nonexistent_resource():
    async def scenario():
        service = make_service()

        try:
            await service.check_availability(
                resource_id=999,
                start_at=datetime(
                    2026,
                    10,
                    15,
                    14,
                    0,
                    tzinfo=CHILE,
                ),
                now=TEST_NOW,
            )
        except ValueError as error:
            assert "no existe" in str(error)
        else:
            raise AssertionError("Se aceptó un recurso inexistente")

    asyncio.run(scenario())


def test_past_hour_is_unavailable():
    async def scenario():
        service = make_service()

        result = await service.check_availability(
            resource_id=1,
            start_at=datetime(
                2026,
                10,
                12,
                9,
                0,
                tzinfo=CHILE,
            ),
            now=TEST_NOW,
        )

        assert result is False

    asyncio.run(scenario())
