"""
Punch photos.

With `use_logphoto=1` in the device config (see commands.set_dev_info) every
`sendlog` record carries the picture the terminal took, as base64 JPG in the
`image` field (~25 KB). They are stored as files, not in the database:

    <photos_dir>/<serial>/<YYYY-MM-DD>/<enrollid>_<HHMMSS>.jpg

Faces are biometric personal data in most jurisdictions (GDPR art. 9 and
its equivalents). Get the consent your law requires, define a retention
period, and keep `photos_local_only` on if the dashboard is exposed.
"""

import base64
import binascii
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_JPEG = b"\xff\xd8"


class PhotoStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def relative_path(sn: str, enrollid: str, when: datetime) -> str:
        return f"{sn}/{when:%Y-%m-%d}/{enrollid}_{when:%H%M%S}.jpg"

    def save(self, sn: str, enrollid: str, when: datetime, image_b64: str) -> Optional[str]:
        """Store the photo and return its relative path, or None. Never raises."""
        if not image_b64:
            return None
        try:
            data = base64.b64decode(image_b64.split(",", 1)[-1], validate=False)
        except (binascii.Error, ValueError):
            logger.warning("Photo %s/%s @ %s: invalid base64", sn, enrollid, when)
            return None
        if not data.startswith(_JPEG):
            logger.warning("Photo %s/%s @ %s: not a JPEG (%d bytes)", sn, enrollid, when, len(data))
            return None
        relative = self.relative_path(sn, enrollid, when)
        target = self.root / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as e:
            logger.error("Could not write %s: %s", target, e)
            return None
        return relative

    def file(self, relative: Optional[str]) -> Optional[Path]:
        """Absolute path of a stored photo, refusing anything outside root."""
        if not relative:
            return None
        root = self.root.resolve()
        target = (self.root / relative).resolve()
        if root not in target.parents or not target.is_file():
            return None
        return target

    def delete(self, relative: Optional[str]) -> None:
        target = self.file(relative)
        if target is not None:
            try:
                target.unlink()
            except OSError as e:
                logger.warning("Could not delete %s: %s", target, e)
