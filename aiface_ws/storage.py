"""
Saving punches without losing any.

The device deletes a punch from its "new" queue as soon as it gets our ACK.
Anything that makes us ACK a punch we did not store is silent, permanent
data loss. The rules below each come from a real loss:

1. **Only the punch unique constraint means "duplicate".** An
   IntegrityError from anything else (the employees table, a NOT NULL) is
   re-raised, so the batch is not ACKed and the device resends it.
   Treating every IntegrityError as a duplicate once lost 198 of 379
   punches in a day, all logged as "duplicate ignored".

2. **One savepoint per punch, one commit per batch.** A commit is an
   fsync (55-71 ms measured). Twenty of them per batch pushed the ACK past
   one second. A savepoint per punch keeps a real duplicate from rolling
   back the rest of the batch.

3. **Look at `session.new` before creating an employee.** With
   autoflush=False a SELECT does not see an Employee added a moment ago in
   the same session. Callers that upsert the name and then save the punch
   (which upserts again) would insert the same employee twice. That is the
   IntegrityError of rule 1.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from aiface_ws.models import Device, Employee, Punch

logger = logging.getLogger(__name__)


class PunchNotSaved(Exception):
    """The punch could not be stored and must NOT be acknowledged."""


def is_duplicate_punch(error: IntegrityError) -> bool:
    text = str(error.orig or error)
    return (
        "uq_punch" in text                                         # PostgreSQL
        or "UNIQUE constraint failed: aiface_punches." in text     # SQLite
    )


async def upsert_employee(session: AsyncSession, sn: str, enrollid: str, name: str) -> Employee:
    """Create or update (device, enrollid). An empty name never erases a known one."""
    employee = next(
        (o for o in session.new
         if isinstance(o, Employee) and o.device_sn == sn and o.enrollid == enrollid),
        None,
    )
    if employee is None:
        employee = (await session.execute(
            select(Employee).where(Employee.device_sn == sn, Employee.enrollid == enrollid)
        )).scalars().first()

    if employee is None:
        employee = Employee(device_sn=sn, enrollid=enrollid, name=name or "")
    else:
        if name and employee.name and employee.name != name:
            # The device reassigned this enrollid to someone else. From now
            # on old punches of this slot look like the new person's.
            logger.warning("enrollid %s on %s renamed: %r -> %r", enrollid, sn, employee.name, name)
        if name:
            employee.name = name
        employee.last_seen = datetime.now()
    session.add(employee)
    return employee


async def save_punch(
    session: AsyncSession,
    sn: str,
    enrollid: str,
    time: datetime,
    *,
    mode: Optional[int] = None,
    inout: int = 0,
    name: str = "",
    temperature: Optional[float] = None,
    duplicate_window: int = 0,
) -> Optional[Punch]:
    """
    Save one punch inside its own savepoint. The caller commits the batch.

    Returns the new Punch, or None when it was a duplicate (safe to ACK).
    Raises PunchNotSaved for anything else (do NOT ACK).
    """
    try:
        async with session.begin_nested():
            if duplicate_window > 0:
                near = (await session.execute(
                    select(Punch.id).where(
                        Punch.device_sn == sn,
                        Punch.enrollid == enrollid,
                        Punch.time >= time - timedelta(seconds=duplicate_window),
                        Punch.time <= time + timedelta(seconds=duplicate_window),
                    ).limit(1)
                )).scalar_one_or_none()
                if near is not None:
                    return None

            punch = Punch(device_sn=sn, enrollid=enrollid, time=time, mode=mode,
                          inout=inout, temperature=temperature)
            session.add(punch)
            if name:
                await upsert_employee(session, sn, enrollid, name)
            # Inside the savepoint: a real duplicate fails here and only
            # this punch is rolled back.
            await session.flush()
            return punch
    except IntegrityError as e:
        if is_duplicate_punch(e):
            return None
        logger.error("Punch NOT saved (%s enrollid %s @ %s): %s",
                     sn, enrollid, time, str(e.orig).splitlines()[0] if e.orig else e)
        raise PunchNotSaved(str(e)) from e


async def touch_device(session: AsyncSession, sn: str, ip: Optional[str],
                       devinfo: Optional[dict] = None) -> Device:
    """Create the device on first contact; refresh ip/last_seen after that."""
    device = await session.get(Device, sn)
    if device is None:
        device = Device(sn=sn, name=f"AiFace {sn[-4:]}")
    device.ip_address = ip or device.ip_address
    device.last_seen = datetime.now()
    if devinfo:
        device.model = devinfo.get("modelname") or device.model
        device.firmware = devinfo.get("firmware") or device.firmware
    session.add(device)
    return device


async def count_punches(session: AsyncSession, sn: str) -> int:
    return int((await session.execute(
        select(func.count(Punch.id)).where(Punch.device_sn == sn)
    )).scalar() or 0)
