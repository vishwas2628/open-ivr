"""Media helpers: prompt name -> ARI media URI, sound file discovery, ffmpeg."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

import anyio

from .config import Config

log = logging.getLogger(__name__)

SOUND_DIR_NAME = "custom"
KNOWN_URIS = ("sound:", "recording:", "digits:", "numbers:", "characters:", "tone:", "file:")
AUDIO_SUFFIXES = {".wav", ".sln", ".sln16", ".gsm", ".ulaw", ".alaw", ".mp3", ".oga", ".ogg"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Upload formats ffmpeg can turn into something Asterisk plays natively.
CONVERTIBLE_SUFFIXES = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma", ".aiff"}
#: Formats Asterisk reads without any conversion.
NATIVE_SUFFIXES = AUDIO_SUFFIXES - CONVERTIBLE_SUFFIXES
#: Result of a conversion - 8 kHz mono signed 16-bit is what Asterisk wants.
TARGET_SUFFIX = ".wav"
FFMPEG_TIMEOUT = 60

PLAYBACK_GUARD = 120.0
"""Safety net: stop waiting on a playback after this many seconds."""


CONVERT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "convert_sound.sh"
ASTERISK_VM_DIR = "/var/spool/asterisk/voicemail"


def ffmpeg_binary() -> str | None:
    """Absolute path of ffmpeg, or None when it is not installed."""
    return shutil.which("ffmpeg")


def needs_conversion(suffix: str) -> bool:
    return suffix.lower() in CONVERTIBLE_SUFFIXES


def convert_audio(source: Path, stem: str, out_dir: Path) -> str:
    """Convert *source* into ``<out_dir>/<stem>.ulaw`` and ``.wav``.

    Prefers scripts/convert_sound.sh (the shell entry point the installer and
    Makefile also use) and falls back to calling ffmpeg directly. Returns a
    human readable note, or an error message when nothing was written.
    """
    if CONVERT_SCRIPT.is_file():
        try:
            proc = subprocess.run(
                ["bash", str(CONVERT_SCRIPT), str(source), stem, str(out_dir)],
                check=False,
                capture_output=True,
                text=True,
                timeout=FFMPEG_TIMEOUT,
            )
            detail = (proc.stdout or proc.stderr or "").strip()
            if proc.returncode == 0 and (out_dir / f"{stem}.wav").is_file():
                return detail.splitlines()[-1] if detail else f"converted to 8 kHz mono wav"
            return detail[:200] or f"convert_sound.sh exited {proc.returncode}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"convert_sound.sh failed: {exc}"

    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        return "ffmpeg is not installed - install it or upload .wav/.ulaw/.alaw/.gsm audio"
    if not source.is_file():
        return f"uploaded file vanished: {source}"

    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[str] = []
    for suffix, codec, muxer in ((".ulaw", "pcm_mulaw", "mulaw"), (".wav", "pcm_s16le", "wav")):
        tmp = out_dir / f".convert-{stem}{suffix}"
        command = [
            ffmpeg, "-y", "-nostdin", "-loglevel", "error",
            "-i", str(source), "-ac", "1", "-ar", "8000",
            "-acodec", codec, "-f", muxer, str(tmp),
        ]
        try:
            proc = subprocess.run(
                command, check=False, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            tmp.unlink(missing_ok=True)
            return f"ffmpeg failed: {exc}"
        if proc.returncode != 0 or not tmp.is_file():
            tmp.unlink(missing_ok=True)
            detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip().splitlines()
            return f"ffmpeg could not convert {source.suffix or 'file'}: {detail[-1] if detail else 'unknown error'}"
        tmp.replace(out_dir / f"{stem}{suffix}")
        made.append(suffix)
    return f"converted {source.suffix} to 8 kHz mono {', '.join(made)}"


def resolve_media(name: str, cfg: Config) -> str:
    """Turn a prompt name into an ARI media URI.

    * full media URIs (``sound:``, ``recording:`` …) pass through untouched
    * ``custom/foo.wav`` keeps the ``custom/`` subdir
    * everything else resolves to ``sound:custom/<name>`` (the folder that
      ``system/deploy.sh`` syncs into Asterisk's sounds directory)
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
