"""Regression: word/grammar TTS must not resume the lesson track afterwards (no file side effects)."""
from pathlib import Path
import sys
from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from dataset import build_device_snapshot

lua = LuaRuntime(unpack_returned_tuples=True)


def table(d):
    if isinstance(d, dict):
        return lua.table_from({k: table(v) for k, v in d.items() if v is not None})
    if isinstance(d, list):
        return lua.table_from([table(v) for v in d])
    return d


model = lua.execute((ROOT / 'app/scripts/model.lua').read_text(encoding='utf-8'))
s = model.new()
model.feed(s, table(build_device_snapshot(lesson_num=6, flash_cat='vocab', quiz_mode='fill_blank')), 1000)

for target in ('esp32', 'bt'):
    # lesson track playing, then user taps a card read-aloud
    model.feed(s, table({'audio_cmd': {'action': 'play', 'target': target, 'seq': 10 if target == 'esp32' else 20,
                                        'start_sec': 0, 'end_sec': 30, 'ab_state': 'off'}}), 2000)
    assert s.playing
    s.page = 1
    # speak event from the top-bar button
    model.touch(s, table({'x': 380, 'y': 20}), 2200)
    act, evt = model.touch(s, None, 2230)
    assert act == 'speak' and evt.type == 'speak'
    assert not s.playing, 'speak must stop the lesson-track timeline'
    # bridge acknowledges TTS clip
    seq = 11 if target == 'esp32' else 21
    model.feed(s, table({'audio_cmd': {'action': 'tts', 'target': target, 'seq': seq}}), 2300)
    assert not s.playing and s.ab_state == 'off'
    assert bool(s.tts_pending) == (target == 'esp32')
    s.tts_pending = False

print('TTS does not resume lesson track: OK')
