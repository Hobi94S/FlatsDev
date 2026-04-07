# Smart Check-in Standardization System for Campina Flats

Flask MVP for short-term rental operations with check-in standardization, public guest acknowledgment, and reservation/availability management.

## 1. Architecture

### Stack

- `Flask` for fast iteration and simple deployment
- `Flask-SQLAlchemy` for ORM-based persistence
- `SQLite` for the MVP database
- `Jinja2` + Tailwind CDN for the UI

### Why this structure

- SQLAlchemy keeps the domain model ready for a future move from SQLite to MySQL or PostgreSQL.
- Models, services, and routes are separated enough to stay readable without overengineering.
- Business rules such as occupancy and overlap validation live in service functions instead of templates or routes.

## 2. Folder Structure

```text
.
|-- app/
|   |-- __init__.py
|   |-- db.py
|   |-- extensions.py
|   |-- models.py
|   |-- routes.py
|   |-- services.py
|   |-- static/
|   |   `-- app.css
|   `-- templates/
|       |-- admin.html
|       |-- base.html
|       |-- flat_calendar.html
|       |-- flats.html
|       |-- link_details.html
|       |-- public_checkin.html
|       `-- reservations.html
|-- instance/
|   `-- campina_flats.sqlite3
|-- requirements.txt
|-- run.py
`-- README.md
```

## 3. Data Model

### `Flat`

Base property/unit information.

- `id`
- `name`
- `slug`
- `address`
- `checkin_time`
- `checkout_time`
- `house_rules`
- `wifi_name`
- `wifi_password`
- `parking_instructions`
- `created_at`
- `updated_at`

Relationships:

- `Flat.reservations`
- `Flat.checkin_links`

### `Reservation`

Reservation management entity.

- `id`
- `flat_id`
- `guest_name`
- `checkin_date`
- `checkout_date`
- `status`
- `created_at`

Statuses:

- `booked`
- `completed`
- `cancelled`

### `CheckinLink`

Public guest communication link.

- `id`
- `token`
- `flat_id`
- `reservation_id` nullable
- `guest_name`
- `view_count`
- `last_viewed`
- `created_at`

### `Confirmation`

Guest acknowledgment record.

- `id`
- `checkin_link_id`
- `agreed_at`
- `ip_address`
- `user_agent`

## 4. Business Logic

### Availability rules

A flat is:

- `Occupied` when `checkin_date <= today < checkout_date`
- `Upcoming` when the next valid reservation is in the future
- `Available` when no blocking reservation exists

Only reservations with status `booked` or `completed` block availability. `cancelled` reservations are ignored.

### Overlap validation

When creating a reservation, the system blocks inserts where:

```text
existing.checkin_date < new.checkout_date
and existing.checkout_date > new.checkin_date
```

This avoids overlapping stays for the same flat.

### Check-in link tracking

- Every public visit increments `view_count`
- `last_viewed` is updated on each page access
- Confirmation is stored once per link

## 5. Routes

### Existing / updated routes

- `GET /`
  Redirects to `/admin`

- `GET /admin`
  Overview page with quick link generation, occupancy summary, and recent check-in links

- `POST /admin/links`
  Creates a unique public check-in link

- `GET /admin/links/<id>`
  Shows link details, views, reservation association, and acknowledgment status

- `GET /checkin/<token>`
  Public guest page

- `POST /checkin/<token>/confirm`
  Stores guest acknowledgment

### New admin routes

- `GET /admin/flats`
  Flats dashboard with status, next check-in, and next check-out

- `GET /admin/flats/<id>/calendar`
  Calendar/date-list view for one flat

- `GET /admin/reservations`
  Reservation list and manual reservation form

- `POST /admin/reservations`
  Creates a reservation with overlap validation

## 6. UI Screens

- `admin.html`
  Main overview with quick operational summary and check-in link generation

- `flats.html`
  Flat dashboard table for status monitoring

- `flat_calendar.html`
  Simple 42-day availability calendar per flat

- `reservations.html`
  Manual reservation form and full reservation list

- `public_checkin.html`
  Guest-facing instructions page with acknowledgment button

## 7. Local Run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Open:

- Admin overview: `http://127.0.0.1:5000/admin`
- Flats dashboard: `http://127.0.0.1:5000/admin/flats`
- Reservations: `http://127.0.0.1:5000/admin/reservations`

## 8. Migration Notes

- The app now uses SQLAlchemy ORM while still storing data in SQLite.
- A lightweight compatibility step adds new `checkin_links` columns (`reservation_id`, `view_count`, `last_viewed`) if the old database already exists.
- For production evolution, the next natural step is adding Alembic migrations and switching the database URL to MySQL or PostgreSQL.
