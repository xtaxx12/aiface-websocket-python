"""
One live WebSocket per device serial.

A terminal can hold two sockets for an instant: it opens the new one before
the old one finishes closing. Removing by serial when the OLD socket closes
would unbind the NEW one, and every command sent in that gap fails with
"not connected". So a socket only unbinds the serial if the map still points
at that same socket (same rule as ConnectionRegistry::remove in
laravel-aiface-websocket).
"""

import json
import logging
from typing import Dict, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionRegistry:
    def __init__(self) -> None:
        self.connections: Dict[str, WebSocket] = {}

    def connect(self, sn: str, websocket: WebSocket) -> None:
        previous = self.connections.get(sn)
        self.connections[sn] = websocket
        if previous is not websocket:
            logger.info("Device %s connected", sn)

    def disconnect(self, sn: str, websocket: Optional[WebSocket] = None) -> None:
        bound = self.connections.get(sn)
        if bound is None:
            return
        if websocket is not None and bound is not websocket:
            logger.debug("Device %s: an old socket closed, still bound to a newer one", sn)
            return
        del self.connections[sn]
        logger.info("Device %s disconnected", sn)

    def is_connected(self, sn: str) -> bool:
        return sn in self.connections

    async def send(self, sn: str, command: dict) -> bool:
        websocket = self.connections.get(sn)
        if websocket is None:
            logger.warning("Command to %s but it is not connected", sn)
            return False
        try:
            await websocket.send_text(json.dumps(command))
            logger.info("-> %s %s", sn, command.get("cmd"))
            return True
        except Exception as e:  # noqa: BLE001 - reported and unbound
            logger.error("Could not send to %s: %s", sn, e)
            self.disconnect(sn, websocket)
            return False
