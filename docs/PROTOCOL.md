# AiFace WebSocket protocol: field notes

What the terminals actually do, measured against real AiFace devices in
production. The vendor documentation and other SDKs describe more; this
page only says what we verified, and marks the traps that cost us data.

## Transport

- The **device opens the connection**: `ws://<server>:<port>/pub/chat`.
  You set the server address and port in the terminal menu (Comm / Cloud
  server). Nothing listens on the device side for this protocol.
- JSON text frames. A message with `cmd` is a request; one with `ret` is a
  reply to a request. Every message from the device carries `sn`.
- **WebSocket pings:** the terminals do not answer uvicorn's pings the way
  it expects. With the default 20 s timeout uvicorn keeps closing every
  device socket. Run uvicorn with `--ws-ping-interval 3600
  --ws-ping-timeout 3600` (`python -m aiface_ws` does it for you).
- A terminal can hold **two sockets for an instant** while it reconnects:
  it opens the new one before the old one finishes closing. Unbind a
  serial only if the closing socket is the one currently bound.
- The TCP port 5005 the devices also open (vendor SDK) serves **one session
  at a time**; a second client gets a handshake of 0. Not used here.

## Registration

```json
← {"cmd":"reg","sn":"SERIAL","devinfo":{"modelname":"...","firmware":"...", ...}}
→ {"ret":"reg","sn":"SERIAL","result":true,"cloudtime":"2026-09-21 07:00:00",
   "tryseconds":120,"nosenduser":true,"nosendlog":0}
→ {"cmd":"settime","cloudtime":"2026-09-21 07:00:00"}
```

- `nosendlog: 0` lets the device push what it stored while offline right
  after registering. That is how nothing is lost across a power cut.
- Times are **local, without timezone**. Push the server clock with
  `settime` on every registration so every device agrees.

## Live punches (`sendlog`)

```json
← {"cmd":"sendlog","sn":"SERIAL","count":1,"logindex":1234,
   "record":[{"enrollid":7,"name":"...","time":"2026-09-21 07:03:11",
              "mode":8,"inout":0,"event":0,"temp":0,"image":"<base64 JPG>"}]}
→ {"ret":"sendlog","result":true,"cloudtime":"2026-09-21 07:03:12","access":1}
```

- **The ACK deletes the punch from the device queue.** Only ACK after the
  batch is committed. If storing fails, send nothing: the device resends.
- Reply with **exactly those four fields**. Echoing `logindex` back made a
  terminal treat it as "resend from here" and replay its history in a loop.
- `inout` is **always 0**: the terminal does not know whether a punch is an
  entry or an exit. Pairing entries and exits is your job.
- `mode` seen: 1 fingerprint, 2 password, 3 card, 8 face, 15 palm.
- The device waits ~30 s for the ACK and then reconnects and resends. A slow
  ACK (1-3 s from one fsync per punch) is enough to cause reconnection
  storms; commit once per batch.
- People touch the screen twice: expect the same person a few seconds apart.

## History (`getalllog`, `getnewlog`)

```json
→ {"cmd":"getalllog","stn":true,"from":"2026-09-01","to":"2026-09-30"}
← {"ret":"getalllog","sn":"SERIAL","result":true,"count":196,"from":0,"to":20,"record":[...]}
→ {"ret":"getalllog","sn":"SERIAL","result":true}          (ACK the page)
→ {"cmd":"getalllog","stn":false}                          (next page)
```

- Pages are **20 records**. `page` / `pagesize` parameters are ignored.
- **`count` is the total, not what is left.** Stop when `to >= count`, or
  on an empty page. "Continue while count > 0" never ends.
- **`stn: true` resets the device cursor.** Two downloads at once corrupt
  each other: allow one per device.
- Leave ~200 ms between pages: the device is still recognising faces.
- `from`/`to` dates are honoured. Downloading day by day is the reliable
  way to verify a day: compare the device's `count` with what you stored.
- **Do not trust the live push to be complete.** A device can consider
  punches delivered that you never stored (for instance, if your server
  answered OK without committing). Reconcile yesterday/today with
  `getalllog` periodically.

## Configuration (`getdevinfo`, `setdevinfo`)

```json
→ {"cmd":"getdevinfo"}
← {"ret":"getdevinfo","sn":"SERIAL","result":true,"volume":8,"use_logphoto":0,
   "netipaddress":3232235876,"serverport":8000, ... ~175 fields}
→ {"cmd":"setdevinfo","volume":8,"use_logphoto":1,"netipaddress":3232235876, ... all fields}
← {"ret":"setdevinfo","sn":"SERIAL","result":true}
```

- Fields come **at the top level** of the reply, and `setdevinfo` must
  send them **at the top level** too. Nested under `"devinfo"` (as another
  SDK does) the device answered `result: true` and applied **nothing**.
- Send the **full configuration** with only your change. A partial
  `setdevinfo` is untested and a missing field could be reset (IP, server).
- Always read it back with another `getdevinfo` and diff.
- IP fields are 32-bit integers (`3232235876` = `192.168.1.100`).

## Punch photos (`use_logphoto`)

- `use_logphoto: 1` makes every `sendlog` record carry the captured face in
  `image` (base64 JPEG, ~25 KB; a batch grows from ~200 B to ~34 KB). ACK
  time stayed under 110 ms.
- The photos also accumulate in the device; `cleanlogphoto` clears them.
- They are biometric data: consent, retention and access control are on you.

## Inventory (`getdevcap`)

```json
← {"ret":"getdevcap","result":true,"usersize":15000,"logsize":500000,
   "useduser":150,"usedlog":80000,"usednewlog":200}
```

- `usedlog` vs your stored count reveals missing history.
- `usedlog / logsize` near 1 means the device will start overwriting.
- `usednewlog` is **not** a reliable "undelivered" counter (seen at 470
  with everything stored). Verify with `getalllog` for the day.

## Users

- `enrollid` is **local to each device**: the same number on two terminals
  is two different people. Key everything by (serial, enrollid).
- Devices reuse enrollids: when a slot is given to someone new, old punches
  of that slot look like the new person's. Watch for name changes.
- `senduser` (device pushes a new enrolment) only arrives if you register
  with `nosenduser: false`; it carries biometric templates and can be big.
- `getuserinfo {"enrollid":7,"backupnum":0}` returns the name and one
  credential. `getuserlist` returned 0 users on the firmware we tested.
