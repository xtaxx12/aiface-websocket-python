"""
When to stop asking a device for history pages.

`getalllog` / `getnewlog` are paged: the device sends a page, we ACK it and
ask for the next one with `{"cmd": ..., "stn": false}`. Deciding when to
stop is harder than it looks:

- `count` is the TOTAL, not what is left. One device answered 11516 on
  three pages in a row. "Continue while count > 0" never ends, and asking
  without a pause starves the device, which still has to recognise faces.
- The good signal is the pair `count` (total) + `to` (how far it got):
  `{"count": 196, "from": 0, "to": 20}` on the first page. Done when to >= count.
- Safety nets for firmwares that do not send them: an empty page ends it,
  three pages in a row with nothing new ends it, and a hard page cap.

Only one download per device at a time: every request carries
`stn: true`, which RESETS the device cursor. A second request in the middle
of the first one restarts the device on the new range and the first one ends
up counting someone else's total.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

MAX_PAGES = 2000
MAX_PAGES_WITHOUT_NEW = 3
PAUSE_BETWEEN_PAGES = 0.2
# A download that stopped receiving pages this long ago is considered dead,
# so a device that disconnects mid-download is not locked forever.
ABANDON_AFTER = 180.0


def _now() -> float:
    return time.monotonic()


@dataclass
class PageState:
    pages: int = 0
    without_new: int = 0
    saved: int = 0
    total: Optional[int] = None
    last_activity: float = field(default_factory=lambda: _now())

    def next(self, records: int, saved: int, total: Optional[int] = None,
             delivered: Optional[int] = None) -> Tuple[bool, str]:
        """Register the page just received. Returns (ask_for_more, reason)."""
        self.pages += 1
        self.saved += saved
        self.total = total if total is not None else self.total
        self.last_activity = _now()

        if records == 0:
            return False, "the device sent no more records"

        if total is not None and delivered is not None and total >= 0:
            if delivered >= total:
                return False, f"the device delivered the {total} it reported"
            return True, ""

        if saved == 0:
            self.without_new += 1
            if self.without_new >= MAX_PAGES_WITHOUT_NEW:
                return False, f"{self.without_new} pages in a row with nothing new"
        else:
            self.without_new = 0

        if self.pages >= MAX_PAGES:
            return False, f"page cap of {MAX_PAGES} reached"
        return True, ""


_RUNNING: Dict[str, PageState] = {}


def _expired(state: PageState) -> bool:
    return _now() - state.last_activity > ABANDON_AFTER


def reserve(sn: str) -> Optional[PageState]:
    """Lock the device for a download, or None if one is already running."""
    current = _RUNNING.get(sn)
    if current is not None and not _expired(current):
        return None
    state = PageState()
    _RUNNING[sn] = state
    return state


def running(sn: str) -> Optional[PageState]:
    current = _RUNNING.get(sn)
    if current is None or _expired(current):
        return None
    return current


def release(sn: str) -> None:
    _RUNNING.pop(sn, None)
