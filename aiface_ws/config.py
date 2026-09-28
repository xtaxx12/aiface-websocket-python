"""
Settings, read from environment variables (prefix ``AIFACE_``) or a ``.env`` file.

Every value has a safe default so ``python -m aiface_ws`` works out of the
box with SQLite. For production use PostgreSQL and set ``AIFACE_API_TOKEN``.
"""

from datetime import date
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: `AIFACE_RELAY_SINCE=` (empty, as in .env.example)
    # means "not set", not an invalid date.
    model_config = SettingsConfigDict(
        env_prefix="AIFACE_", env_file=".env", extra="ignore", env_ignore_empty=True,
    )

    # -- server ---------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./aiface.db"
    host: str = "0.0.0.0"
    port: int = 8000
    # Path the terminals connect to. It is what you type in the device's
    # "Server" menu; AiFace firmware defaults to /pub/chat.
    ws_path: str = "/pub/chat"

    # -- device protocol ------------------------------------------------
    # Sent to the device in the `reg` reply: seconds between reconnection
    # attempts after the socket drops.
    try_seconds: int = 120
    # Two punches of the same person on the same device closer than this
    # are one punch (people touch the screen twice). 0 disables it.
    duplicate_window_seconds: int = 60
    # Push the server clock to the device on every registration.
    sync_device_time: bool = True

    # -- REST API / dashboard -------------------------------------------
    # When set, every /api request must send `Authorization: Bearer <token>`.
    # The device WebSocket never needs it (the firmware cannot send it).
    api_token: Optional[str] = None
    # Punch photos are biometric data. When true they are never served to
    # requests that came through a reverse proxy or tunnel (Cloudflare,
    # nginx with X-Forwarded-For), only to direct local/LAN requests.
    photos_local_only: bool = True
    photos_dir: str = "data/photos"

    # -- webhook relay --------------------------------------------------
    relay_url: Optional[str] = None
    relay_token: Optional[str] = None
    relay_token_header: str = "X-Relay-Token"
    # Comma separated serial numbers. Empty = every device.
    relay_devices: str = ""
    # Punches before this date are kept but never relayed.
    relay_since: Optional[date] = None
    relay_interval: int = 60
    relay_batch_size: int = 500
    relay_max_batches: int = 20
    relay_timeout: float = 30.0

    @property
    def relay_device_list(self) -> List[str]:
        return [s.strip() for s in self.relay_devices.split(",") if s.strip()]

    @property
    def relay_enabled(self) -> bool:
        return bool(self.relay_url)


settings = Settings()
