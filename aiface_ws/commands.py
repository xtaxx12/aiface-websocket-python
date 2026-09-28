"""
Command builder: server -> device messages.

Each method returns the JSON dict to send with ``registry.send(sn, cmd)``.
Answers come back asynchronously on the device socket and are handled in
handler.py.

Tags in the docstrings:
  [verified] tested against real AiFace terminals
  [docs]     from the protocol docs / laravel-aiface-websocket, not tested here
"""

from datetime import date, datetime
from typing import Optional

from aiface_ws.protocol import BACKUP_ALL, cloud_time


class Commands:
    # -- punches --------------------------------------------------------

    @staticmethod
    def get_all_log(since: Optional[date] = None, until: Optional[date] = None,
                    log_photo: bool = False) -> dict:
        """
        [verified] Every punch the device holds, paged, optionally by date.

        `stn: true` restarts the device cursor, so only one download per
        device at a time (pagination.reserve). `page`/`pagesize` are
        ignored by the firmware we tested: pages are always 20 records.
        """
        cmd = {"cmd": "getalllog", "stn": True,
               "from": str(since or "2000-01-01"), "to": str(until or "2099-12-31")}
        if log_photo:
            cmd["logphoto"] = 1
        return cmd

    @staticmethod
    def get_new_log() -> dict:
        """[verified] Only the punches the device has not marked as delivered."""
        return {"cmd": "getnewlog", "stn": True}

    @staticmethod
    def clean_log() -> dict:
        """[docs] Delete ALL punches from the device. Irreversible."""
        return {"cmd": "cleanlog"}

    @staticmethod
    def clean_log_photo() -> dict:
        """[docs] Delete the stored punch photos from the device memory."""
        return {"cmd": "cleanlogphoto"}

    # -- device ---------------------------------------------------------

    @staticmethod
    def get_dev_info() -> dict:
        """[verified] The full configuration (~175 fields), top level in the reply."""
        return {"cmd": "getdevinfo"}

    @staticmethod
    def set_dev_info(current: dict, **changes) -> dict:
        """
        [verified] Change configuration fields.

        Send the FULL current configuration (from the last getdevinfo) with
        only your changes, and with the fields at the TOP LEVEL of the
        message. Nested under "devinfo" the terminal answered result=true
        and applied nothing. A partial message is untested: a missing field
        could be reset to 0 (IP address, server address...).

            Commands.set_dev_info(last_devinfo, use_logphoto=1)
        """
        unknown = set(changes) - set(current)
        if unknown:
            raise ValueError(f"The device does not report these fields: {sorted(unknown)}")
        return {"cmd": "setdevinfo", **current, **changes}

    @staticmethod
    def get_dev_cap() -> dict:
        """[verified] Capacity and usage: useduser, usedlog, logsize... (inventory.py)."""
        return {"cmd": "getdevcap"}

    @staticmethod
    def set_time(when: Optional[datetime] = None) -> dict:
        """[verified] Set the device clock (local time, no timezone)."""
        return {"cmd": "settime", "cloudtime": when.strftime("%Y-%m-%d %H:%M:%S") if when else cloud_time()}

    @staticmethod
    def reboot() -> dict:
        """[docs]"""
        return {"cmd": "reboot"}

    @staticmethod
    def enable_device() -> dict:
        """[docs] Resume recognition."""
        return {"cmd": "enabledevice"}

    @staticmethod
    def disable_device() -> dict:
        """[docs] Stop recognition (the screen shows it is disabled)."""
        return {"cmd": "disabledevice"}

    @staticmethod
    def open_door() -> dict:
        """[docs] Trigger the lock relay."""
        return {"cmd": "opendoor"}

    # -- users ----------------------------------------------------------

    @staticmethod
    def get_user_list(restart: bool = True) -> dict:
        """[docs] Paged list of enrollid + backupnum (no names)."""
        return {"cmd": "getuserlist", "stn": restart}

    @staticmethod
    def get_user_info(enrollid: int, backupnum: int = 0) -> dict:
        """[verified] One user record: name + the credential in `record`."""
        return {"cmd": "getuserinfo", "enrollid": int(enrollid), "backupnum": backupnum}

    @staticmethod
    def set_user_info(enrollid: int, name: str, backupnum: int, record: object,
                      admin: int = 0, enable: int = 1) -> dict:
        """[docs] Create/update a user credential (backupnum 50 = face JPG base64)."""
        return {"cmd": "setuserinfo", "enrollid": int(enrollid), "name": name,
                "backupnum": backupnum, "admin": admin, "record": record, "enable": enable}

    @staticmethod
    def delete_user(enrollid: int, backupnum: int = BACKUP_ALL) -> dict:
        """[docs] Delete a user (13) or only one credential (0-9, 10, 11, 50)."""
        return {"cmd": "deleteuser", "enrollid": int(enrollid), "backupnum": backupnum}

    @staticmethod
    def enable_user(enrollid: int, enable: bool = True) -> dict:
        """[docs]"""
        return {"cmd": "enableuser", "enrollid": int(enrollid), "enable": 1 if enable else 0}
