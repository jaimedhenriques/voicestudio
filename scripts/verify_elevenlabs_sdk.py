"""End-to-end proof: the official ElevenLabs Python SDK against this server.

Every other test in this repo drives the ElevenLabs-compatible endpoints with a
mocked backend and a hand-built payload. This script does the opposite: it runs
the real `elevenlabs` SDK, unmodified, against a live local server, so the thing
being verified is the claim a creator actually cares about — "point your
existing integration at the local base URL and it works".

What it refuses to accept as a pass:

* An unverified engine. The active TTS engine id must equal `--engine` before
  any synthesis runs. A wrong engine would still return 200 with real audio,
  and the transcript would silently be evidence about a different engine — so
  this is fatal and aborts before the first generate.
* Audio it has not decoded. Byte counts prove nothing about playability, so
  every render is parsed with ffprobe (real mp3 stream, duration > 0) and
  measured with ffmpeg volumedetect (not silence). ffprobe/ffmpeg missing is a
  FAIL, never a skip.
* A speed knob that was merely accepted. `speed: 1.5` must produce audio whose
  decoded duration is ~1.5x shorter than the same text at 1.0x.

Deliberately NOT a pytest: it needs a running server and a real model, so it is
a manual verification tool. It adds no repository dependency — the SDK comes in
through uv's `--with` for the length of the run.

Usage:
    uv run --with elevenlabs python scripts/verify_elevenlabs_sdk.py

    # against a server on another port, pinned to an engine, keeping evidence:
    uv run --with elevenlabs python scripts/verify_elevenlabs_sdk.py \
        --base-url http://localhost:3999 \
        --engine kittentts \
        --transcript /tmp/vs-sdk-e2e-transcript.txt

Exit code is 0 only when every check passed.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

# Below this mean volume a render is indistinguishable from silence — a
# "successful" generate that produced nothing audible must not pass.
SILENCE_FLOOR_DBFS = -50.0

# speed=1.5 should shorten the audio by ~1.5x. The band is wide enough for
# encoder padding and engine-side rounding, narrow enough that "ignored"
# (ratio ~1.0) and "wrong factor" both fail.
SPEED_RATIO_MIN, SPEED_RATIO_MAX = 1.3, 1.7

_RESULTS: list[tuple[str, bool, str]] = []
_LINES: list[str] = []


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:-3]}Z] {msg}"
    print(line, flush=True)
    _LINES.append(line)


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, ok, detail))
    log(f"{'PASS' if ok else 'FAIL'}  {name}{' — ' + detail if detail else ''}")
    return ok


def finish(transcript: Optional[str], started: float) -> int:
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    failed = len(_RESULTS) - passed
    log(f"--- {passed} passed, {failed} failed, {time.time() - started:.1f}s ---")
    for name, ok, detail in _RESULTS:
        if not ok:
            log(f"    FAILED: {name} — {detail}")
    log("RESULT: PASS" if failed == 0 else "RESULT: FAIL")
    if transcript:
        with open(transcript, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_LINES) + "\n")
        print(f"transcript written to {transcript}")
    return 0 if failed == 0 else 1


# ── audio inspection (ffprobe / ffmpeg) ─────────────────────────────────────


def ffprobe_audio(path: str) -> dict[str, Any]:
    """Container/stream facts for an audio file, via ffprobe's JSON output."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json",
         "-show_entries", "format=format_name,duration",
         "-show_entries", "stream=codec_name,codec_type,sample_rate,channels",
         path],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout
    parsed = json.loads(out)
    fmt = parsed.get("format", {})
    streams = [s for s in parsed.get("streams", []) if s.get("codec_type") == "audio"]
    return {
        "format_name": fmt.get("format_name", ""),
        "duration": float(fmt["duration"]) if fmt.get("duration") else 0.0,
        "codec_name": streams[0].get("codec_name", "") if streams else "",
        "sample_rate": streams[0].get("sample_rate", "") if streams else "",
        "channels": streams[0].get("channels") if streams else None,
    }


def mean_volume_dbfs(path: str) -> float:
    """Mean volume in dBFS via ffmpeg's volumedetect. -inf (silence) → -999."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf)\s*dB", proc.stderr)
    if not m:
        return -999.0
    return -999.0 if m.group(1) == "-inf" else float(m.group(1))


def validate_render(label: str, path: str) -> Optional[float]:
    """Decode-level checks on one render. Returns its duration, or None."""
    try:
        info = ffprobe_audio(path)
    except subprocess.CalledProcessError as e:
        check(f"ffprobe parsed {label}", False, (e.stderr or "").strip()[:200])
        return None
    except Exception as e:  # noqa: BLE001
        check(f"ffprobe parsed {label}", False, f"{type(e).__name__}: {e}")
        return None

    is_mp3 = "mp3" in info["format_name"] and info["codec_name"] == "mp3"
    ok = check(
        f"ffprobe parsed {label} as playable mp3",
        is_mp3 and info["duration"] > 0,
        f"format={info['format_name']} codec={info['codec_name']} "
        f"{info['sample_rate']}Hz ch={info['channels']} "
        f"duration={info['duration']:.3f}s",
    )

    vol = mean_volume_dbfs(path)
    check(
        f"{label} is audible, not silence",
        vol > SILENCE_FLOOR_DBFS,
        f"mean_volume {vol:.1f} dBFS (floor {SILENCE_FLOOR_DBFS:.0f})",
    )
    return info["duration"] if ok else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:3900")
    ap.add_argument(
        "--engine", default="kittentts",
        help="TTS engine id the server MUST be running (fatal if it is not)",
    )
    ap.add_argument("--text", default="Made on my own hardware.")
    ap.add_argument("--transcript", default=None, help="also write the transcript here")
    ap.add_argument("--audio-dir", default="/tmp", help="where to save rendered audio")
    args = ap.parse_args()

    started = time.time()
    log(f"target base URL: {args.base_url}")
    log(f"expected TTS engine: {args.engine}")

    # ── ffprobe/ffmpeg preflight ────────────────────────────────────────────
    # These carry the real audio assertions, so a missing binary invalidates
    # the run. Fail loudly rather than degrading to a byte-count proxy.
    missing = [t for t in ("ffprobe", "ffmpeg") if shutil.which(t) is None]
    if not check(
        "ffprobe and ffmpeg available", not missing,
        f"missing: {', '.join(missing)} — install ffmpeg (this repo's documented "
        f"media dependency); audio cannot be validated without it"
        if missing else "found on PATH",
    ):
        return finish(args.transcript, started)

    # ── SDK import ──────────────────────────────────────────────────────────
    try:
        import elevenlabs
        from elevenlabs import VoiceSettings
        from elevenlabs.client import ElevenLabs
    except ImportError as e:
        check("official elevenlabs SDK importable", False, str(e))
        log("run with: uv run --with elevenlabs python scripts/verify_elevenlabs_sdk.py")
        return finish(args.transcript, started)
    check(
        "official elevenlabs SDK importable", True,
        f"elevenlabs {getattr(elevenlabs, '__version__', 'unknown')}",
    )

    import httpx

    # ── engine identity: fatal ──────────────────────────────────────────────
    # Which engine served the audio is the difference between "the shim
    # replied" and "the shim synthesized on the engine we claim". A wrong
    # engine still returns 200, so the transcript would be honest-looking
    # evidence about something else. Abort before generating anything.
    try:
        r = httpx.get(f"{args.base_url}/engines", timeout=60.0)
        if r.status_code != 200:
            check(
                f"active TTS engine is {args.engine!r}", False,
                f"GET /engines returned HTTP {r.status_code}",
            )
            return finish(args.transcript, started)
        tts = r.json()["tts"]
        active = tts.get("active")
        if not check(
            f"active TTS engine is {args.engine!r}", active == args.engine,
            f"server reports {active!r} (env_override={tts.get('env_override')})",
        ):
            log("       aborting: refusing to record audio evidence from an "
                "engine other than the expected one")
            return finish(args.transcript, started)
    except Exception as e:  # noqa: BLE001
        check(f"active TTS engine is {args.engine!r}", False, f"{type(e).__name__}: {e}")
        return finish(args.transcript, started)

    # api_key is required by the constructor; the local server's auth is the
    # app-wide bearer middleware, which lets loopback callers through unkeyed.
    client = ElevenLabs(base_url=args.base_url, api_key="local")

    # ── 1. voices list, through the SDK's own model parsing ─────────────────
    voice_id: Optional[str] = None
    try:
        voices = list(client.voices.get_all().voices or [])
        check("SDK voices.get_all() parsed the response", True, f"{len(voices)} voice(s)")
        if voices:
            voice_id = voices[0].voice_id
            log(f"       using voice_id={voice_id!r} name={voices[0].name!r}")
        else:
            check(
                "at least one voice profile installed", False,
                "the server returned an empty voices list — create a profile first",
            )
    except Exception as e:  # noqa: BLE001
        check("SDK voices.get_all() parsed the response", False, f"{type(e).__name__}: {e}")

    # ── 2 & 3. synthesis, plain and with voice_settings.speed ───────────────
    durations: dict[str, float] = {}
    if voice_id:
        renders = (
            ("1.0x (default speed)", "vs-sdk-default.mp3", None),
            ("1.5x (voice_settings speed)", "vs-sdk-speed15.mp3", VoiceSettings(speed=1.5)),
        )
        for label, filename, settings in renders:
            path = f"{args.audio_dir}/{filename}"
            try:
                kwargs: dict[str, Any] = {"voice_id": voice_id, "text": args.text}
                if settings is not None:
                    kwargs["voice_settings"] = settings
                # with_raw_response so the HTTP status and content type are
                # observable, not just the decoded audio. This endpoint
                # streams, so the raw response is a context manager and
                # `data` is a byte iterator to drain inside the `with`.
                with client.text_to_speech.with_raw_response.convert(**kwargs) as raw:
                    status = raw.status_code
                    content_type = raw.headers.get("content-type", "")
                    audio = b"".join(raw.data)
            except Exception as e:  # noqa: BLE001
                check(f"SDK text_to_speech.convert {label}", False, f"{type(e).__name__}: {e}")
                continue

            if not check(
                f"SDK text_to_speech.convert {label}",
                status == 200 and content_type.startswith("audio/") and len(audio) > 0,
                f"HTTP {status}, {len(audio)} bytes, {content_type}",
            ):
                continue

            with open(path, "wb") as fh:
                fh.write(audio)
            log(f"       saved {path}")
            dur = validate_render(label, path)
            if dur is not None:
                durations[label] = dur

        # Speed is the one honoured knob, so prove it changed the AUDIO, not
        # just that the request was accepted. Decoded durations, no proxies.
        d1 = durations.get("1.0x (default speed)")
        d15 = durations.get("1.5x (voice_settings speed)")
        if d1 and d15:
            ratio = d1 / d15
            check(
                "speed=1.5 shortened the decoded audio by ~1.5x",
                SPEED_RATIO_MIN <= ratio <= SPEED_RATIO_MAX,
                f"{d1:.3f}s at 1.0x / {d15:.3f}s at 1.5x = {ratio:.3f}x "
                f"(accepted {SPEED_RATIO_MIN}–{SPEED_RATIO_MAX})",
            )
        else:
            check(
                "speed=1.5 shortened the decoded audio by ~1.5x", False,
                "one or both renders failed decode-level validation",
            )

    # ── 4. validation contract, live over HTTP ──────────────────────────────
    # The SDK cannot express these payloads (its own models reject them client
    # side), so they go over plain HTTP against the same running server.
    probe_id = voice_id or "no-such-voice-id"
    probes: list[tuple[str, dict[str, Any], int]] = [
        ("empty text", {"text": ""}, 422),
        ("speed out of range", {"text": "hi", "voice_settings": {"speed": 9.0}}, 422),
        ("speed as a string", {"text": "hi", "voice_settings": {"speed": "1.5"}}, 422),
        ("stability as a boolean", {"text": "hi", "voice_settings": {"stability": True}}, 422),
        ("use_speaker_boost as 1", {"text": "hi", "voice_settings": {"use_speaker_boost": 1}}, 422),
        ("explicit null speed", {"text": "hi", "voice_settings": {"speed": None}}, 422),
        ("null voice_settings object", {"text": "hi", "voice_settings": None}, 200),
    ]
    for label, body, expected in probes:
        try:
            r = httpx.post(
                f"{args.base_url}/v1/text-to-speech/{probe_id}", json=body, timeout=120.0
            )
            check(
                f"validation: {label} → {expected}", r.status_code == expected,
                f"got HTTP {r.status_code}",
            )
        except Exception as e:  # noqa: BLE001
            check(f"validation: {label} → {expected}", False, f"{type(e).__name__}: {e}")

    try:
        r = httpx.post(
            f"{args.base_url}/v1/text-to-speech/definitely-not-a-voice",
            json={"text": "hi"}, timeout=30.0,
        )
        check("validation: unknown voice_id → 404", r.status_code == 404, f"got HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        check("validation: unknown voice_id → 404", False, f"{type(e).__name__}: {e}")

    return finish(args.transcript, started)


if __name__ == "__main__":
    sys.exit(main())
