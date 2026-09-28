"""Pagination, registry, commands, photos, inventory and relay."""
import asyncio
from datetime import date, datetime

import httpx
import pytest

from aiface_ws import inventory, pagination
from aiface_ws.commands import Commands
from aiface_ws.photos import PhotoStore
from aiface_ws.registry import ConnectionRegistry
from conftest import JPEG, JPEG_B64

# -- pagination --------------------------------------------------------------

def test_count_is_the_total_so_the_to_field_decides():
    st = pagination.PageState()
    assert st.next(records=20, saved=20, total=196, delivered=20) == (True, "")
    more, reason = st.next(records=16, saved=16, total=196, delivered=196)
    assert not more and "196" in reason


def test_without_count_and_to_three_pages_with_nothing_new_stop_it():
    st = pagination.PageState()
    assert st.next(records=20, saved=0)[0] and st.next(records=20, saved=0)[0]
    assert st.next(records=20, saved=0)[0] is False


def test_only_one_download_per_device_and_a_dead_one_expires(monkeypatch):
    pagination.release("X")
    assert pagination.reserve("X") is not None
    assert pagination.reserve("X") is None
    later = pagination._now() + pagination.ABANDON_AFTER + 1
    monkeypatch.setattr(pagination, "_now", lambda: later)
    assert pagination.reserve("X") is not None
    pagination.release("X")

# -- registry ----------------------------------------------------------------

def test_closing_the_old_socket_does_not_unbind_the_new_one():
    reg, old, new = ConnectionRegistry(), object(), object()
    reg.connect("SN", old)
    reg.connect("SN", new)
    reg.disconnect("SN", old)
    assert reg.connections["SN"] is new
    reg.disconnect("SN", new)
    assert not reg.is_connected("SN")

# -- commands ----------------------------------------------------------------

def test_set_dev_info_is_flat_full_and_rejects_unknown_fields():
    current = {"use_logphoto": 0, "serverport": 8000}
    assert Commands.set_dev_info(current, use_logphoto=1) == {"cmd": "setdevinfo", "use_logphoto": 1, "serverport": 8000}
    with pytest.raises(ValueError):
        Commands.set_dev_info(current, typo=1)


def test_get_all_log_by_date():
    assert Commands.get_all_log(date(2026, 9, 1), date(2026, 9, 30)) == {
        "cmd": "getalllog", "stn": True, "from": "2026-09-01", "to": "2026-09-30"}

# -- photos --------------------------------------------------------------------

def test_photo_store_saves_jpeg_rejects_junk_and_stays_in_its_folder(tmp_path):
    store, when = PhotoStore(tmp_path), datetime(2026, 9, 28, 7, 5, 9)
    rel = store.save("SN1", "16", when, JPEG_B64)
    assert rel == "SN1/2026-09-28/16_070509.jpg" and store.file(rel).read_bytes() == JPEG
    assert store.save("SN1", "16", when, "not base64!!") is None
    assert store.file("../../.env") is None
    store.delete(rel)
    assert store.file(rel) is None

# -- inventory -------------------------------------------------------------------

def test_inventory_levels():
    inv = inventory.parse({"ret": "getdevcap", "result": True, "useduser": 3,
                           "usedlog": 95, "usednewlog": 0, "logsize": 100})
    assert inventory.compare(inv, 95)[0] == "memory"
    inv2 = inventory.parse({"ret": "getdevcap", "result": True, "usedlog": 50, "logsize": 1000})
    assert inventory.compare(inv2, 40)[0] == "missing"
    assert inventory.compare(inv2, 50)[0] == "ok"

# -- relay -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_relay_advances_only_on_2xx_and_skips_before_since(tmp_path):
    from aiface_ws.config import Settings
    from aiface_ws.db import create_tables, make_engine, make_session_factory
    from aiface_ws.models import Device
    from aiface_ws.relay import Relay
    from aiface_ws.storage import save_punch

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
    await create_tables(engine)
    sessions = make_session_factory(engine)
    async with sessions() as s:
        s.add(Device(sn="SN1"))
        await save_punch(s, "SN1", "1", datetime(2025, 12, 31, 8, 0))   # before `since`
        await save_punch(s, "SN1", "1", datetime(2026, 1, 2, 8, 0))
        await s.commit()

    status = {"code": 500}
    received = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(status["code"], json={})

    settings = Settings(relay_url="http://hook.test/p", relay_token="t", relay_since=date(2026, 1, 1), _env_file=None)
    relay = Relay(settings, sessions, transport=httpx.MockTransport(handler))

    assert (await relay.send_batch("SN1"))["status"] == "http_error"
    async with sessions() as s:
        assert await relay.pending(s, "SN1") == 2, "a failed POST must not move the cursor"

    status["code"] = 200
    result = await relay.send_batch("SN1")
    assert result == {"status": "ok", "sn": "SN1", "sent": 1, "skipped": 1, "cursor": 2}
    body = received[-1].read().decode()
    assert '"2026-01-02 08:00:00"' in body and "2025-12-31" not in body
    assert received[-1].headers["X-Relay-Token"] == "t"
    async with sessions() as s:
        assert await relay.pending(s, "SN1") == 0
    await engine.dispose()
