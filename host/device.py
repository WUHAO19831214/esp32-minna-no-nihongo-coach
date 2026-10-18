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


OWN_MAIN = '/nand/apps/minna_nihongo/scripts/main.lua'
MAX_LUA_SLOTS = 4
# Foreground UI entry scripts, keyed by their FULL on-device path (never matched by suffix/substring).
FOREGROUND_UIS = {
    OWN_MAIN: 'minna',
    '/nand/apps/ai_mac_mini/scripts/main.lua': 'mini',
    '/nand/apps/ai_badge/scripts/main.lua': 'air',
    '/nand/apps/pomodoro_clock/scripts/main.lua': 'pomodoro',
}
MINI_MAIN = '/nand/apps/ai_mac_mini/scripts/main.lua'
STOP_WAIT_SEC = 6.0
STOP_POLL_SEC = 0.4
DEPLOY_FILES = ('main.lua', 'model.lua', 'render.lua', 'receiver.lua', 'ownership.lua', 'font_metrics.lua', 'verify.lua')
LUA_NETWORK_ERRORS = (OSError, ValueError, KeyError)


def _minna_token() -> str:
    return json.loads((ROOT / 'bridge.local.json').read_text(encoding='utf-8'))['token']


def _mini_token() -> str:
    return json.loads(BADGE_BRIDGE_CFG.read_text(encoding='utf-8'))['token']


def control(action: str, **extra) -> dict:
    return json.loads(
        request('/api/lua/minna_nihongo/control', {'token': _minna_token(), 'action': action, **extra}, 'POST')
    )


def mini_control(action: str, **extra) -> dict:
    """Mini's receiver route. Used only to list jobs and to stop a job whose full path was verified."""
    return json.loads(
        request('/api/lua/ai_mac_mini/control', {'token': _mini_token(), 'action': action, **extra}, 'POST')
    )


def parse_jobs(listing: str | None) -> list[dict]:
    jobs = []
    for line in (listing or '').splitlines():
        head = line.split(' | ', 1)
        match = re.search(r'path=(\S+)', line)
        if len(head) == 2 and match:
            jobs.append({'id': head[0].strip(), 'path': match.group(1), 'line': line})
    return jobs


def list_jobs() -> tuple[str | None, list[dict] | None]:
    """Return (route, jobs); (None, None) when no receiver route can list jobs (state unknown)."""
    for route, fn in (('minna', control), ('mini', mini_control)):
        try:
            res = fn('jobs')
        except LUA_NETWORK_ERRORS:
            continue
        if res.get('ok'):
            return route, parse_jobs(res.get('result'))
    return None, None


def foreground_state() -> dict:
    route, jobs = list_jobs()
    if jobs is None:
        return {'reachable': False, 'route': None, 'owner': 'unknown', 'others': [], 'jobs': [], 'slots_used': 0}
    owners = [(j['path'], FOREGROUND_UIS[j['path']]) for j in jobs if j['path'] in FOREGROUND_UIS]
    others = [(p, o) for p, o in owners if o != 'minna']
    owner = others[0][1] if others else ('minna' if owners else None)
    return {
        'reachable': True,
        'route': route,
        'owner': owner,
        'others': others,
        'jobs': jobs,
        'slots_used': len(jobs),
    }


def wait_until_gone(path: str, timeout: float | None = None) -> bool:
    """Confirm (by re-listing jobs) that no job with this exact path is left; unverifiable counts as not gone."""
    deadline = time.monotonic() + (STOP_WAIT_SEC if timeout is None else timeout)
    while True:
        _, jobs = list_jobs()
        if jobs is not None and not any(j['path'] == path for j in jobs):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(STOP_POLL_SEC)


def stop_app() -> dict:
    """Stop only this project's UI job (exact path match) and confirm resources are released."""
    route, jobs = list_jobs()
    if jobs is None:
        return {'ok': False, 'state': 'device_unreachable', 'recoverable': True, 'stopped_ui_jobs': []}
    mine = [j for j in jobs if j['path'] == OWN_MAIN]
    if not mine:
        return {'ok': True, 'state': 'not_running', 'stopped_ui_jobs': []}
    try:
        for job in mine:
            if route == 'minna':
                control('stop')  # cooperative exit through our own lifecycle queue
            else:
                mini_control('stop', id=job['id'])
    except LUA_NETWORK_ERRORS:
        return {'ok': False, 'state': 'stop_request_failed', 'recoverable': True, 'stopped_ui_jobs': []}
    released = wait_until_gone(OWN_MAIN)
    return {
        'ok': released,
        'state': 'stopped' if released else 'stop_not_confirmed',
        'recoverable': not released,
        'stopped_ui_jobs': [j['id'] for j in mine] if released else [],
    }


def release_other_foreground(fg: dict) -> dict:
    """EXPLICIT user switch only: stop other apps' foreground UI (main.lua, exact path). Receivers/indicators are kept."""
    released = []
    for path, _owner in fg.get('others', []):
        job = next((j for j in fg['jobs'] if j['path'] == path), None)
        if job is None:
            continue
        try:
            mini_control('stop', id=job['id'])
        except LUA_NETWORK_ERRORS:
            return {'ok': False, 'state': 'mini_route_unavailable', 'recoverable': True, 'released': released}
        if not wait_until_gone(path):
            return {'ok': False, 'state': 'release_not_confirmed', 'recoverable': True, 'released': released}
        released.append(path)
    return {'ok': True, 'released': released}


def switch_to_badge() -> dict:
    """EXPLICIT user switch: stop our UI (verified), then hand the screen to Mini if its route exists."""
    stopped = stop_app()
    if not stopped.get('ok'):
        return {'ok': False, 'state': stopped.get('state'), 'recoverable': True, 'stop': stopped}
    try:
        mini = mini_control('start')
    except LUA_NETWORK_ERRORS as exc:
        return {
            'ok': False,
            'state': 'mini_route_unavailable',
            'recoverable': True,
            'minna_ui_stopped': True,
            'detail': str(exc),
            'hint': 'Japanese UI stopped; open the AI Mac mini icon on the device desktop, nothing else was changed.',
        }
    _, jobs = list_jobs()
    running = jobs is not None and any(j['path'] == MINI_MAIN for j in jobs)
    return {
        'ok': bool(mini.get('ok')) and running,
        'state': 'mini_started' if running else 'mini_start_not_confirmed',
        'recoverable': not running,
        'minna_ui_stopped': True,
        'badge_start': mini,
    }


def pending_deploy() -> dict:
    """Read-only: which local package scripts differ from the device. Never uploads, stops or starts anything."""
    try:
        diff = []
        for name in DEPLOY_FILES:
            local = ROOT / 'app' / 'scripts' / name
            if not local.exists():
                continue
            try:
                same = request('/files' + REMOTE + '/scripts/' + name, timeout=6.0) == local.read_bytes()
            except urllib.error.HTTPError as err:
                if err.code != 404:
                    raise
                same = False
            if not same:
                diff.append(name)
        return {'ok': True, 'pending': diff}
    except LUA_NETWORK_ERRORS as exc:
        return {'ok': False, 'pending': [], 'error': str(exc)}


def deploy_scripts() -> dict:
    """EXPLICIT deploy: upload differing scripts. Refuses to overwrite scripts while our UI is running."""
    state = pending_deploy()
    if not state['ok'] or not state['pending']:
        return {**state, 'uploaded': []}
    _, jobs = list_jobs()
    if jobs is None:
        return {'ok': False, 'state': 'device_unreachable', 'recoverable': True, 'pending': state['pending'], 'uploaded': []}
    if any(j['path'] == OWN_MAIN for j in jobs):
        return {'ok': False, 'state': 'ui_running', 'recoverable': True, 'pending': state['pending'], 'uploaded': []}
    uploaded = []
    for name in state['pending']:
        body = (ROOT / 'app' / 'scripts' / name).read_bytes()
        out = json.loads(
            request(
                '/api/files/upload?' + urllib.parse.urlencode({'path': REMOTE + '/scripts/' + name}),
                body,
                'POST',
                'application/octet-stream',
                timeout=25.0,
            )
        )
        if not out.get('ok'):
            return {'ok': False, 'state': 'upload_failed', 'file': name, 'pending': state['pending'], 'uploaded': uploaded}
        uploaded.append(name)
    return {'ok': True, 'pending': [], 'uploaded': uploaded}


def ensure_start(explicit: bool = False) -> dict:
    """Start the Japanese UI. Activation is an explicit user switch; background callers get a refusal."""
    if not explicit:
        return {'ok': False, 'state': 'explicit_required', 'recoverable': True}
    fg = foreground_state()
    if not fg['reachable']:
        return {'ok': False, 'state': 'device_unreachable', 'recoverable': True}
    if fg['owner'] == 'minna':
        return {'ok': True, 'already_running': True}
    if fg['others']:
        released = release_other_foreground(fg)
        if not released.get('ok'):
            return released
        fg = foreground_state()
        if not fg['reachable']:
            return {'ok': False, 'state': 'device_unreachable', 'recoverable': True}
    if fg['route'] != 'minna':
        if fg['slots_used'] >= MAX_LUA_SLOTS:
            return {'ok': False, 'state': 'no_free_slot', 'recoverable': True}
        bootstrap()
    out = control('start')
    _, jobs = list_jobs()
    running = jobs is not None and any(j['path'] == OWN_MAIN for j in jobs)
    return {**out, 'ok': bool(out.get('ok')) and running, 'state': 'started' if running else 'start_not_confirmed'}


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

    stopped = stop_app()
    if not stopped.get('ok'):
        raise RuntimeError(f'Deploy aborted, own UI stop not confirmed: {stopped}')

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
    """Start ONLY minna_nihongo_receiver. Never stops, pauses or replaces any other app's task."""
    host = resolve_host()

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
        '安装独立应用 大家的日语（minna_nihongo），保留 ai_mac_mini、ai_badge 与 pomodoro_clock 的所有文件与正在运行的任务。'
        '严禁停止或替换任何其他应用的任务；若4个Lua异步任务槽位已满，不要做任何改动，直接回复“槽位已满”。'
        '有空闲槽位时，使用 lua_run_script_async 启动 /nand/apps/minna_nihongo/scripts/receiver.lua，'
        'name 与 exclusive 都是 minna_nihongo_receiver，replace=false，timeout_ms=0，log_bytes=4096。'
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
            'deploy',
            'pending-deploy',
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
        result = ensure_start(explicit=True)
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(0 if result.get('ok') else 1)
    elif args.action == 'deploy':
        result = deploy_scripts()
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(0 if result.get('ok') else 1)
    elif args.action == 'pending-deploy':
        print(json.dumps(pending_deploy(), ensure_ascii=False))
    elif args.action == 'switch-to-badge':
        result = switch_to_badge()
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(0 if result.get('ok') else 1)
    elif args.action == 'stop' and not args.id:
        print(json.dumps(stop_app(), ensure_ascii=False))
    else:
        extra = {}
        if args.id:
            extra['id'] = args.id
        if args.smoke_ms:
            extra['args'] = {'smoke_ms': args.smoke_ms}
        print(json.dumps(control(args.action, **extra), ensure_ascii=False))
