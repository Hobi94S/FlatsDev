from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from urllib.parse import quote_plus

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from .extensions import db
from .models import CheckinLink, CheckinLinkStatus, Confirmation, Flat, Reservation, ReservationStatus
from .services import (
    build_flat_availability,
    build_flat_calendar,
    build_flats_dashboard,
    create_flat,
    create_reservation,
    generate_token,
    is_link_expired,
    mark_link_expired_if_needed,
    mark_link_viewed,
    record_link_event,
    update_flat,
)

ADMIN_SESSION_KEY = "operator_authenticated"
ADMIN_USER_SESSION_KEY = "operator_username"
CHANNEL_OPTIONS = ("WhatsApp", "Booking", "Airbnb", "Instagram")


def register_routes(app: Flask) -> None:
    public_root = Path(app.root_path).parent / "public"

    @app.get("/app.css")
    def stylesheet():
        return send_from_directory(public_root, "app.css", mimetype="text/css")

    @app.get("/brand/logo-light.png")
    def brand_logo():
        return send_from_directory(public_root / "brand", "logo-light.png")

    @app.get("/favicon.svg")
    def favicon_svg():
        return send_from_directory(public_root, "favicon.svg", mimetype="image/svg+xml")

    @app.get("/favicon.ico")
    def favicon():
        return redirect("/favicon.svg", code=307)

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

            if (
                username == app.config["ADMIN_USERNAME"]
                and password == app.config["ADMIN_PASSWORD"]
            ):
                session[ADMIN_SESSION_KEY] = True
                session[ADMIN_USER_SESSION_KEY] = username
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
        linkable_reservations = db.session.scalars(
            select(Reservation)
            .options(joinedload(Reservation.flat), selectinload(Reservation.checkin_links))
            .where(Reservation.status == ReservationStatus.BOOKED)
            .where(Reservation.checkout_date >= date.today())
            .order_by(Reservation.checkin_date, Reservation.guest_name)
        ).all()
        recent_links = db.session.scalars(
            select(CheckinLink)
            .options(
                joinedload(CheckinLink.flat),
                joinedload(CheckinLink.confirmation),
                joinedload(CheckinLink.reservation),
            )
            .order_by(CheckinLink.created_at.desc())
            .limit(12)
        ).all()

        for link in recent_links:
            mark_link_expired_if_needed(link)

        reservations_catalog = build_reservations_catalog(linkable_reservations)
        reservation_link_map = {
            reservation.id: latest_active_link(reservation.checkin_links)
            for reservation in linkable_reservations
        }
        operational_queue = build_operational_queue(
            reservations=linkable_reservations,
            reservation_link_map=reservation_link_map,
        )
        follow_up_links = [
            link
            for link in recent_links
            if link.first_guest_opened_at and link.confirmation is None and link.status == CheckinLinkStatus.ACTIVE
        ][:5]

        summary = {
            "entries_today": operational_queue["entries_today"],
            "without_link": operational_queue["without_link"],
            "opened_unconfirmed": operational_queue["opened_unconfirmed"],
            "never_opened": operational_queue["never_opened"],
        }

        generated_link = None
        generated_link_url = None
        generated_link_preview_url = None
        generated_link_whatsapp_url = None
        if generated_link_id:
            generated_link = db.session.get(CheckinLink, generated_link_id)
            if generated_link is not None:
                mark_link_expired_if_needed(generated_link)
                generated_link_url = url_for(
                    "public_checkin",
                    token=generated_link.token,
                    _external=True,
                )
                generated_link_preview_url = url_for(
                    "public_checkin",
                    token=generated_link.token,
                    preview="admin",
                )
                generated_link_whatsapp_url = build_whatsapp_share_url(generated_link, generated_link_url)

        db.session.commit()

        return render_template(
            "admin.html",
            flats=flats,
            recent_links=recent_links,
            availability_cards=availability_cards,
            linkable_reservations=linkable_reservations,
            reservations_catalog=reservations_catalog,
            summary=summary,
            operational_queue=operational_queue,
            follow_up_links=follow_up_links,
            generated_link=generated_link,
            generated_link_url=generated_link_url,
            generated_link_preview_url=generated_link_preview_url,
            generated_link_whatsapp_url=generated_link_whatsapp_url,
            channel_options=CHANNEL_OPTIONS,
        )

    @app.post("/admin/links")
    def create_checkin_link():
        reservation_id = request.form.get("reservation_id", type=int)
        sent_channel = normalize_sent_channel(request.form.get("platform"))
        if not reservation_id:
            flash("Selecione uma reserva para gerar o link de check-in.", "error")
            return redirect(url_for("admin_dashboard"))

        reservation = fetch_reservation_for_linking(reservation_id)
        if reservation is None:
            abort(404)

        existing_link = latest_active_link(reservation.checkin_links)
        if existing_link is not None and not is_link_expired(existing_link):
            existing_link.sent_channel = sent_channel
            if not existing_link.generated_by:
                existing_link.generated_by = resolve_generated_by()
            db.session.commit()
            flash("Ja existia um link ativo para esta reserva. O link foi reutilizado.", "success")
            return redirect(url_for("admin_dashboard", generated_link_id=existing_link.id))

        if existing_link is not None:
            mark_link_expired_if_needed(existing_link)

        checkin_link = build_checkin_link(
            reservation=reservation,
            sent_channel=sent_channel,
            generated_by=resolve_generated_by(),
        )
        db.session.add(checkin_link)
        db.session.commit()

        flash(
            f"Link de check-in criado para {reservation.flat.display_name}.",
            "success",
        )
        return redirect(url_for("admin_dashboard", generated_link_id=checkin_link.id))

    @app.post("/admin/links/<int:link_id>/regenerate")
    def regenerate_checkin_link(link_id: int):
        link = db.session.scalar(
            select(CheckinLink)
            .options(joinedload(CheckinLink.reservation), joinedload(CheckinLink.flat))
            .where(CheckinLink.id == link_id)
        )
        if link is None or link.reservation is None:
            abort(404)

        link.status = CheckinLinkStatus.REPLACED
        replacement_link = build_checkin_link(
            reservation=link.reservation,
            sent_channel=link.sent_channel,
            generated_by=resolve_generated_by(),
        )
        db.session.add(replacement_link)
        db.session.commit()
        flash("Novo link gerado e link anterior substituido.", "success")
        return redirect(url_for("admin_dashboard", generated_link_id=replacement_link.id))

    @app.post("/admin/links/<int:link_id>/mark-sent")
    def mark_link_sent(link_id: int):
        link = db.session.scalar(
            select(CheckinLink)
            .options(joinedload(CheckinLink.flat), joinedload(CheckinLink.reservation))
            .where(CheckinLink.id == link_id)
        )
        if link is None:
            abort(404)

        link.sent_at = datetime.utcnow()
        db.session.commit()
        public_url = url_for("public_checkin", token=link.token, _external=True)
        return jsonify(
            {
                "ok": True,
                "whatsapp_url": build_whatsapp_share_url(link, public_url),
            }
        )

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

        mark_link_expired_if_needed(link)
        db.session.commit()

        public_url = url_for("public_checkin", token=link.token, _external=True)
        return render_template(
            "link_details.html",
            link=link,
            public_url=public_url,
            preview_url=url_for("public_checkin", token=link.token, preview="admin"),
            maps_directions_url=build_google_maps_directions_url(link.flat.address),
            whatsapp_url=build_whatsapp_share_url(link, public_url),
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
                arrival_steps=request.form.get("arrival_steps", ""),
                building_access=request.form.get("building_access", ""),
                door_code=request.form.get("door_code", ""),
                fallback_contact=request.form.get("fallback_contact", ""),
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
                arrival_steps=request.form.get("arrival_steps", ""),
                building_access=request.form.get("building_access", ""),
                door_code=request.form.get("door_code", ""),
                fallback_contact=request.form.get("fallback_contact", ""),
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
        reservations = db.session.scalars(
            select(Reservation)
            .options(selectinload(Reservation.checkin_links), joinedload(Reservation.flat))
            .where(Reservation.status == ReservationStatus.BOOKED)
            .where(Reservation.checkout_date >= date.today())
            .order_by(Reservation.checkin_date)
        ).all()
        latest_links = db.session.scalars(
            select(CheckinLink)
            .options(joinedload(CheckinLink.flat), joinedload(CheckinLink.confirmation), joinedload(CheckinLink.reservation))
            .order_by(CheckinLink.created_at.desc())
            .limit(10)
        ).all()

        for link in latest_links:
            mark_link_expired_if_needed(link)

        links_without_open = []
        opened_without_confirmation = []
        reservations_without_link = []
        for reservation in reservations:
            active_link = latest_active_link(reservation.checkin_links)
            if active_link is None:
                reservations_without_link.append(reservation)
                continue

            if active_link.first_guest_opened_at is None:
                links_without_open.append(active_link)
            elif active_link.confirmation is None:
                opened_without_confirmation.append(active_link)

        confirmed_before_checkin = sum(
            1
            for link in latest_links
            if link.confirmation and link.reservation and link.confirmation.agreed_at.date() <= link.reservation.checkin_date
        )
        opened_before_checkin = sum(
            1
            for link in latest_links
            if link.first_guest_opened_at and link.reservation and link.first_guest_opened_at.date() <= link.reservation.checkin_date
        )
        db.session.commit()

        return render_template(
            "reports.html",
            total_active_links=sum(link.status == CheckinLinkStatus.ACTIVE for link in latest_links),
            links_without_open=links_without_open,
            opened_without_confirmation=opened_without_confirmation,
            reservations_without_link=reservations_without_link,
            confirmed_before_checkin=confirmed_before_checkin,
            opened_before_checkin=opened_before_checkin,
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

        admin_preview = request.args.get("preview") == "admin" and bool(session.get(ADMIN_SESSION_KEY))
        mark_link_expired_if_needed(link)
        link_unavailable = link.status != CheckinLinkStatus.ACTIVE and not admin_preview

        if not link_unavailable:
            session_key = f"guest_link_seen_{link.id}"
            is_unique_guest = not session.get(session_key)
            if is_unique_guest and not admin_preview:
                session[session_key] = True

            mark_link_viewed(
                link,
                is_admin_preview=admin_preview,
                is_unique_guest=is_unique_guest and not admin_preview,
            )
        db.session.commit()

        return render_template(
            "public_checkin.html",
            link=link,
            maps_directions_url=build_google_maps_directions_url(link.flat.address),
            preview_mode=admin_preview,
            link_unavailable=link_unavailable,
        )

    @app.post("/checkin/<token>/confirm")
    def confirm_checkin(token: str):
        link = fetch_link_by_token(token)

        if link is None:
            abort(404)

        mark_link_expired_if_needed(link)
        if link.status != CheckinLinkStatus.ACTIVE:
            db.session.commit()
            flash("Este link nao esta mais disponivel para confirmacao.", "error")
            return redirect(url_for("public_checkin", token=token))

        if request.form.get("acknowledged") != "yes":
            flash("Marque a confirmacao antes de continuar.", "error")
            return redirect(url_for("public_checkin", token=token))

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

    @app.post("/checkin/<token>/events")
    def record_checkin_event(token: str):
        link = fetch_link_by_token(token)
        if link is None:
            abort(404)

        mark_link_expired_if_needed(link)
        if link.status != CheckinLinkStatus.ACTIVE:
            db.session.commit()
            return ("", 204)

        payload = request.get_json(silent=True) or {}
        event_name = (payload.get("event") or "").strip()
        scroll_depth = payload.get("scroll_depth")
        if event_name:
            try:
                numeric_scroll_depth = int(scroll_depth) if scroll_depth is not None else None
            except (TypeError, ValueError):
                numeric_scroll_depth = None
            record_link_event(link, event_name, scroll_depth=numeric_scroll_depth)
            db.session.commit()

        return ("", 204)


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


def fetch_reservation_for_linking(reservation_id: int) -> Reservation | None:
    return db.session.scalar(
        select(Reservation)
        .options(joinedload(Reservation.flat), selectinload(Reservation.checkin_links))
        .where(Reservation.id == reservation_id)
    )


def latest_active_link(links: list[CheckinLink]) -> CheckinLink | None:
    valid_links = []
    for link in links:
        mark_link_expired_if_needed(link)
        if link.status == CheckinLinkStatus.ACTIVE:
            valid_links.append(link)

    if not valid_links:
        return None

    return sorted(valid_links, key=lambda item: item.created_at, reverse=True)[0]


def build_reservations_catalog(reservations: list[Reservation]) -> dict[int, dict[str, object]]:
    catalog: dict[int, dict[str, object]] = {}
    for reservation in reservations:
        flat = reservation.flat
        active_link = latest_active_link(reservation.checkin_links)
        catalog[reservation.id] = {
            "guest_name": reservation.guest_name,
            "checkin_date": reservation.checkin_date.isoformat(),
            "checkout_date": reservation.checkout_date.isoformat(),
            "flat_id": reservation.flat_id,
            "flat_name": flat.display_name,
            "address": flat.address,
            "checkin_time": flat.checkin_time,
            "checkout_time": flat.checkout_time,
            "wifi_name": flat.wifi_name,
            "wifi_password": flat.wifi_password,
            "parking_instructions": flat.parking_instructions,
            "arrival_steps": flat.arrival_steps,
            "building_access": flat.building_access,
            "door_code": flat.door_code,
            "fallback_contact": flat.fallback_contact,
            "house_rules": [line for line in flat.house_rules.splitlines() if line.strip()],
            "active_link": {
                "id": active_link.id,
                "token": active_link.token,
                "status": active_link.status,
            } if active_link else None,
        }

    return catalog


def build_operational_queue(
    *,
    reservations: list[Reservation],
    reservation_link_map: dict[int, CheckinLink | None],
) -> dict[str, object]:
    today = date.today()
    tomorrow = today.fromordinal(today.toordinal() + 1)

    entries_today = [reservation for reservation in reservations if reservation.checkin_date == today]
    entries_tomorrow = [reservation for reservation in reservations if reservation.checkin_date == tomorrow]
    without_link = [
        reservation
        for reservation in reservations
        if reservation_link_map.get(reservation.id) is None
    ]
    opened_unconfirmed = [
        link
        for link in reservation_link_map.values()
        if link and link.first_guest_opened_at and link.confirmation is None
    ]
    never_opened = [
        link
        for link in reservation_link_map.values()
        if link and link.first_guest_opened_at is None
    ]
    return {
        "entries_today": len(entries_today),
        "entries_tomorrow": len(entries_tomorrow),
        "without_link": len(without_link),
        "opened_unconfirmed": len(opened_unconfirmed),
        "never_opened": len(never_opened),
        "today_reservations": entries_today[:4],
        "tomorrow_reservations": entries_tomorrow[:4],
        "without_link_reservations": without_link[:4],
        "opened_unconfirmed_links": opened_unconfirmed[:4],
    }


def build_checkin_link(
    *,
    reservation: Reservation,
    sent_channel: str,
    generated_by: str,
) -> CheckinLink:
    return CheckinLink(
        token=generate_token(),
        flat_id=reservation.flat_id,
        guest_name=reservation.guest_name,
        reservation=reservation,
        sent_channel=sent_channel,
        status=CheckinLinkStatus.ACTIVE,
        expires_at=build_link_expiration(reservation),
        generated_by=generated_by,
    )


def build_link_expiration(reservation: Reservation) -> datetime:
    return datetime.combine(reservation.checkout_date, time(hour=23, minute=59))


def normalize_sent_channel(raw_value: str | None) -> str:
    if raw_value in CHANNEL_OPTIONS:
        return raw_value

    return "WhatsApp"


def resolve_generated_by() -> str:
    return session.get(
        ADMIN_USER_SESSION_KEY,
        current_app.config.get("ADMIN_USERNAME", "admin"),
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


def build_google_maps_directions_url(address: str) -> str:
    encoded_address = quote_plus(address)
    return f"https://www.google.com/maps/search/?api=1&query={encoded_address}"


def build_whatsapp_share_url(link: CheckinLink, public_url: str) -> str:
    message = (
        f"Ola{f' {link.guest_name}' if link.guest_name else ''}! "
        f"Aqui esta o link do seu check-in em {link.flat.display_name}.\n\n"
        f"3 coisas importantes antes da chegada:\n"
        f"1. Check-in: {link.flat.checkin_time}\n"
        f"2. Wi-Fi: {link.flat.wifi_name}\n"
        f"3. Estacionamento e acesso estao no link abaixo.\n\n"
        f"Confirme no final com 'Li e estou ciente'.\n{public_url}"
    )
    return f"https://wa.me/?text={quote_plus(message)}"
