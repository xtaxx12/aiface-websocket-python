"""
A fake AiFace terminal, to try the server without hardware.

    python -m aiface_ws                      # terminal 1
    python examples/mock_device.py           # terminal 2

It registers, pushes a live punch every few seconds, answers paged
getalllog requests from its own in-memory history, and answers getdevinfo /
setdevinfo / getdevcap like a real device (setdevinfo only applies fields
sent at the top level, as the real firmware does).

    python examples/mock_device.py --sn DEMO0001 --url ws://localhost:8000/pub/chat --every 20
"""

import argparse
import asyncio
import json
import random
from datetime import datetime, timedelta

import websockets

PEOPLE = {1: "Ada Lovelace", 2: "Alan Turing", 3: "Grace Hopper", 4: "Linus Torvalds"}
PAGE = 20


def fake_history(days: int = 3) -> list[dict]:
    out, start = [], datetime.now().replace(second=0, microsecond=0) - timedelta(days=days)
    for d in range(days):
        for uid in PEOPLE:
            day = start + timedelta(days=d)
            for hour in (7, 19):
                t = day.replace(hour=hour, minute=random.randint(0, 20), second=random.randint(0, 59))
                out.append({"enrollid": uid, "name": PEOPLE[uid], "time": t.strftime("%Y-%m-%d %H:%M:%S"),
                            "mode": 8, "inout": 0, "event": 0})
    return sorted(out, key=lambda r: r["time"])


async def run(url: str, sn: str, every: float) -> None:
    history = fake_history()
    devinfo = {"deviceid": 1, "volume": 8, "use_logphoto": 0, "netportnum": 5005, "serverport": 8000}
    cursor = {"rows": [], "pos": 0}

    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"cmd": "reg", "sn": sn, "cpusn": "MOCK", "devinfo": {
            "modelname": "AiFace-Mock", "firmware": "mock-1.0", "usersize": 3000, "logsize": 100000}}))

        async def live_punches() -> None:
            # One person after the other: the same person twice within the
            # server's duplicate window (60 s) is dropped as a double touch.
            turn = 0
            while True:
                await asyncio.sleep(every)
                uid = list(PEOPLE)[turn % len(PEOPLE)]
                turn += 1
                record = {"enrollid": uid, "name": PEOPLE[uid], "mode": 8, "inout": 0,
                          "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                history.append(record)
                await ws.send(json.dumps({"cmd": "sendlog", "sn": sn, "count": 1,
                                          "logindex": len(history), "record": [record]}))
                print("punch ->", record["name"], record["time"])

        async def send_page(cmd: str) -> None:
            rows, pos = cursor["rows"], cursor["pos"]
            page = rows[pos:pos + PAGE]
            cursor["pos"] = pos + len(page)
            await ws.send(json.dumps({"ret": cmd, "sn": sn, "result": True, "count": len(rows),
                                      "from": pos, "to": cursor["pos"], "record": page}))

        task = asyncio.create_task(live_punches())
        try:
            async for raw in ws:
                msg = json.loads(raw)
                cmd, ret = msg.get("cmd"), msg.get("ret")
                if ret:
                    print("server ack:", ret)
                    continue
                print("server cmd:", cmd)
                if cmd in ("getalllog", "getnewlog"):
                    if msg.get("stn"):
                        since, until = msg.get("from", "2000-01-01"), msg.get("to", "2099-12-31")
                        cursor.update(rows=[r for r in history if since <= r["time"][:10] <= until], pos=0)
                    await send_page(cmd)
                elif cmd == "getdevinfo":
                    await ws.send(json.dumps({"ret": "getdevinfo", "sn": sn, "result": True, **devinfo}))
                elif cmd == "setdevinfo":
                    devinfo.update({k: v for k, v in msg.items() if k in devinfo})
                    await ws.send(json.dumps({"ret": "setdevinfo", "sn": sn, "result": True}))
                elif cmd == "getdevcap":
                    await ws.send(json.dumps({"ret": "getdevcap", "sn": sn, "result": True, "useduser": len(PEOPLE),
                                              "usedlog": len(history), "usednewlog": 0, "logsize": 100000}))
                else:
                    await ws.send(json.dumps({"ret": cmd, "sn": sn, "result": True}))
        finally:
            task.cancel()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="ws://localhost:8000/pub/chat")
    p.add_argument("--sn", default="MOCK00000001")
    p.add_argument("--every", type=float, default=20.0, help="seconds between live punches")
    a = p.parse_args()
    asyncio.run(run(a.url, a.sn, a.every))
