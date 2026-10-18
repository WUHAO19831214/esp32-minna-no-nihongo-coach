#!/usr/bin/env python3
"""Download all 238 tracks of 《大家的日语初级1(第二版听力练习)》 (Album ID: 67798305) from Ximalaya."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.request

ALBUM_ID = 67798305
AES_KEY_HEX = "aaad3e4fd540b0f79dca95606e72bf93"
ROOT = Path(__file__).resolve().parents[1]
AUDIO_ROOT = ROOT / "audio" / "minna_no_nihongo_1"
AGENT_AUDIO_LINK = Path(
    "/Users/wuhao/LocalProjects/Codex/macmini/local-teaching-agent/minna-no-nihongo-agent/data/audio"
)

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def parse_cn_lesson(text: str) -> tuple[str, str]:
    m = re.search(r"第([一二三四五六七八九十]+)课(.+)", text)
    if not m:
        return "000", text.split(" ", 1)[-1].strip()
    s = m.group(1)
    sec = m.group(2).strip()
    if s == "十":
        n = 10
    elif s.startswith("十"):
        n = 10 + CN_NUM[s[1:]]
    elif s.endswith("十"):
        n = CN_NUM[s[0]] * 10
    elif "十" in s:
        parts = s.split("十")
        n = CN_NUM[parts[0]] * 10 + CN_NUM[parts[1]]
    else:
        n = CN_NUM[s]
    return f"{n:03d}", sec


def decrypt_ximalaya_url(enc: str) -> str:
    padded = enc + "=" * ((4 - len(enc) % 4) % 4)
    raw = base64.urlsafe_b64decode(padded)
    proc = subprocess.run(
        ["openssl", "enc", "-d", "-aes-128-ecb", "-K", AES_KEY_HEX],
        input=raw,
        capture_output=True,
        check=True,
    )
    return proc.stdout.decode("utf-8", errors="ignore").strip()


def fetch_all_tracks() -> list[dict]:
    all_tracks: list[dict] = []
    for page in range(1, 12):
        url = f"https://www.ximalaya.com/revision/play/v1/show?id={ALBUM_ID}&num={page}&sort=0&size=30&ptype=0"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tracks = data.get("data", {}).get("tracksAudioPlay", [])
        if not tracks:
            break
        all_tracks.extend(tracks)
    return all_tracks


def resolve_mp3_url(track_id: int) -> tuple[str, dict]:
    ca_url = f"http://mobile.ximalaya.com/mobile/v1/track/ca/playpage/{track_id}"
    req = urllib.request.Request(ca_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        txt = resp.read().decode("utf-8", errors="ignore")
    txt = re.sub(r"<esi:include[^>]+/>", "null", txt)
    data = json.loads(txt)
    ti = data.get("trackInfo") or {}
    if ti.get("playUrl64"):
        return ti["playUrl64"], {"type": "MP3_64"}
    if ti.get("playUrl32"):
        return ti["playUrl32"], {"type": "MP3_32"}

    ts = int(time.time() * 1000)
    url = f"https://www.ximalaya.com/mobile-playpage/track/v3/baseInfo/{ts}?device=web&trackId={track_id}&trackQualityLevel=1"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    info = data.get("trackInfo", {})
    play_list = info.get("playUrlList", [])
    preferred_order = ["MP3_64", "MP3_32", "128KMP3", "M4A_64", "M4A_24"]
    by_type = {item.get("type"): item for item in play_list if item.get("url")}
    chosen = None
    for t in preferred_order:
        if t in by_type:
            chosen = by_type[t]
            break
    if not chosen and play_list:
        chosen = play_list[0]
    if not chosen:
        raise RuntimeError(f"No playUrlList for track {track_id}")
    real_url = decrypt_ximalaya_url(chosen["url"])
    return real_url, chosen


def download_one(item: dict) -> dict:
    idx = int(item["index"])
    tid = int(item["trackId"])
    title = str(item["trackName"]).strip()
    duration = int(item.get("duration") or 0)
    lesson, section = parse_cn_lesson(title)
    safe_sec = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", section)
    rel_path = f"lesson_{lesson}/{idx:03d}_{safe_sec}.mp3"
    dest = AUDIO_ROOT / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size > 4096:
        return {
            "index": idx,
            "track_id": tid,
            "title": title,
            "lesson": lesson,
            "section": section,
            "duration": duration,
            "file": rel_path,
            "bytes": dest.stat().st_size,
            "cached": True,
        }

    last_err = None
    for attempt in range(3):
        try:
            mp3_url, meta = resolve_mp3_url(tid)
            req = urllib.request.Request(mp3_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read()
            if len(content) < 1024:
                raise RuntimeError(f"Downloaded too few bytes ({len(content)})")
            tmp = dest.with_suffix(".mp3.tmp")
            tmp.write_bytes(content)
            tmp.replace(dest)
            return {
                "index": idx,
                "track_id": tid,
                "title": title,
                "lesson": lesson,
                "section": section,
                "duration": duration,
                "file": rel_path,
                "bytes": len(content),
                "format": meta.get("type", "MP3_64"),
                "cached": False,
            }
        except Exception as exc:
            last_err = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Failed track {idx} ({title}): {last_err}")


def main() -> None:
    AUDIO_ROOT.mkdir(parents=True, exist_ok=True)
    tracks = fetch_all_tracks()
    print(f"Fetched {len(tracks)} tracks from Ximalaya album {ALBUM_ID}")
    results: list[dict] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(download_one, t): t for t in tracks}
        done_count = 0
        for fut in as_completed(futures):
            done_count += 1
            try:
                res = fut.result()
                results.append(res)
                if done_count % 20 == 0 or done_count == len(tracks):
                    print(f"[{done_count}/{len(tracks)}] {res['title']} ({res['bytes']} bytes)")
            except Exception as exc:
                failures.append(str(exc))
                print(f"[ERROR] {exc}")

    results.sort(key=lambda r: r["index"])
    by_lesson: dict[str, list[dict]] = {}
    for r in results:
        by_lesson.setdefault(r["lesson"], []).append(r)

    manifest = {
        "album_id": ALBUM_ID,
        "album_title": "大家的日语初级1(第二版听力练习)",
        "anchor": "和富岳日语",
        "source_url": f"https://www.ximalaya.com/album/{ALBUM_ID}",
        "total_tracks": len(results),
        "lessons": by_lesson,
        "tracks": results,
    }
    manifest_path = AUDIO_ROOT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved manifest with {len(results)} tracks to {manifest_path} (failures={len(failures)})")

    # Symlink into minna-no-nihongo-agent/data/audio so the local teaching agent can also access it
    try:
        AGENT_AUDIO_LINK.parent.mkdir(parents=True, exist_ok=True)
        if not AGENT_AUDIO_LINK.exists():
            AGENT_AUDIO_LINK.symlink_to(AUDIO_ROOT)
            print(f"Linked {AGENT_AUDIO_LINK} -> {AUDIO_ROOT}")
    except Exception as exc:
        print(f"Symlink note: {exc}")


if __name__ == "__main__":
    main()
