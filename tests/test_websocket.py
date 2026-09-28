"""End to end: a fake terminal talking to the real app over the WebSocket."""
from conftest import JPEG, JPEG_B64, register


def test_registration_reply_and_clock_sync(client):
    with client.websocket_connect("/pub/chat") as ws:
        reply, settime = register(ws)
    assert reply["ret"] == "reg" and reply["result"] is True
    assert reply["nosenduser"] is True and reply["nosendlog"] == 0
    assert settime["cmd"] == "settime" and "cloudtime" in settime
    device = client.get("/api/devices").json()[0]
    assert device["sn"] == "TEST0001" and device["model"] == "AiFace-Test"


def test_sendlog_is_stored_and_acked_with_exactly_four_fields(client):
    with client.websocket_connect("/pub/chat") as ws:
        register(ws)
        ws.send_json({"cmd": "sendlog", "sn": "TEST0001", "count": 1, "logindex": 42, "record": [
            {"enrollid": 7, "name": "Ada Lovelace", "time": "2026-09-21 07:03:11", "mode": 8, "inout": 0}]})
        ack = ws.receive_json()
    # Echoing logindex back made a terminal replay its history in a loop.
    assert set(ack) == {"ret", "result", "cloudtime", "access"}
    data = client.get("/api/punches").json()["data"]
    assert [(p["enrollid"], p["name"], p["time"]) for p in data] == [("7", "Ada Lovelace", "2026-09-21T07:03:11")]


def test_a_resent_batch_is_acked_but_not_stored_twice(client):
    batch = {"cmd": "sendlog", "sn": "TEST0001", "record": [{"enrollid": 7, "time": "2026-09-21 07:03:11"}]}
    with client.websocket_connect("/pub/chat") as ws:
        register(ws)
        ws.send_json(batch)
        ws.receive_json()
        ws.send_json(batch)
        assert ws.receive_json()["ret"] == "sendlog"
    assert client.get("/api/punches").json()["total"] == 1


def test_punch_photo_is_saved_and_only_served_locally(client):
    with client.websocket_connect("/pub/chat") as ws:
        register(ws)
        ws.send_json({"cmd": "sendlog", "sn": "TEST0001", "record": [
            {"enrollid": 7, "time": "2026-09-21 07:03:11", "image": JPEG_B64}]})
        ws.receive_json()
    row = client.get("/api/punches").json()["data"][0]
    assert row["photo_url"]
    assert client.get(row["photo_url"]).content == JPEG
    # Through a tunnel or reverse proxy: hidden from the list and refused.
    assert client.get("/api/punches", headers={"cf-ray": "x"}).json()["data"][0]["photo_url"] is None
    assert client.get(row["photo_url"], headers={"x-forwarded-for": "1.2.3.4"}).status_code == 403


def test_history_download_pages_until_the_device_says_it_is_done(client):
    rows = [{"enrollid": i, "time": f"2026-09-20 07:{i:02d}:00"} for i in range(25)]
    with client.websocket_connect("/pub/chat") as ws:
        register(ws)
        assert client.post("/api/devices/TEST0001/logs?since=2026-09-20&until=2026-09-20").status_code == 200
        cmd = ws.receive_json()
        assert cmd["cmd"] == "getalllog" and cmd["stn"] is True and cmd["from"] == "2026-09-20"
        # A second download while this one runs would reset the device cursor.
        assert client.post("/api/devices/TEST0001/logs").status_code == 409

        ws.send_json({"ret": "getalllog", "sn": "TEST0001", "result": True,
                      "count": 25, "from": 0, "to": 20, "record": rows[:20]})
        assert ws.receive_json() == {"ret": "getalllog", "sn": "TEST0001", "result": True}
        assert ws.receive_json() == {"cmd": "getalllog", "stn": False}

        ws.send_json({"ret": "getalllog", "sn": "TEST0001", "result": True,
                      "count": 25, "from": 20, "to": 25, "record": rows[20:]})
        assert ws.receive_json()["ret"] == "getalllog"
    assert client.get("/api/devices/TEST0001/logs/status").json() == {"downloading": False}
    assert client.get("/api/punches").json()["total"] == 25


def test_disabled_device_is_refused_without_ack(client):
    with client.websocket_connect("/pub/chat") as ws:
        register(ws)
    client.patch("/api/devices/TEST0001", json={"is_active": False})
    with client.websocket_connect("/pub/chat") as ws:
        ws.send_json({"cmd": "sendlog", "sn": "TEST0001", "record": [{"enrollid": 1, "time": "2026-09-21 08:00:00"}]})
        msg = ws.receive()
    assert msg["type"] == "websocket.close" and msg["code"] == 1008
    assert client.get("/api/punches").json()["total"] == 0


def test_setdevinfo_sends_the_full_config_flat(client):
    with client.websocket_connect("/pub/chat") as ws:
        register(ws)
        assert client.patch("/api/devices/TEST0001/devinfo", json={"use_logphoto": 1}).status_code == 409
        client.post("/api/devices/TEST0001/devinfo/refresh")
        assert ws.receive_json() == {"cmd": "getdevinfo"}
        ws.send_json({"ret": "getdevinfo", "sn": "TEST0001", "result": True,
                      "use_logphoto": 0, "netportnum": 5005, "serverport": 8000})
        # the reply is processed asynchronously; wait until the server has it
        for _ in range(50):
            if client.get("/api/devices/TEST0001/devinfo").status_code == 200:
                break
        assert client.patch("/api/devices/TEST0001/devinfo", json={"nonexistent": 1}).status_code == 400
        r = client.patch("/api/devices/TEST0001/devinfo", json={"use_logphoto": 1})
        assert r.json()["changes"] == {"use_logphoto": {"before": 0, "after": 1}}
        sent = ws.receive_json()
    assert sent == {"cmd": "setdevinfo", "use_logphoto": 1, "netportnum": 5005, "serverport": 8000}


def test_api_token_protects_the_api_but_not_the_device_socket(settings):
    from fastapi.testclient import TestClient

    from aiface_ws import create_app

    settings.api_token = "s3cret"
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/devices").status_code == 401
        assert c.get("/api/devices", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        with c.websocket_connect("/pub/chat") as ws:
            reply, _ = register(ws)
        assert reply["result"] is True
