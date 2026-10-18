"""ESP-Claw HTTP file and application manager for /apps/minna_nihongo."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from discovery import LOCAL_HTTP, _resolver, resolve_host

ROOT = Path(__file__).resolve().parents[1]
REMOTE = '/apps/minna_nihongo'
BADGE_BRIDGE_CFG = Path('/Users/wuhao/LocalProjects/Codex/macmini/ESP32 in macmini/bridge.local.json')


def request(path: str, data=None, method: str = 'GET', content_type: str | None = None, timeout: float = 15.0) -> bytes:
    host = resolve_host()
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        content_type = 'application/json'
    req = urllib.request.Request(
        'http://' + host + path,
        data=data,
        method=method,
        headers={'Content-Type': content_type} if content_type else {},
    )
    try:
        with LOCAL_HTTP.open(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError:
        raise
    except OSError:
        _resolver.invalidate()
        raise


def control(action: str, **extra) -> dict:
    cfg = json.loads((ROOT / 'bridge.local.json').read_text(encoding='utf-8'))
    return json.loads(
        request('/api/lua/minna_nihongo/control', {'token': cfg['token'], 'action': action, **extra}, 'POST')
    )


def pause_badge_ui() -> dict:
    """Politely stop ai_mac_mini UI & indicator_worker jobs so ESP32's 4-job limit has room for minna_nihongo."""
    if not BADGE_BRIDGE_CFG.exists():
        return {'ok': False, 'reason': 'no_badge_cfg'}
    try:
        bcfg = json.loads(BADGE_BRIDGE_CFG.read_text(encoding='utf-8'))
        token = bcfg.get('token')
        if not token:
            return {'ok': False, 'reason': 'no_token'}
        listed = json.loads(
            request('/api/lua/ai_mac_mini/control', {'token': token, 'action': 'jobs'}, 'POST')
        )
        stopped = []
        for line in (listed.get('result') or '').splitlines():
            if any(
                line.endswith(p)
                for p in (
                    'path=/nand/apps/ai_mac_mini/scripts/main.lua',
                    'path=/nand/apps/ai_mac_mini/scripts/indicator_worker.lua',
                    'path=/nand/apps/ai_badge/scripts/main.lua',
                    'path=/nand/apps/ai_badge/scripts/indicator_worker.lua',
                )
            ):
                jid = line.split(' | ', 1)[0]
                request('/api/lua/ai_mac_mini/control', {'token': token, 'action': 'stop', 'id': jid}, 'POST')
                stopped.append(jid)
        return {'ok': True, 'stopped': stopped}
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}


def switch_to_badge() -> dict:
    """Stop minna_nihongo UI and restore AI Mac mini UI + indicators."""
    try:
        stop_app()
    except Exception:
        pass
    if not BADGE_BRIDGE_CFG.exists():
        return {'ok': False, 'reason': 'no_badge_cfg'}
    bcfg = json.loads(BADGE_BRIDGE_CFG.read_text(encoding='utf-8'))
    token = bcfg['token']
    out = json.loads(request('/api/lua/ai_mac_mini/control', {'token': token, 'action': 'start'}, 'POST'))
    return {'ok': True, 'badge_start': out}


def stop_app() -> dict:
    stopped = []
    try:
        listed = control('jobs')
        if listed.get('ok'):
            for line in (listed.get('result') or '').splitlines():
                if line.endswith('path=/nand/apps/minna_nihongo/scripts/main.lua'):
                    job = line.split(' | ', 1)[0]
                    control('stop', id=job)
                    stopped.append(job)
            return {'ok': True, 'stopped_ui_jobs': stopped}
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    if BADGE_BRIDGE_CFG.exists():
        try:
            bcfg = json.loads(BADGE_BRIDGE_CFG.read_text(encoding='utf-8'))
            token = bcfg.get('token')
            if token:
                listed = json.loads(
                    request('/api/lua/ai_mac_mini/control', {'token': token, 'action': 'jobs'}, 'POST')
                )
                for line in (listed.get('result') or '').splitlines():
                    if line.endswith('path=/nand/apps/minna_nihongo/scripts/main.lua'):
                        job = line.split(' | ', 1)[0]
                        request('/api/lua/ai_mac_mini/control', {'token': token, 'action': 'stop', 'id': job}, 'POST')
                        stopped.append(job)
        except Exception:
            pass
    return {'ok': True, 'stopped_ui_jobs': stopped}


def ensure_start() -> dict:
    pause_badge_ui()
    listed = control('jobs')
    if listed.get('ok') and any(
        line.endswith('path=/nand/apps/minna_nihongo/scripts/main.lua')
        for line in (listed.get('result') or '').splitlines()
    ):
        return {'ok': True, 'already_running': True}
    return control('start')


def upload_clip(mp3_bytes: bytes) -> bool:
    out = json.loads(
        request(
            '/api/files/upload?' + urllib.parse.urlencode({'path': REMOTE + '/clip.mp3'}),
            mp3_bytes,
            'POST',
            'application/octet-stream',
            timeout=20.0,
        )
    )
    return bool(out.get('ok'))


def upload() -> None:
    from audio_engine import synthesize_japanese_mp3
    from dataset import build_device_snapshot, build_lesson_bundle

    (ROOT / 'evidence').mkdir(exist_ok=True)
    lessons_dir = ROOT / 'app/lessons'
    lessons_dir.mkdir(parents=True, exist_ok=True)
    for lnum in range(1, 26):
        bundle = build_lesson_bundle(lesson_num=lnum)
        (lessons_dir / f'L{lnum:02d}.json').write_text(
            json.dumps(bundle, ensure_ascii=False) + '\n', encoding='utf-8'
        )

    snapshot = build_device_snapshot(lesson_num=6, flash_cat='vocab', quiz_mode='fill_blank')
    (ROOT / 'app/snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False) + '\n', encoding='utf-8')
    (ROOT / 'evidence/latest.json').write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )

    try:
        existing = json.loads(request('/api/files?' + urllib.parse.urlencode({'path': REMOTE})))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        existing = None

    if existing:
        backup = ROOT / 'backups' / dt.datetime.now().strftime('%Y%m%d-%H%M%S')

        def copy(directory, listing):
            for entry in listing.get('entries', []):
                target = entry['path']
                assert target.startswith(REMOTE + '/')
                if entry['is_dir']:
                    copy(target, json.loads(request('/api/files?' + urllib.parse.urlencode({'path': target}))))
                else:
                    p = backup / target[len(REMOTE) + 1 :]
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(request('/files' + target))
                    if p.name == 'config.json':
                        p.chmod(0o600)

        copy(REMOTE, existing)
        print('Backed up existing package:', backup)

    try:
        stop_app()
        time.sleep(0.4)
    except Exception:
        pass

    for directory in [REMOTE, REMOTE + '/scripts', REMOTE + '/assets', REMOTE + '/lessons']:
        assert json.loads(request('/api/files/mkdir', {'path': directory, 'recursive': True}, 'POST')).get('ok')

    evidence = []
    for p in sorted((ROOT / 'app').rglob('*')):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT / 'app').as_posix()
        if existing and rel == 'config.json':
            try:
                current = json.loads(request('/files' + REMOTE + '/config.json'))
                if current.get('token') == json.loads(p.read_text(encoding='utf-8'))['token']:
                    continue
            except Exception:
                pass
        target = REMOTE + '/' + rel
        body = p.read_bytes()
        try:
            if request('/files' + target, timeout=8.0) == body:
                evidence.append({'path': target, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'cached': True})
                continue
        except Exception:
            pass
        for attempt in range(3):
            try:
                out = json.loads(
                    request(
                        '/api/files/upload?' + urllib.parse.urlencode({'path': target}),
                        body,
                        'POST',
                        'application/octet-stream',
                        timeout=25.0,
                    )
                )
                assert out.get('ok'), out
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(0.5)
        assert request('/files' + target) == body, target
        evidence.append({'path': target, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()})
        time.sleep(0.05)

    # Upload initial clip.mp3 so ESP32 audio verification and immediate playback work out of the box
    seed_mp3 = synthesize_japanese_mp3('皆の日本語、始めましょう。')
    assert upload_clip(seed_mp3)
    evidence.append(
        {'path': REMOTE + '/clip.mp3', 'bytes': len(seed_mp3), 'sha256': hashlib.sha256(seed_mp3).hexdigest()}
    )

    (ROOT / 'evidence/upload.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print('Uploaded and byte-verified', len(evidence), 'package files to', REMOTE)


class WebIM:
    """Minimal read-only WebSocket subscriber for native device-agent replies."""

    def __init__(self, host: str):
        self.sock = socket.create_connection((host, 80), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f'GET /ws/webim HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n'
                f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'
            ).encode()
        )
        self.buffer = b''
        while b'\r\n\r\n' not in self.buffer:
            self.buffer += self.sock.recv(4096)
        header, self.buffer = self.buffer.split(b'\r\n\r\n', 1)
        assert header.startswith(b'HTTP/1.1 101'), header

    def readn(self, n: int) -> bytes:
        while len(self.buffer) < n:
            part = self.sock.recv(65536)
            if not part:
                raise ConnectionError('Device WebSocket closed')
            self.buffer += part
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def frame(self) -> tuple[int, bytes]:
        a, b = self.readn(2)
        n = b & 127
        if n == 126:
            n = struct.unpack('!H', self.readn(2))[0]
        elif n == 127:
            n = struct.unpack('!Q', self.readn(8))[0]
        assert n <= 2 * 1024 * 1024
        mask = self.readn(4) if b & 128 else None
        body = self.readn(n)
        if mask:
            body = bytes(x ^ mask[i % 4] for i, x in enumerate(body))
        return a & 15, body

    def close(self) -> None:
        self.sock.close()


def bootstrap() -> None:
    host = resolve_host()
    paused = pause_badge_ui()
    print('Pre-bootstrap pause_badge_ui:', json.dumps(paused, ensure_ascii=False))

    def receiver_id():
        result = control('job', id='minna_nihongo_receiver')
        out = result.get('result') or ''
        match = re.search(r'^job_id=(\w+)', out)
        return match.group(1) if match and 'status=running' in out else None

    try:
        previous = receiver_id()
    except (OSError, ValueError):
        previous = None

    def ready():
        try:
            current = receiver_id()
            return current is not None and current != previous
        except (OSError, ValueError):
            return False

    ws = WebIM(host)
    chat = 'esp-minna-nihongo-install'
    prompt = (
        '安装独立应用 大家的日语（minna_nihongo），保留 ai_mac_mini、ai_badge 与 pomodoro_clock 的所有文件。'
        '若4个Lua异步任务槽位已满，请先用 lua_stop_async_job 停止 ai_mac_mini_ui 或 ai_mac_mini_indicators 任务（保留 ai_mac_mini_receiver）。'
        '然后使用 lua_run_script_async 启动 /nand/apps/minna_nihongo/scripts/receiver.lua，'
        'name 与 exclusive 都是 minna_nihongo_receiver，replace=true，timeout_ms=0，log_bytes=4096。'
        '无需编写或修改脚本，文件已经上传且校验。启动后用 lua_get_async_job 核实 running 并回复结果。'
    )
    print(request('/api/webim/send', {'chat_id': chat, 'text': prompt}, 'POST').decode('utf-8', errors='ignore'))
    deadline = time.monotonic() + 75
    messages = []
    try:
        while time.monotonic() < deadline:
            ws.sock.settimeout(min(3.0, max(0.1, deadline - time.monotonic())))
            try:
                opcode, body = ws.frame()
            except socket.timeout:
                if ready():
                    break
                continue
            if opcode == 1:
                d = json.loads(body)
                if d.get('chat_id') == chat:
                    messages.append(d)
                    print(str(d.get('text', ''))[:1400])
                    if ready():
                        break
    finally:
        ws.close()
    (ROOT / 'evidence/bootstrap.json').write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding='utf-8')
    assert ready(), 'minna_nihongo_receiver did not start; inspect native job status'
    print(request('/api/lua/minna_nihongo/status').decode('utf-8', errors='ignore'))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument(
        'action',
        choices=[
            'upload',
            'bootstrap',
            'publish',
            'start',
            'ensure-start',
            'discover',
            'stop',
            'verify',
            'status',
            'job',
            'jobs',
            'switch-to-badge',
        ],
    )
    p.add_argument('--id')
    p.add_argument('--smoke-ms', type=int)
    args = p.parse_args()
    if args.action == 'upload':
        upload()
    elif args.action == 'bootstrap':
        bootstrap()
    elif args.action == 'status':
        print(request('/api/lua/minna_nihongo/status').decode('utf-8', errors='ignore'))
    elif args.action == 'discover':
        print(json.dumps({'ok': True, 'device_host': resolve_host(force=True)}, ensure_ascii=False))
    elif args.action == 'ensure-start':
        print(json.dumps(ensure_start(), ensure_ascii=False))
    elif args.action == 'switch-to-badge':
        print(json.dumps(switch_to_badge(), ensure_ascii=False))
    elif args.action == 'stop' and not args.id:
        print(json.dumps(stop_app(), ensure_ascii=False))
    else:
        extra = {}
        if args.id:
            extra['id'] = args.id
        if args.smoke_ms:
            extra['args'] = {'smoke_ms': args.smoke_ms}
        print(json.dumps(control(args.action, **extra), ensure_ascii=False))
