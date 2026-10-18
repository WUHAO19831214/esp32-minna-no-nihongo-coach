"""Exercise production Lua model & rendering for Minna no Nihongo ESP32 app (5 tabs + 25 lesson bundles)."""
from pathlib import Path
import json
import sys
from lupa.lua54 import LuaRuntime
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
sys.path.insert(0, str(ROOT / 'tests'))

from dataset import build_device_snapshot, build_lesson_bundle
from preview import BitmapFont, Screen

(ROOT / 'evidence').mkdir(exist_ok=True)
(ROOT / 'app/assets').mkdir(parents=True, exist_ok=True)

# 1. Generate seed snapshot from Lesson 6
snapshot = build_device_snapshot(lesson_num=6, flash_cat='vocab', quiz_mode='fill_blank')
(ROOT / 'evidence/latest.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
(ROOT / 'app/snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False) + '\n', encoding='utf-8')

# 2. Load Lua runtime and modules
lua = LuaRuntime(unpack_returned_tuples=True)


def table(data):
    if isinstance(data, dict):
        return lua.table_from({k: table(v) for k, v in data.items() if v is not None})
    if isinstance(data, list):
        return lua.table_from([table(v) for v in data])
    return data


model = lua.execute((ROOT / 'app/scripts/model.lua').read_text(encoding='utf-8'))
render = lua.execute((ROOT / 'app/scripts/render.lua').read_text(encoding='utf-8'))
render.init(lua.table_from({'load_font': lambda p: BitmapFont(p)}), str(ROOT / 'app'))

s = model.new()
assert model.feed(s, table(snapshot), 1000)
assert model.linked(s, 1000)


def touch(x, y, t):
    return model.touch(s, table({'x': x, 'y': y}), t)


# Verify Page 1 (Flashcards) interactions + instant category switching
assert s.page == 1
c_vocab, _, _ = model.current_card(s)
assert 'L06 词汇' in c_vocab.tag

# Tap Grammar category pill (x=220, y=70) -> should switch to grammar cards immediately!
touch(220, 70, 1050)
act, evt = model.touch(s, None, 1080)
assert act == 'cat' and s.flash_cat == 'grammar'
c_gram, _, _ = model.current_card(s)
assert 'L06 语法' in c_gram.tag

# Tap back to Vocab category pill (x=80, y=70)
touch(80, 70, 1090)
model.touch(s, None, 1100)
assert s.flash_cat == 'vocab'

# Tap card to flip
touch(240, 200, 1110)
assert model.touch(s, None, 1140) == 'flip'
assert s.card_flipped

# Side button KEY_L -> next flashcard
act, evt = model.buttons(s, 0, 1, 1200)
assert act == 'key_l_next_card'
model.buttons(s, 1, 1, 1220)
assert s.card_index == 2 and not s.card_flipped

# Side button KEY_R -> flip + speak
act, evt = model.buttons(s, 1, 0, 1250)
assert act == 'key_r_flash' and evt.type == 'speak'
model.buttons(s, 1, 1, 1270)
assert s.card_flipped

# Render Page 1
screen = Screen()
s.card_flipped = False
render.draw(screen, s, 1200)
screen.im.save(ROOT / 'evidence/flashcards_front.png')
s.card_flipped = True
render.draw(screen, s, 1260)
screen.im.save(ROOT / 'evidence/flashcards.png')

# Switch to Page 2 (Quiz) via 5-tab bottom bar (Tab 2 is x=103..189)
touch(145, 452, 2000)
assert model.touch(s, None, 2040) == 'tab'
assert s.page == 2
assert s.quiz_index == 1 and s.quiz_selected == 0

# Render Page 2 before selection
render.draw(screen, s, 2050)
screen.im.save(ROOT / 'evidence/quiz_question.png')

# Tap Option 1 (y=220)
touch(240, 220, 2100)
act, evt = model.touch(s, None, 2140)
assert act == 'answer' and evt.type == 'quiz_answer'
assert s.quiz_selected == 1

# Render Page 2 after answering
render.draw(screen, s, 2150)
screen.im.save(ROOT / 'evidence/quiz.png')

# Switch to Page 3 (A-B Repeater) via 5-tab bottom bar (Tab 3 is x=196..282)
touch(240, 452, 3000)
assert model.touch(s, None, 3040) == 'tab'
assert s.page == 3
assert s.ab_state == 'off'

# Press physical KEY_R (Button 1) once -> Set Point A
act, evt = model.buttons(s, 1, 0, 3100)
assert act == 'key_r_ab'
model.buttons(s, 1, 1, 3120)
assert s.ab_state == 'a_set'

# Simulate 14 seconds of playback and press KEY_R again -> Set Point B & start A-B loop!
act, evt = model.buttons(s, 1, 0, 17100)
assert act == 'key_r_ab' and evt.type == 'audio_play' and evt.ab_state == 'loop'
model.buttons(s, 1, 1, 17120)
assert s.ab_state == 'loop'
assert s.ab_b > s.ab_a

# Render Page 3 (in active A-B Repeat state!)
model.feed(s, table({'heartbeat': True}), 17500)
render.draw(screen, s, 18500)
screen.im.save(ROOT / 'evidence/repeater.png')

# Switch to Page 4 (Lessons & Mastery) via 5-tab bottom bar (Tab 4 is x=289..375)
touch(330, 452, 19100)
assert model.touch(s, None, 19140) == 'tab'
assert s.page == 4
render.draw(screen, s, 19150)
screen.im.save(ROOT / 'evidence/lessons.png')

# Tap Lesson 13 in grid (row 2, col 2 -> x=240, y=165) and verify apply_lesson_bundle updates all tabs!
touch(240, 165, 19200)
act, evt = model.touch(s, None, 19240)
assert act == 'lesson' and evt.type == 'select_lesson' and evt.lesson == 13
bundle_13 = build_lesson_bundle(13)
assert model.apply_lesson_bundle(s, table(bundle_13))
c_13, _, _ = model.current_card(s)
tr_13, _, _ = model.current_track(s)
assert 'L13' in c_13.tag
assert '第十三课' in tr_13.title

# Switch to Page 5 (Settings Tab) via 5-tab bottom bar (Tab 5 is x=382..468)
touch(425, 452, 20000)
assert model.touch(s, None, 20040) == 'tab'
assert s.page == 5

# Test Volume + button (x=290, y=108)
assert s.volume == 80
touch(290, 108, 20100)
act, evt = model.touch(s, None, 20130)
assert act == 'volume' and s.volume == 90 and evt.type == 'volume_change'

# Test Volume - button (x=180, y=108)
touch(180, 108, 20200)
act, evt = model.touch(s, None, 20230)
assert act == 'volume' and s.volume == 80

# Test opening Bluetooth Audio Device Scanner & Pairing Selector modal (x=350, y=196)
assert s.audio_target == 'esp32'
assert not s.bt_modal
touch(350, 196, 20250)
act, evt = model.touch(s, None, 20280)
assert act == 'bt_open' and s.bt_modal

# Feed discovered nearby Bluetooth audio devices (including BV370 and AirPods Pro)
model.feed(
    s,
    table(
        {
            'heartbeat': True,
            'bt_name': 'BV370',
            'bt_connected': True,
            'audio_target': 'bt',
            'bt_scanning': False,
            'bt_devices': [
                {'name': 'BV370', 'address': 'f4-4e-fd-0e-11-fd', 'connected': True, 'paired': True},
                {'name': 'AirPods Pro', 'address': '2c-76-00-c5-6b-a0', 'connected': False, 'paired': True},
                {'name': 'Sony WH-1000XM5', 'address': 'a4-c1-38-91-02-b8', 'connected': False, 'paired': False},
            ],
        }
    ),
    20290,
)
assert s.bt_connected and s.bt_name == 'BV370'

# Render Bluetooth Device Scanner & Pairing Selector Modal
render.draw(screen, s, 20300)
screen.im.save(ROOT / 'evidence/bt_modal.png')

# Tap Row 3 (unpaired nearby device -> should emit pair event!)
touch(380, 250, 20320)
act, evt = model.touch(s, None, 20350)
assert act == 'bt_connect' and evt.type == 'bt_control' and evt.action == 'pair' and evt.target == 'a4-c1-38-91-02-b8'

# Tap "返回设定" button (x=405, y=75) to return to main Settings page
s.bt_name = 'BV370'
touch(405, 75, 20370)
act = model.touch(s, None, 20400)
assert act == 'tap' and not s.bt_modal

# Render Page 5 (System Settings Tab with BV370 connected)
render.draw(screen, s, 20450)
screen.im.save(ROOT / 'evidence/settings.png')

# Build 2x2 combined contact sheet
captures = [
    Image.open(ROOT / 'evidence/flashcards.png'),
    Image.open(ROOT / 'evidence/repeater.png'),
    Image.open(ROOT / 'evidence/settings.png'),
    Image.open(ROOT / 'evidence/bt_modal.png'),
]
contact = Image.new('RGB', (480 * 2, 480 * 2), (8, 13, 20))
for idx, im in enumerate(captures):
    contact.paste(im, ((idx % 2) * 480, (idx // 2) * 480))
contact.save(ROOT / 'evidence/screens.png')

render.close()
print('All 5 tabs + Bluetooth Audio Scanner/Pairing Modal verified!')
