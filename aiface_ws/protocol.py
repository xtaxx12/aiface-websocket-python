"""
The replies the terminal expects, byte for byte where it matters.

The device talks JSON over one WebSocket it opens itself. A message is a
request when it carries `cmd` and a reply when it carries `ret`. See
docs/PROTOCOL.md for the field notes behind each of these.
"""

import time
from datetime import datetime
from typing import Optional

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# backupnum values (what kind of credential a user record carries)
BACKUP_PASSWORD = 10
BACKUP_CARD = 11
BACKUP_FACE_PHOTO = 50
BACKUP_ALL = 13          # deleteuser: remove the whole user
# 0-9 are fingerprints

# Verification modes seen in punch records
MODES = {1: "fingerprint", 2: "password", 3: "card", 8: "face", 15: "palm"}


def cloud_time() -> str:
    return time.strftime(TIME_FORMAT)


def parse_time(value: object) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value), TIME_FORMAT)
    except ValueError:
        return None


def reg_reply(sn: str, try_seconds: int) -> dict:
    """
    Answer to `{"cmd":"reg", ...}`.

    `nosenduser: True` tells the device not to push its user list on every
    connection. `nosendlog: 0` lets it push pending punches right away
    (that is how a device delivers what it stored while offline).
    """
    return {
        "ret": "reg", "sn": sn, "result": True, "cloudtime": cloud_time(),
        "tryseconds": try_seconds, "nosenduser": True, "nosendlog": 0,
    }


def sendlog_ack() -> dict:
    """
    ACK for a batch of live punches. Exactly these four fields, as in the
    vendor's CloudDemo.

    Do NOT echo `logindex`, `count` or `sn` back: a terminal treated the
    echoed logindex as "resend from here" and replayed its whole history in
    a loop.
    """
    return {"ret": "sendlog", "result": True, "cloudtime": cloud_time(), "access": 1}


def page_ack(cmd: str, sn: str) -> dict:
    """ACK for one page of getalllog / getnewlog / getuserlist."""
    return {"ret": cmd, "sn": sn, "result": True}


def next_page(cmd: str) -> dict:
    """Ask for the next page. `stn: False` = continue; True would restart."""
    return {"cmd": cmd, "stn": False}


def senduser_ack(sn: str) -> dict:
    return {"ret": "senduser", "sn": sn, "result": True, "cloudtime": cloud_time()}
