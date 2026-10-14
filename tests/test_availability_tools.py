import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
import json

import pytest

from agendaai.tools.availability import (
    AvailabilityToolExecutor,
)

CHILE = ZoneInfo("America/Santiago")


class FakeAvailabilityService:
    def __init__(self):
        self.last_call = None

    async def check_availability(
        self,
        resource_id,
        start_at,
    ):
        self.last_call = (
            "check_availability",
            resource_id,
            start_at,
        )
        return True

    async def get_available_slots(
        self,
        resource_id,
        target_date,
    ):
        self.last_call = (
            "get_available_slots",
            resource_id,
            target_date,
        )

        return [
            datetime(2030, 10, 15, 9, 0, tzinfo=CHILE),
            datetime(2030, 10, 15, 9, 30, tzinfo=CHILE),
        ]


def test_check_availability_tool():
    async def scenario():
        service = FakeAvailabilityService()

        executor = AvailabilityToolExecutor(
            service=service,
            resource_id=1,
        )

        result = await executor.execute(
            "check_availability",
            {
                "start_at": ("2030-10-15T10:00:00-03:00"),
            },
        )

        payload = json.loads(result)

        assert payload["type"] == "availability_check"
        assert payload["available"] is True
        assert payload["reservation_created"] is False
        assert service.last_call[0] == "check_availability"
        assert service.last_call[1] == 1

    asyncio.run(scenario())


def test_get_available_slots_tool():
    async def scenario():
        service = FakeAvailabilityService()

        executor = AvailabilityToolExecutor(
            service=service,
            resource_id=1,
        )

        result = await executor.execute(
            "get_available_slots",
            {"target_date": "2030-10-15"},
        )

        payload = json.loads(result)
        assert payload["type"] == "available_slots"
        assert payload["date"] == "2030-10-15"
        assert payload["slots"] == ["09:00", "09:30"]
        assert payload["reservation_created"] is False
        assert service.last_call[0] == "get_available_slots"

    asyncio.run(scenario())


def test_reject_invalid_date():
    async def scenario():
        service = FakeAvailabilityService()

        executor = AvailabilityToolExecutor(
            service=service,
            resource_id=1,
        )

        result = await executor.execute(
            "get_available_slots",
            {"target_date": "fecha incorrecta"},
        )

        assert "no son válidos" in result
        assert service.last_call is None

    asyncio.run(scenario())


def test_reject_unknown_tool():
    async def scenario():
        executor = AvailabilityToolExecutor(
            service=FakeAvailabilityService(),
            resource_id=1,
        )

        with pytest.raises(ValueError):
            await executor.execute(
                "delete_all_bookings",
                {},
            )

    asyncio.run(scenario())
