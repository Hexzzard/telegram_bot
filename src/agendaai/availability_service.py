from datetime import (
    UTC,
    date,
    datetime,
    time,
    timedelta,
)
from zoneinfo import ZoneInfo

from agendaai.booking_repository import (
    AvailabilityRule,
    BookedInterval,
    BookingRepository,
)


class AvailabilityService:
    def __init__(self, repository: BookingRepository):
        self._repository = repository

    @staticmethod
    def _overlaps(
        start: datetime,
        end: datetime,
        booking: BookedInterval,
    ) -> bool:
        return start < booking.end_at and end > booking.start_at

    @staticmethod
    def _inside_working_hours(
        start: datetime,
        end: datetime,
        rules: list[AvailabilityRule],
        tz: ZoneInfo,
    ) -> bool:
        local_start = start.astimezone(tz)
        local_end = end.astimezone(tz)

        if local_start.date() != local_end.date():
            return False

        for rule in rules:
            if rule.weekday != local_start.isoweekday():
                continue

            opening = datetime.combine(
                local_start.date(),
                rule.start_time,
                tzinfo=tz,
            )

            closing = datetime.combine(
                local_start.date(),
                rule.end_time,
                tzinfo=tz,
            )

            if local_start >= opening and local_end <= closing:
                return True

        return False

    async def check_availability(
        self,
        resource_id: int,
        start_at: datetime,
        *,
        now: datetime | None = None,
    ) -> bool:
        if start_at.tzinfo is None:
            raise ValueError("start_at debe incluir zona horaria")

        resource = await self._repository.get_resource(resource_id)

        if resource is None:
            raise ValueError("El recurso no existe o está inactivo")

        tz = ZoneInfo(resource.timezone)

        local_start = start_at.astimezone(tz)
        end_at = local_start.astimezone(UTC) + timedelta(minutes=resource.duration_minutes)

        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError("now debe incluir zona horaria")

        if start_at <= current_time:
            return False

        rules = await self._repository.get_rules(resource_id)

        if not self._inside_working_hours(
            local_start,
            end_at,
            rules,
            tz,
        ):
            return False

        bookings = await self._repository.get_bookings(
            resource_id,
            local_start,
            end_at,
        )

        return not any(self._overlaps(local_start, end_at, booking) for booking in bookings)

    async def get_available_slots(
        self,
        resource_id: int,
        target_date: date,
        *,
        now: datetime | None = None,
    ) -> list[datetime]:
        resource = await self._repository.get_resource(resource_id)

        if resource is None:
            raise ValueError("El recurso no existe o está inactivo")

        tz = ZoneInfo(resource.timezone)

        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError("now debe incluir zona horaria")

        rules = await self._repository.get_rules(resource_id)

        day_rules = [rule for rule in rules if rule.weekday == target_date.isoweekday()]

        if not day_rules:
            return []

        day_start = datetime.combine(
            target_date,
            time.min,
            tzinfo=tz,
        )

        next_day = datetime.combine(
            target_date + timedelta(days=1),
            time.min,
            tzinfo=tz,
        )

        bookings = await self._repository.get_bookings(
            resource_id,
            day_start,
            next_day,
        )

        duration = timedelta(minutes=resource.duration_minutes)

        available = []

        for rule in day_rules:
            opening = datetime.combine(
                target_date,
                rule.start_time,
                tzinfo=tz,
            )

            closing = datetime.combine(
                target_date,
                rule.end_time,
                tzinfo=tz,
            )

            candidate = opening

            while candidate + duration <= closing:
                candidate_end = candidate.astimezone(UTC) + duration

                if (
                    candidate > current_time
                    and self._inside_working_hours(
                        candidate,
                        candidate_end,
                        rules,
                        tz,
                    )
                    and not any(
                        self._overlaps(
                            candidate,
                            candidate_end,
                            booking,
                        )
                        for booking in bookings
                    )
                ):
                    available.append(candidate)

                candidate += duration

        return sorted(set(available))
