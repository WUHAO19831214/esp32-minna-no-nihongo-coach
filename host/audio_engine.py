"""Audio catalog, ffmpeg A-B slicer, and Kyoko Japanese TTS encoder for ESP32 built-in speaker playback."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "audio" / "minna_no_nihongo_1"
AUDIO_ROOT = AUDIO_DIR
MANIFEST_PATH = AUDIO_DIR / "manifest.json"
CACHE_DIR = ROOT / ".runtime" / "audio_cache"
FFMPEG_BIN = "/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").exists() else "ffmpeg"


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"total_tracks": 0, "lessons": {}, "tracks": []}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def get_lesson_tracks(lesson: str) -> list[dict[str, Any]]:
    s = str(lesson).strip().removeprefix("lesson_").removeprefix("L").removeprefix("l")
    try:
        lid = f"{int(s):03d}"
    except ValueError:
        lid = "006"
    manifest = load_manifest()
    lessons = manifest.get("lessons") or {}
    tracks = lessons.get(lid) or []
    if not tracks and lid != "000":
        tracks = [t for t in manifest.get("tracks", []) if t.get("lesson") == lid]
    return [
        {
            "index": int(t["index"]),
            "track_id": int(t["track_id"]),
            "title": str(t["title"]),
            "lesson": str(t["lesson"]),
            "section": str(t["section"]),
            "duration": max(1, int(t.get("duration") or 1)),
            "file": str(t["file"]),
        }
        for t in tracks
    ]


def get_track_by_index(track_index: int) -> dict[str, Any] | None:
    manifest = load_manifest()
    for t in manifest.get("tracks", []):
        if int(t.get("index") or 0) == int(track_index):
            return t
    return None


def _volume_gain(volume_pct: int | float | None, base_gain: float = 1.45) -> float:
    if volume_pct is None:
        v = 80.0
    else:
        v = max(0.0, min(100.0, float(volume_pct)))
    if v <= 0.5:
        return 0.0
    # Non-linear perceptual loudness curve: 80% -> base_gain (~1.45), 100% -> 2.30, 30% -> 0.35
    return round(base_gain * ((v / 80.0) ** 1.35), 2)


def slice_track_mp3(
    track_index: int,
    start_sec: float = 0.0,
    end_sec: float | None = None,
    max_window_sec: float = 45.0,
    volume_pct: int = 80,
) -> tuple[bytes, float, float]:
    """Slice [start_sec, end_sec] from the local Ximalaya MP3 into a compact mono MP3 for ESP32 speaker."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    track = get_track_by_index(track_index)
    if not track:
        raise FileNotFoundError(f"Track index {track_index} not found in manifest")
    src = AUDIO_DIR / track["file"]
    if not src.exists():
        raise FileNotFoundError(f"Audio file missing: {src}")

    duration = float(max(1, int(track.get("duration") or 1)))
    s = max(0.0, min(float(start_sec), max(0.0, duration - 1.0)))
    if end_sec is not None and end_sec > s + 0.8:
        e = min(duration, float(end_sec))
    else:
        e = min(duration, s + max_window_sec)

    clip_len = max(0.8, e - s)
    gain = _volume_gain(volume_pct, base_gain=1.40)
    key = f"trk_{track_index}_{s:.1f}_{e:.1f}_v{int(volume_pct)}_32k"
    out_path = CACHE_DIR / f"{hashlib.md5(key.encode()).hexdigest()[:16]}.mp3"
    if not out_path.exists() or out_path.stat().st_size < 256:
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-ss",
            f"{s:.2f}",
            "-t",
            f"{clip_len:.2f}",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-b:a",
            "32k",
            "-af",
            f"volume={gain:.2f}",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    return out_path.read_bytes(), s, e


def synthesize_japanese_mp3(text: str, volume_pct: int = 80, slow: bool = False) -> bytes:
    """Synthesize Japanese word/sentence via macOS Kyoko voice into a compact MP3 for ESP32 speaker."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    clean = str(text or "").strip()
    if not clean:
        clean = "こんにちは"
    clean = clean.replace("（　）", "、").replace("()", "、")
    rate = "130" if slow else "168"
    gain = _volume_gain(volume_pct, base_gain=1.55)
    key = f"{clean}_r{rate}_v{int(volume_pct)}"
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
    mp3_path = CACHE_DIR / f"tts_{digest}.mp3"
    if mp3_path.exists() and mp3_path.stat().st_size > 256:
        return mp3_path.read_bytes()

    aiff_path = CACHE_DIR / f"tts_{digest}.aiff"
    subprocess.run(
        ["/usr/bin/say", "-v", "Kyoko", "-r", rate, "-o", str(aiff_path), clean],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            FFMPEG_BIN,
            "-y",
            "-i",
            str(aiff_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-b:a",
            "32k",
            "-af",
            f"volume={gain:.2f}",
            str(mp3_path),
        ],
        check=True,
        capture_output=True,
    )
    try:
        aiff_path.unlink(missing_ok=True)
    except Exception:
        pass
    return mp3_path.read_bytes()


BT_HELPER_BIN = ROOT / ".runtime" / "bt_helper"
FFPLAY_BIN = "/opt/homebrew/bin/ffplay" if Path("/opt/homebrew/bin/ffplay").exists() else "ffplay"


def get_bt_status() -> dict[str, Any]:
    """Query paired and cached nearby Bluetooth audio devices and connection status via .runtime/bt_helper."""
    if not BT_HELPER_BIN.exists():
        return {"ok": False, "connected": False, "name": "未连接", "devices": []}
    try:
        out = subprocess.run([str(BT_HELPER_BIN), "status"], capture_output=True, text=True, timeout=4.0, check=True)
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip().startswith("{")]
        return json.loads(lines[-1] if lines else "{}")
    except Exception:
        return {"ok": False, "connected": False, "name": "未连接", "devices": []}


def scan_bt_devices(duration_sec: int = 4) -> dict[str, Any]:
    """Scan nearby discoverable Bluetooth audio devices via .runtime/bt_helper scan."""
    if not BT_HELPER_BIN.exists():
        return {"ok": False, "connected": False, "name": "未连接", "devices": []}
    try:
        out = subprocess.run(
            [str(BT_HELPER_BIN), "scan", str(max(3, min(8, int(duration_sec))))],
            capture_output=True,
            text=True,
            timeout=float(duration_sec) + 5.0,
            check=True,
        )
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip().startswith("{")]
        return json.loads(lines[-1] if lines else "{}")
    except Exception as exc:
        st = get_bt_status()
        st["error"] = str(exc)
        return st


def connect_bt_headphones(target: str = "") -> dict[str, Any]:
    """Pair (if unpaired) and connect to target Bluetooth audio device (by MAC address or name) and set CoreAudio default output."""
    if not BT_HELPER_BIN.exists():
        return {"ok": False, "connected": False, "name": target or "未连接", "devices": []}
    try:
        cmd = [str(BT_HELPER_BIN), "connect"]
        if str(target or "").strip():
            cmd.append(str(target).strip())
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=14.0, check=True)
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip().startswith("{")]
        return json.loads(lines[-1] if lines else "{}")
    except Exception as exc:
        st = get_bt_status()
        st["error"] = str(exc)
        return st


def disconnect_bt_headphones(target: str = "") -> dict[str, Any]:
    """Disconnect target (or all active) Bluetooth audio devices."""
    if not BT_HELPER_BIN.exists():
        return {"ok": False, "connected": False, "devices": []}
    try:
        cmd = [str(BT_HELPER_BIN), "disconnect"]
        if str(target or "").strip():
            cmd.append(str(target).strip())
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=6.0, check=True)
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip().startswith("{")]
        return json.loads(lines[-1] if lines else "{}")
    except Exception as exc:
        return {"ok": False, "connected": False, "error": str(exc), "devices": []}


class HostAudioPlayer:
    """Manages local Mac mini -> Bluetooth Audio playback (including seamless A-B segment looping)."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=0.5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def play_mp3_bytes(self, mp3_bytes: bytes, loop: bool = False, volume_pct: int = 80) -> None:
        self.stop()
        if volume_pct <= 0:
            return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = CACHE_DIR / "bt_active_clip.mp3"
        tmp_path.write_bytes(mp3_bytes)
        cmd = [
            FFPLAY_BIN,
            "-nodisp",
            "-vn",
            "-loglevel",
            "quiet",
            "-volume",
            str(max(0, min(100, int(volume_pct)))),
        ]
        if loop:
            cmd.extend(["-loop", "0"])
        else:
            cmd.append("-autoexit")
        cmd.append(str(tmp_path))
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def play_ab_loop_mp3(self, mp3_bytes: bytes, volume_pct: int = 80) -> None:
        self.play_mp3_bytes(mp3_bytes, loop=True, volume_pct=volume_pct)

    def play_track_full(
        self,
        track_index: int,
        start_sec: float = 0.0,
        volume_pct: int = 80,
    ) -> tuple[float, float]:
        self.stop()
        track = get_track_by_index(track_index)
        if not track:
            return 0.0, 30.0
        duration = float(max(1, int(track.get("duration") or 1)))
        s = max(0.0, min(float(start_sec), max(0.0, duration - 1.0)))
        e = duration
        if volume_pct <= 0:
            return s, e
        src = AUDIO_DIR / track["file"]
        gain = _volume_gain(volume_pct, base_gain=1.25)
        cmd = [
            FFPLAY_BIN,
            "-nodisp",
            "-vn",
            "-loglevel",
            "quiet",
            "-autoexit",
            "-ss",
            f"{s:.2f}",
            "-af",
            f"volume={gain:.2f}",
            str(src),
        ]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return s, e


HOST_PLAYER = HostAudioPlayer()


