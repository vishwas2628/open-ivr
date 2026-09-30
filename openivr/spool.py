"""Moving finished Asterisk recordings out of the spool.

``POST /channels/{channelId}/record`` tells Asterisk to write
``<asterisk_spool>/<name>.<format>``.  That spool is emptied by Asterisk's own
housekeeping, so both the voicemail greetings and the optional whole-IVR-leg
recording are moved into the project directory as soon as they are finished.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import anyio

log = logging.getLogger(__name__)


async def wait_for_file(path: Path, *, timeout: float = 5.0, interval: float = 0.2) -> bool:
    """Wait for Asterisk to finish writing *path* (it lags the API call)."""
    waited = 0.0
    while waited < timeout:
        try:
            if path.exists() and path.stat().st_size > 0:
                return True
        except OSError:  # pragma: no cover - transient spool state
            pass
        await anyio.sleep(interval)
        waited += interval
    return path.exists()


async def move_from_spool(
    spool_dir: Path,
    name: str,
    fmt: str,
    target_dir: Path,
    *,
    timeout: float = 5.0,
) -> Path | None:
    """Move ``<spool>/<name>.<fmt>`` into *target_dir* and return the new path."""
    spool = Path(spool_dir) / f"{name}.{fmt}"
    if not await wait_for_file(spool, timeout=timeout):
        return None

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{name}.{fmt}"
    try:
        shutil.move(str(spool), str(target))
    except OSError:
        try:
            shutil.copy2(str(spool), str(target))
            spool.unlink(missing_ok=True)
        except OSError as exc:
            log.error("Cannot store recording %s: %s", name, exc)
            return None
    try:
        target.chmod(0o640)
    except OSError:  # pragma: no cover - best effort
        pass
    log.info("Recording %s stored at %s", name, target)
    return target
