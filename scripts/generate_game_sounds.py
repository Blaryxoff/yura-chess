#!/usr/bin/env python3
"""Build Alice previews from licensed source recordings."""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path

SAMPLE_RATE = 48_000
PEAK = 10 ** (-3 / 20)
SOUND_NAMES = ("start", "move", "check", "checkmate", "success")
SOURCE_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "sounds" / "source"


def _recording(source_name: str, start: float, duration: float) -> list[float]:
    """Decode one exact licensed source slice without synthetic resonance."""
    source = SOURCE_DIR / source_name
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-af",
            "highpass=f=70,lowpass=f=11000",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    ).stdout
    samples = [value[0] for value in struct.iter_unpack("<f", decoded)]
    fade = min(round(0.025 * SAMPLE_RATE), len(samples) // 2)
    for offset in range(fade):
        samples[offset] *= offset / fade
        samples[-offset - 1] *= offset / fade
    maximum = max(abs(sample) for sample in samples) or 1.0
    return [sample * PEAK / maximum for sample in samples]


def _start() -> list[float]:
    # One complete take: a handful of pieces dropped over a wooden chess board.
    return _recording("ienba-chess-pieces-drop-755250.mp3", 0.0, 0.92)


def _move() -> list[float]:
    # A light snap from a chess piece placed on a wooden board.
    return _recording("el-boss-piece-placement-546119.mp3", 0.0, 0.111771)


def _check() -> list[float]:
    # A single chess piece placed firmly onto the board.
    return _recording("designerschoice-piece-place-onto-board-815629.mp3", 0.0, 0.743042)


def _checkmate() -> list[float]:
    # One chess piece physically knocking another piece down.
    return _recording("designerschoice-piece-knocks-other-down-815624.mp3", 0.0, 0.510833)


def _success() -> list[float]:
    # A real crowd cheering briefly in affirmation.
    return _recording("shangusburger-short-affirmative-cheer-764274.mp3", 0.0, 2.928792)


BUILDERS: dict[str, Callable[[], list[float]]] = {
    "start": _start,
    "move": _move,
    "check": _check,
    "checkmate": _checkmate,
    "success": _success,
}


def _write_wav(path: Path, samples: list[float]) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(
            b"".join(struct.pack("<h", round(max(-1.0, min(1.0, sample)) * 32767)) for sample in samples)
        )


def _encode(wav_path: Path, mp3_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(mp3_path),
        ],
        check=True,
    )


def generate(output_dir: Path, names: tuple[str, ...], preview: bool) -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is required to encode MP3 previews")
    output_dir.mkdir(parents=True, exist_ok=True)
    built = {name: BUILDERS[name]() for name in SOUND_NAMES}
    with tempfile.TemporaryDirectory(prefix="yura-chess-sounds-") as temporary:
        temp = Path(temporary)
        for name in names:
            wav_path = temp / f"{name}.wav"
            _write_wav(wav_path, built[name])
            _encode(wav_path, output_dir / f"{name}.mp3")
        if preview:
            silence = [0.0] * round(0.75 * SAMPLE_RATE)
            reel: list[float] = []
            for name in SOUND_NAMES:
                reel.extend(built[name])
                reel.extend(silence)
            wav_path = temp / "preview.wav"
            _write_wav(wav_path, reel)
            _encode(wav_path, output_dir / "yura-chess-sounds-preview.mp3")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/sounds"))
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("names", nargs="*", choices=SOUND_NAMES)
    args = parser.parse_args()
    names = tuple(args.names) if args.names else SOUND_NAMES
    generate(args.output, names, args.preview)


if __name__ == "__main__":
    main()
