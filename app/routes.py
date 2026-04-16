from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus

from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from .extensions import db
from .models import CheckinLink, Confirmation, Flat, Reservation, ReservationStatus
from .services import (
    build_flat_availability,
    build_flat_calendar,
    build_flats_dashboard,
    create_flat,
    create_reservation,
    generate_token,
    mark_link_viewed,
    update_flat,
)

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"
ADMIN_SESSION_KEY = "operator_authenticated"
ADMIN_USER_SESSION_KEY = "operator_username"


def register_routes(app: Flask) -> None:
    @app.get("/brand/logo-light.png")
    def brand_logo():
        return send_from_directory(app.root_path, "logoLight.png")

    @app.before_request
    def require_admin_authentication():
        if not request.path.startswith("/admin"):
            return None

        if session.get(ADMIN_SESSION_KEY):
            return None

        next_url = build_safe_next_url(request.full_path if request.query_string else request.path)
        flash("Faca login para acessar o painel administrativo.", "error")
        return redirect(url_for("login", next=next_url))

    @app.get("/")
    def home():
        if not session.get(ADMIN_SESSION_KEY):
            return redirect(url_for("login"))

        return redirect(url_for("admin_dashboard"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get(ADMIN_SESSION_KEY):
            return redirect(url_for("admin_dashboard"))

        next_url = build_safe_next_url(request.values.get("next"))
        username = (request.form.get("username") or "").strip()

        if request.method == "POST":
            password = request.form.get("password") or ""

            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                session[ADMIN_SESSION_KEY] = True
                session[ADMIN_USER_SESSION_KEY] = ADMIN_USERNAME
                flash("Login realizado com sucesso.", "success")
                return redirect(next_url or url_for("admin_dashboard"))

            flash("Usuario ou senha invalidos.", "error")

        return render_template("login.html", next_url=next_url, username=username)

    @app.post("/admin/logout")
    def logout():
        session.pop(ADMIN_SESSION_KEY, None)
        session.pop(ADMIN_USER_SESSION_KEY, None)
        flash("Sessao encerrada com sucesso.", "success")
        return redirect(url_for("login"))

    @app.get("/admin")
    def admin_dashboard():
        generated_link_id = request.args.get("generated_link_id", type=int)
        flats = db.session.scalars(
            select(Flat)
            .options(selectinload(Flat.reservations))
            .order_by(Flat.name)
        ).all()
        availability_cards = build_flats_dashboard(flats)
        recent_links = db.session.scalars(
            select(CheckinLink)
            .options(
                joinedload(CheckinLink.flat),
                joinedload(CheckinLink.confirmation),
                joinedload(CheckinLink.reservation),
            )
            .order_by(CheckinLink.created_at.desc())
            .limit(10)
        ).all()
        linkable_reservations = db.session.scalars(
            select(Reservation)
            .options(joinedload(Reservation.flat))
            .where(Reservation.status == ReservationStatus.BOOKED)
            .where(Reservation.checkout_date >= date.today())
            .order_by(Reservation.checkin_date, Reservation.guest_name)
        ).all()
        reservations_catalog = {
            reservation.id: {
                "guest_name": reservation.guest_name,
                "checkin_date": reservation.checkin_date.isoformat(),
                "checkout_date": reservation.checkout_date.isoformat(),
                "flat_id": reservation.flat_id,
            }
            for reservation in linkable_reservations
        }
        summary = {
            "occupied": sum(card.status == "Occupied" for card in availability_cards),
            "upcoming": sum(card.status == "Upcoming" for card in availability_cards),
            "available": sum(card.status == "Available" for card in availability_cards),
            "total": len(availability_cards),
        }
        generated_link_url = None

        if generated_link_id:
            generated_link = db.session.get(CheckinLink, generated_link_id)

            if generated_link is not None:
                generated_link_url = url_for(
                    "public_checkin",
                    token=generated_link.token,
                    _external=True,
                )

        return render_template(
            "admin.html",
            flats=flats,
            recent_links=recent_links,
            availability_cards=availability_cards,
            linkable_reservations=linkable_reservations,
            reservations_catalog=reservations_catalog,
            summary=summary,
            generated_link_url=generated_link_url,
        )

    @app.post("/admin/links")
    def create_checkin_link():
        reservation_id = request.form.get("reservation_id", type=int)
        if not reservation_id:
            flash("Selecione uma reserva para gerar o link de check-in.", "error")
            return redirect(url_for("admin_dashboard"))

        reservation = db.session.scalar(
            select(Reservation)
            .options(joinedload(Reservation.flat))
            .where(Reservation.id == reservation_id)
        )
        if reservation is None:
            abort(404)

        checkin_link = CheckinLink(
            token=generate_token(),
            flat_id=reservation.flat_id,
            guest_name=reservation.guest_name,
            reservation=reservation,
        )
        db.session.add(checkin_link)
        db.session.commit()

        flash(
            f"Link de check-in criado para {reservation.flat.display_name}.",
            "success",
        )
        return redirect(url_for("admin_dashboard", generated_link_id=checkin_link.id))

    @app.get("/admin/links/<int:link_id>")
    def link_details(link_id: int):
        link = db.session.scalar(
            select(CheckinLink)
            .options(
                joinedload(CheckinLink.flat),
                joinedload(CheckinLink.confirmation),
                joinedload(CheckinLink.reservation),
            )
            .where(CheckinLink.id == link_id)
        )

        if link is None:
            abort(404)

        return render_template(
            "link_details.html",
            link=link,
            public_url=url_for("public_checkin", token=link.token, _external=True),
            maps_embed_url=build_google_maps_embed_url(link.flat.address),
            maps_directions_url=build_google_maps_directions_url(link.flat.address),
        )

    @app.get("/admin/flats")
    def flats_dashboard():
        flats = db.session.scalars(
            select(Flat)
            .options(selectinload(Flat.reservations))
            .order_by(Flat.name)
        ).all()
        availability_cards = build_flats_dashboard(flats)
        grouped_flats: list[dict[str, object]] = []
        for flat in flats:
            if grouped_flats and grouped_flats[-1]["building_name"] == flat.building_name:
                grouped_flats[-1]["rooms"].append(flat)
                continue

            grouped_flats.append(
                {
                    "building_name": flat.building_name or flat.name,
                    "address": flat.address,
                    "rooms": [flat],
                }
            )

        return render_template(
            "flats.html",
            availability_cards=availability_cards,
            grouped_flats=grouped_flats,
        )

    @app.post("/admin/flats")
    def create_flat_route():
        try:
            flat = create_flat(
                building_name=request.form.get("building_name", ""),
                room_number=request.form.get("room_number", ""),
                address=request.form.get("address", ""),
                checkin_time=request.form.get("checkin_time", ""),
                checkout_time=request.form.get("checkout_time", ""),
                house_rules=request.form.get("house_rules", ""),
                wifi_name=request.form.get("wifi_name", ""),
                wifi_password=request.form.get("wifi_password", ""),
                parking_instructions=request.form.get("parking_instructions", ""),
            )
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("flats_dashboard"))

        db.session.commit()
        flash(f"Flat {flat.display_name} cadastrado com sucesso.", "success")
        return redirect(url_for("flat_calendar", flat_id=flat.id))

    @app.get("/admin/flats/<int:flat_id>/edit")
    def edit_flat_route(flat_id: int):
        flat = db.session.get(Flat, flat_id)

        if flat is None:
            abort(404)

        return render_template("edit_flat.html", flat=flat)

    @app.post("/admin/flats/<int:flat_id>")
    def update_flat_route(flat_id: int):
        flat = db.session.get(Flat, flat_id)

        if flat is None:
            abort(404)

        try:
            update_flat(
                flat,
                building_name=request.form.get("building_name", ""),
                room_number=request.form.get("room_number", ""),
                address=request.form.get("address", ""),
                checkin_time=request.form.get("checkin_time", ""),
                checkout_time=request.form.get("checkout_time", ""),
                house_rules=request.form.get("house_rules", ""),
                wifi_name=request.form.get("wifi_name", ""),
                wifi_password=request.form.get("wifi_password", ""),
                parking_instructions=request.form.get("parking_instructions", ""),
            )
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("edit_flat_route", flat_id=flat.id))

        db.session.commit()
        flash(f"Flat {flat.display_name} atualizado com sucesso.", "success")
        return redirect(url_for("flat_calendar", flat_id=flat.id))

    @app.get("/admin/flats/<int:flat_id>/calendar")
    def flat_calendar(flat_id: int):
        flat = db.session.scalar(
            select(Flat)
            .options(selectinload(Flat.reservations))
            .where(Flat.id == flat_id)
        )

        if flat is None:
            abort(404)

        availability = build_flat_availability(flat)
        calendar_days = build_flat_calendar(flat, number_of_days=42)
        reservations = sorted(flat.reservations, key=lambda item: item.checkin_date)

        return render_template(
            "flat_calendar.html",
            flat=flat,
            availability=availability,
            calendar_days=calendar_days,
            reservations=reservations,
        )

    @app.get("/admin/reservations")
    def reservations_dashboard():
        flats = db.session.scalars(select(Flat).order_by(Flat.name)).all()
        reservations = db.session.scalars(
            select(Reservation)
            .options(joinedload(Reservation.flat))
            .order_by(Reservation.checkin_date.desc(), Reservation.created_at.desc())
        ).all()

        return render_template(
            "reservations.html",
            flats=flats,
            reservations=reservations,
            status_options=ReservationStatus.choices(),
            selected_flat_id=request.args.get("flat_id", type=int),
        )

    @app.get("/admin/reports")
    def reports_dashboard():
        total_links = db.session.scalar(select(func.count(CheckinLink.id))) or 0
        confirmed_links = db.session.scalar(select(func.count(Confirmation.id))) or 0
        total_reservations = db.session.scalar(select(func.count(Reservation.id))) or 0
        occupied_flats = sum(
            item.status == "Occupied"
            for item in build_flats_dashboard(
                db.session.scalars(
                    select(Flat)
                    .options(selectinload(Flat.reservations))
                    .order_by(Flat.name)
                ).all()
            )
        )
        latest_links = db.session.scalars(
            select(CheckinLink)
            .options(joinedload(CheckinLink.flat), joinedload(CheckinLink.confirmation))
            .order_by(CheckinLink.created_at.desc())
            .limit(8)
        ).all()

        return render_template(
            "reports.html",
            total_links=total_links,
            confirmed_links=confirmed_links,
            total_reservations=total_reservations,
            occupied_flats=occupied_flats,
            latest_links=latest_links,
        )

    @app.post("/admin/reservations")
    def create_reservation_route():
        flat_id = request.form.get("flat_id", type=int)
        guest_name = (request.form.get("guest_name") or "").strip()
        checkin_date = parse_date_field(request.form.get("checkin_date"))
        checkout_date = parse_date_field(request.form.get("checkout_date"))
        status = request.form.get("status") or ReservationStatus.BOOKED

        if not flat_id:
            flash("Selecione um flat para a reserva.", "error")
            return redirect(url_for("reservations_dashboard"))

        if checkin_date is None or checkout_date is None:
            flash("Informe datas validas de check-in e check-out.", "error")
            return redirect(url_for("reservations_dashboard", flat_id=flat_id))

        if db.session.get(Flat, flat_id) is None:
            abort(404)

        try:
            reservation = create_reservation(
                flat_id=flat_id,
                guest_name=guest_name,
                checkin_date=checkin_date,
                checkout_date=checkout_date,
                status=status,
            )
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("reservations_dashboard", flat_id=flat_id))

        db.session.commit()
        flash(f"Reserva criada para {reservation.guest_name}.", "success")
        return redirect(url_for("flat_calendar", flat_id=flat_id))

    @app.get("/checkin/<token>")
    def public_checkin(token: str):
        link = fetch_link_by_token(token)

        if link is None:
            abort(404)

        mark_link_viewed(link)
        db.session.commit()

        return render_template(
            "public_checkin.html",
            link=link,
            maps_embed_url=build_google_maps_embed_url(link.flat.address),
            maps_directions_url=build_google_maps_directions_url(link.flat.address),
        )

    @app.post("/checkin/<token>/confirm")
    def confirm_checkin(token: str):
        link = fetch_link_by_token(token)

        if link is None:
            abort(404)

        if link.confirmation is None:
            db.session.add(
                Confirmation(
                    checkin_link_id=link.id,
                    ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
                    user_agent=request.headers.get("User-Agent"),
                )
            )
            db.session.commit()
            flash("Confirmacao registrada com sucesso. Obrigado.", "success")
        else:
            flash("Esta confirmacao ja havia sido registrada.", "success")

        return redirect(url_for("public_checkin", token=token))


def fetch_link_by_token(token: str) -> CheckinLink | None:
    return db.session.scalar(
        select(CheckinLink)
        .options(
            joinedload(CheckinLink.flat),
            joinedload(CheckinLink.confirmation),
            joinedload(CheckinLink.reservation),
        )
        .where(CheckinLink.token == token)
    )


def parse_date_field(raw_value: str | None) -> date | None:
    if not raw_value:
        return None

    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        return None


def build_safe_next_url(raw_target: str | None) -> str | None:
    if not raw_target:
        return None

    cleaned_target = raw_target.strip()
    if not cleaned_target.startswith("/") or cleaned_target.startswith("//"):
        return None

    return cleaned_target


def build_google_maps_embed_url(address: str) -> str:
    encoded_address = quote_plus(address)
    return f"https://maps.google.com/maps?q={encoded_address}&t=&z=15&ie=UTF8&iwloc=&output=embed"


def build_google_maps_directions_url(address: str) -> str:
    encoded_address = quote_plus(address)
    return f"https://www.google.com/maps/search/?api=1&query={encoded_address}"
