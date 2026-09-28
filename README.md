# AiFace WebSocket Server & Device SDK for Python

[Español](README.es.md)

A FastAPI server for **AiFace** face-recognition attendance terminals (the
ones that speak JSON over WebSocket, `ws://server:port/pub/chat`). It
receives punches in real time, downloads the device history, changes the
device configuration, stores punch photos, and forwards everything to your
payroll or ERP through a webhook.

It is the Python counterpart of
[olorunda/laravel-aiface-websocket](https://github.com/olorunda/laravel-aiface-websocket)
(PHP/Laravel), and it was written the hard way: running against real
terminals in a factory, where every lost punch is someone's unpaid hour.
The lessons are in [docs/PROTOCOL.md](docs/PROTOCOL.md) and in the comments
of the code.

- **No punch is lost.** A punch is acknowledged only after it is committed;
  anything else is not ACKed and the device resends it.
- **Paged history download** by date, one per device, with the stop rule
  the firmware actually needs.
- **Device configuration** read and written the way the firmware accepts it.
- **Punch photos** (`use_logphoto`) stored as files, with a local-only switch.
- **Webhook relay** with a cursor per destination and retries.
- **Dashboard**, REST API, event listeners and a **mock device** to try it all
  without hardware.

---

## ⚡ Architecture

```
 AiFace terminal ──WebSocket /pub/chat──▶ DeviceHandler ──▶ storage ──▶ SQL database
   (opens the          reg / sendlog /         │  one savepoint per punch,       ▲
    connection)        getalllog pages …       │  one commit + ACK per batch     │
                                               ▼                                 │
                                          EventBus ──▶ your listeners            │
                                                                                 │
 Your code / dashboard ──REST /api──▶ ConnectionRegistry ──▶ command to device   │
                                                                                 │
                        Relay (every 60 s) ──reads punches with id > cursor──────┘
                             │
                             └──POST JSON──▶ your webhook (payroll, ERP …)
```

| Module | What it does |
|---|---|
| `handler.py` | The device socket: registration, live punches, history pages, replies |
| `storage.py` | Saving punches without losing any (read its docstring) |
| `commands.py` | Command builder, tagged `[verified]` or `[docs]` |
| `pagination.py` | When to stop asking for history pages; one download per device |
| `registry.py` | One live socket per serial, safe against overlapping reconnections |
| `relay.py` | Webhook forwarding with cursor and retries |
| `photos.py` | Punch photos on disk |
| `inventory.py` | `getdevcap`: missing history and memory usage |
| `api.py` | REST API used by the dashboard |

## 🚀 Quickstart

```bash
git clone https://github.com/<you>/aiface-websocket-python
cd aiface-websocket-python
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

python -m aiface_ws                               # http://localhost:8000
```

Open `http://localhost:8000` for the dashboard. With no configuration it uses
SQLite (`./aiface.db`). No device yet? Start the simulator in another terminal:

```bash
python examples/mock_device.py                    # a fake terminal pushing punches
```

### Configuration (`.env`)

Copy `.env.example` to `.env`. Every variable is optional.

| Variable | Default | |
|---|---|---|
| `AIFACE_DATABASE_URL` | `sqlite+aiosqlite:///./aiface.db` | PostgreSQL: `postgresql+asyncpg://user:pass@host/db` (`pip install -e ".[postgres]"`) |
| `AIFACE_PORT` | `8000` | The port you configure in the terminals |
| `AIFACE_WS_PATH` | `/pub/chat` | Firmware default |
| `AIFACE_API_TOKEN` | — | If set, `/api` requires `Authorization: Bearer <token>`. **Set it before exposing the server.** |
| `AIFACE_DUPLICATE_WINDOW_SECONDS` | `60` | Same person, same device, closer than this = one punch |
| `AIFACE_PHOTOS_DIR` | `data/photos` | |
| `AIFACE_PHOTOS_LOCAL_ONLY` | `true` | Never serve photos through a proxy/tunnel |
| `AIFACE_RELAY_URL` | — | Enables the webhook relay |
| `AIFACE_RELAY_TOKEN` | — | Sent in `AIFACE_RELAY_TOKEN_HEADER` (default `X-Relay-Token`) |
| `AIFACE_RELAY_DEVICES` | all | Comma separated serials |
| `AIFACE_RELAY_SINCE` | — | `YYYY-MM-DD`: older punches are kept but never relayed |

Run it yourself with uvicorn if you prefer, but **keep the ping flags**
(the terminals do not answer uvicorn's pings and the default closes them):

```bash
uvicorn aiface_ws.app:create_app --factory --host 0.0.0.0 --port 8000 \
        --ws-ping-interval 3600 --ws-ping-timeout 3600
```

## 📱 Device configuration

On the terminal: **Menu → Comm (Network) → Cloud server**

- Server address: the IP (or domain) of the machine running this server
- Port: `AIFACE_PORT` (8000)
- The device connects to `ws://<address>:<port>/pub/chat`

Within seconds it appears in the dashboard as *connected*, and anything it
stored while it had no server is pushed right away.

## 💻 Using it from Python

`create_app()` returns a FastAPI app. Everything lives in `app.state.aiface`:

```python
from aiface_ws import Commands, create_app

app = create_app()
aiface = app.state.aiface

@aiface.events.on("punch")
async def on_punch(punch, device_sn):
    print(device_sn, punch.enrollid, punch.time, punch.mode)

@aiface.events.on("device_registered")
async def on_register(device_sn, ip, devinfo):
    await aiface.registry.send(device_sn, Commands.get_dev_cap())
```

Events: `device_registered`, `device_disconnected`, `punch`, `user_pushed`,
`download_finished`, `reply` (any answer to a command you sent). A listener
that raises is logged; it never breaks the device connection.

See [examples/custom_app.py](examples/custom_app.py).

## 🛠️ Commands

Commands are asynchronous: you send them, and the answer arrives later on the
device socket (`reply` event, or `GET /api/devices/{sn}/replies`).

| Method | Command | |
|---|---|---|
| `get_all_log(since, until)` | `getalllog` | ✅ History by date, paged |
| `get_new_log()` | `getnewlog` | ✅ Only undelivered punches |
| `get_dev_info()` | `getdevinfo` | ✅ Full configuration (~175 fields) |
| `set_dev_info(current, **changes)` | `setdevinfo` | ✅ Flat, full config (see PROTOCOL.md) |
| `get_dev_cap()` | `getdevcap` | ✅ Users, punches, capacity |
| `set_time(when)` | `settime` | ✅ |
| `get_user_info(enrollid, backupnum)` | `getuserinfo` | ✅ |
| `set_user_info(...)` | `setuserinfo` | 📄 backupnum 50 = face JPG |
| `delete_user(enrollid, backupnum=13)` | `deleteuser` | 📄 |
| `enable_user(enrollid, enable)` | `enableuser` | 📄 |
| `get_user_list()` | `getuserlist` | 📄 returned 0 users on our firmware |
| `clean_log()` / `clean_log_photo()` | `cleanlog` / `cleanlogphoto` | 📄 irreversible |
| `reboot()`, `enable_device()`, `disable_device()`, `open_door()` | | 📄 |

✅ verified against real terminals · 📄 from the protocol docs, untested here.
Anything else: `POST /api/devices/{sn}/command` with the raw JSON.

## 🌐 REST API

All under `/api`, protected by `AIFACE_API_TOKEN` when set. Interactive docs at `/docs`.

| Method & path | |
|---|---|
| `GET /api/devices` | Devices and whether they are connected |
| `PATCH /api/devices/{sn}` | `{"name": "...", "is_active": false}` (disabled = refused without ACK, keeps its buffer) |
| `POST /api/devices/{sn}/command` | Send any raw command |
| `GET /api/devices/{sn}/replies` | Last answer per command |
| `POST /api/devices/{sn}/logs?since=&until=&new_only=` | Download history (409 if one is running) |
| `GET /api/devices/{sn}/logs/status` | Download progress |
| `POST /api/devices/{sn}/devinfo/refresh` · `GET …/devinfo` | Read the configuration |
| `PATCH /api/devices/{sn}/devinfo` | `{"use_logphoto": 1}`: change fields safely |
| `POST /api/devices/{sn}/inventory/refresh` · `GET …/inventory` | `getdevcap` vs stored |
| `GET /api/punches?sn=&since=&until=&q=&page=` | Punches, newest first, with name and photo URL |
| `GET /api/punches/{id}/photo` | The JPEG (local requests only by default) |
| `GET /api/relay/status` · `POST /api/relay/cursor?sn=&start=now\|all\|YYYY-MM-DD` · `POST /api/relay/send?sn=` | Relay control |

## 🔁 Webhook relay

Set `AIFACE_RELAY_URL` and every 60 s the server POSTs new punches:

```json
{"sn": "SERIAL", "ip": "192.168.1.100", "last_id": 1234,
 "records": [{"enrollid": 7, "time": "2026-09-21 07:03:11", "mode": 8, "inout": 0, "temperature": null}]}
```

- A **cursor per device and destination** stores the last punch id your
  webhook confirmed. It advances **only on a 2xx**; errors retry the same
  batch next cycle.
- It walks insertion order, so **history recovered later is still sent**.
- Your receiver must **deduplicate by (sn, enrollid, time)**: resending is
  how failures heal. Photos are never relayed.
- New destination = cursor at 0. Position it before going live:
  `POST /api/relay/cursor?sn=SERIAL&start=now` (or a date).

Try it with [examples/webhook_receiver.py](examples/webhook_receiver.py).

## 📷 Punch photos

Enable them per device, then look at the thumbnails in the dashboard:

```bash
curl -X POST localhost:8000/api/devices/SERIAL/devinfo/refresh
curl -X PATCH localhost:8000/api/devices/SERIAL/devinfo -H 'Content-Type: application/json' -d '{"use_logphoto": 1}'
curl -X POST localhost:8000/api/devices/SERIAL/devinfo/refresh   # verify it stuck
```

~25 KB per punch, stored under `AIFACE_PHOTOS_DIR`. **Faces are biometric
personal data** (GDPR art. 9 and equivalents): obtain the consent your law
requires, define a retention period, and keep `AIFACE_PHOTOS_LOCAL_ONLY=true`
if the dashboard is reachable from outside (tunnels, reverse proxies).

## 🧪 Tests

```bash
pytest
```

The suite includes an end-to-end test that talks to the real app over the
WebSocket as a terminal would, plus the storage rules that once lost data.

## 🔐 Security notes

- Terminals cannot authenticate: anything that can reach the WebSocket port
  can pretend to be a device. Keep that port on the LAN.
- Set `AIFACE_API_TOKEN` before exposing the dashboard or API anywhere.
- Never commit `.env`, the database, logs or `data/`: they contain personal data.

## 🙏 Credits

Inspired by and cross-checked against
[olorunda/laravel-aiface-websocket](https://github.com/olorunda/laravel-aiface-websocket).
Where the two disagree, docs/PROTOCOL.md says what our devices did.

## 📜 License

MIT. See [LICENSE](LICENSE).
