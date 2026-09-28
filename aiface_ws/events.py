"""
Tiny async event bus, the equivalent of the Laravel events in the PHP package.

    from aiface_ws import create_app

    app = create_app()

    @app.state.aiface.events.on("punch")
    async def notify(punch, device_sn):
        print(device_sn, punch.enrollid, punch.time)

Events:
  device_registered(device_sn, ip, devinfo)
  device_disconnected(device_sn)
  punch(punch, device_sn)              after it is committed
  user_pushed(device_sn, enrollid, name)
  download_finished(device_sn, state, reason)
  reply(device_sn, cmd, payload)       any answer to a command we sent

A failing listener is logged and never breaks the device connection.
"""

import logging
from collections import defaultdict
from typing import Awaitable, Callable, DefaultDict, List

logger = logging.getLogger(__name__)

Listener = Callable[..., Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._listeners: DefaultDict[str, List[Listener]] = defaultdict(list)

    def on(self, event: str) -> Callable[[Listener], Listener]:
        def register(fn: Listener) -> Listener:
            self._listeners[event].append(fn)
            return fn
        return register

    async def emit(self, event: str, **data) -> None:
        for fn in self._listeners.get(event, []):
            try:
                await fn(**data)
            except Exception:  # noqa: BLE001 - listeners must not break the device
                logger.exception("Listener %s for %r failed", getattr(fn, "__name__", fn), event)
