#!/usr/bin/env python3
"""Build DFN1 Unicode bitmap fonts (ASCII + Japanese Kana/Kanji + Simplified Chinese) for ESP-Mosaico."""
from __future__ import annotations

from pathlib import Path
import shutil
import struct
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "app" / "assets"
SCRIPTS_DIR = ROOT / "app" / "scripts"
AGENT_DATA_DIR = Path(
    "/Users/wuhao/LocalProjects/Codex/macmini/local-teaching-agent/minna-no-nihongo-agent/data"
)
AUDIO_MANIFEST = ROOT / "audio" / "minna_no_nihongo_1" / "manifest.json"
BADGE_ASSETS = Path("/Users/wuhao/LocalProjects/Codex/macmini/ESP32 in macmini/app/assets")

# Extra UI characters to guarantee 100% coverage of all UI labels, feedback, and explanations
UI_EXTRA_TEXT = (
    "大家的日语初级听力练习复读机词句闪卡刷题巩固错题本选课学情掌握度推荐"
    "第一二三四五六七八九十百千零课单词文型例文会话练习问题开篇介绍"
    "填空改错中译日自主输入正面背面翻面发音播放暂停停止循环起点终点设置清除微调"
    "上一张下一张上一题下一题上一轨下一轨正确错误解析诊断语法句型读音意思"
    "请先选择至少一个教材资源点击或按右键揭晓答案按左键切换已连接离线本地缓存"
    "喇叭外放音量亮度保存取消返回退出秒分时次待复习已掌握风险先修"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    " !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~，。！？、；：“”‘’（）【】《》「」『』・ー〜…—＋－＜＞／"
)


def collect_charset() -> list[int]:
    chars: set[str] = set(chr(c) for c in range(32, 127))
    chars.update(UI_EXTRA_TEXT)
    # Full Hiragana (3040-309F), Katakana (30A0-30FF), CJK Symbols/Punctuation (3000-303F)
    for cp in range(0x3000, 0x3100):
        chars.add(chr(cp))
    # Fullwidth ASCII/punctuation
    for cp in range(0xFF01, 0xFF60):
        chars.add(chr(cp))

    if AGENT_DATA_DIR.exists():
        for p in AGENT_DATA_DIR.rglob("*"):
            if p.suffix in {".csv", ".json", ".md"} and "raw_backup" not in str(p) and "sessions" not in str(p):
                try:
                    chars.update(p.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    pass

    if AUDIO_MANIFEST.exists():
        try:
            chars.update(AUDIO_MANIFEST.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass

    for scan_dir in [ROOT / "app" / "scripts", ROOT / "host"]:
        if scan_dir.exists():
            for p in scan_dir.rglob("*"):
                if p.suffix in {".lua", ".py", ".json"}:
                    try:
                        chars.update(p.read_text(encoding="utf-8", errors="ignore"))
                    except Exception:
                        pass

    chars.update("　✓✗○×★☆♪▶⏸")

    codepoints = sorted(
        cp for cp in (ord(c) for c in chars)
        if cp in {32, 0x3000} or (cp > 32 and not chr(cp).isspace() and cp <= 0xFFFF)
    )
    return codepoints


def pack_1bit(im: Image.Image) -> bytes:
    w, h = im.size
    stride = (w + 7) // 8
    out = bytearray(stride * h)
    px = im.load()
    for row in range(h):
        base = row * stride
        for col in range(w):
            if px[col, row] >= 110:
                out[base + (col // 8)] |= 0x80 >> (col % 8)
    return bytes(out)


def build_dfn1(size: int, line_height: int, codepoints: list[int]) -> tuple[bytes, dict[int, int]]:
    font_primary = ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", size)
    font_fallback = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", size)

    entries: list[bytes] = []
    bitmaps = bytearray()
    ascii_advances: dict[int, int] = {}

    for cp in codepoints:
        ch = chr(cp)
        font = font_primary
        mask_check = font.getmask(ch)
        if cp != 32 and (mask_check.size[0] == 0 or mask_check.size[1] == 0):
            font = font_fallback

        if cp < 128:
            bbox = font.getbbox(ch)
            adv = max(4, int(round(font.getlength(ch))))
            if cp == 32:
                adv = max(4, size // 3)
            w = max(adv, (bbox[2] if bbox else adv))
            w = max(1, min(w, size + 4))
            ascii_advances[cp] = adv
        else:
            adv = size
            w = size

        canvas = Image.new("L", (w, line_height), 0)
        if cp not in {32, 0x3000}:
            draw = ImageDraw.Draw(canvas)
            y_offset = max(0, (line_height - size) // 2 - 1)
            draw.text((0, y_offset), ch, font=font, fill=255)
        bmp = pack_1bit(canvas)

        offset = len(bitmaps)
        bitmaps.extend(bmp)
        entries.append(struct.pack("<IIhhhHH", cp, offset, adv, 0, 0, w, line_height))

    header = struct.pack(
        "<4shhhHII",
        b"DFN1",
        line_height,
        0,
        size,
        0,
        len(codepoints),
        63,  # '?' fallback
    )
    return header + b"".join(entries) + bytes(bitmaps), ascii_advances


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    codepoints = collect_charset()
    print(f"Collected {len(codepoints)} unique Unicode codepoints for DFN1 CJK fonts")

    # Build CJK + ASCII DFN1 fonts in sizes 16, 20, 26
    specs = [
        (16, 18, "cjk-16.dfn"),
        (20, 22, "cjk-20.dfn"),
        (26, 28, "cjk-26.dfn"),
    ]
    metrics_lua_lines = [
        "-- Auto-generated by scripts/build_fonts.py",
        "return {",
    ]
    for size, lh, filename in specs:
        blob, ascii_adv = build_dfn1(size, lh, codepoints)
        out_path = ASSETS_DIR / filename
        out_path.write_bytes(blob)
        print(f"Wrote {out_path.name}: {len(blob):,} bytes ({len(codepoints)} glyphs)")
        adv_items = ",".join(f"[{k}]={v}" for k, v in sorted(ascii_adv.items()))
        metrics_lua_lines.append(f"  [{size}]={{height={lh},cjk_advance={size},advance={{{adv_items}}}}},")

    # Also copy rounded-40.dfn for big clock/timer digits and include its metrics
    if (BADGE_ASSETS / "rounded-40.dfn").exists():
        shutil.copy2(BADGE_ASSETS / "rounded-40.dfn", ASSETS_DIR / "rounded-40.dfn")
        metrics_lua_lines.append(
            "  [40]={height=30,cjk_advance=40,advance={[48]=25,[49]=25,[50]=25,[51]=25,[52]=25,[53]=25,[54]=25,[55]=25,[56]=25,[57]=25,[58]=14,[63]=24}},"
        )

    metrics_lua_lines.append("}")
    (SCRIPTS_DIR / "font_metrics.lua").write_text("\n".join(metrics_lua_lines) + "\n", encoding="utf-8")
    print("Wrote app/scripts/font_metrics.lua")


if __name__ == "__main__":
    main()
