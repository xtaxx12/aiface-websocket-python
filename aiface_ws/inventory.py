"""
What the device says it holds (`getdevcap`).

Answer from a real terminal (numbers rounded):

    {"ret":"getdevcap","result":true,"usersize":15000,"logsize":500000,
     "useduser":150,"usedlog":80000,"usednewlog":200, ...}

- `usedlog` vs what we stored tells whether we are missing history.
- `usedlog` vs `logsize` warns before the memory fills up and the device
  starts overwriting its oldest punches.
- `usednewlog` is NOT a reliable "undelivered" counter: it was seen at 470
  with every punch already stored. Verify a day with a `getalllog` for that
  date instead of trusting it.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

MEMORY_WARNING = 0.90


@dataclass(frozen=True)
class Inventory:
    users: int
    punches: int
    new_punches: int
    capacity: int


def parse(payload: dict) -> Optional[Inventory]:
    if payload.get("ret") != "getdevcap" or not payload.get("result"):
        return None

    def num(key: str) -> int:
        value = payload.get(key)
        return value if isinstance(value, int) else 0

    return Inventory(users=num("useduser"), punches=num("usedlog"),
                     new_punches=num("usednewlog"), capacity=num("logsize"))


def compare(inv: Optional[Inventory], stored: int) -> Tuple[str, str]:
    """(level, message). Levels: memory > missing > ok."""
    if inv is None:
        return "ok", "The device did not report its inventory."
    if inv.capacity > 0 and inv.punches / inv.capacity >= MEMORY_WARNING:
        return "memory", (f"Device memory at {inv.punches / inv.capacity:.0%} "
                          f"({inv.punches} of {inv.capacity}). Download and clean it.")
    missing = inv.punches - stored
    if missing > 0:
        return "missing", f"The device holds {inv.punches} punches and we stored {stored}: {missing} missing."
    return "ok", f"Up to date: {inv.punches} punches, {inv.users} users."
