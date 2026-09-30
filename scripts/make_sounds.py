#!/usr/bin/env python3
"""Generate the sample prompt set with espeak-ng (or a tone fallback).

Usage::

    python scripts/make_sounds.py --out data/sounds          # synthesise prompts
    python scripts/make_sounds.py --out data/sounds --force  # overwrite existing

Requires ``espeak-ng`` (or ``espeak``) on PATH.  Without it the script falls
back to generating short tones so the flow can be smoke-tested without any
voice at all.
"""

from __future__ import annotations

import argparse
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

PROMPTS: dict[str, str] = {
    "welcome": "Thank you for calling. Please hold.",
    "main-menu": "For sales press 1. For support press 2. "
    "To leave a voicemail press 3. For an operator press 0.",
    "support-menu": "Support menu. For the support desk press 1. "
    "For voicemail press 2. For sales press 3. "
    "To go back press 0.",
    "timeout-msg": "I did not get that. Please press a key.",
    "invalid-msg": "That option is not available. Please try again.",
    "vm-support": "You have reached the support voicemail. "
    "Please leave your message after the tone.",
    "enter-account": "Please enter your account number and press pound.",
    "goodbye": "Thank you for calling. Goodbye.",
}

RATE = 8000


def synth_tone(path: Path, seconds: float = 1.2, freq: float = 440.0) -> None:
    """Write a mono 8 kHz sine-ish tone (fallback when no TTS is installed)."""
    frames = bytearray()
    for i in range(int(RATE * seconds)):
        t = i / RATE
        envelope = min(1.0, t * 8, (seconds - t) * 8)
        value = 0.35 * math.sin(2 * math.pi * freq * t) * envelope
        frames += struct.pack("<h", int(value * 32767))
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(bytes(frames))


def espeak(text: str, path: Path, binary: str) -> bool:
    try:
        result = subprocess.run(
            [binary, "-s", "160", "-w", str(path.with_suffix(".raw.wav")), text],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  espeak failed: {exc}", file=sys.stderr)
        return False
    raw = path.with_suffix(".raw.wav")
    if result.returncode != 0 or not raw.exists():
        print(f"  espeak said: {result.stderr.decode(errors='replace')[:200]}", file=sys.stderr)
        raw.unlink(missing_ok=True)
        return False
    result = subprocess.run(
        ["sox", str(raw), "-r", str(RATE), "-c", "1", "-b", "16", str(path)],
        capture_output=True,
        timeout=60,
    )
    raw.unlink(missing_ok=True)
    if result.returncode != 0:
        print(f"  sox failed: {result.stderr.decode(errors='replace')[:200]}", file=sys.stderr)
        path.unlink(missing_ok=True)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default="data/sounds", help="output folder")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    has_sox = shutil.which("sox") is not None
    print(f"espeak: {binary or 'not found'}   sox: {'yes' if has_sox else 'no'}")

    written = skipped = 0
    for name, text in PROMPTS.items():
        target = out / f"{name}.wav"
        if target.exists() and not args.force:
            print(f"  skip  {target.name} (exists)")
            skipped += 1
            continue
        ok = bool(binary) and has_sox and espeak(text, target, binary)
        if ok:
            print(f"  voice {target.name} ({target.stat().st_size:,} bytes)")
        else:
            synth_tone(target)
            print(f"  tone  {target.name} ({target.stat().st_size:,} bytes)")
        written += 1

    print(f"\n{written} prompt(s) written, {skipped} skipped -> {out}")
    print("Replace the tones with real recordings - see data/sounds/README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
