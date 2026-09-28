# Servidor WebSocket y SDK para relojes AiFace en Python

[English](README.md)

Un servidor FastAPI para los relojes de asistencia con reconocimiento facial
**AiFace** (los que hablan JSON por WebSocket, `ws://servidor:puerto/pub/chat`).
Recibe las marcaciones en tiempo real, descarga el historial del equipo,
cambia su configuración, guarda la foto de cada marcación y reenvía todo a tu
nómina o ERP mediante un webhook.

Es la contraparte en Python de
[olorunda/laravel-aiface-websocket](https://github.com/olorunda/laravel-aiface-websocket)
(PHP/Laravel), y se escribió a golpes: funcionando contra relojes reales en
una planta, donde cada marcación perdida es una hora que alguien no cobra.
Lo aprendido está en [docs/PROTOCOL.md](docs/PROTOCOL.md) (en inglés) y en
los comentarios del código.

- **No se pierde ninguna marcación.** Se confirma al reloj solo después de
  guardarla; si algo falla no se confirma y el reloj la reenvía.
- **Descarga del historial por fecha**, paginada, una por equipo, con la
  regla de corte que el firmware realmente necesita.
- **Configuración del reloj**, leída y escrita como el firmware la acepta.
- **Fotos de las marcaciones** (`use_logphoto`) guardadas como archivos, con
  opción para servirlas solo en la red local.
- **Relay a webhook** con cursor por destino y reintentos.
- **Dashboard**, API REST, eventos y un **reloj simulado** para probar todo
  sin equipo.

---

## ⚡ Arquitectura

```
 Reloj AiFace ──WebSocket /pub/chat──▶ DeviceHandler ──▶ storage ──▶ Base de datos
  (abre la         reg / sendlog /          │  un savepoint por marcación,     ▲
   conexión)       páginas getalllog …      │  un commit + ACK por lote        │
                                            ▼                                  │
                                       EventBus ──▶ tus listeners              │
                                                                               │
 Tu código / dashboard ──REST /api──▶ ConnectionRegistry ──▶ comando al reloj  │
                                                                               │
                      Relay (cada 60 s) ──lee marcaciones con id > cursor──────┘
                           │
                           └──POST JSON──▶ tu webhook (nómina, ERP …)
```

| Módulo | Qué hace |
|---|---|
| `handler.py` | La conexión del reloj: registro, marcaciones en vivo, páginas de historial, respuestas |
| `storage.py` | Guardar marcaciones sin perder ninguna (lee su docstring) |
| `commands.py` | Constructor de comandos, marcados `[verified]` o `[docs]` |
| `pagination.py` | Cuándo dejar de pedir páginas; una descarga por equipo |
| `registry.py` | Una conexión viva por serial, a prueba de reconexiones superpuestas |
| `relay.py` | Reenvío al webhook con cursor y reintentos |
| `photos.py` | Fotos de las marcaciones en disco |
| `inventory.py` | `getdevcap`: historial faltante y memoria del equipo |
| `api.py` | API REST que usa el dashboard |

## 🚀 Inicio rápido

```bash
git clone https://github.com/<tu-usuario>/aiface-websocket-python
cd aiface-websocket-python
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

python -m aiface_ws                               # http://localhost:8000
```

Abre `http://localhost:8000` para ver el dashboard. Sin configuración usa
SQLite (`./aiface.db`). ¿Todavía sin reloj? Arranca el simulador en otra terminal:

```bash
python examples/mock_device.py                    # un reloj falso que envía marcaciones
```

### Configuración (`.env`)

Copia `.env.example` a `.env`. Todas las variables son opcionales.

| Variable | Por defecto | |
|---|---|---|
| `AIFACE_DATABASE_URL` | `sqlite+aiosqlite:///./aiface.db` | PostgreSQL: `postgresql+asyncpg://usuario:clave@host/db` (`pip install -e ".[postgres]"`) |
| `AIFACE_PORT` | `8000` | El puerto que configuras en los relojes |
| `AIFACE_WS_PATH` | `/pub/chat` | El que trae el firmware |
| `AIFACE_API_TOKEN` | — | Si se define, `/api` exige `Authorization: Bearer <token>`. **Defínelo antes de exponer el servidor.** |
| `AIFACE_DUPLICATE_WINDOW_SECONDS` | `60` | Misma persona y mismo reloj a menos de esto = una sola marcación |
| `AIFACE_PHOTOS_DIR` | `data/photos` | |
| `AIFACE_PHOTOS_LOCAL_ONLY` | `true` | Nunca servir fotos a través de un proxy o túnel |
| `AIFACE_RELAY_URL` | — | Activa el relay a webhook |
| `AIFACE_RELAY_TOKEN` | — | Se envía en `AIFACE_RELAY_TOKEN_HEADER` (por defecto `X-Relay-Token`) |
| `AIFACE_RELAY_DEVICES` | todos | Seriales separados por coma |
| `AIFACE_RELAY_SINCE` | — | `AAAA-MM-DD`: lo anterior se guarda pero no se reenvía |

Si prefieres lanzarlo con uvicorn, **conserva los parámetros de ping**: los
relojes no responden los pings de uvicorn y con el valor por defecto los desconecta.

```bash
uvicorn aiface_ws.app:create_app --factory --host 0.0.0.0 --port 8000 \
        --ws-ping-interval 3600 --ws-ping-timeout 3600
```

## 📱 Configurar el reloj

En el reloj: **Menú → Comunicación (Red) → Servidor en la nube**

- Dirección del servidor: la IP (o dominio) de la máquina donde corre este servidor
- Puerto: `AIFACE_PORT` (8000)
- El reloj se conecta a `ws://<dirección>:<puerto>/pub/chat`

En segundos aparece como *connected* en el dashboard, y envía enseguida lo
que haya guardado mientras no tenía servidor.

## 💻 Uso desde Python

`create_app()` devuelve una app FastAPI. Todo está en `app.state.aiface`:

```python
from aiface_ws import Commands, create_app

app = create_app()
aiface = app.state.aiface

@aiface.events.on("punch")
async def al_marcar(punch, device_sn):
    print(device_sn, punch.enrollid, punch.time, punch.mode)

@aiface.events.on("device_registered")
async def al_conectar(device_sn, ip, devinfo):
    await aiface.registry.send(device_sn, Commands.get_dev_cap())
```

Eventos: `device_registered`, `device_disconnected`, `punch`, `user_pushed`,
`download_finished`, `reply` (cualquier respuesta a un comando enviado). Un
listener que falla queda registrado en el log; nunca corta la conexión del reloj.

Ver [examples/custom_app.py](examples/custom_app.py).

## 🛠️ Comandos

Los comandos son asincrónicos: los envías y la respuesta llega después por la
conexión del reloj (evento `reply`, o `GET /api/devices/{sn}/replies`).

| Método | Comando | |
|---|---|---|
| `get_all_log(since, until)` | `getalllog` | ✅ Historial por fecha, paginado |
| `get_new_log()` | `getnewlog` | ✅ Solo lo no entregado |
| `get_dev_info()` | `getdevinfo` | ✅ Configuración completa (~175 campos) |
| `set_dev_info(current, **changes)` | `setdevinfo` | ✅ Campos sueltos y completos (ver PROTOCOL.md) |
| `get_dev_cap()` | `getdevcap` | ✅ Usuarios, marcaciones, capacidad |
| `set_time(when)` | `settime` | ✅ |
| `get_user_info(enrollid, backupnum)` | `getuserinfo` | ✅ |
| `set_user_info(...)` | `setuserinfo` | 📄 backupnum 50 = foto del rostro en JPG |
| `delete_user(enrollid, backupnum=13)` | `deleteuser` | 📄 |
| `enable_user(enrollid, enable)` | `enableuser` | 📄 |
| `get_user_list()` | `getuserlist` | 📄 devolvió 0 usuarios en nuestro firmware |
| `clean_log()` / `clean_log_photo()` | `cleanlog` / `cleanlogphoto` | 📄 irreversibles |
| `reboot()`, `enable_device()`, `disable_device()`, `open_door()` | | 📄 |

✅ verificado contra relojes reales · 📄 según la documentación, sin probar aquí.
Para cualquier otro: `POST /api/devices/{sn}/command` con el JSON crudo.

## 🌐 API REST

Todo bajo `/api`, protegido por `AIFACE_API_TOKEN` si está definido. Documentación interactiva en `/docs`.

| Método y ruta | |
|---|---|
| `GET /api/devices` | Relojes y si están conectados |
| `PATCH /api/devices/{sn}` | `{"name": "...", "is_active": false}` (desactivado = se rechaza sin ACK y conserva su memoria) |
| `POST /api/devices/{sn}/command` | Enviar cualquier comando crudo |
| `GET /api/devices/{sn}/replies` | Última respuesta de cada comando |
| `POST /api/devices/{sn}/logs?since=&until=&new_only=` | Descargar historial (409 si ya hay una descarga) |
| `GET /api/devices/{sn}/logs/status` | Avance de la descarga |
| `POST /api/devices/{sn}/devinfo/refresh` · `GET …/devinfo` | Leer la configuración |
| `PATCH /api/devices/{sn}/devinfo` | `{"use_logphoto": 1}`: cambiar campos de forma segura |
| `POST /api/devices/{sn}/inventory/refresh` · `GET …/inventory` | `getdevcap` contra lo guardado |
| `GET /api/punches?sn=&since=&until=&q=&page=` | Marcaciones, más nuevas primero, con nombre y URL de foto |
| `GET /api/punches/{id}/photo` | El JPEG (por defecto solo para pedidos locales) |
| `GET /api/relay/status` · `POST /api/relay/cursor?sn=&start=now\|all\|AAAA-MM-DD` · `POST /api/relay/send?sn=` | Control del relay |

## 🔁 Relay a webhook

Define `AIFACE_RELAY_URL` y cada 60 s el servidor envía por POST las marcaciones nuevas:

```json
{"sn": "SERIAL", "ip": "192.168.1.100", "last_id": 1234,
 "records": [{"enrollid": 7, "time": "2026-09-21 07:03:11", "mode": 8, "inout": 0, "temperature": null}]}
```

- Un **cursor por reloj y destino** guarda el último id que tu webhook
  confirmó. Solo **avanza con una respuesta 2xx**; ante un error se reintenta
  el mismo lote en el ciclo siguiente.
- Recorre el orden de inserción, así que **el historial recuperado después
  también se envía**.
- Tu receptor debe **descartar duplicados por (sn, enrollid, time)**: reenviar
  es como se reparan los fallos. Las fotos nunca se envían.
- Destino nuevo = cursor en 0. Posiciónalo antes de salir en vivo:
  `POST /api/relay/cursor?sn=SERIAL&start=now` (o una fecha).

Pruébalo con [examples/webhook_receiver.py](examples/webhook_receiver.py).

## 📷 Fotos de las marcaciones

Actívalas por reloj y míralas como miniaturas en el dashboard:

```bash
curl -X POST localhost:8000/api/devices/SERIAL/devinfo/refresh
curl -X PATCH localhost:8000/api/devices/SERIAL/devinfo -H 'Content-Type: application/json' -d '{"use_logphoto": 1}'
curl -X POST localhost:8000/api/devices/SERIAL/devinfo/refresh   # verificar que quedó
```

Unos 25 KB por marcación, guardados en `AIFACE_PHOTOS_DIR`. **Los rostros son
datos personales biométricos** (en Ecuador, la LOPDP los trata como datos
sensibles; en Europa, el art. 9 del RGPD): consigue el consentimiento que
exija tu ley, define cuánto tiempo se guardan y deja
`AIFACE_PHOTOS_LOCAL_ONLY=true` si el dashboard es accesible desde afuera
(túneles, proxies).

## 🧪 Pruebas

```bash
pytest
```

Incluye una prueba de punta a punta que habla con la app real por WebSocket
como lo haría un reloj, y las reglas de guardado que alguna vez perdieron datos.

## 🔐 Seguridad

- Los relojes no pueden autenticarse: cualquiera que llegue al puerto del
  WebSocket puede hacerse pasar por un reloj. Mantén ese puerto en la red local.
- Define `AIFACE_API_TOKEN` antes de exponer el dashboard o la API.
- Nunca subas `.env`, la base de datos, los logs ni `data/`: contienen datos personales.

## 🙏 Créditos

Inspirado en y contrastado con
[olorunda/laravel-aiface-websocket](https://github.com/olorunda/laravel-aiface-websocket).
Donde los dos difieren, docs/PROTOCOL.md cuenta lo que hicieron nuestros relojes.

## 📜 Licencia

MIT. Ver [LICENSE](LICENSE).
