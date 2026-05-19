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
    "1. Não fumar dentro do flat.\n"
    "2. Respeitar o horário de silêncio das 22:00 às 08:00.\n"
    "3. Descartar o lixo antes do check-out.\n"
    "4. Informe imediatamente qualquer problema na hospedagem."
)
DEFAULT_WIFI_NAME = "FlatsDev"
DEFAULT_WIFI_PASSWORD = "alterar-senha"
DEFAULT_PARKING = "Consulte a recepção ou a administração para receber a vaga correta do quarto."
DEFAULT_ARRIVAL_STEPS = (
    "1. Confira o prédio e o número do quarto antes de sair.\n"
    "2. Ao chegar, siga para a recepção ou entrada principal.\n"
    "3. Use as instruções de acesso ao prédio e depois a senha da porta do flat."
)
DEFAULT_BUILDING_ACCESS = "Apresente-se na portaria e siga as orientações de acesso informadas no link."
DEFAULT_DOOR_CODE = "Senha da fechadura digital a confirmar pela operação."
DEFAULT_FALLBACK_CONTACT = "WhatsApp da operação: +55 (83) 99999-9999"


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
    datetime_type = "TIMESTAMP" if db.engine.dialect.name == "postgresql" else "DATETIME"

    if "flats" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("flats")}
        missing_flat_columns = {
            "building_name": "ALTER TABLE flats ADD COLUMN building_name VARCHAR(120)",
            "room_number": "ALTER TABLE flats ADD COLUMN room_number VARCHAR(40)",
            "arrival_steps": "ALTER TABLE flats ADD COLUMN arrival_steps TEXT NOT NULL DEFAULT ''",
            "building_access": "ALTER TABLE flats ADD COLUMN building_access TEXT NOT NULL DEFAULT ''",
            "door_code": "ALTER TABLE flats ADD COLUMN door_code VARCHAR(120) NOT NULL DEFAULT ''",
            "fallback_contact": "ALTER TABLE flats ADD COLUMN fallback_contact VARCHAR(120) NOT NULL DEFAULT ''",
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
            "last_viewed": f"ALTER TABLE checkin_links ADD COLUMN last_viewed {datetime_type}",
            "sent_channel": "ALTER TABLE checkin_links ADD COLUMN sent_channel VARCHAR(40) NOT NULL DEFAULT 'WhatsApp'",
            "status": "ALTER TABLE checkin_links ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active'",
            "expires_at": f"ALTER TABLE checkin_links ADD COLUMN expires_at {datetime_type}",
            "generated_by": "ALTER TABLE checkin_links ADD COLUMN generated_by VARCHAR(120)",
            "sent_at": f"ALTER TABLE checkin_links ADD COLUMN sent_at {datetime_type}",
            "first_guest_opened_at": f"ALTER TABLE checkin_links ADD COLUMN first_guest_opened_at {datetime_type}",
            "last_guest_opened_at": f"ALTER TABLE checkin_links ADD COLUMN last_guest_opened_at {datetime_type}",
            "unique_guest_views": "ALTER TABLE checkin_links ADD COLUMN unique_guest_views INTEGER NOT NULL DEFAULT 0",
            "admin_preview_count": "ALTER TABLE checkin_links ADD COLUMN admin_preview_count INTEGER NOT NULL DEFAULT 0",
            "wifi_copy_count": "ALTER TABLE checkin_links ADD COLUMN wifi_copy_count INTEGER NOT NULL DEFAULT 0",
            "address_copy_count": "ALTER TABLE checkin_links ADD COLUMN address_copy_count INTEGER NOT NULL DEFAULT 0",
            "access_copy_count": "ALTER TABLE checkin_links ADD COLUMN access_copy_count INTEGER NOT NULL DEFAULT 0",
            "maps_click_count": "ALTER TABLE checkin_links ADD COLUMN maps_click_count INTEGER NOT NULL DEFAULT 0",
            "max_scroll_depth": "ALTER TABLE checkin_links ADD COLUMN max_scroll_depth INTEGER NOT NULL DEFAULT 0",
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
        else:
            if " - " in flat.name:
                building_name, room_number = flat.name.rsplit(" - ", 1)
                flat.building_name = building_name.strip()
                flat.room_number = room_number.strip()
            else:
                flat.building_name = flat.name.strip()
                flat.room_number = flat.room_number or "Sem numero"

        flat.name = f"{flat.building_name} - {flat.room_number}"

        if not flat.arrival_steps:
            flat.arrival_steps = DEFAULT_ARRIVAL_STEPS

        if not flat.building_access:
            flat.building_access = DEFAULT_BUILDING_ACCESS

        if not flat.door_code:
            flat.door_code = DEFAULT_DOOR_CODE

        if not flat.fallback_contact or flat.fallback_contact == "WhatsApp da operação: +55 (83) 99999-9999":
            flat.fallback_contact = DEFAULT_FALLBACK_CONTACT


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
            if not flat.arrival_steps:
                flat.arrival_steps = DEFAULT_ARRIVAL_STEPS
            if not flat.building_access:
                flat.building_access = DEFAULT_BUILDING_ACCESS
            if not flat.door_code:
                flat.door_code = DEFAULT_DOOR_CODE
            if not flat.fallback_contact or flat.fallback_contact == "WhatsApp da operação: +55 (83) 99999-9999":
                flat.fallback_contact = DEFAULT_FALLBACK_CONTACT
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
                arrival_steps=DEFAULT_ARRIVAL_STEPS,
                building_access=DEFAULT_BUILDING_ACCESS,
                door_code=DEFAULT_DOOR_CODE,
                fallback_contact=DEFAULT_FALLBACK_CONTACT,
            )
        )
