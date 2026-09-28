"""
Database tables.

Identity is always (device serial, enrollid): the enrollid is local to each
terminal, so the same number on two devices is two different people.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now()


def naive(**kw):
    """
    A timezone-naive datetime column. Device times are local without zone
    (see Punch.time), and recent SQLModel versions reject naive values
    unless the column says so explicitly.
    """
    return Field(sa_type=DateTime(timezone=False), **kw)


class Device(SQLModel, table=True):
    __tablename__ = "aiface_devices"

    sn: str = Field(primary_key=True)
    name: str = ""
    ip_address: Optional[str] = None
    model: Optional[str] = None
    firmware: Optional[str] = None
    # Administrative switch. A disabled device is disconnected WITHOUT an
    # ACK, so it keeps its punches in memory and delivers them when enabled.
    is_active: bool = True
    last_seen: Optional[datetime] = naive(default=None)
    created_at: datetime = naive(default_factory=_now)


class Employee(SQLModel, table=True):
    __tablename__ = "aiface_employees"
    __table_args__ = (UniqueConstraint("device_sn", "enrollid", name="uq_employee"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    device_sn: str = Field(index=True)
    enrollid: str = Field(index=True)
    name: str = ""
    last_seen: datetime = naive(default_factory=_now)


class Punch(SQLModel, table=True):
    __tablename__ = "aiface_punches"
    # This exact name is how storage.py tells a real duplicate apart from
    # any other integrity error. Do not rename it without updating it there.
    __table_args__ = (
        UniqueConstraint("device_sn", "enrollid", "time", name="uq_punch"),
    )

    # Insertion order. The relay cursor walks this, NOT `time`: a punch
    # recovered from last month gets a new id and is still relayed.
    id: Optional[int] = Field(default=None, primary_key=True)
    device_sn: str = Field(index=True)
    enrollid: str = Field(index=True)
    # Device local time, naive. The device has no timezone; the server
    # sets its clock with `settime` on every registration.
    time: datetime = naive(index=True)
    # Verification mode reported by the device (1 fingerprint, 2 password,
    # 3 card, 8 face, 15 palm on the firmwares we have seen).
    mode: Optional[int] = None
    # AiFace terminals always report 0 here: they do not know whether a
    # punch is an entry or an exit.
    inout: int = 0
    temperature: Optional[float] = None
    # Relative path under settings.photos_dir, when use_logphoto=1.
    photo: Optional[str] = None
    received_at: datetime = naive(default_factory=_now)


class RelayCursor(SQLModel, table=True):
    """Last punch id the webhook confirmed, per device and destination."""

    __tablename__ = "aiface_relay_cursors"

    device_sn: str = Field(primary_key=True)
    # Host of the webhook URL: pointing the relay somewhere else starts a
    # new cursor instead of inheriting (and skipping) another server's.
    destination: str = Field(primary_key=True)
    last_id: int = 0
    updated_at: datetime = naive(default_factory=_now)
