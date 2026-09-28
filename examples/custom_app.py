"""
Embedding the server in your own app, with event listeners and extra routes.

    uvicorn examples.custom_app:app --port 8000 --ws-ping-interval 3600 --ws-ping-timeout 3600
"""

from aiface_ws import Commands, create_app

app = create_app()
aiface = app.state.aiface


@aiface.events.on("punch")
async def on_punch(punch, device_sn):
    # Runs after the punch is committed. Keep it fast; hand slow work to a queue.
    print(f"[{device_sn}] enrollid {punch.enrollid} at {punch.time} (photo: {bool(punch.photo)})")


@aiface.events.on("device_registered")
async def on_register(device_sn, ip, devinfo):
    print(f"{device_sn} online from {ip}, model {devinfo.get('modelname')}")
    # Ask every device for its inventory as soon as it connects.
    await aiface.registry.send(device_sn, Commands.get_dev_cap())


@aiface.events.on("reply")
async def on_reply(device_sn, cmd, payload):
    if cmd == "setdevinfo":
        print(f"{device_sn} setdevinfo result={payload.get('result')}")


@app.get("/hello")
async def hello():
    return {"connected": list(aiface.registry.connections)}
