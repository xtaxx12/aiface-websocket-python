"""
The device WebSocket: one coroutine per connected terminal.

Order of business for every message: identify the device, refuse it if it
is disabled, then dispatch on `cmd` (a request from the device) or `ret`
(an answer to a command we sent).
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

from aiface_ws import inventory, pagination, protocol
from aiface_ws.config import Settings
from aiface_ws.events import EventBus
from aiface_ws.models import Device, Punch
from aiface_ws.photos import PhotoStore
from aiface_ws.registry import ConnectionRegistry
from aiface_ws.storage import PunchNotSaved, count_punches, save_punch, touch_device, upsert_employee

logger = logging.getLogger(__name__)

LIVE_PUNCH_CMDS = ("sendlog", "attlog", "realtime")
HISTORY_CMDS = ("getalllog", "getnewlog")


@dataclass
class DeviceState:
    """What the server remembers about each device while it runs."""
    devinfo: Dict[str, Any] = field(default_factory=dict)       # last getdevinfo
    devinfo_at: Optional[datetime] = None
    inventory: Optional[dict] = None                            # last getdevcap
    replies: Dict[str, dict] = field(default_factory=dict)      # last answer per cmd


class DeviceHandler:
    def __init__(self, settings: Settings, session_factory, registry: ConnectionRegistry,
                 photos: PhotoStore, events: EventBus) -> None:
        self.settings = settings
        self.sessions = session_factory
        self.registry = registry
        self.photos = photos
        self.events = events
        self.state: Dict[str, DeviceState] = {}

    def device_state(self, sn: str) -> DeviceState:
        return self.state.setdefault(sn, DeviceState())

    # ------------------------------------------------------------------

    async def serve(self, websocket: WebSocket) -> None:
        await websocket.accept()
        ip = websocket.client.host if websocket.client else None
        sn: Optional[str] = None
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON message from %s ignored", ip)
                    continue
                if not isinstance(payload, dict):
                    continue

                sn = payload.get("sn") or sn
                if not sn:
                    continue
                if not self.registry.is_connected(sn) or self.registry.connections[sn] is not websocket:
                    if not await self._accept_device(sn):
                        # No ACK on purpose: the device keeps its buffer and
                        # delivers it once it is enabled again.
                        await websocket.close(code=1008)
                        return
                    self.registry.connect(sn, websocket)

                await self._dispatch(websocket, sn, ip, payload, len(raw))
        except WebSocketDisconnect:
            pass
        finally:
            if sn:
                self.registry.disconnect(sn, websocket)
                await self.events.emit("device_disconnected", device_sn=sn)

    async def _accept_device(self, sn: str) -> bool:
        async with self.sessions() as session:
            device = await session.get(Device, sn)
            if device is not None and not device.is_active:
                logger.info("Device %s is disabled: connection refused (it keeps its buffer)", sn)
                return False
        return True

    async def _dispatch(self, ws: WebSocket, sn: str, ip: Optional[str], payload: dict, size: int) -> None:
        cmd = payload.get("cmd") or payload.get("ret")

        if cmd == "reg":
            await self._on_reg(ws, sn, ip, payload)
        elif cmd in LIVE_PUNCH_CMDS:
            await self._on_sendlog(ws, sn, payload, size)
        elif cmd in HISTORY_CMDS:
            await self._on_history_page(ws, sn, cmd, payload)
        elif cmd == "senduser":
            await self._on_senduser(ws, sn, payload)
        elif cmd == "getuserinfo":
            await self._on_userinfo(ws, sn, payload)
        else:
            await self._on_reply(sn, cmd, payload)

    # -- registration ----------------------------------------------------

    async def _on_reg(self, ws: WebSocket, sn: str, ip: Optional[str], payload: dict) -> None:
        devinfo = payload.get("devinfo") if isinstance(payload.get("devinfo"), dict) else {}
        async with self.sessions() as session:
            await touch_device(session, sn, ip, devinfo)
            await session.commit()
        await ws.send_text(json.dumps(protocol.reg_reply(sn, self.settings.try_seconds)))
        if self.settings.sync_device_time:
            await ws.send_text(json.dumps({"cmd": "settime", "cloudtime": protocol.cloud_time()}))
        logger.info("Device %s registered from %s", sn, ip)
        await self.events.emit("device_registered", device_sn=sn, ip=ip, devinfo=devinfo)

    # -- punches -----------------------------------------------------------

    async def _save_records(self, sn: str, records: List[dict]) -> List[Punch]:
        """Save a batch with one commit. Raises PunchNotSaved -> caller must not ACK."""
        new: List[Punch] = []
        photos_written: List[str] = []
        async with self.sessions() as session:
            try:
                for entry in records:
                    if not isinstance(entry, dict):
                        continue
                    # Explicit None checks: enrollid 0 is falsy but valid,
                    # and dropping it here would still ACK it (= lost).
                    raw_id = next((entry[k] for k in ("enrollid", "id", "pin")
                                   if entry.get(k) is not None and entry.get(k) != ""), None)
                    enrollid = "" if raw_id is None else str(raw_id)
                    when = protocol.parse_time(entry.get("time"))
                    if not enrollid or when is None:
                        logger.warning("%s: record without enrollid/time ignored: %s", sn,
                                       {k: v for k, v in entry.items() if k != "image"})
                        continue
                    mode = entry.get("mode")
                    punch = await save_punch(
                        session, sn, enrollid, when,
                        mode=mode if isinstance(mode, int) else None,
                        inout=entry.get("inout") if isinstance(entry.get("inout"), int) else 0,
                        name=entry.get("name") or "",
                        temperature=entry.get("temp"),
                        duplicate_window=self.settings.duplicate_window_seconds,
                    )
                    if punch is None:
                        continue      # duplicate: no photo, nothing to announce
                    if entry.get("image"):
                        path = self.photos.save(sn, enrollid, when, entry["image"])
                        if path:
                            punch.photo = path
                            photos_written.append(path)
                    new.append(punch)
                await session.commit()
            except BaseException:
                await session.rollback()
                for path in photos_written:
                    self.photos.delete(path)
                raise
        return new

    async def _on_sendlog(self, ws: WebSocket, sn: str, payload: dict, size: int) -> None:
        started = time.monotonic()
        records = payload.get("record")
        items = records if isinstance(records, list) else [payload]
        try:
            new = await self._save_records(sn, items)
        except PunchNotSaved:
            logger.error("%s: batch of %d NOT acknowledged; the device will resend it", sn, len(items))
            return
        await ws.send_text(json.dumps(protocol.sendlog_ack()))
        ms = (time.monotonic() - started) * 1000
        (logger.warning if ms > 5000 else logger.info)(
            "%s sendlog: %d records, %d new, ACK in %.0f ms (%d bytes)", sn, len(items), len(new), ms, size)
        for punch in new:
            await self.events.emit("punch", punch=punch, device_sn=sn)

    async def _on_history_page(self, ws: WebSocket, sn: str, cmd: str, payload: dict) -> None:
        records = payload.get("record") if isinstance(payload.get("record"), list) else []
        state = pagination.running(sn) or pagination.reserve(sn) or pagination.PageState()
        if state.pages == 0:
            logger.info("%s %s first page: %s", sn, cmd, {k: v for k, v in payload.items() if k != "record"})
        try:
            new = await self._save_records(sn, records)
        except PunchNotSaved:
            logger.error("%s %s page NOT acknowledged; the download stops here", sn, cmd)
            pagination.release(sn)
            return
        await ws.send_text(json.dumps(protocol.page_ack(cmd, sn)))

        total, delivered = payload.get("count"), payload.get("to")
        more, reason = state.next(
            records=len(records), saved=len(new),
            total=total if isinstance(total, int) else None,
            delivered=delivered if isinstance(delivered, int) else None,
        )
        logger.info("%s %s page %d: %d records, %d new", sn, cmd, state.pages, len(records), len(new))
        for punch in new:
            await self.events.emit("punch", punch=punch, device_sn=sn)
        if more:
            await asyncio.sleep(pagination.PAUSE_BETWEEN_PAGES)
            await ws.send_text(json.dumps(protocol.next_page(cmd)))
        else:
            logger.info("%s %s finished after %d pages (%d new): %s", sn, cmd, state.pages, state.saved, reason)
            pagination.release(sn)
            await self.events.emit("download_finished", device_sn=sn, state=state, reason=reason)

    # -- users -------------------------------------------------------------

    async def _on_senduser(self, ws: WebSocket, sn: str, payload: dict) -> None:
        enrollid, name = payload.get("enrollid"), payload.get("name") or ""
        if enrollid is not None:
            async with self.sessions() as session:
                await upsert_employee(session, sn, str(enrollid), name)
                await session.commit()
            await self.events.emit("user_pushed", device_sn=sn, enrollid=str(enrollid), name=name)
        await ws.send_text(json.dumps(protocol.senduser_ack(sn)))

    async def _on_userinfo(self, ws: WebSocket, sn: str, payload: dict) -> None:
        enrollid = payload.get("enrollid")
        if payload.get("result") and enrollid is not None:
            async with self.sessions() as session:
                await upsert_employee(session, sn, str(enrollid), payload.get("name") or "")
                await session.commit()
        await ws.send_text(json.dumps(protocol.page_ack("getuserinfo", sn)))
        await self._on_reply(sn, "getuserinfo", payload)

    # -- answers to our commands ---------------------------------------------

    async def _on_reply(self, sn: str, cmd: Optional[str], payload: dict) -> None:
        st = self.device_state(sn)
        clean = {k: v for k, v in payload.items() if k not in ("record", "image")}
        if cmd == "getdevinfo" and payload.get("result", True):
            st.devinfo = {k: v for k, v in payload.items() if k not in ("ret", "sn", "result")}
            st.devinfo_at = datetime.now()
        elif cmd == "getdevcap":
            inv = inventory.parse(payload)
            async with self.sessions() as session:
                stored = await count_punches(session, sn)
            level, message = inventory.compare(inv, stored)
            st.inventory = {"level": level, "message": message, "stored": stored,
                            **(inv.__dict__ if inv else {}), "at": datetime.now().isoformat()}
            logger.info("%s inventory [%s] %s", sn, level, message)
        if cmd:
            st.replies[cmd] = {"at": datetime.now().isoformat(), "payload": clean}
            logger.info("%s <- %s %s", sn, cmd, json.dumps(clean, ensure_ascii=False)[:500])
            await self.events.emit("reply", device_sn=sn, cmd=cmd, payload=payload)
