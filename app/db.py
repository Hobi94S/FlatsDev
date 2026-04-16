from __future__ import annotations

from flask import Flask
from sqlalchemy import inspect, select, text

from .extensions import db
from .models import Flat
from .services import generate_unique_flat_slug


LEGACY_SEED_SLUGS = {"campina-standard-101", "campina-premium-202"}
LEGACY_DEMO_FLAT_KEYS = {
    ("Campina Standard 101", "Sem numero"),
    ("Campina Premium 202", "Sem numero"),
}
SEED_FLATS: list[dict[str, str]] = [
    {
        "building_name": "Exclusive Home - Campina Grande PB",
        "room_number": "1407",
        "address": "Vila Nova da Rainha, 169 - Centro, Campina Grande - PB, 58400-220",
    },
    {
        "building_name": "Exclusive Home - Campina Grande PB",
        "room_number": "704",
        "address": "Vila Nova da Rainha, 169 - Centro, Campina Grande - PB, 58400-220",
    },
    {
        "building_name": "Exclusive Home - Campina Grande PB",
        "room_number": "404",
        "address": "Vila Nova da Rainha, 169 - Centro, Campina Grande - PB, 58400-220",
    },
    {
        "building_name": "Sirius - Campina Grande PB",
        "room_number": "107",
        "address": "R. Olegario Mariano, 270 - Catole, Campina Grande - PB, 58410-124",
    },
    {
        "building_name": "Sirius - Campina Grande PB",
        "room_number": "206",
        "address": "R. Olegario Mariano, 270 - Catole, Campina Grande - PB, 58410-124",
    },
    {
        "building_name": "Sirius - Campina Grande PB",
        "room_number": "207",
        "address": "R. Olegario Mariano, 270 - Catole, Campina Grande - PB, 58410-124",
    },
    {
        "building_name": "Boulevard Plaza Flat",
        "room_number": "106",
        "address": "Rua Malaquias de Souza do O, 17 - Mirante",
    },
    {
        "building_name": "Boulevard Plaza Flat",
        "room_number": "301",
        "address": "Rua Malaquias de Souza do O, 17 - Mirante",
    },
    {
        "building_name": "Boulevard Plaza Flat",
        "room_number": "302",
        "address": "Rua Malaquias de Souza do O, 17 - Mirante",
    },
    {
        "building_name": "Boulevard Plaza Flat",
        "room_number": "306",
        "address": "Rua Malaquias de Souza do O, 17 - Mirante",
    },
    {
        "building_name": "Boulevard Plaza Flat",
        "room_number": "604",
        "address": "Rua Malaquias de Souza do O, 17 - Mirante",
    },
    {
        "building_name": "Boulevard Plaza Flat",
        "room_number": "605",
        "address": "Rua Malaquias de Souza do O, 17 - Mirante",
    },
    {
        "building_name": "Beach Haus - Joao Pessoa PB",
        "room_number": "222",
        "address": "Av. Gov. Argemiro de Figueiredo, 280 - Jardim Oceania, Joao Pessoa - PB, 58037-030",
    },
]
DEFAULT_CHECKIN_TIME = "14:00"
DEFAULT_CHECKOUT_TIME = "11:00"
DEFAULT_HOUSE_RULES = (
    "1. Nao fumar dentro do flat.\n"
    "2. Respeitar o horario de silencio das 22:00 as 08:00.\n"
    "3. Descartar o lixo antes do check-out.\n"
    "4. Informe imediatamente qualquer problema na hospedagem."
)
DEFAULT_WIFI_NAME = "FlatsDev"
DEFAULT_WIFI_PASSWORD = "alterar-senha"
DEFAULT_PARKING = "Consulte a recepcao ou a administracao para receber a vaga correta do quarto."


def initialize_database(app: Flask) -> None:
    with app.app_context():
        db.create_all()
        apply_legacy_migrations()
        remove_legacy_demo_flats()
        seed_flats()
        db.session.commit()


def apply_legacy_migrations() -> None:
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())

    if "flats" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("flats")}
        missing_flat_columns = {
            "building_name": "ALTER TABLE flats ADD COLUMN building_name VARCHAR(120)",
            "room_number": "ALTER TABLE flats ADD COLUMN room_number VARCHAR(40)",
        }

        for column_name, ddl in missing_flat_columns.items():
            if column_name not in existing_columns:
                db.session.execute(text(ddl))

        backfill_flat_structure()

    if "checkin_links" in table_names:
        existing_columns = {
            column["name"] for column in inspector.get_columns("checkin_links")
        }
        missing_link_columns = {
            "reservation_id": "ALTER TABLE checkin_links ADD COLUMN reservation_id INTEGER REFERENCES reservations (id)",
            "view_count": "ALTER TABLE checkin_links ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0",
            "last_viewed": "ALTER TABLE checkin_links ADD COLUMN last_viewed DATETIME",
        }

        for column_name, ddl in missing_link_columns.items():
            if column_name not in existing_columns:
                db.session.execute(text(ddl))

    db.session.commit()


def backfill_flat_structure() -> None:
    flats = db.session.scalars(select(Flat).order_by(Flat.id)).all()

    for flat in flats:
        if flat.building_name and flat.room_number:
            if flat.name != f"{flat.building_name} - {flat.room_number}":
                flat.name = f"{flat.building_name} - {flat.room_number}"
            continue

        if " - " in flat.name:
            building_name, room_number = flat.name.rsplit(" - ", 1)
            flat.building_name = building_name.strip()
            flat.room_number = room_number.strip()
        else:
            flat.building_name = flat.name.strip()
            flat.room_number = flat.room_number or "Sem numero"

        flat.name = f"{flat.building_name} - {flat.room_number}"


def remove_legacy_demo_flats() -> None:
    flats = db.session.scalars(select(Flat).order_by(Flat.id)).all()
    legacy_flats = [
        flat
        for flat in flats
        if flat.slug in LEGACY_SEED_SLUGS
        or ((flat.building_name or "").strip(), (flat.room_number or "").strip())
        in LEGACY_DEMO_FLAT_KEYS
    ]

    for flat in legacy_flats:
        db.session.execute(
            text(
                "DELETE FROM confirmations "
                "WHERE checkin_link_id IN (SELECT id FROM checkin_links WHERE flat_id = :flat_id)"
            ),
            {"flat_id": flat.id},
        )
        db.session.execute(
            text("DELETE FROM checkin_links WHERE flat_id = :flat_id"),
            {"flat_id": flat.id},
        )
        db.session.execute(
            text("DELETE FROM reservations WHERE flat_id = :flat_id"),
            {"flat_id": flat.id},
        )
        db.session.delete(flat)


def seed_flats() -> None:
    existing_pairs = {
        ((flat.building_name or "").strip(), (flat.room_number or "").strip()): flat
        for flat in db.session.scalars(select(Flat)).all()
    }

    for flat_data in SEED_FLATS:
        key = (flat_data["building_name"], flat_data["room_number"])
        if key in existing_pairs:
            flat = existing_pairs[key]
            flat.building_name = flat_data["building_name"]
            flat.room_number = flat_data["room_number"]
            flat.name = f"{flat_data['building_name']} - {flat_data['room_number']}"
            flat.address = flat_data["address"]
            continue

        db.session.add(
            Flat(
                building_name=flat_data["building_name"],
                room_number=flat_data["room_number"],
                name=f"{flat_data['building_name']} - {flat_data['room_number']}",
                slug=generate_unique_flat_slug(
                    flat_data["building_name"],
                    flat_data["room_number"],
                ),
                address=flat_data["address"],
                checkin_time=DEFAULT_CHECKIN_TIME,
                checkout_time=DEFAULT_CHECKOUT_TIME,
                house_rules=DEFAULT_HOUSE_RULES,
                wifi_name=DEFAULT_WIFI_NAME,
                wifi_password=DEFAULT_WIFI_PASSWORD,
                parking_instructions=DEFAULT_PARKING,
            )
        )
