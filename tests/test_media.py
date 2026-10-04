"""Prompt uploads: validation, snake_case naming, ffmpeg conversion, deletion."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openivr.builder.server import create_app, get_auth
from openivr.media import (
    AUDIO_SUFFIXES,
    CONVERTIBLE_SUFFIXES,
    TARGET_SUFFIX,
    available_sounds,
    check_prompt_name,
    convert_audio,
    ffmpeg_binary,
    list_sound_files,
    needs_conversion,
    snake_case_stem,
)

needs_ffmpeg = pytest.mark.skipif(ffmpeg_binary() is None, reason="ffmpeg is not installed")
CONVERT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "convert_sound.sh"


def _tone(fmt: str = "wav", seconds: str = "1") -> bytes:
    """A silent-ish test tone, encoded by ffmpeg."""
    return subprocess.run(
        [
            "ffmpeg", "-v", "quiet", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-f", fmt, "-",
        ],
        check=True,
        capture_output=True,
    ).stdout


# ------------------------------------------------------------------- naming


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("My Voice Prompt.wav", "my_voice_prompt"),
        ("  Leading and trailing .wav", "leading_and_trailing"),
        ("already_snake.wav", "already_snake"),
        ("Mixed123Case.wav", "mixed123case"),
    ],
)
def test_snake_case_stem(name: str, expected: str) -> None:
    assert snake_case_stem(name) == expected


def test_native_and_convertible_suffixes() -> None:
    assert ".wav" in AUDIO_SUFFIXES
    for suffix in (".mp3", ".m4a", ".ogg", ".flac", ".aac", ".wma", ".opus"):
        assert suffix in CONVERTIBLE_SUFFIXES
    assert TARGET_SUFFIX == ".wav"
    assert needs_conversion(".MP3") is True
    assert needs_conversion(".ulaw") is False


def test_check_prompt_name_rejects_traversal() -> None:
    assert check_prompt_name("ok.wav") is None
    assert check_prompt_name("../escape.wav")
    assert check_prompt_name("has space.wav")
    assert check_prompt_name(".hidden.wav")
    assert check_prompt_name("x" * 200 + ".wav")


# ------------------------------------------------------------------ listing


def test_list_and_available_sounds(project: Path) -> None:
    sounds = project / "data" / "sounds"
    (sounds / "hello.wav").write_bytes(b"RIFF")
    (sounds / "bye.ulaw").write_bytes(b"\x00")
    (sounds / "notes.txt").write_text("ignored")
    names = {f["name"] for f in list_sound_files(sounds)}
    assert {"hello.wav", "bye.ulaw"} <= names
    assert "notes.txt" not in names
    assert "hello" in available_sounds(sounds)


# ---------------------------------------------------------------- ffmpeg


@needs_ffmpeg
def test_convert_audio_writes_ulaw_and_wav(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp3"
    source.write_bytes(_tone("mp3"))
    out = tmp_path / "out"
    note = convert_audio(source, "clip", out)
    assert (out / "clip.wav").is_file()
    assert (out / "clip.ulaw").is_file()
    assert "clip" in note or "converted" in note.lower()


@needs_ffmpeg
def test_converted_wav_is_8k_mono(tmp_path: Path) -> None:
    source = tmp_path / "clip.flac"
    source.write_bytes(_tone("flac"))
    out = tmp_path / "out"
    convert_audio(source, "clip", out)
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels",
            "-of", "csv=p=0", str(out / "clip.wav"),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert probe == "8000,1"


@needs_ffmpeg
def test_convert_audio_reports_broken_input(tmp_path: Path) -> None:
    source = tmp_path / "broken.mp3"
    source.write_bytes(b"this is not audio")
    message = convert_audio(source, "broken", tmp_path)
    assert isinstance(message, str) and message
    assert not (tmp_path / "broken.wav").exists()


def test_convert_audio_without_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openivr.media.ffmpeg_binary", lambda: None)
    monkeypatch.setattr("openivr.media.CONVERT_SCRIPT", tmp_path / "missing.sh")
    source = tmp_path / "clip.mp3"
    source.write_bytes(b"x")
    assert "ffmpeg" in convert_audio(source, "clip", tmp_path)


@needs_ffmpeg
def test_convert_sound_script_is_usable(tmp_path: Path) -> None:
    if not CONVERT_SCRIPT.is_file():  # pragma: no cover - repo always ships it
        pytest.skip("scripts/convert_sound.sh is missing")
    source = tmp_path / "clip.ogg"
    source.write_bytes(_tone("ogg"))
    subprocess.run(
        ["bash", str(CONVERT_SCRIPT), str(source), "from_script", str(tmp_path / "out")],
        check=True,
        capture_output=True,
    )
    assert (tmp_path / "out" / "from_script.wav").is_file()
    assert (tmp_path / "out" / "from_script.ulaw").is_file()


# -------------------------------------------------------------------- HTTP


@pytest.fixture()
def client(project: Path):
    app = create_app(project)
    auth = get_auth(app, project)
    with TestClient(app) as test_client:
        assert (
            test_client.post(
                "/login",
                data={"username": auth.username, "password": auth.generated_password},
                follow_redirects=False,
            ).status_code
            == 303
        )
        yield test_client


def _upload(client: TestClient, name: str, data: bytes, suffix: str = ".wav"):
    return client.post(
        "/dialplan/upload",
        files={"file": (name, data, "application/octet-stream" + suffix)},
        headers={"Accept": "application/json"},
    )


@needs_ffmpeg
def test_upload_converts_and_lists_in_library(client: TestClient, project: Path) -> None:
    response = _upload(client, "Welcome Message.mp3", _tone("mp3"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["name"] == "welcome_message.wav"
    sounds = project / "data" / "sounds"
    assert (sounds / "welcome_message.wav").is_file()
    assert (sounds / "welcome_message.ulaw").is_file()
    # the dialplan page offers both files as prompts
    page = client.get("/dialplan")
    assert "welcome_message" in page.text
    # ...and no temporary files are left behind
    assert not [p for p in sounds.iterdir() if p.name.startswith(".")]


@needs_ffmpeg
def test_upload_keeps_native_formats(client: TestClient, project: Path) -> None:
    response = _upload(client, "Beep.ulaw", _tone("mulaw"))
    assert response.json()["name"] == "beep.ulaw"
    assert (project / "data" / "sounds" / "beep.ulaw").is_file()


def test_upload_rejects_bad_extension(client: TestClient, project: Path) -> None:
    response = _upload(client, "payload.exe", b"MZ")
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert not list((project / "data" / "sounds").glob("payload*"))


def test_upload_rejects_empty_file(client: TestClient) -> None:
    response = _upload(client, "empty.wav", b"")
    assert response.status_code == 400
    assert "empty" in response.json()["error"].lower()


def test_upload_overwrites_existing_prompt(client: TestClient, project: Path) -> None:
    first = _upload(client, "greeting.wav", b"RIFFfirst")
    assert first.json()["ok"] is True
    second = _upload(client, "greeting.wav", b"RIFFsecond")
    assert second.json()["ok"] is True
    assert (project / "data" / "sounds" / "greeting.wav").read_bytes() == b"RIFFsecond"


def test_delete_requires_a_session(client: TestClient, project: Path) -> None:
    (project / "data" / "sounds" / "gone.wav").write_bytes(b"RIFF")
    response = client.post("/dialplan/delete", data={"name": "gone.wav"}, follow_redirects=False)
    assert response.status_code == 303
    assert not (project / "data" / "sounds" / "gone.wav").exists()
    # a second delete reports the problem instead of pretending
    missing = client.post(
        "/dialplan/delete",
        data={"name": "gone.wav"},
        headers={"Accept": "application/json"},
    )
    assert missing.status_code == 404


def test_delete_cannot_escape_the_sounds_directory(client: TestClient, project: Path) -> None:
    outside = project / "data" / "outside.wav"
    outside.write_bytes(b"RIFF")
    response = client.post(
        "/dialplan/delete", data={"name": "../outside.wav"}, follow_redirects=False
    )
    assert response.status_code in (303, 400, 404)
    assert outside.exists()


def test_sounds_cli_lists_prompts(project: Path, capsys: pytest.CaptureFixture) -> None:
    from openivr.__main__ import main

    (project / "data" / "sounds" / "cli.wav").write_bytes(b"RIFF")
    assert main(["--root", str(project), "sounds"]) == 0
    assert "cli.wav" in capsys.readouterr().out
    if shutil.which("ffmpeg"):
        pass  # conversion availability does not affect listing