"""
python -m aiface_ws

Starts the server with the WebSocket keepalive settings AiFace terminals need.
"""

import logging

import uvicorn

from aiface_ws.config import Settings


def main() -> None:
    settings = Settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    uvicorn.run(
        "aiface_ws.app:create_app", factory=True,
        host=settings.host, port=settings.port,
        # The terminals do not answer WebSocket pings the way uvicorn
        # expects. With the default 20 s ping timeout uvicorn closes every
        # device socket over and over, and each reconnection is a window
        # where punches can be lost. Keepalive is left to the device.
        ws_ping_interval=3600, ws_ping_timeout=3600,
    )


if __name__ == "__main__":
    main()
