import os
from datetime import date, datetime
from pathlib import Path

from flask import Flask

from .db import initialize_database
from .extensions import db
from .routes import register_routes


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    database_path = Path(app.instance_path) / "campina_flats.sqlite3"

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "campina-flats-dev-key"),
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path.as_posix()}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    register_template_filters(app)
    initialize_database(app)
    register_routes(app)

    return app


def register_template_filters(app: Flask) -> None:
    status_labels = {
        "booked": "Reservada",
        "completed": "Concluida",
        "cancelled": "Cancelada",
        "Occupied": "Ocupado",
        "Upcoming": "Proxima",
        "Available": "Disponivel",
    }

    @app.template_filter("date_label")
    def date_label(value: date | None) -> str:
        if value is None:
            return "-"

        return value.strftime("%d/%m/%Y")

    @app.template_filter("datetime_label")
    def datetime_label(value: datetime | None) -> str:
        if value is None:
            return "-"

        return value.strftime("%d/%m/%Y %H:%M")

    @app.template_filter("status_label")
    def status_label(value: str | None) -> str:
        if value is None:
            return "-"

        return status_labels.get(value, value)
