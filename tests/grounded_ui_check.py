"""Offline Lua checks for stable question identity, empty modes, full text and font coverage."""
from pathlib import Path
import random
import sys
from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "host"), str(ROOT / "tests")]
from dataset import build_lesson_bundle, load_lesson_sentences
from quiz_bank import make_question
from preview import BitmapFont, Screen

lua = LuaRuntime(unpack_returned_tuples=True)


def table(value):
    if isinstance(value, dict):
        return lua.table_from({k: table(v) for k, v in value.items() if v is not None})
    if isinstance(value, list):
        return lua.table_from([table(v) for v in value])
    return value


model = lua.execute((ROOT / "app/scripts/model.lua").read_text())
render = lua.execute((ROOT / "app/scripts/render.lua").read_text())
render.init(lua.table_from({"load_font": lambda p: BitmapFont(p)}), str(ROOT / "app"))
font = BitmapFont(ROOT / "app/assets/cjk-16.dfn")
missing = set()
for n in range(1, 26):
    bundle = build_lesson_bundle(n)
    s = model.new()
    assert model.apply_lesson_bundle(s, table(bundle))
    for mode, questions in bundle["quizzes_by_mode"].items():
        s.quiz_mode = mode
        if not questions:
            q, _, total = model.current_quiz(s)
            assert q.empty and total == 0
            s.page = 2
            model.touch(s, table({"x": 230, "y": 220}), 1000)
            assert model.touch(s, None, 1030) == "tap"
            assert s.quiz_selected == 0
        for q in questions:
            for value in [q["prompt"], q["hint"], q["explanation"], *q["options"]]:
                missing.update(ch for ch in value if ord(ch) not in font.glyphs)
    s.page = 2
    render.draw(Screen(), s, 1000)
assert not missing, f"Missing glyphs: {''.join(sorted(missing))}"

source = next(r for r in load_lesson_sentences("13") if r["sentence"] == "わたしはすしを食べたいです。")
q = make_question(source, "fill_blank", random.Random(0))
s = model.new()
model.feed(s, table({"lesson": 13, "quiz_mode": "fill_blank", "quizzes_by_mode": {"fill_blank": [q]}}), 1000)
s.page = 2
model.touch(s, table({"x": 230, "y": 220}), 1100)
action, event = model.touch(s, None, 1130)
assert action == "answer" and event.question_id == q["question_id"]
output = ROOT / "evidence/grounded-20261024"
output.mkdir(parents=True, exist_ok=True)
screen = Screen()
s.quiz_selected = 0
render.draw(screen, s, 1200)
screen.im.save(output / "lesson13-fill.png")
s.quiz_selected = q["correct"]
render.draw(screen, s, 1230)
screen.im.save(output / "lesson13-answer.png")
print("25 lesson bundles: empty modes, font coverage, question IDs and L13 rendering OK")
