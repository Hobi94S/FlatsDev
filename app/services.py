from __future__ import annotations

import secrets
import re
import unicodedata
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


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug or "flat"


def generate_unique_flat_slug(building_name: str, room_number: str) -> str:
    return build_unique_flat_slug(building_name, room_number)


def normalize_flat_payload(
    *,
    building_name: str,
    room_number: str,
    address: str,
    checkin_time: str,
    checkout_time: str,
    house_rules: str,
    wifi_name: str,
    wifi_password: str,
    parking_instructions: str,
) -> dict[str, str]:
    return {
        "building_name": building_name.strip(),
        "room_number": room_number.strip(),
        "address": address.strip(),
        "checkin_time": checkin_time.strip(),
        "checkout_time": checkout_time.strip(),
        "house_rules": house_rules.strip(),
        "wifi_name": wifi_name.strip(),
        "wifi_password": wifi_password.strip(),
        "parking_instructions": parking_instructions.strip(),
    }


def validate_flat_payload(
    payload: dict[str, str],
    *,
    exclude_flat_id: int | None = None,
) -> dict[str, str]:
    if not payload["building_name"]:
        raise ValueError("O nome do empreendimento e obrigatorio.")

    if not payload["room_number"]:
        raise ValueError("O numero do quarto ou flat e obrigatorio.")

    if not payload["address"]:
        raise ValueError("O endereco e obrigatorio.")

    if not payload["checkin_time"]:
        raise ValueError("O horario de check-in e obrigatorio.")

    if not payload["checkout_time"]:
        raise ValueError("O horario de check-out e obrigatorio.")

    if not payload["house_rules"]:
        raise ValueError("As regras da hospedagem sao obrigatorias.")

    if not payload["wifi_name"]:
        raise ValueError("O nome da rede Wi-Fi e obrigatorio.")

    if not payload["wifi_password"]:
        raise ValueError("A senha do Wi-Fi e obrigatoria.")

    if not payload["parking_instructions"]:
        raise ValueError("As instrucoes de estacionamento sao obrigatorias.")

    existing_flat_query = (
        select(Flat)
        .where(Flat.building_name == payload["building_name"])
        .where(Flat.room_number == payload["room_number"])
    )
    if exclude_flat_id is not None:
        existing_flat_query = existing_flat_query.where(Flat.id != exclude_flat_id)

    existing_flat = db.session.scalar(existing_flat_query)
    if existing_flat is not None:
        raise ValueError("Ja existe um flat com esse empreendimento e quarto.")

    return payload


def build_flat_name(building_name: str, room_number: str) -> str:
    return f"{building_name} - {room_number}"


def build_unique_flat_slug(
    building_name: str,
    room_number: str,
    *,
    exclude_flat_id: int | None = None,
) -> str:
    base_slug = slugify(f"{building_name}-{room_number}")
    slug = base_slug
    suffix = 2

    while True:
        existing_flat_id = db.session.scalar(select(Flat.id).where(Flat.slug == slug))
        if existing_flat_id is None or existing_flat_id == exclude_flat_id:
            return slug

        slug = f"{base_slug}-{suffix}"
        suffix += 1


def create_flat(
    *,
    building_name: str,
    room_number: str,
    address: str,
    checkin_time: str,
    checkout_time: str,
    house_rules: str,
    wifi_name: str,
    wifi_password: str,
    parking_instructions: str,
) -> Flat:
    payload = validate_flat_payload(
        normalize_flat_payload(
            building_name=building_name,
            room_number=room_number,
            address=address,
            checkin_time=checkin_time,
            checkout_time=checkout_time,
            house_rules=house_rules,
            wifi_name=wifi_name,
            wifi_password=wifi_password,
            parking_instructions=parking_instructions,
        )
    )

    flat = Flat(
        building_name=payload["building_name"],
        room_number=payload["room_number"],
        name=build_flat_name(payload["building_name"], payload["room_number"]),
        slug=build_unique_flat_slug(payload["building_name"], payload["room_number"]),
        address=payload["address"],
        checkin_time=payload["checkin_time"],
        checkout_time=payload["checkout_time"],
        house_rules=payload["house_rules"],
        wifi_name=payload["wifi_name"],
        wifi_password=payload["wifi_password"],
        parking_instructions=payload["parking_instructions"],
    )
    db.session.add(flat)
    return flat


def update_flat(
    flat: Flat,
    *,
    building_name: str,
    room_number: str,
    address: str,
    checkin_time: str,
    checkout_time: str,
    house_rules: str,
    wifi_name: str,
    wifi_password: str,
    parking_instructions: str,
) -> Flat:
    payload = validate_flat_payload(
        normalize_flat_payload(
            building_name=building_name,
            room_number=room_number,
            address=address,
            checkin_time=checkin_time,
            checkout_time=checkout_time,
            house_rules=house_rules,
            wifi_name=wifi_name,
            wifi_password=wifi_password,
            parking_instructions=parking_instructions,
        ),
        exclude_flat_id=flat.id,
    )

    flat.building_name = payload["building_name"]
    flat.room_number = payload["room_number"]
    flat.name = build_flat_name(payload["building_name"], payload["room_number"])
    flat.slug = build_unique_flat_slug(
        payload["building_name"],
        payload["room_number"],
        exclude_flat_id=flat.id,
    )
    flat.address = payload["address"]
    flat.checkin_time = payload["checkin_time"]
    flat.checkout_time = payload["checkout_time"]
    flat.house_rules = payload["house_rules"]
    flat.wifi_name = payload["wifi_name"]
    flat.wifi_password = payload["wifi_password"]
    flat.parking_instructions = payload["parking_instructions"]

    return flat


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
        raise ValueError("O nome do hospede e obrigatorio.")

    if status not in ReservationStatus.choices():
        raise ValueError("Status de reserva invalido.")

    if checkout_date <= checkin_date:
        raise ValueError("A data de check-out deve ser posterior ao check-in.")

    if status != ReservationStatus.CANCELLED:
        overlapping_reservation = find_overlapping_reservation(
            flat_id,
            checkin_date,
            checkout_date,
        )

        if overlapping_reservation is not None:
            raise ValueError(
                "Este flat ja possui uma reserva que conflita com o periodo informado."
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
