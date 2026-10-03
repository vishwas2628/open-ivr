"""Write rendered confs into the Asterisk etc dir and reload."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from .render import render_all

log = logging.getLogger("openivr.builder")

RELOAD_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "asterisk_reload.sh"


def asterisk_etc() -> Path:
    return Path(os.environ.get("OPENIVR_ASTERISK_ETC") or "/etc/asterisk")


def staging_dir(root: Path) -> Path:
    path = root / "data" / "asterisk-build"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_rendered(files: dict[str, str], dest: Path) -> list[str]:
    written: list[str] = []
    for rel, body in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        try:
            target.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
        except OSError:
            pass
        written.append(str(target))
    return written


def publish(root: Path, *, dest: Path | None = None, reload: bool = True) -> dict[str, Any]:
    files = render_all(root)
    stage = staging_dir(root)
    write_rendered(files, stage)
    target = dest or asterisk_etc()
    copied: list[str] = []
    copy_error = ""
    try:
        copied = write_rendered(files, target)
    except OSError as exc:
        copy_error = str(exc)
        log.warning("Could not write %s: %s — left files in %s", target, exc, stage)
    reload_ok = False
    reload_detail = "skipped"
    if reload and not copy_error and str(target) in {str(asterisk_etc()), "/etc/asterisk"}:
        reload_ok, reload_detail = run_reload()
    return {
        "files": sorted(files),
        "staging": str(stage),
        "dest": str(target),
        "copied": copied,
        "copy_error": copy_error,
        "reload_ok": reload_ok,
        "reload_detail": reload_detail,
    }


def run_reload() -> tuple[bool, str]:
    script = RELOAD_SCRIPT
    if script.is_file():
        try:
            proc = subprocess.run(
                ["bash", str(script)],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            detail = (proc.stdout or proc.stderr or "").strip() or f"exit {proc.returncode}"
            return proc.returncode == 0, detail[:500]
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
    asterisk = shutil.which("asterisk")
    if not asterisk:
        return False, "asterisk binary not found"
    try:
        proc = subprocess.run(
            [asterisk, "-rx", "core reload"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        detail = (proc.stdout or proc.stderr or "").strip() or f"exit {proc.returncode}"
        return proc.returncode == 0, detail[:500]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
