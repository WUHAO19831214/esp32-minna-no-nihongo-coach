"""Mac mini <-> ESP32 live study & A-B audio repeater bridge + Web Companion Console (port 7865)."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Any
import urllib.error
import urllib.parse

from audio_engine import (
    AUDIO_ROOT,
    HOST_PLAYER,
    connect_bt_headphones,
    disconnect_bt_headphones,
    get_bt_status,
    get_lesson_tracks,
    load_manifest,
    scan_bt_devices,
    slice_track_mp3,
    synthesize_japanese_mp3,
)
from dataset import build_device_snapshot, record_quiz_submission
from device import (
    MAX_LUA_SLOTS,
    bootstrap,
    control,
    deploy_scripts,
    ensure_start,
    foreground_state,
    pending_deploy,
    request,
    switch_to_badge,
    upload_clip,
)
from discovery import _resolver

ROOT = Path(__file__).resolve().parents[1]
WEB_PORT = 7865


class BridgeState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        cfg = json.loads((ROOT / 'bridge.local.json').read_text(encoding='utf-8'))
        self.token: str = cfg['token']
        self.student_id: str = cfg.get('student_id', 'default_student')
        self.lesson: int = 6
        self.flash_cat: str = 'vocab'
        self.quiz_mode: str = 'fill_blank'
        self.volume: int = 80
        self.slow_tts: bool = False
        self.single_loop: bool = True
        bt_st = get_bt_status()
        self.bt_name: str = str(bt_st.get('name') or '未选择设备')
        self.bt_connected: bool = bool(bt_st.get('connected'))
        self.bt_devices: list[dict[str, Any]] = list(bt_st.get('devices') or [])
        self.bt_scanning: bool = False
        self.audio_target: str = 'bt' if self.bt_connected else 'esp32'
        self.last_bt_check_ts: float = time.monotonic()
        self.last_audio_req: dict[str, Any] | None = None
        self.clip_seq: int = int(time.time()) % 100000
        self.snapshot: dict[str, Any] = build_device_snapshot(
            lesson_num=self.lesson,
            flash_cat=self.flash_cat,
            quiz_mode=self.quiz_mode,
            student_id=self.student_id,
        )
        self.last_push_ok: bool = False
        self.last_push_ts: float = 0.0
        self.deploy_checked: bool = False
        self.pending_deploy: list[str] = []
        self.receiver_state: str = 'ok'
        self.next_recover_ts: float = 0.0
        self.last_event: dict[str, Any] | None = None

    def refresh_bt_status(self, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and (now - self.last_bt_check_ts) < 4.0:
            return {'name': self.bt_name, 'connected': self.bt_connected, 'devices': self.bt_devices}
        st = get_bt_status()
        with self.lock:
            self.bt_name = str(st.get('name') or self.bt_name or '未选择设备')
            self.bt_connected = bool(st.get('connected'))
            if isinstance(st.get('devices'), list):
                self.bt_devices = list(st['devices'])
            self.last_bt_check_ts = now
        return st

    def next_seq(self) -> int:
        with self.lock:
            self.clip_seq += 1
            return self.clip_seq

    def rebuild_snapshot(self, audio_cmd: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            snap = build_device_snapshot(
                lesson_num=self.lesson,
                flash_cat=self.flash_cat,
                quiz_mode=self.quiz_mode,
                student_id=self.student_id,
                audio_cmd=audio_cmd,
            )
            snap['bt_name'] = self.bt_name
            snap['bt_connected'] = self.bt_connected
            snap['bt_devices'] = self.bt_devices
            snap['bt_scanning'] = self.bt_scanning
            snap['audio_target'] = self.audio_target
            self.snapshot = snap
            (ROOT / 'evidence').mkdir(exist_ok=True)
            (ROOT / 'evidence/latest.json').write_text(
                json.dumps(snap, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
            )
            return snap


STATE = BridgeState()


RECOVER_BACKOFF_SEC = 30.0


def _check_pending_deploy_once() -> None:
    """Read-only check. Background code never uploads, stops or starts anything; deploy is explicit."""
    if STATE.deploy_checked:
        return
    result = pending_deploy()
    if result.get('ok'):
        STATE.pending_deploy = list(result.get('pending') or [])
        STATE.deploy_checked = True
        if STATE.pending_deploy:
            print(f'[Bridge] Scripts differ from device (deploy is explicit): {STATE.pending_deploy}', flush=True)


def recover_receiver() -> bool:
    """Bring back ONLY our receiver after a device reboot, and only when it cannot disturb another app.

    The screen owner is never changed here: if Mini/Air/another UI is in the foreground, or the job list is
    unreadable, or all 4 Lua slots are taken, we just record a waiting state and retry later.
    """
    now = time.monotonic()
    if now < STATE.next_recover_ts:
        return False
    STATE.next_recover_ts = now + RECOVER_BACKOFF_SEC
    fg = foreground_state()
    if not fg['reachable']:
        STATE.receiver_state = 'waiting_state_unknown'
        return False
    if fg['others']:
        STATE.receiver_state = 'waiting_other_app_active'
        return False
    if fg['slots_used'] >= MAX_LUA_SLOTS:
        STATE.receiver_state = 'waiting_free_slot'
        return False
    print('[Bridge Recover] Receiver missing, no other app active and a slot is free: starting receiver only.', flush=True)
    try:
        bootstrap()
        control('publish')
    except Exception as exc:
        STATE.receiver_state = 'recover_failed'
        print(f'[Bridge Recover] failed: {exc}', flush=True)
        return False
    STATE.receiver_state = 'ok'
    return True


def push_state(audio_cmd: dict[str, Any] | None = None, rebuild: bool = False) -> bool:
    try:
        if not STATE.bt_scanning:
            STATE.refresh_bt_status(force=False)
        if not STATE.deploy_checked:
            _check_pending_deploy_once()
        if rebuild:
            payload = STATE.rebuild_snapshot(audio_cmd=audio_cmd)
            raw_snap = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            up = json.loads(
                request(
                    '/api/files/upload?' + urllib.parse.urlencode({'path': '/apps/minna_nihongo/snapshot.json'}),
                    raw_snap,
                    'POST',
                    'application/octet-stream',
                    timeout=12.0,
                )
            )
            if not up.get('ok'):
                STATE.last_push_ok = False
                return False
            feed_data: dict[str, Any] = {
                'reload_snapshot': True,
                'lesson': STATE.lesson,
                'flash_cat': STATE.flash_cat,
                'quiz_mode': STATE.quiz_mode,
                'bt_name': STATE.bt_name,
                'bt_connected': STATE.bt_connected,
                'bt_devices': STATE.bt_devices,
                'bt_scanning': STATE.bt_scanning,
                'audio_target': STATE.audio_target,
            }
            if audio_cmd:
                feed_data['audio_cmd'] = audio_cmd
        else:
            with STATE.lock:
                feed_data = {
                    'heartbeat': True,
                    'summary': STATE.snapshot.get('summary'),
                    'bt_name': STATE.bt_name,
                    'bt_connected': STATE.bt_connected,
                    'bt_devices': STATE.bt_devices,
                    'bt_scanning': STATE.bt_scanning,
                    'audio_target': STATE.audio_target,
                }
            if audio_cmd:
                feed_data['audio_cmd'] = audio_cmd

        body = {'token': STATE.token, 'data': feed_data}
        try:
            res = json.loads(request('/api/lua/minna_nihongo/feed', body, 'POST', timeout=6.0))
        except urllib.error.HTTPError as http_err:
            if http_err.code != 404 or not recover_receiver():
                STATE.last_push_ok = False
                if http_err.code != 404:
                    raise
                return False
            res = json.loads(request('/api/lua/minna_nihongo/feed', body, 'POST', timeout=6.0))
        STATE.receiver_state = 'ok'
        ok = bool(res.get('ok'))
        STATE.last_push_ok = ok
        STATE.last_push_ts = time.time()
        return ok
    except Exception:
        STATE.last_push_ok = False
        return False


def _play_or_upload_tts(text: str) -> None:
    if not text or STATE.volume <= 0:
        return
    mp3_bytes = synthesize_japanese_mp3(text, volume_pct=STATE.volume, slow=STATE.slow_tts)
    # TTS replaces whatever track was playing; forget it so volume/target changes
    # or device-side continuation never resume the lesson audio afterwards.
    STATE.last_audio_req = None
    if STATE.audio_target == 'bt':
        HOST_PLAYER.play_mp3_bytes(mp3_bytes, volume_pct=STATE.volume)
        seq = STATE.next_seq()
        push_state(audio_cmd={'action': 'tts', 'target': 'bt', 'seq': seq}, rebuild=False)
    else:
        HOST_PLAYER.stop()
        if upload_clip(mp3_bytes):
            seq = STATE.next_seq()
            push_state(audio_cmd={'action': 'tts', 'target': 'esp32', 'seq': seq}, rebuild=False)


def _start_track_playback(
    track_index: int,
    start_sec: float,
    end_sec: float,
    ab_state: str,
    single_loop: bool | None = None,
) -> None:
    if single_loop is None:
        single_loop = STATE.single_loop
    if STATE.volume <= 0:
        HOST_PLAYER.stop()
        seq = STATE.next_seq()
        push_state(audio_cmd={'action': 'stop', 'target': STATE.audio_target, 'seq': seq}, rebuild=False)
        return

    if STATE.audio_target == 'bt':
        if ab_state == 'loop':
            mp3_bytes, actual_start, actual_end = slice_track_mp3(
                track_index=track_index,
                start_sec=start_sec,
                end_sec=end_sec,
                max_window_sec=120.0,
                volume_pct=STATE.volume,
            )
            HOST_PLAYER.play_ab_loop_mp3(mp3_bytes, volume_pct=STATE.volume)
        else:
            actual_start, actual_end = HOST_PLAYER.play_track_full(
                track_index=track_index,
                start_sec=start_sec,
                volume_pct=STATE.volume,
                single_loop=bool(single_loop),
            )
        seq = STATE.next_seq()
        push_state(
            audio_cmd={
                'action': 'play',
                'target': 'bt',
                'seq': seq,
                'start_sec': actual_start,
                'end_sec': actual_end,
                'ab_state': ab_state,
                'ab_a': int(round(actual_start)),
                'ab_b': int(round(actual_end)),
                'single_loop': bool(single_loop),
            },
            rebuild=False,
        )
    else:
        HOST_PLAYER.stop()
        max_win = 45.0 if ab_state == 'loop' else 35.0
        mp3_bytes, actual_start, actual_end = slice_track_mp3(
            track_index=track_index,
            start_sec=start_sec,
            end_sec=end_sec if ab_state == 'loop' else (start_sec + max_win),
            max_window_sec=max_win,
            volume_pct=STATE.volume,
        )
        if upload_clip(mp3_bytes):
            seq = STATE.next_seq()
            push_state(
                audio_cmd={
                    'action': 'play',
                    'target': 'esp32',
                    'seq': seq,
                    'start_sec': actual_start,
                    'end_sec': actual_end,
                    'ab_state': ab_state,
                    'ab_a': int(round(actual_start)),
                    'ab_b': int(round(actual_end)),
                    'single_loop': bool(single_loop),
                },
                rebuild=False,
            )


def handle_device_event(ev: dict[str, Any]) -> None:
    if not isinstance(ev, dict):
        return
    STATE.last_event = ev
    etype = ev.get('type')
    if ev.get('volume') is not None:
        STATE.volume = max(0, min(100, int(ev['volume'])))
    if ev.get('slow_tts') is not None:
        STATE.slow_tts = bool(ev['slow_tts'])
    if ev.get('single_loop') is not None:
        STATE.single_loop = bool(ev['single_loop'])
    if ev.get('audio_target') in ('esp32', 'bt'):
        STATE.audio_target = str(ev['audio_target'])
    print(f'[ESP32 Event] {json.dumps(ev, ensure_ascii=False)}', flush=True)

    if etype == 'select_lesson':
        HOST_PLAYER.stop()
        with STATE.lock:
            STATE.lesson = max(1, min(25, int(ev.get('lesson') or STATE.lesson)))
            if ev.get('flash_cat'):
                STATE.flash_cat = str(ev['flash_cat'])
            if ev.get('quiz_mode'):
                STATE.quiz_mode = str(ev['quiz_mode'])
        push_state(rebuild=True)

    elif etype == 'select_flash_cat':
        with STATE.lock:
            STATE.lesson = max(1, min(25, int(ev.get('lesson') or STATE.lesson)))
            STATE.flash_cat = str(ev.get('flash_cat') or 'vocab')
        push_state(rebuild=True)

    elif etype == 'select_quiz_mode':
        with STATE.lock:
            STATE.lesson = max(1, min(25, int(ev.get('lesson') or STATE.lesson)))
            STATE.quiz_mode = str(ev.get('quiz_mode') or 'fill_blank')
        push_state(rebuild=True)

    elif etype == 'bt_control':
        action = str(ev.get('action') or 'set_target')
        target = str(ev.get('target') or '').strip()
        if action == 'scan':
            with STATE.lock:
                STATE.bt_scanning = True
            push_state(rebuild=False)
            res = scan_bt_devices(duration_sec=4)
            with STATE.lock:
                STATE.bt_scanning = False
                STATE.bt_name = str(res.get('name') or STATE.bt_name or '未选择设备')
                STATE.bt_connected = bool(res.get('connected'))
                if isinstance(res.get('devices'), list):
                    STATE.bt_devices = list(res['devices'])
                STATE.last_bt_check_ts = time.monotonic()
            print(f'[Bluetooth Scan] {json.dumps(res, ensure_ascii=False)}', flush=True)
            push_state(rebuild=False)
        elif action in ('connect', 'pair'):
            STATE.audio_target = 'bt'
            res = connect_bt_headphones(target)
            with STATE.lock:
                STATE.bt_name = str(res.get('name') or target or STATE.bt_name or '蓝牙音频')
                STATE.bt_connected = bool(res.get('connected'))
                if isinstance(res.get('devices'), list):
                    STATE.bt_devices = list(res['devices'])
                STATE.last_bt_check_ts = time.monotonic()
            print(f'[Bluetooth Connect/Pair] {json.dumps(res, ensure_ascii=False)}', flush=True)
            push_state(rebuild=False)
            if STATE.volume > 0 and STATE.bt_connected:
                _play_or_upload_tts('Bluetoothオーディオに接続しました。')
        elif action == 'disconnect':
            res = disconnect_bt_headphones(target)
            HOST_PLAYER.stop()
            with STATE.lock:
                STATE.bt_connected = bool(res.get('connected'))
                if isinstance(res.get('devices'), list):
                    STATE.bt_devices = list(res['devices'])
                if not STATE.bt_connected:
                    STATE.audio_target = 'esp32'
                STATE.last_bt_check_ts = time.monotonic()
            push_state(rebuild=False)
        elif action == 'status':
            STATE.refresh_bt_status(force=True)
            push_state(rebuild=False)
        else:
            # set_target toggle between 'bt' and 'esp32'
            if STATE.audio_target == 'bt' and not STATE.bt_connected:
                res = connect_bt_headphones(target)
                with STATE.lock:
                    STATE.bt_name = str(res.get('name') or STATE.bt_name or '蓝牙音频')
                    STATE.bt_connected = bool(res.get('connected'))
                    if isinstance(res.get('devices'), list):
                        STATE.bt_devices = list(res['devices'])
                    STATE.last_bt_check_ts = time.monotonic()
            elif STATE.audio_target == 'esp32':
                HOST_PLAYER.stop()
            push_state(rebuild=False)
            if ev.get('playing') and STATE.last_audio_req:
                req = STATE.last_audio_req
                _start_track_playback(
                    track_index=req['track_index'],
                    start_sec=req['start_sec'],
                    end_sec=req['end_sec'],
                    ab_state=req['ab_state'],
                )

    elif etype == 'quiz_answer':
        if ev.get('lesson'):
            STATE.lesson = max(1, min(25, int(ev['lesson'])))
        q_idx = int(ev.get('quiz_index') or 1)
        chosen = int(ev.get('chosen') or 1)
        mode = str(ev.get('quiz_mode') or STATE.quiz_mode)
        quizzes_by_mode = STATE.snapshot.get('quizzes_by_mode') or {}
        quizzes = quizzes_by_mode.get(mode) or STATE.snapshot.get('quizzes') or []
        quiz_item = quizzes[q_idx - 1] if 1 <= q_idx <= len(quizzes) else {}
        if quiz_item:
            rec = record_quiz_submission(
                student_id=STATE.student_id,
                lesson=f'{STATE.lesson:03d}',
                quiz_item=quiz_item,
                chosen_index=chosen,
            )
            print(f'[Quiz Sync -> minna-no-nihongo-agent] {json.dumps(rec, ensure_ascii=False)}', flush=True)
        speak_text = str(ev.get('speak') or quiz_item.get('speak') or '').strip()
        if speak_text and STATE.volume > 0:
            _play_or_upload_tts(speak_text)

    elif etype == 'speak':
        text = str(ev.get('text') or '').strip()
        if text and STATE.volume > 0:
            _play_or_upload_tts(text)

    elif etype == 'audio_play':
        track_index = int(ev.get('track_index') or 1)
        start_sec = float(ev.get('start_sec') or 0)
        end_sec = float(ev.get('end_sec') or (start_sec + 30))
        ab_state = str(ev.get('ab_state') or 'off')
        STATE.last_audio_req = {
            'track_index': track_index,
            'start_sec': start_sec,
            'end_sec': end_sec,
            'ab_state': ab_state,
            'single_loop': STATE.single_loop,
        }
        _start_track_playback(
            track_index=track_index,
            start_sec=start_sec,
            end_sec=end_sec,
            ab_state=ab_state,
            single_loop=STATE.single_loop,
        )

    elif etype in ('volume_change', 'toggle_single_loop'):
        if STATE.volume <= 0:
            HOST_PLAYER.stop()
            seq = STATE.next_seq()
            push_state(audio_cmd={'action': 'stop', 'target': STATE.audio_target, 'seq': seq}, rebuild=False)
        elif ev.get('playing') and STATE.last_audio_req:
            req = STATE.last_audio_req
            req['single_loop'] = STATE.single_loop
            _start_track_playback(
                track_index=req['track_index'],
                start_sec=req['start_sec'],
                end_sec=req['end_sec'],
                ab_state=req['ab_state'],
                single_loop=STATE.single_loop,
            )

    elif etype in ('audio_stop', 'audio_ab_clear'):
        HOST_PLAYER.stop()
        seq = STATE.next_seq()
        push_state(
            audio_cmd={
                'action': 'stop',
                'target': STATE.audio_target,
                'seq': seq,
            },
            rebuild=False,
        )


_last_ack_id: str | int | None = None


def poll_events_once() -> int:
    global _last_ack_id
    count = 0
    try:
        for _ in range(8):
            req_body: dict[str, Any] = {'token': STATE.token}
            if _last_ack_id is not None:
                req_body['ack'] = _last_ack_id
            res = json.loads(
                request('/api/lua/minna_nihongo/events', req_body, 'POST', timeout=4.0)
            )
            ev = res.get('event')
            if not isinstance(ev, dict):
                break
            ev_id = ev.get('id')
            if ev_id == _last_ack_id:
                break
            _last_ack_id = ev_id
            handle_device_event(ev)
            request('/api/lua/minna_nihongo/events', {'token': STATE.token, 'ack': ev_id}, 'POST', timeout=4.0)
            count += 1
        return count
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return count
        return count
    except Exception:
        return count


WEB_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>大家的日语 · ESP32 学习端与喜马拉雅 AB 复读控制台</title>
<style>
  :root {
    --bg: #080d14;
    --panel: #121c2a;
    --card: #1a273a;
    --border: #2a3c56;
    --teal: #5ce6c0;
    --blue: #7cb2ff;
    --orange: #ffad75;
    --text: #f0f5fc;
    --dim: #8b9bb1;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans", sans-serif;
    background: var(--bg); color: var(--text); padding: 20px;
  }
  .wrap { max-width: 1120px; margin: 0 auto; }
  header {
    display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
    background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
    padding: 16px 22px; margin-bottom: 18px; gap: 12px;
  }
  h1 { font-size: 20px; margin: 0; display: flex; align-items: center; gap: 10px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--teal); display: inline-block; }
  .btns { display: flex; gap: 10px; flex-wrap: wrap; }
  button {
    background: var(--card); color: var(--text); border: 1px solid var(--border);
    padding: 8px 14px; border-radius: 10px; cursor: pointer; font-weight: 600; font-size: 13px;
  }
  button.primary { background: var(--teal); color: #08121a; border-color: var(--teal); }
  button.orange { background: var(--orange); color: #08121a; border-color: var(--orange); }
  .grid { display: grid; grid-template-columns: 340px 1fr; gap: 18px; }
  @media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 16px; padding: 18px;
  }
  h2 { font-size: 16px; margin: 0 0 12px 0; color: var(--teal); }
  .lesson-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; margin-bottom: 14px; }
  .lesson-grid button { padding: 8px 4px; font-size: 12px; }
  .lesson-grid button.active { background: var(--teal); color: #08121a; }
  .track-list { max-height: 360px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
  .track-item {
    display: flex; align-items: center; justify-content: space-between;
    background: var(--card); padding: 10px 14px; border-radius: 10px; border: 1px solid transparent;
  }
  .track-item.active { border-color: var(--teal); }
  .ab-box {
    background: var(--card); border-radius: 12px; padding: 14px; margin-top: 14px;
    border: 1px solid var(--border);
  }
  .ab-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 10px; }
  input[type=number] {
    width: 76px; padding: 6px 8px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); font-size: 14px;
  }
  audio { width: 100%; margin-top: 10px; }
  .stat { font-size: 13px; color: var(--dim); line-height: 1.7; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span class="dot" id="connDot"></span> 大家的日语 · ESP32 随身学习端 & 喜马拉雅 AB 复读机</h1>
    <div class="btns">
      <button class="primary" onclick="apiCall('/api/esp/activate')">在 ESP32 上打开「大家的日语」</button>
      <button onclick="apiCall('/api/esp/badge')">切回「AI Mac mini 状态牌」</button>
    </div>
  </header>
  <div class="grid">
    <div class="card">
      <h2>课次选择 (与 ESP32 实时同步 L01-L25)</h2>
      <div class="lesson-grid" id="lessonGrid"></div>
      <h2>学情与 GNN 导学状态</h2>
      <div class="stat" id="summaryBox">正在读取学情...</div>
    </div>
    <div class="card">
      <h2 id="trackHeader">喜马拉雅《大家的日语初级1(第二版听力练习)》全套离线音轨 (238首)</h2>
      <div class="ab-box">
        <div style="font-weight:600;margin-bottom:6px" id="nowPlayingTitle">点击下方任意音轨试听或投送到 ESP32 喇叭复读</div>
        <audio id="webAudio" controls></audio>
        <div class="ab-row">
          <button onclick="markWebA()">设为起点 A</button>
          <span>A(秒): <input type="number" id="inA" value="0" min="0"/></span>
          <button onclick="markWebB()">设为终点 B</button>
          <span>B(秒): <input type="number" id="inB" value="12" min="1"/></span>
          <button class="orange" onclick="beamABToEsp(true)">投送 AB 段到 ESP32 喇叭循环复读</button>
          <button class="primary" onclick="beamABToEsp(false)">从 A 点播放 (ESP32 喇叭)</button>
        </div>
      </div>
      <div class="track-list" id="trackList" style="margin-top:14px"></div>
    </div>
  </div>
</div>
<script>
let currentLesson = 6;
let currentTrackIndex = 52;
let tracks = [];
const audio = document.getElementById('webAudio');
let webLoop = false;

audio.addEventListener('timeupdate', () => {
  if (webLoop) {
    const a = parseFloat(document.getElementById('inA').value || 0);
    const b = parseFloat(document.getElementById('inB').value || 0);
    if (b > a && audio.currentTime >= b) {
      audio.currentTime = a;
      audio.play();
    }
  }
});

function markWebA() {
  document.getElementById('inA').value = Math.floor(audio.currentTime || 0);
}
function markWebB() {
  const a = parseInt(document.getElementById('inA').value || 0);
  const b = Math.max(a + 2, Math.ceil(audio.currentTime || (a + 6)));
  document.getElementById('inB').value = b;
  webLoop = true;
}

async function apiCall(url, body) {
  const res = await fetch(url, {
    method: body ? 'POST' : 'GET',
    headers: body ? {'Content-Type': 'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await res.json();
  await loadState();
  return data;
}

async function selectLesson(n) {
  currentLesson = n;
  await apiCall('/api/esp/lesson', {lesson: n});
}

function playWebTrack(tr) {
  currentTrackIndex = tr.index;
  document.getElementById('nowPlayingTitle').textContent = `当前音轨: ${tr.title} (${tr.section} · ${tr.duration}s)`;
  audio.src = `/api/audio/${tr.index}.mp3`;
  audio.play();
  renderTracks();
}

async function beamABToEsp(loop) {
  const a = parseInt(document.getElementById('inA').value || 0);
  const b = parseInt(document.getElementById('inB').value || 15);
  await apiCall('/api/esp/ab', {
    track_index: currentTrackIndex,
    start_sec: a,
    end_sec: Math.max(a + 2, b),
    ab_state: loop ? 'loop' : 'off'
  });
}

function renderTracks() {
  const box = document.getElementById('trackList');
  box.innerHTML = '';
  for (const tr of tracks) {
    const row = document.createElement('div');
    row.className = 'track-item' + (tr.index === currentTrackIndex ? ' active' : '');
    row.innerHTML = `
      <div>
        <div style="font-weight:600">${tr.title}</div>
        <div style="font-size:12px;color:var(--dim)">类型: ${tr.section} · 时长: ${tr.duration}秒</div>
      </div>
      <div class="btns">
        <button onclick='playWebTrack(${JSON.stringify(tr)})'>网页试听</button>
        <button class="primary" onclick='currentTrackIndex=${tr.index};beamABToEsp(false)'>ESP32 播放</button>
      </div>
    `;
    box.appendChild(row);
  }
}

async function loadState() {
  const res = await fetch('/api/state');
  const st = await res.json();
  currentLesson = st.lesson;
  tracks = st.tracks || [];
  if (tracks.length && !tracks.some(t => t.index === currentTrackIndex)) {
    currentTrackIndex = tracks[0].index;
  }
  const g = document.getElementById('lessonGrid');
  g.innerHTML = '';
  for (let i = 1; i <= 25; i++) {
    const b = document.createElement('button');
    b.textContent = `第${String(i).padStart(2,'0')}课`;
    if (i === currentLesson) b.className = 'active';
    b.onclick = () => selectLesson(i);
    g.appendChild(b);
  }
  const s = st.summary || {};
  document.getElementById('summaryBox').innerHTML = `
    <div><b>ESP32 在线状态:</b> ${st.esp_online ? '<span style="color:#5ce6c0">已连接 (' + st.device_host + ')</span>' : '等待连接'}</div>
    <div><b>当前同步课次:</b> 第 ${String(st.lesson).padStart(2,'0')} 课</div>
    <div><b>累计练习交互:</b> ${s.total_interactions || 0} 次 (正确率 ${s.accuracy || 100}%)</div>
    <div><b>待复习错题数:</b> ${s.pending_reviews || 0} 题</div>
    <div><b>GNN 推荐重点:</b> <span style="color:#ffad75">${s.gnn_focus || '判断句'}</span></div>
    <div><b>推荐行动:</b> ${s.gnn_action || ''}</div>
  `;
  renderTracks();
}
loadState();
setInterval(loadState, 4000);
</script>
</body>
</html>
"""


class WebHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ('/', '/index.html'):
            raw = WEB_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if parsed.path == '/api/state':
            snap = STATE.snapshot
            self._send_json(
                {
                    'ok': True,
                    'esp_online': STATE.last_push_ok,
                    'device_host': _resolver.host or '192.168.31.124',
                    'lesson': STATE.lesson,
                    'flash_cat': STATE.flash_cat,
                    'quiz_mode': STATE.quiz_mode,
                    'audio_target': STATE.audio_target,
                    'bt_name': STATE.bt_name,
                    'bt_connected': STATE.bt_connected,
                    'receiver_state': STATE.receiver_state,
                    'pending_deploy': STATE.pending_deploy,
                    'summary': snap.get('summary'),
                    'tracks': get_lesson_tracks(f'{STATE.lesson:03d}'),
                }
            )
            return

        if parsed.path.startswith('/api/audio/') and parsed.path.endswith('.mp3'):
            try:
                idx = int(parsed.path.split('/')[-1].removesuffix('.mp3'))
                manifest = load_manifest()
                item = next((t for t in manifest.get('tracks', []) if int(t['index']) == idx), None)
                if not item:
                    self.send_error(404)
                    return
                mp3_path = AUDIO_ROOT / item['file']
                data = mp3_path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'audio/mpeg')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception:
                self.send_error(404)
                return

        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length) or b'{}') if length > 0 else {}

        if self.path == '/api/esp/lesson':
            handle_device_event({'type': 'select_lesson', 'lesson': int(body.get('lesson') or 6)})
            self._send_json({'ok': True, 'lesson': STATE.lesson})
            return

        if self.path == '/api/esp/bt':
            handle_device_event(
                {
                    'type': 'bt_control',
                    'action': str(body.get('action') or 'connect'),
                    'audio_target': str(body.get('audio_target') or 'bt'),
                    'target': str(body.get('target') or ''),
                }
            )
            self._send_json(
                {
                    'ok': True,
                    'audio_target': STATE.audio_target,
                    'bt_name': STATE.bt_name,
                    'bt_connected': STATE.bt_connected,
                }
            )
            return

        if self.path == '/api/esp/ab':
            handle_device_event(
                {
                    'type': 'audio_play',
                    'track_index': int(body.get('track_index') or 52),
                    'start_sec': float(body.get('start_sec') or 0),
                    'end_sec': float(body.get('end_sec') or 15),
                    'ab_state': str(body.get('ab_state') or 'loop'),
                }
            )
            self._send_json({'ok': True})
            return

        if self.path == '/api/esp/activate':
            # Explicit user switch of the screen to the Japanese app.
            out = ensure_start(explicit=True)
            if out.get('ok'):
                push_state(rebuild=True)
            self._send_json({'ok': bool(out.get('ok')), 'result': out})
            return

        if self.path == '/api/esp/badge':
            # Explicit user switch back to AI Mac mini. Reports recoverable states honestly.
            out = switch_to_badge()
            self._send_json({'ok': bool(out.get('ok')), 'result': out})
            return

        if self.path == '/api/esp/deploy':
            # Explicit deploy of changed scripts; never starts or activates the UI.
            out = deploy_scripts()
            self._send_json({'ok': bool(out.get('ok')), 'result': out})
            return

        self.send_error(404)


def run_daemon() -> None:
    server = ThreadingHTTPServer(('127.0.0.1', WEB_PORT), WebHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f'[Minna no Nihongo Bridge] Web console running at http://127.0.0.1:{WEB_PORT}', flush=True)

    push_state(rebuild=True)
    last_heartbeat = time.monotonic()

    while True:
        try:
            poll_events_once()
            now = time.monotonic()
            if now - last_heartbeat >= 5.0:
                push_state(rebuild=False)
                last_heartbeat = now
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f'[Bridge Loop Warning] {exc}', flush=True)
        time.sleep(0.25)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Push snapshot once and exit')
    args = parser.parse_args()
    if args.once:
        ok = push_state(rebuild=True)
        print(json.dumps({'ok': ok, 'lesson': STATE.lesson}, ensure_ascii=False))
        raise SystemExit(0 if ok else 1)
    run_daemon()
