"""
REST API used by the dashboard and by your own code.

All routes live under /api. When `AIFACE_API_TOKEN` is set they require
`Authorization: Bearer <token>`. Device commands are fire-and-forget: the
answer arrives later on the device socket and can be read from
GET /api/devices/{sn}/replies.
"""

import hmac
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlmodel import func, select

from aiface_ws import pagination
from aiface_ws.commands import Commands
from aiface_ws.models import Device, Employee, Punch, RelayCursor


def get_ctx(request: Request):
    return request.app.state.aiface


def require_token(request: Request) -> None:
    token = get_ctx(request).settings.api_token
    if not token:
        return
    sent = request.headers.get("authorization", "")
    if not hmac.compare_digest(sent, f"Bearer {token}"):
        raise HTTPException(401, "Missing or wrong API token", headers={"WWW-Authenticate": "Bearer"})


def via_proxy(request: Request) -> bool:
    """True when the request crossed a tunnel/reverse proxy (Cloudflare, nginx...)."""
    h = request.headers
    return any(k in h for k in ("cf-ray", "cf-connecting-ip", "x-forwarded-for", "forwarded"))


router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _connected_or_503(ctx, sn: str) -> None:
    if not ctx.registry.is_connected(sn):
        raise HTTPException(503, f"{sn} has no open WebSocket session")


async def _send_or_502(ctx, sn: str, command: dict) -> dict:
    _connected_or_503(ctx, sn)
    if not await ctx.registry.send(sn, command):
        raise HTTPException(502, "Could not send the command")
    return {"status": "sent", "command": command.get("cmd"),
            "note": f"The answer arrives asynchronously: GET /api/devices/{sn}/replies"}


# -- devices ---------------------------------------------------------------

@router.get("/devices")
async def list_devices(ctx=Depends(get_ctx)):
    async with ctx.sessions() as session:
        devices = list((await session.execute(select(Device).order_by(Device.sn))).scalars())
    return [{**d.model_dump(), "connected": ctx.registry.is_connected(d.sn)} for d in devices]


@router.patch("/devices/{sn}")
async def update_device(sn: str, changes: dict, ctx=Depends(get_ctx)):
    """{"name": "...", "is_active": false}. Disabling refuses the device WITHOUT ACK."""
    async with ctx.sessions() as session:
        device = await session.get(Device, sn)
        if device is None:
            raise HTTPException(404, "Unknown device")
        for key in ("name", "is_active"):
            if key in changes:
                setattr(device, key, changes[key])
        session.add(device)
        await session.commit()
        return device.model_dump()


@router.post("/devices/{sn}/command")
async def raw_command(sn: str, command: dict, ctx=Depends(get_ctx)):
    """Send any command as-is, e.g. {"cmd": "getdevcap"}. For exploring a firmware."""
    if "cmd" not in command:
        raise HTTPException(400, 'The body needs a "cmd" field')
    return await _send_or_502(ctx, sn, command)


@router.get("/devices/{sn}/replies")
async def last_replies(sn: str, ctx=Depends(get_ctx)):
    return ctx.handler.device_state(sn).replies


@router.post("/devices/{sn}/devinfo/refresh")
async def refresh_devinfo(sn: str, ctx=Depends(get_ctx)):
    return await _send_or_502(ctx, sn, Commands.get_dev_info())


@router.get("/devices/{sn}/devinfo")
async def get_devinfo(sn: str, ctx=Depends(get_ctx)):
    st = ctx.handler.device_state(sn)
    if not st.devinfo:
        raise HTTPException(404, f"No getdevinfo yet: POST /api/devices/{sn}/devinfo/refresh")
    return {"at": st.devinfo_at.isoformat(), "devinfo": st.devinfo}


@router.patch("/devices/{sn}/devinfo")
async def set_devinfo(sn: str, changes: dict, ctx=Depends(get_ctx)):
    """
    Change config fields, e.g. {"use_logphoto": 1}. Sends the FULL config
    from a getdevinfo of the last 10 minutes with only these changes.
    Verify afterwards with another refresh.
    """
    st = ctx.handler.device_state(sn)
    if not st.devinfo or datetime.now() - st.devinfo_at > timedelta(minutes=10):
        raise HTTPException(409, f"Refresh first: POST /api/devices/{sn}/devinfo/refresh")
    try:
        command = Commands.set_dev_info(st.devinfo, **changes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    result = await _send_or_502(ctx, sn, command)
    result["changes"] = {k: {"before": st.devinfo[k], "after": v} for k, v in changes.items()}
    return result


@router.post("/devices/{sn}/inventory/refresh")
async def refresh_inventory(sn: str, ctx=Depends(get_ctx)):
    return await _send_or_502(ctx, sn, Commands.get_dev_cap())


@router.get("/devices/{sn}/inventory")
async def get_inventory(sn: str, ctx=Depends(get_ctx)):
    inv = ctx.handler.device_state(sn).inventory
    if inv is None:
        raise HTTPException(404, f"No getdevcap yet: POST /api/devices/{sn}/inventory/refresh")
    return inv


@router.post("/devices/{sn}/logs")
async def download_logs(sn: str, since: Optional[date] = None, until: Optional[date] = None,
                        new_only: bool = False, ctx=Depends(get_ctx)):
    """Download history from the device (getalllog by date, or getnewlog). One at a time."""
    _connected_or_503(ctx, sn)
    state = pagination.reserve(sn)
    if state is None:
        raise HTTPException(409, f"{sn} is already downloading (page {pagination.running(sn).pages})."
                                 " A second request would reset the device cursor.")
    command = Commands.get_new_log() if new_only else Commands.get_all_log(since, until)
    if not await ctx.registry.send(sn, command):
        pagination.release(sn)
        raise HTTPException(502, "Could not send the command")
    return {"status": "started", "command": command}


@router.get("/devices/{sn}/logs/status")
async def download_status(sn: str):
    st = pagination.running(sn)
    return {"downloading": st is not None,
            **({"pages": st.pages, "saved": st.saved, "total": st.total} if st else {})}


# -- punches ---------------------------------------------------------------

@router.get("/punches")
async def list_punches(request: Request, sn: Optional[str] = None, since: Optional[date] = None,
                       until: Optional[date] = None, q: Optional[str] = None,
                       page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=500),
                       ctx=Depends(get_ctx)):
    """Raw punches, newest first, with the employee name and a photo URL when there is one."""
    stmt = select(Punch, Employee.name).outerjoin(
        Employee, (Employee.device_sn == Punch.device_sn) & (Employee.enrollid == Punch.enrollid))
    if sn:
        stmt = stmt.where(Punch.device_sn == sn)
    if since:
        stmt = stmt.where(Punch.time >= datetime.combine(since, datetime.min.time()))
    if until:
        stmt = stmt.where(Punch.time < datetime.combine(until + timedelta(days=1), datetime.min.time()))
    if q:
        stmt = stmt.where(or_(Employee.name.ilike(f"%{q}%"), Punch.enrollid == q))
    async with ctx.sessions() as session:
        total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
        rows = (await session.execute(
            stmt.order_by(Punch.time.desc()).offset((page - 1) * per_page).limit(per_page))).all()
    hide = ctx.settings.photos_local_only and via_proxy(request)
    return {
        "total": total, "page": page, "per_page": per_page,
        "data": [{**p.model_dump(exclude={"photo"}), "name": name or "",
                  "photo_url": None if hide or not p.photo else f"/api/punches/{p.id}/photo"}
                 for p, name in rows],
    }


@router.get("/punches/{punch_id}/photo")
async def punch_photo(punch_id: int, request: Request, ctx=Depends(get_ctx)):
    if ctx.settings.photos_local_only and via_proxy(request):
        raise HTTPException(403, "Photos are only served to local requests (photos_local_only)")
    async with ctx.sessions() as session:
        punch = await session.get(Punch, punch_id)
    path = ctx.photos.file(punch.photo if punch else None)
    if path is None:
        raise HTTPException(404, "This punch has no photo")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# -- relay -----------------------------------------------------------------

@router.get("/relay/status")
async def relay_status(ctx=Depends(get_ctx)):
    s = ctx.settings
    out = {"enabled": s.relay_enabled, "destination": ctx.relay.dest, "since": s.relay_since, "devices": []}
    if s.relay_enabled:
        async with ctx.sessions() as session:
            for sn in await ctx.relay.devices(session):
                cur = await ctx.relay.cursor(session, sn)
                out["devices"].append({"sn": sn, "cursor": cur.last_id,
                                       "pending": await ctx.relay.pending(session, sn)})
            await session.commit()
    return out


@router.post("/relay/cursor")
async def move_cursor(sn: str, start: str = Query(..., pattern=r"^(now|all|\d{4}-\d{2}-\d{2})$"),
                      ctx=Depends(get_ctx)):
    """
    Where the relay starts for a device: `now` (only future punches), `all`
    (resend everything) or a date (resend from that day on).
    """
    async with ctx.sessions() as session:
        cur = await ctx.relay.cursor(session, sn)
        before = cur.last_id
        if start == "all":
            cur.last_id = 0
        else:
            stmt = select(func.max(Punch.id)).where(Punch.device_sn == sn)
            if start != "now":
                stmt = stmt.where(Punch.time < datetime.fromisoformat(start))
            cur.last_id = (await session.execute(stmt)).scalar() or 0
        session.add(cur)
        await session.commit()
        pending = await ctx.relay.pending(session, sn)
    return {"sn": sn, "before": before, "after": cur.last_id, "pending": pending}


@router.post("/relay/send")
async def relay_now(sn: str, ctx=Depends(get_ctx)):
    """Send one batch now instead of waiting for the next cycle."""
    result = await ctx.relay.send_batch(sn)
    if result["status"] in ("network_error", "http_error"):
        raise HTTPException(502, result)
    return result
