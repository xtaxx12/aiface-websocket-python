"""The rules in storage.py, each tied to a real way punches were lost."""
from datetime import datetime

import pytest
from sqlmodel import func, select

from aiface_ws.models import Employee, Punch
from aiface_ws.storage import PunchNotSaved, save_punch, upsert_employee

T = datetime(2026, 9, 21, 7, 33, 35)


async def _count(session, model=Punch):
    return (await session.execute(select(func.count()).select_from(model))).scalar()


@pytest.mark.asyncio
async def test_first_punch_of_an_unknown_person_is_saved(session):
    """The handler upserts the name, then save_punch upserts again: one employee, one punch."""
    await upsert_employee(session, "SN1", "299", "New Person")
    punch = await save_punch(session, "SN1", "299", T, name="New Person")
    await session.commit()
    assert punch is not None
    assert await _count(session) == 1
    assert await _count(session, Employee) == 1


@pytest.mark.asyncio
async def test_a_real_duplicate_returns_none_and_keeps_the_rest_of_the_batch(session):
    await save_punch(session, "SN1", "7", T)
    assert await save_punch(session, "SN1", "7", T) is None
    await save_punch(session, "SN1", "8", T)
    await session.commit()
    assert await _count(session) == 2


@pytest.mark.asyncio
async def test_duplicate_window_drops_a_double_touch(session):
    await save_punch(session, "SN1", "7", T, duplicate_window=60)
    assert await save_punch(session, "SN1", "7", T.replace(second=59), duplicate_window=60) is None
    await session.commit()
    assert await _count(session) == 1


@pytest.mark.asyncio
async def test_any_other_integrity_error_is_not_a_duplicate(session, monkeypatch):
    """Answering OK here would make the device delete a punch we never stored."""
    import aiface_ws.storage as storage

    async def broken_upsert(session, sn, enrollid, name):
        session.add(Employee(device_sn=None, enrollid=enrollid, name=name))  # NOT NULL

    monkeypatch.setattr(storage, "upsert_employee", broken_upsert)
    with pytest.raises(PunchNotSaved):
        await save_punch(session, "SN1", "9", T, name="X")


@pytest.mark.asyncio
async def test_without_the_final_commit_nothing_is_stored(session):
    await save_punch(session, "SN1", "10", T)
    await session.rollback()
    assert await _count(session) == 0


@pytest.mark.asyncio
async def test_an_empty_name_never_erases_a_known_one(session):
    await upsert_employee(session, "SN1", "5", "Grace Hopper")
    await session.commit()
    await upsert_employee(session, "SN1", "5", "")
    await session.commit()
    name = (await session.execute(select(Employee.name))).scalar()
    assert name == "Grace Hopper"
