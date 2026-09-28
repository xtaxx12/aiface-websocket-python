"""
A minimal webhook receiver, to see what the relay sends.

    uvicorn examples.webhook_receiver:app --port 9000
    AIFACE_RELAY_URL=http://localhost:9000/punches AIFACE_RELAY_TOKEN=secret python -m aiface_ws

Deduplicate by (sn, enrollid, time): the relay may resend a batch after a
network error, and that must be harmless.
"""

from fastapi import FastAPI, Header, HTTPException

app = FastAPI()
seen: set[tuple] = set()


@app.post("/punches")
async def receive(body: dict, x_relay_token: str | None = Header(default=None)):
    if x_relay_token != "secret":
        raise HTTPException(401, "bad token")
    new = 0
    for r in body.get("records", []):
        key = (body["sn"], r["enrollid"], r["time"])
        if key not in seen:
            seen.add(key)
            new += 1
            print(body["sn"], r["enrollid"], r["time"], "mode", r["mode"])
    return {"received": len(body.get("records", [])), "inserted": new}
