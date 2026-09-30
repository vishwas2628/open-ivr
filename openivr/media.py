"""Media helpers: prompt name -> ARI media URI, plus sound file discovery."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import anyio

from .config import Config

log = logging.getLogger(__name__)

SOUND_DIR_NAME = "custom"
KNOWN_URIS = ("sound:", "recording:", "digits:", "numbers:", "characters:", "tone:", "file:")
AUDIO_SUFFIXES = {".wav", ".sln", ".sln16", ".gsm", ".ulaw", ".alaw", ".mp3", ".oga", ".ogg"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

PLAYBACK_GUARD = 120.0
"""Safety net: stop waiting on a playback after this many seconds."""


def resolve_media(name: str, cfg: Config) -> str:
    """Turn a prompt name into an ARI media URI.

    * full media URIs (``sound:``, ``recording:`` …) pass through untouched
    * ``custom/foo.wav`` keeps the ``custom/`` subdir
    * everything else resolves to ``sound:custom/<name>`` (the folder that
      ``run_ivr.sh`` syncs into Asterisk's sounds directory)
    """
    raw = (name or "").strip()
    if not raw:
        raise ValueError("empty prompt name")
    if raw.startswith(KNOWN_URIS):
        return raw
    if "/" in raw:
        return f"sound:{raw.lstrip('/')}"
    return f"sound:{SOUND_DIR_NAME}/{raw}"


def check_prompt_name(name: str) -> str | None:
    """Return an error message if *name* is not a safe prompt name."""
    if not name:
        return "empty prompt name"
    if not SAFE_NAME.match(name):
        return f"{name!r} is not a valid prompt name (letters, digits, . _ - only)"
    if Path(name).suffix.lower() not in AUDIO_SUFFIXES and "." in name:
        return f"{name!r} has an unsupported audio extension"
    return None


def available_sounds(sounds_dir: str | Path) -> set[str]:
    """Prompt names (without extension) available for playback."""
    directory = Path(sounds_dir)
    if not directory.is_dir():
        return set()
    found: set[str] = set()
    for entry in directory.rglob("*"):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        if entry.suffix.lower() == ".wav":
            found.add(entry.stem)
        else:
            found.add(entry.name)
    return found


def list_sound_files(sounds_dir: str | Path) -> list[dict[str, object]]:
    """Detailed listing of the sounds folder (name, file, size, format)."""
    directory = Path(sounds_dir)
    if not directory.is_dir():
        return []
    out: list[dict[str, object]] = []
    for entry in sorted(directory.rglob("*")):
        if not entry.is_file() or entry.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        out.append(
            {
                "name": entry.stem if entry.suffix.lower() == ".wav" else entry.name,
                "file": entry.name,
                "relpath": str(entry.relative_to(directory)),
                "suffix": entry.suffix.lower().lstrip("."),
                "size": entry.stat().st_size,
            }
        )
    return out


def prompt_uri_for_asterisk(name: str) -> str:
    """The name Asterisk itself sees (used by dialplan ``Playback()``)."""
    return f"{SOUND_DIR_NAME}/{name}"


PLAY_DONE = "done"
PLAY_STOPPED = "stopped"


async def play_prompt(channel, prompt: str, cfg: Config, *, guard: float = PLAYBACK_GUARD) -> str:
    """Play *prompt* on *channel* and wait until it finishes.

    Returns :data:`PLAY_DONE` when Asterisk reported ``PlaybackFinished`` and
    :data:`PLAY_STOPPED` when the guard expired or the playback was cancelled -
    a caller hanging up mid-prompt therefore never blocks the state machine.
    """
    media = resolve_media(prompt, cfg)
    playback = await channel.play(media=media)
    done = anyio.Event()

    async def waiter() -> None:
        try:
            await playback.wait_done()
        finally:
            done.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(waiter)
        with anyio.move_on_after(guard):
            while not done.is_set():
                await anyio.sleep(0.05)
        tg.cancel_scope.cancel()
    return PLAY_DONE if done.is_set() else PLAY_STOPPED
