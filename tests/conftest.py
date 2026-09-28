import base64

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from aiface_ws import create_app
from aiface_ws.config import Settings
from aiface_ws.db import create_tables, make_engine, make_session_factory

# Smallest thing that passes as a JPEG: the SOI marker is all we check.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9"
JPEG_B64 = base64.b64encode(JPEG).decode()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        photos_dir=str(tmp_path / "photos"),
        relay_url=None, api_token=None, _env_file=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'unit.db'}")
    await create_tables(engine)
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
    await engine.dispose()


def register(ws, sn="TEST0001"):
    ws.send_json({"cmd": "reg", "sn": sn, "devinfo": {"modelname": "AiFace-Test", "firmware": "t1"}})
    reply = ws.receive_json()
    settime = ws.receive_json()
    return reply, settime
