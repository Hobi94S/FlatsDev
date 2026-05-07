import os
from datetime import date, datetime
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .db import initialize_database
from .extensions import db
from .routes import register_routes


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    database_path = Path(app.instance_path) / "campina_flats.sqlite3"
    database_uri = resolve_database_uri(database_path)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "campina-flats-dev-key"),
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        PREFERRED_URL_SCHEME="https" if os.environ.get("VERCEL") else "http",
    )

    if database_uri.startswith("sqlite:///"):
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    db.init_app(app)
    register_template_filters(app)
    initialize_database(app)
    register_routes(app)

    return app


def resolve_database_uri(database_path: Path) -> str:
    database_uri = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if database_uri:
        return normalize_database_uri(database_uri)

    return f"sqlite:///{database_path.as_posix()}"


def normalize_database_uri(database_uri: str) -> str:
    if database_uri.startswith("postgres://"):
        return database_uri.replace("postgres://", "postgresql://", 1)

    return database_uri


def register_template_filters(app: Flask) -> None:
    status_labels = {
        "booked": "Reservada",
        "completed": "Concluida",
        "cancelled": "Cancelada",
        "Occupied": "Ocupado",
        "Upcoming": "Proxima",
        "Available": "Disponivel",
        "active": "Ativo",
        "replaced": "Substituido",
        "revoked": "Revogado",
        "expired": "Expirado",
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

    @app.template_filter("nonempty_lines")
    def nonempty_lines(value: str | None) -> list[str]:
        if not value:
            return []

        return [line.strip() for line in value.splitlines() if line.strip()]


app = create_app()
