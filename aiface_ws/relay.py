"""
Webhook relay: forward raw punches to any HTTP endpoint (payroll, ERP...).

How it works
- A cursor per (device, destination host) holds the last punch id the
  webhook confirmed. It walks `Punch.id` (insertion order), not the punch
  time, so history recovered later is still delivered.
- Every `relay_interval` seconds: take punches with id > cursor, POST them,
  and advance the cursor ONLY on a 2xx. Network error or non-2xx: the
  cursor stays and the same batch is retried next cycle. Nothing is lost.
- Punches before `relay_since` are skipped (kept in the database) and the
  cursor moves past them, or it would stall on them forever.
- Resending is harmless as long as the receiver deduplicates by
  (sn, enrollid, time), which it should: that triple is unique here too.

Payload (POST, JSON):

    {"sn": "SERIAL", "ip": "10.0.0.5", "last_id": 1234,
     "records": [{"enrollid": 7, "time": "2026-09-21 07:03:11",
                  "mode": 8, "inout": 0, "temperature": null}]}

Header: `<relay_token_header>: <relay_token>` when a token is configured.
Photos are never relayed.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from aiface_ws.config import Settings
from aiface_ws.models import Device, Punch, RelayCursor

logger = logging.getLogger(__name__)


def destination(url: Optional[str]) -> str:
    return urlparse(url or "").netloc or "none"


def _record(p: Punch) -> Dict[str, Any]:
    enrollid: Any = int(p.enrollid) if p.enrollid.isdigit() else p.enrollid
    return {"enrollid": enrollid, "time": p.time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": p.mode, "inout": p.inout, "temperature": p.temperature}


class Relay:
    def __init__(self, settings: Settings, session_factory,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.settings = settings
        self.sessions = session_factory
        self._transport = transport          # tests inject a MockTransport

    @property
    def dest(self) -> str:
        return destination(self.settings.relay_url)

    async def cursor(self, session: AsyncSession, sn: str) -> RelayCursor:
        cur = await session.get(RelayCursor, (sn, self.dest))
        if cur is None:
            # A new destination starts at 0 on purpose: better to have to
            # position it (POST /api/relay/cursor) than to skip punches.
            cur = RelayCursor(device_sn=sn, destination=self.dest, last_id=0)
            session.add(cur)
            await session.flush()
        return cur

    async def pending(self, session: AsyncSession, sn: str) -> int:
        cur = await self.cursor(session, sn)
        return int((await session.execute(
            select(func.count(Punch.id)).where(Punch.device_sn == sn, Punch.id > cur.last_id)
        )).scalar() or 0)

    async def devices(self, session: AsyncSession) -> list[str]:
        if self.settings.relay_device_list:
            return self.settings.relay_device_list
        return list((await session.execute(select(Device.sn))).scalars())

    async def send_batch(self, sn: str) -> Dict[str, Any]:
        s = self.settings
        if not s.relay_enabled:
            return {"status": "disabled"}
        async with self.sessions() as session:
            cur = await self.cursor(session, sn)
            punches = list((await session.execute(
                select(Punch).where(Punch.device_sn == sn, Punch.id > cur.last_id)
                .order_by(Punch.id).limit(min(s.relay_batch_size, 1000))
            )).scalars())
            if not punches:
                await session.commit()
                return {"status": "empty", "sn": sn}

            to_send = [p for p in punches if not (s.relay_since and p.time.date() < s.relay_since)]
            skipped = len(punches) - len(to_send)
            if to_send:
                device = await session.get(Device, sn)
                body = {"sn": sn, "ip": device.ip_address if device else None,
                        "last_id": punches[-1].id, "records": [_record(p) for p in to_send]}
                headers = {s.relay_token_header: s.relay_token} if s.relay_token else {}
                try:
                    async with httpx.AsyncClient(timeout=s.relay_timeout, transport=self._transport) as client:
                        resp = await client.post(s.relay_url, json=body, headers=headers)
                except httpx.HTTPError as e:
                    logger.error("Relay %s: network error, will retry: %s", sn, e)
                    return {"status": "network_error", "sn": sn, "error": str(e)}
                if not 200 <= resp.status_code < 300:
                    logger.error("Relay %s: webhook answered %d, will retry: %s", sn, resp.status_code, resp.text[:300])
                    return {"status": "http_error", "sn": sn, "http": resp.status_code}

            cur.last_id = punches[-1].id
            cur.updated_at = datetime.now()
            session.add(cur)
            await session.commit()
        logger.info("Relay %s: sent %d, skipped %d (before %s), cursor=%d",
                    sn, len(to_send), skipped, s.relay_since, cur.last_id)
        return {"status": "ok", "sn": sn, "sent": len(to_send), "skipped": skipped, "cursor": cur.last_id}

    async def run_forever(self) -> None:
        s = self.settings
        logger.info("Relay active -> %s (devices: %s)", s.relay_url, s.relay_devices or "all")
        while True:
            try:
                async with self.sessions() as session:
                    sns = await self.devices(session)
                for sn in sns:
                    for _ in range(s.relay_max_batches):
                        r = await self.send_batch(sn)
                        if r.get("status") != "ok" or r.get("sent", 0) + r.get("skipped", 0) < s.relay_batch_size:
                            break
            except Exception:  # noqa: BLE001 - the loop must survive anything
                logger.exception("Relay cycle failed")
            await asyncio.sleep(s.relay_interval)
