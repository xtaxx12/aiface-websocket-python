"""
aiface-websocket-python: WebSocket server and device SDK for AiFace
face-recognition attendance terminals, built on FastAPI.
"""

from aiface_ws.app import create_app
from aiface_ws.commands import Commands
from aiface_ws.config import Settings

__all__ = ["create_app", "Commands", "Settings"]
__version__ = "0.1.0"
