"""Mock regression checks for AI Mac mini / Minna no Nihongo coexistence on the shared ESP32.

Run:  python3 tests/coexistence_check.py

Everything is simulated (FakeDevice replaces the HTTP layer); nothing touches the real device, launchctl,
GitHub or the Codex directory. subprocess is blocked so any attempt to stop/start Mac services fails the run.
"""
from __future__ import annotations

import io
from pathlib import Path
import sys
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))

with mock.patch('audio_engine.get_bt_status', return_value={'name': 'x', 'connected': False, 'devices': []}):
    import bridge  # noqa: E402
import device  # noqa: E402

MINNA_RECEIVER = '/nand/apps/minna_nihongo/scripts/receiver.lua'
MINI_RECEIVER = '/nand/apps/ai_mac_mini/scripts/receiver.lua'
MINI_UI = device.MINI_MAIN
MINI_IND = '/nand/apps/ai_mac_mini/scripts/indicator_worker.lua'
AIR_RECEIVER = '/nand/apps/ai_badge/scripts/receiver.lua'
OWN_UI = device.OWN_MAIN


def http_error(code: int, url: str = 'http://fake') -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, 'fake', {}, io.BytesIO(b'{}'))


class FakeDevice:
    """Tiny simulation of the ESP32 Lua job table (4 slots) and the two receiver routes."""

    def __init__(self, paths, stop_works=True, mini_start_works=True, files=None):
        self.jobs = [(f'j{i}', p) for i, p in enumerate(paths)]
        self.seq = len(self.jobs)
        self.stop_works = stop_works
        self.mini_start_works = mini_start_works
        self.files = dict(files or {})
        self.mutations: list[tuple] = []
        self.list_calls = 0

    def has(self, path):
        return any(p == path for _, p in self.jobs)

    def add(self, path):
        self.seq += 1
        self.jobs.append((f'j{self.seq}', path))

    def remove_path(self, path):
        self.jobs = [(i, p) for i, p in self.jobs if p != path]

    def listing(self):
        return '\n'.join(f'{i} | running | name=n{i} | exclusive=e | runtime=9s | path={p}' for i, p in self.jobs)

    def request(self, path, data=None, method='GET', content_type=None, timeout=15.0):
        import json as _json

        body = data if isinstance(data, dict) else {}
        if path.startswith('/files'):
            if path[len('/files'):] in self.files:
                return self.files[path[len('/files'):]]
            raise http_error(404, path)
        if path.startswith('/api/files/upload'):
            self.mutations.append(('upload', path))
            return b'{"ok":true}'
        if path == '/api/lua/minna_nihongo/feed':
            if not self.has(MINNA_RECEIVER):
                raise http_error(404, path)
            return b'{"ok":true}'
        route = {'/api/lua/minna_nihongo/control': MINNA_RECEIVER, '/api/lua/ai_mac_mini/control': MINI_RECEIVER}.get(path)
        if route is None:
            raise AssertionError(f'unexpected request {path}')
        if not self.has(route):
            raise http_error(404, path)
        mini_route = path.endswith('/ai_mac_mini/control')
        action = body.get('action')
        if action == 'jobs':
            self.list_calls += 1
            return _json.dumps({'ok': True, 'result': self.listing()}).encode()
        if action == 'publish':
            self.mutations.append(('publish',))
        elif action == 'stop' and not mini_route:
            self.mutations.append(('stop_minna_exit',))
            if self.stop_works:
                self.remove_path(OWN_UI)
        elif action == 'stop' and mini_route:
            target = next(((i, p) for i, p in self.jobs if i == body.get('id')), None)
            self.mutations.append(('stop', body.get('id'), target[1] if target else None))
            if self.stop_works and target:
                self.remove_path(target[1])
        elif action == 'start' and mini_route:
            self.mutations.append(('start_mini',))
            if self.mini_start_works and len(self.jobs) < device.MAX_LUA_SLOTS:
                self.add(MINI_UI)
        elif action == 'start' and not mini_route:
            self.mutations.append(('start_minna',))
            self.add(OWN_UI)
        return b'{"ok":true,"result":"ok"}'

    def bootstrap(self):
        self.mutations.append(('bootstrap',))
        self.add(MINNA_RECEIVER)


class CoexistenceTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        for target in ('subprocess.run', 'subprocess.Popen'):
            mock.patch(target, side_effect=AssertionError('no Mac service / launchctl commands allowed')).start()
        mock.patch.object(device, '_minna_token', return_value='t').start()
        mock.patch.object(device, '_mini_token', return_value='t').start()
        mock.patch.object(device.time, 'sleep', lambda _s: None).start()
        mock.patch.object(device, 'STOP_WAIT_SEC', 0.05).start()
        mock.patch.object(bridge.STATE, 'refresh_bt_status', return_value={}).start()
        bridge.STATE.deploy_checked = True
        bridge.STATE.pending_deploy = []
        bridge.STATE.receiver_state = 'ok'
        bridge.STATE.next_recover_ts = 0.0

    def install(self, fake: FakeDevice):
        mock.patch.object(device, 'request', fake.request).start()
        mock.patch.object(bridge, 'request', fake.request).start()
        mock.patch.object(device, 'bootstrap', fake.bootstrap).start()
        mock.patch.object(bridge, 'bootstrap', fake.bootstrap).start()
        return fake

    # -- background behaviour -------------------------------------------------------------------------------
    def test_background_404_with_mini_active_does_not_bootstrap_or_steal(self):
        fake = self.install(FakeDevice([MINI_RECEIVER, MINI_UI, MINI_IND]))
        self.assertFalse(bridge.push_state())
        self.assertEqual(fake.mutations, [])
        self.assertEqual(bridge.STATE.receiver_state, 'waiting_other_app_active')
        self.assertTrue(fake.has(MINI_UI) and fake.has(MINI_IND) and fake.has(MINI_RECEIVER))

    def test_backoff_then_receiver_only_recovery_when_mini_idle(self):
        fake = self.install(FakeDevice([MINI_RECEIVER, MINI_UI, MINI_IND]))
        bridge.push_state()
        calls = fake.list_calls
        bridge.push_state()  # within backoff window: no new device inspection at all
        self.assertEqual(fake.list_calls, calls)
        fake.remove_path(MINI_UI)  # user left Mini on their own
        bridge.STATE.next_recover_ts = 0.0
        self.assertTrue(bridge.push_state())
        self.assertEqual(fake.mutations, [('bootstrap',), ('publish',)])  # receiver + icon only, never a UI start
        self.assertEqual(bridge.STATE.receiver_state, 'ok')

    def test_404_with_all_slots_taken_waits_without_touching_anything(self):
        fake = self.install(FakeDevice([MINI_RECEIVER, MINI_IND, AIR_RECEIVER, '/nand/apps/other/scripts/worker.lua']))
        self.assertFalse(bridge.push_state())
        self.assertEqual(fake.mutations, [])
        self.assertEqual(bridge.STATE.receiver_state, 'waiting_free_slot')

    def test_404_with_unreadable_device_state_waits(self):
        fake = self.install(FakeDevice([]))
        self.assertFalse(bridge.push_state())
        self.assertEqual(fake.mutations, [])
        self.assertEqual(bridge.STATE.receiver_state, 'waiting_state_unknown')

    def test_startup_script_check_is_read_only(self):
        fake = self.install(
            FakeDevice([MINI_RECEIVER, MINNA_RECEIVER, OWN_UI], files={'/apps/minna_nihongo/scripts/main.lua': b'old'})
        )
        bridge.STATE.deploy_checked = False
        self.assertTrue(bridge.push_state())
        self.assertIn('main.lua', bridge.STATE.pending_deploy)
        self.assertEqual(fake.mutations, [])
        self.assertTrue(fake.has(OWN_UI))

    # -- activation / deploy are explicit and separate ------------------------------------------------------------
    def test_ensure_start_refuses_without_explicit_flag(self):
        fake = self.install(FakeDevice([MINI_RECEIVER, MINI_UI, MINNA_RECEIVER]))
        self.assertEqual(device.ensure_start()['state'], 'explicit_required')
        self.assertEqual(fake.mutations, [])

    def test_explicit_activation_stops_only_mini_ui_by_exact_path(self):
        fake = self.install(FakeDevice([MINI_RECEIVER, MINI_UI, MINNA_RECEIVER, AIR_RECEIVER]))
        out = device.ensure_start(explicit=True)
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['state'], 'started')
        self.assertEqual([m[0] for m in fake.mutations], ['stop', 'start_minna'])
        self.assertEqual(fake.mutations[0][2], MINI_UI)
        self.assertTrue(fake.has(MINI_RECEIVER) and fake.has(AIR_RECEIVER))  # receivers (incl. Air) untouched

    def test_lookalike_paths_are_never_stopped_or_counted(self):
        lookalike_own = '/tmp/x' + OWN_UI
        lookalike_mini = '/tmp/x' + MINI_UI
        fake = self.install(FakeDevice([MINI_RECEIVER, lookalike_own, lookalike_mini]))
        self.assertEqual(device.stop_app()['state'], 'not_running')
        self.assertIsNone(device.foreground_state()['owner'])
        self.assertEqual(fake.mutations, [])

    def test_deploy_refuses_while_ui_running_and_uploads_only_diffs_otherwise(self):
        files = {'/apps/minna_nihongo/scripts/main.lua': b'old'}
        for name in device.DEPLOY_FILES:
            local = ROOT / 'app' / 'scripts' / name
            if name != 'main.lua' and local.exists():
                files['/apps/minna_nihongo/scripts/' + name] = local.read_bytes()
        fake = self.install(FakeDevice([MINNA_RECEIVER, OWN_UI], files=files))
        out = device.deploy_scripts()
        self.assertEqual(out['state'], 'ui_running')
        self.assertEqual(fake.mutations, [])
        fake.remove_path(OWN_UI)
        out = device.deploy_scripts()
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['uploaded'], ['main.lua'])
        self.assertEqual([m[0] for m in fake.mutations], ['upload'])  # no stop/start/activation

    # -- explicit switch back to Mini -----------------------------------------------------------------------------
    def test_switch_to_badge_verifies_stop_then_starts_mini(self):
        fake = self.install(FakeDevice([MINNA_RECEIVER, OWN_UI, MINI_RECEIVER, MINI_IND]))
        out = device.switch_to_badge()
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['state'], 'mini_started')
        self.assertEqual([m[0] for m in fake.mutations], ['stop_minna_exit', 'start_mini'])
        self.assertFalse(fake.has(OWN_UI))
        self.assertTrue(fake.has(MINI_RECEIVER) and fake.has(MINI_IND))

    def test_switch_to_badge_does_not_hand_over_when_stop_unconfirmed(self):
        fake = self.install(FakeDevice([MINNA_RECEIVER, OWN_UI, MINI_RECEIVER], stop_works=False))
        out = device.switch_to_badge()
        self.assertFalse(out['ok'])
        self.assertEqual(out['state'], 'stop_not_confirmed')
        self.assertTrue(out['recoverable'])
        self.assertNotIn(('start_mini',), fake.mutations)

    def test_switch_to_badge_reports_missing_mini_route_as_recoverable(self):
        fake = self.install(FakeDevice([MINNA_RECEIVER, OWN_UI]))
        out = device.switch_to_badge()
        self.assertFalse(out['ok'])
        self.assertEqual(out['state'], 'mini_route_unavailable')
        self.assertTrue(out['recoverable'] and out['minna_ui_stopped'])
        self.assertEqual(fake.mutations, [('stop_minna_exit',)])

    def test_switch_to_badge_does_not_claim_ok_when_mini_never_shows_up(self):
        fake = self.install(FakeDevice([MINNA_RECEIVER, OWN_UI, MINI_RECEIVER], mini_start_works=False))
        out = device.switch_to_badge()
        self.assertFalse(out['ok'])
        self.assertEqual(out['state'], 'mini_start_not_confirmed')
        self.assertEqual([m[0] for m in fake.mutations], ['stop_minna_exit', 'start_mini'])

    def test_receiver_lua_has_no_toplevel_ownership_activate(self):
        rec_lua = (ROOT / 'app/scripts/receiver.lua').read_text(encoding='utf-8')
        # Ensure ownership.activate is not called in the top-level body of receiver.lua
        lines = rec_lua.splitlines()
        for idx, line in enumerate(lines, 1):
            if 'ownership' in line and 'activate' in line:
                # Should only be within function blocks (like app:post("/control", ...))
                self.assertTrue(
                    any('app:post' in l for l in lines[max(0, idx - 15) : idx]),
                    f'receiver.lua calls ownership.activate at line {idx} outside explicit control handler: {line}',
                )

    def test_main_lua_drains_stale_lifecycle_exit_queue(self):
        main_lua = (ROOT / 'app/scripts/main.lua').read_text(encoding='utf-8')
        self.assertIn('while thread.sync.queue_recv("minna_nihongo_lifecycle",0) do end', main_lua)

    def test_ownership_activate_never_stops_receivers_or_indicators(self):
        owner_lua = (ROOT / 'app/scripts/ownership.lua').read_text(encoding='utf-8')
        self.assertNotIn('receiver.lua', owner_lua)
        self.assertNotIn('indicator_worker.lua', owner_lua)
        self.assertNotIn('ai_badge/scripts/main.lua', owner_lua)


if __name__ == '__main__':
    unittest.main(verbosity=2)
