from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db


class ReservationStatus:
    BOOKED = "booked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @classmethod
    def choices(cls) -> tuple[str, str, str]:
        return (cls.BOOKED, cls.COMPLETED, cls.CANCELLED)

    @classmethod
    def blocking(cls) -> tuple[str, str]:
        return (cls.BOOKED, cls.COMPLETED)


class Flat(db.Model):
    __tablename__ = "flats"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    checkin_time: Mapped[str] = mapped_column(String(20), nullable=False)
    checkout_time: Mapped[str] = mapped_column(String(20), nullable=False)
    house_rules: Mapped[str] = mapped_column(Text, nullable=False)
    wifi_name: Mapped[str] = mapped_column(String(120), nullable=False)
    wifi_password: Mapped[str] = mapped_column(String(120), nullable=False)
    parking_instructions: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    reservations: Mapped[list["Reservation"]] = relationship(
        back_populates="flat",
        cascade="all, delete-orphan",
        order_by="Reservation.checkin_date",
    )
    checkin_links: Mapped[list["CheckinLink"]] = relationship(back_populates="flat")


class Reservation(db.Model):
    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(primary_key=True)
    flat_id: Mapped[int] = mapped_column(
        ForeignKey("flats.id"),
        nullable=False,
        index=True,
    )
    guest_name: Mapped[str] = mapped_column(String(120), nullable=False)
    checkin_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    checkout_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ReservationStatus.BOOKED,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    flat: Mapped["Flat"] = relationship(back_populates="reservations")
    checkin_links: Mapped[list["CheckinLink"]] = relationship(back_populates="reservation")


class CheckinLink(db.Model):
    __tablename__ = "checkin_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    flat_id: Mapped[int] = mapped_column(
        ForeignKey("flats.id"),
        nullable=False,
        index=True,
    )
    reservation_id: Mapped[int | None] = mapped_column(
        ForeignKey("reservations.id"),
        nullable=True,
        index=True,
    )
    guest_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_viewed: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    flat: Mapped["Flat"] = relationship(back_populates="checkin_links")
    reservation: Mapped["Reservation | None"] = relationship(back_populates="checkin_links")
    confirmation: Mapped["Confirmation | None"] = relationship(
        back_populates="checkin_link",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Confirmation(db.Model):
    __tablename__ = "confirmations"

    id: Mapped[int] = mapped_column(primary_key=True)
    checkin_link_id: Mapped[int] = mapped_column(
        ForeignKey("checkin_links.id"),
        nullable=False,
        unique=True,
    )
    agreed_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    ip_address: Mapped[str | None] = mapped_column(String(120), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    checkin_link: Mapped["CheckinLink"] = relationship(back_populates="confirmation")
