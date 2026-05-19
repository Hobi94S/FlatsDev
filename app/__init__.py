import os
import tempfile
from datetime import date, datetime
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .db import initialize_database
from .extensions import db
from .routes import register_routes


def create_app() -> Flask:
    runtime_is_vercel = is_vercel_runtime()
    app = Flask(__name__, instance_relative_config=True, static_folder=None)
    database_path = resolve_sqlite_database_path(app)
    database_uri = resolve_database_uri(database_path)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "campina-flats-dev-key"),
        ADMIN_USERNAME=os.environ.get("ADMIN_USERNAME", "admin"),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", "admin"),
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS=resolve_engine_options(database_uri),
        PREFERRED_URL_SCHEME="https" if runtime_is_vercel else "http",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=runtime_is_vercel,
    )

    if not runtime_is_vercel and database_uri.startswith("sqlite:///"):
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    db.init_app(app)
    register_template_filters(app)
    initialize_database(app)
    register_routes(app)

    return app


def resolve_database_uri(database_path: Path) -> str:
    configured_uri = (
        os.environ.get("SQLALCHEMY_DATABASE_URI")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
    )
    if configured_uri:
        return normalize_database_uri(configured_uri)

    return f"sqlite:///{database_path.as_posix()}"


def resolve_sqlite_database_path(app: Flask) -> Path:
    if is_vercel_runtime():
        return Path(tempfile.gettempdir()) / "campina_flats.sqlite3"

    return Path(app.instance_path) / "campina_flats.sqlite3"


def is_vercel_runtime() -> bool:
    return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))


def normalize_database_uri(database_uri: str) -> str:
    if database_uri.startswith("postgresql+psycopg://"):
        return database_uri

    if database_uri.startswith("postgres://"):
        return database_uri.replace("postgres://", "postgresql+psycopg://", 1)

    if database_uri.startswith("postgresql://"):
        return database_uri.replace("postgresql://", "postgresql+psycopg://", 1)

    return database_uri


def resolve_engine_options(database_uri: str) -> dict[str, object]:
    if database_uri.startswith("sqlite:///"):
        return {"connect_args": {"check_same_thread": False}}

    return {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }


def register_template_filters(app: Flask) -> None:
    status_labels = {
        "booked": "Reservada",
        "completed": "Concluída",
        "cancelled": "Cancelada",
        "Occupied": "Ocupado",
        "Upcoming": "Próxima",
        "Available": "Disponível",
        "active": "Ativo",
        "replaced": "Substituído",
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
