from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select

from .extensions import db
from .models import CheckinLink, Flat, Reservation, ReservationStatus


@dataclass(slots=True)
class FlatAvailability:
    flat: Flat
    status: str
    current_reservation: Reservation | None
    upcoming_reservation: Reservation | None
    next_checkin_date: date | None
    next_checkout_date: date | None

    @property
    def focus_reservation(self) -> Reservation | None:
        return self.current_reservation or self.upcoming_reservation


def generate_token(length: int = 8) -> str:
    while True:
        token = secrets.token_hex(length // 2)
        exists = db.session.scalar(
            select(CheckinLink.id).where(CheckinLink.token == token)
        )

        if exists is None:
            return token


def build_flat_availability(flat: Flat, reference_date: date | None = None) -> FlatAvailability:
    reference_date = reference_date or date.today()
    blocking_reservations = [
        reservation
        for reservation in flat.reservations
        if reservation.status in ReservationStatus.blocking()
    ]
    current_reservation = next(
        (
            reservation
            for reservation in blocking_reservations
            if reservation.checkin_date <= reference_date < reservation.checkout_date
        ),
        None,
    )
    upcoming_reservation = next(
        (
            reservation
            for reservation in blocking_reservations
            if reservation.checkin_date > reference_date
        ),
        None,
    )

    if current_reservation is not None:
        return FlatAvailability(
            flat=flat,
            status="Occupied",
            current_reservation=current_reservation,
            upcoming_reservation=upcoming_reservation,
            next_checkin_date=current_reservation.checkin_date,
            next_checkout_date=current_reservation.checkout_date,
        )

    if upcoming_reservation is not None:
        return FlatAvailability(
            flat=flat,
            status="Upcoming",
            current_reservation=None,
            upcoming_reservation=upcoming_reservation,
            next_checkin_date=upcoming_reservation.checkin_date,
            next_checkout_date=upcoming_reservation.checkout_date,
        )

    return FlatAvailability(
        flat=flat,
        status="Available",
        current_reservation=None,
        upcoming_reservation=None,
        next_checkin_date=None,
        next_checkout_date=None,
    )


def build_flats_dashboard(
    flats: list[Flat],
    reference_date: date | None = None,
) -> list[FlatAvailability]:
    return [build_flat_availability(flat, reference_date) for flat in flats]


def build_flat_calendar(
    flat: Flat,
    reference_date: date | None = None,
    number_of_days: int = 45,
) -> list[dict[str, object]]:
    reference_date = reference_date or date.today()
    days: list[dict[str, object]] = []
    blocking_reservations = [
        reservation
        for reservation in flat.reservations
        if reservation.status in ReservationStatus.blocking()
    ]

    for day_offset in range(number_of_days):
        current_day = reference_date + timedelta(days=day_offset)
        matching_reservation = next(
            (
                reservation
                for reservation in blocking_reservations
                if reservation.checkin_date <= current_day < reservation.checkout_date
            ),
            None,
        )

        if matching_reservation is None:
            status = "available"
        elif matching_reservation.checkin_date <= reference_date < matching_reservation.checkout_date:
            status = "occupied"
        else:
            status = "upcoming"

        days.append(
            {
                "date": current_day,
                "label": current_day.strftime("%d %b"),
                "weekday": current_day.strftime("%a"),
                "status": status,
                "reservation": matching_reservation,
                "is_today": current_day == reference_date,
            }
        )

    return days


def find_overlapping_reservation(
    flat_id: int,
    checkin_date: date,
    checkout_date: date,
    exclude_reservation_id: int | None = None,
) -> Reservation | None:
    query = (
        select(Reservation)
        .where(Reservation.flat_id == flat_id)
        .where(Reservation.status.in_(ReservationStatus.blocking()))
        .where(Reservation.checkin_date < checkout_date)
        .where(Reservation.checkout_date > checkin_date)
        .order_by(Reservation.checkin_date)
    )

    if exclude_reservation_id is not None:
        query = query.where(Reservation.id != exclude_reservation_id)

    return db.session.scalar(query)


def create_reservation(
    *,
    flat_id: int,
    guest_name: str,
    checkin_date: date,
    checkout_date: date,
    status: str,
) -> Reservation:
    normalized_guest_name = guest_name.strip()

    if not normalized_guest_name:
        raise ValueError("Guest name is required.")

    if status not in ReservationStatus.choices():
        raise ValueError("Invalid reservation status.")

    if checkout_date <= checkin_date:
        raise ValueError("Check-out date must be after check-in date.")

    if status != ReservationStatus.CANCELLED:
        overlapping_reservation = find_overlapping_reservation(
            flat_id,
            checkin_date,
            checkout_date,
        )

        if overlapping_reservation is not None:
            raise ValueError(
                "This flat already has a reservation overlapping the selected period."
            )

    reservation = Reservation(
        flat_id=flat_id,
        guest_name=normalized_guest_name,
        checkin_date=checkin_date,
        checkout_date=checkout_date,
        status=status,
    )
    db.session.add(reservation)

    return reservation


def mark_link_viewed(checkin_link: CheckinLink) -> None:
    checkin_link.view_count = (checkin_link.view_count or 0) + 1
    checkin_link.last_viewed = datetime.utcnow()
