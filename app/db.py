from __future__ import annotations

from datetime import date, timedelta

from flask import Flask
from sqlalchemy import func, inspect, select, text

from .extensions import db
from .models import Flat, Reservation, ReservationStatus


SEED_FLATS: list[dict[str, str]] = [
    {
        "name": "Campina Standard 101",
        "slug": "campina-standard-101",
        "address": "Rua das Flores, 101 - Campina Grande, PB",
        "checkin_time": "14:00",
        "checkout_time": "11:00",
        "house_rules": (
            "1. No smoking inside the flat.\n"
            "2. Quiet hours from 22:00 to 08:00.\n"
            "3. Please dispose of trash before check-out."
        ),
        "wifi_name": "CampinaFlats_101",
        "wifi_password": "campina101",
        "parking_instructions": (
            "Use the parking space labeled 101. "
            "Please keep the access gate closed after entering or leaving."
        ),
    },
    {
        "name": "Campina Premium 202",
        "slug": "campina-premium-202",
        "address": "Av. Central, 202 - Campina Grande, PB",
        "checkin_time": "15:00",
        "checkout_time": "11:00",
        "house_rules": (
            "1. No parties or events.\n"
            "2. Respect condominium common areas.\n"
            "3. Inform us immediately about any issue in the flat."
        ),
        "wifi_name": "CampinaFlats_202",
        "wifi_password": "campina202",
        "parking_instructions": (
            "Visitor access is through Gate B. "
            "Use parking spot P-202 and identify yourself at the concierge if requested."
        ),
    },
]


def initialize_database(app: Flask) -> None:
    with app.app_context():
        db.create_all()
        apply_legacy_migrations()
        seed_flats()
        seed_reservations()
        db.session.commit()


def apply_legacy_migrations() -> None:
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())

    if "checkin_links" not in table_names:
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("checkin_links")
    }
    missing_columns = {
        "reservation_id": "ALTER TABLE checkin_links ADD COLUMN reservation_id INTEGER REFERENCES reservations (id)",
        "view_count": "ALTER TABLE checkin_links ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0",
        "last_viewed": "ALTER TABLE checkin_links ADD COLUMN last_viewed DATETIME",
    }

    for column_name, ddl in missing_columns.items():
        if column_name not in existing_columns:
            db.session.execute(text(ddl))

    db.session.commit()


def seed_flats() -> None:
    flat_count = db.session.scalar(select(func.count(Flat.id))) or 0

    if flat_count > 0:
        return

    db.session.add_all(Flat(**flat_data) for flat_data in SEED_FLATS)


def seed_reservations() -> None:
    reservation_count = db.session.scalar(select(func.count(Reservation.id))) or 0

    if reservation_count > 0:
        return

    flats = db.session.scalars(select(Flat).order_by(Flat.id)).all()

    if len(flats) < 2:
        return

    today = date.today()
    db.session.add_all(
        [
            Reservation(
                flat_id=flats[0].id,
                guest_name="Marina Costa",
                checkin_date=today,
                checkout_date=today + timedelta(days=3),
                status=ReservationStatus.BOOKED,
            ),
            Reservation(
                flat_id=flats[1].id,
                guest_name="Carlos Lima",
                checkin_date=today + timedelta(days=5),
                checkout_date=today + timedelta(days=9),
                status=ReservationStatus.BOOKED,
            ),
        ]
    )
