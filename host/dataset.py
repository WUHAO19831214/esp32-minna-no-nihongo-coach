"""Bridge to local-teaching-agent/minna-no-nihongo-agent (Lessons 001-025, Quiz Generator, and Student Profile)."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import random
import re
import sys
import uuid
from typing import Any

AGENT_ROOT = Path(
    "/Users/wuhao/LocalProjects/Codex/macmini/local-teaching-agent/minna-no-nihongo-agent"
)
MATERIALS_DIR = AGENT_ROOT / "data" / "materials" / "japanese" / "minna_no_nihongo"
STRUCTURED_DIR = AGENT_ROOT / "data" / "structured" / "japanese" / "minna_no_nihongo"
STUDENTS_DIR = AGENT_ROOT / "data" / "students"
GRAPHS_DIR = AGENT_ROOT / "data" / "graphs"

COMMON_PARTICLES = ["は", "を", "へ", "に", "で", "が", "と", "も", "の", "から", "まで", "か"]
PARTICLE_CONFUSIONS = {
    "を": ["へ", "に", "で"],
    "へ": ["を", "で", "が"],
    "に": ["を", "で", "と"],
    "で": ["に", "を", "へ"],
    "は": ["を", "に", "へ"],
    "が": ["を", "へ", "で"],
    "と": ["を", "に", "へ"],
    "の": ["を", "に", "で"],
}


def normalize_lesson(lesson: str | int) -> str:
    s = str(lesson).strip().removeprefix("lesson_").removeprefix("L").removeprefix("l")
    try:
        n = max(1, min(25, int(s)))
    except ValueError:
        n = 6
    return f"{n:03d}"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_lesson_vocab(lesson: str) -> list[dict[str, str]]:
    lid = normalize_lesson(lesson)
    rows = read_csv(STRUCTURED_DIR / f"lesson_{lid}" / "vocabulary.csv")
    if not rows:
        rows = read_csv(MATERIALS_DIR / f"lesson_{lid}" / "vocabulary.csv")
    out = []
    seen = set()
    for r in rows:
        word = (r.get("word") or "").strip()
        reading = (r.get("reading") or "").strip()
        meaning = (r.get("meaning") or "").strip()
        pos = (r.get("part_of_speech") or "词汇").strip()
        if not word or word in seen:
            continue
        seen.add(word)
        out.append({"word": word, "reading": reading, "meaning": meaning, "pos": pos, "lesson": lid})
    return out


def load_lesson_grammar(lesson: str) -> list[dict[str, Any]]:
    lid = normalize_lesson(lesson)
    rows = read_csv(MATERIALS_DIR / f"lesson_{lid}" / "grammar.csv")
    if not rows:
        rows = read_csv(STRUCTURED_DIR / f"lesson_{lid}" / "grammar.csv")
    out = []
    for r in rows:
        label = (r.get("label") or "语法").strip()
        pattern = (r.get("pattern") or label).strip()
        explanation = (r.get("explanation") or "").strip()
        examples_raw = r.get("examples") or "[]"
        try:
            examples = json.loads(examples_raw) if examples_raw.startswith("[") else [examples_raw]
        except Exception:
            examples = []
        example = examples[0] if examples else ""
        out.append({
            "label": label,
            "pattern": pattern,
            "explanation": explanation,
            "example": example,
            "lesson": lid,
        })
    return out


def load_lesson_sentences(lesson: str) -> list[dict[str, str]]:
    lid = normalize_lesson(lesson)
    rows = read_csv(STRUCTURED_DIR / f"lesson_{lid}" / "examples.csv")
    if not rows:
        rows = read_csv(MATERIALS_DIR / f"lesson_{lid}" / "sentences.csv")
    out = []
    seen = set()
    for r in rows:
        sent = (r.get("sentence") or r.get("text") or "").strip()
        meaning = (r.get("meaning") or r.get("translation") or r.get("explanation") or "").strip()
        label = (r.get("label") or "例句").strip()
        pattern = (r.get("pattern") or "").strip()
        if not sent or sent in seen or len(sent) < 3:
            continue
        seen.add(sent)
        out.append({
            "sentence": sent,
            "meaning": meaning,
            "label": label,
            "pattern": pattern,
            "lesson": lid,
        })
    return out


def build_flashcards(lesson: str, category: str = "vocab", limit: int = 24) -> list[dict[str, str]]:
    lid = normalize_lesson(lesson)
    cards: list[dict[str, str]] = []
    if category == "grammar":
        for g in load_lesson_grammar(lid)[:limit]:
            cards.append({
                "type": "grammar",
                "tag": f"L{int(lid):02d} 语法 · {g['label'][:8]}",
                "front": g["pattern"][:26],
                "sub": g["example"][:28] or g["label"],
                "back": g["explanation"][:64],
                "detail": g["example"][:32],
                "speak": g["example"] or g["pattern"],
            })
    elif category == "sentence":
        for s in load_lesson_sentences(lid)[:limit]:
            cards.append({
                "type": "sentence",
                "tag": f"L{int(lid):02d} 例句 · {s['label'][:8]}",
                "front": s["sentence"][:32],
                "sub": s["pattern"][:24] or "点击或按右键看中文与发音",
                "back": s["meaning"][:48] or s["label"],
                "detail": f"句型: {s['pattern'] or s['label']}"[:32],
                "speak": s["sentence"],
            })
    else:
        for v in load_lesson_vocab(lid)[:limit]:
            cards.append({
                "type": "vocab",
                "tag": f"L{int(lid):02d} 词汇 · {v['pos'][:6]}",
                "front": v["word"][:18],
                "sub": f"假名: {v['reading']}"[:24] if v["reading"] else v["pos"],
                "back": v["meaning"][:40],
                "detail": f"{v['word']} ({v['reading']})"[:30],
                "speak": v["word"],
            })
    if not cards:
        cards.append({
            "type": category,
            "tag": f"L{int(lid):02d}",
            "front": "本课暂无该类条目",
            "sub": "请切换其他分类或课次",
            "back": "可切换词汇/语法/例句",
            "detail": "",
            "speak": "",
        })
    return cards


def _find_particle_span(s: str, p: str) -> int:
    """Find a genuine grammatical particle `p` in sentence `s` (avoiding copula です/でした or verb endings)."""
    search_from = 1
    while True:
        idx = s.find(p, search_from)
        if idx <= 0:
            return -1
        search_from = idx + 1
        after = s[idx + len(p) :]
        prev_ch = s[idx - 1]
        # Never split です / でした / ですね / ですか / でしょう / でも / では
        if p == "で" and after.startswith(("す", "した", "しょう", "も", "は")):
            continue
        # Never split ません / ました / ます / ましょう
        if prev_ch == "ま" and after.startswith(("す", "した", "せん", "しょう")):
            continue
        # Prefer particles right after Kanji, Katakana, digits, or common pronouns (わたし/あなた/ここ/そこ/あそこ/どこ/だれ/なに)
        prefix = s[:idx]
        if (
            ("\u4e00" <= prev_ch <= "\u9fff")
            or ("\u30a0" <= prev_ch <= "\u30ff")
            or prefix.endswith(("わたし", "あなた", "かれ", "かのじょ", "ここ", "そこ", "あそこ", "どこ", "だれ", "なに", "なん", "これ", "それ", "あれ", "どれ", "きょう", "あした", "きのう", "いま"))
        ):
            return idx
    return -1


def _make_blank_question(sent_row: dict[str, str], rng: random.Random) -> dict[str, Any] | None:
    s = sent_row["sentence"]
    for p, distractors in PARTICLE_CONFUSIONS.items():
        idx = _find_particle_span(s, p)
        if idx > 0:
            prompt = s[:idx] + "（　）" + s[idx + len(p) :]
            wrongs = rng.sample(distractors, 2)
            opts = [p] + wrongs
            rng.shuffle(opts)
            correct_idx = opts.index(p) + 1
            return {
                "mode": "fill_blank",
                "mode_label": "填空练习",
                "lesson": sent_row["lesson"],
                "prompt": prompt[:34],
                "hint": (sent_row["meaning"] or sent_row["label"])[:28],
                "options": opts,
                "correct": correct_idx,
                "answer_sentence": s,
                "expected_answer": p,
                "source_sentence": s,
                "target_label": sent_row["label"],
                "target_pattern": sent_row["pattern"],
                "explanation": f"正确助词「{p}」：{sent_row['label']} ({sent_row['pattern'] or s})"[:46],
                "speak": s,
            }
    return None


def _make_correction_question(sent_row: dict[str, str], all_sents: list[dict[str, str]], rng: random.Random) -> dict[str, Any] | None:
    s = sent_row["sentence"]
    for p, distractors in PARTICLE_CONFUSIONS.items():
        idx = _find_particle_span(s, p)
        if idx > 0:
            wrong_p = distractors[0]
            corrupted = s[:idx] + wrong_p + s[idx + len(p) :]
            wrong2 = s[:idx] + distractors[1] + s[idx + len(p) :]
            opts = [s[:20], corrupted[:20], wrong2[:20]]
            if len(set(opts)) < 3:
                continue
            rng.shuffle(opts)
            correct_idx = opts.index(s[:20]) + 1
            return {
                "mode": "correction",
                "mode_label": "纠错重写",
                "lesson": sent_row["lesson"],
                "prompt": f"改错: {corrupted}"[:34],
                "hint": f"原意: {sent_row['meaning'] or sent_row['label']}"[:28],
                "options": opts,
                "correct": correct_idx,
                "answer_sentence": s,
                "expected_answer": s,
                "source_sentence": s,
                "target_label": sent_row["label"],
                "target_pattern": sent_row["pattern"],
                "explanation": f"助词应为「{p}」而非「{wrong_p}」({sent_row['label']})"[:46],
                "speak": s,
            }
    return None


def _make_translation_question(sent_row: dict[str, str], all_sents: list[dict[str, str]], vocab: list[dict[str, str]], rng: random.Random) -> dict[str, Any] | None:
    meaning = sent_row.get("meaning", "").strip()
    s = sent_row.get("sentence", "").strip()
    if meaning and s and meaning != s:
        other_sents = [r["sentence"][:20] for r in all_sents if r["sentence"] != s and len(r["sentence"]) >= 4]
        if len(other_sents) >= 2:
            wrongs = rng.sample(other_sents, 2)
            target = s[:20]
            opts = [target] + wrongs
            if len(set(opts)) == 3:
                rng.shuffle(opts)
                return {
                    "mode": "translate_zh_to_ja",
                    "mode_label": "中译日",
                    "lesson": sent_row["lesson"],
                    "prompt": f"中译日: {meaning}"[:34],
                    "hint": f"语法: {sent_row['label']}"[:28],
                    "options": opts,
                    "correct": opts.index(target) + 1,
                    "answer_sentence": s,
                    "expected_answer": s,
                    "source_sentence": s,
                    "target_label": sent_row["label"],
                    "target_pattern": sent_row["pattern"],
                    "explanation": f"标准表达：{s[:28]}"[:46],
                    "speak": s,
                }
    # Fallback to vocabulary translation
    valid_v = [v for v in vocab if v.get("meaning") and v.get("word")]
    if len(valid_v) >= 3:
        v = rng.choice(valid_v)
        others = [x["word"][:20] for x in valid_v if x["word"] != v["word"]]
        wrongs = rng.sample(others, 2)
        target = v["word"][:20]
        opts = [target] + wrongs
        rng.shuffle(opts)
        return {
            "mode": "translate_zh_to_ja",
            "mode_label": "中译日",
            "lesson": v["lesson"],
            "prompt": f"中文：{v['meaning']}"[:34],
            "hint": f"词性：{v['pos']} / 假名：{v['reading']}"[:28],
            "options": opts,
            "correct": opts.index(target) + 1,
            "answer_sentence": v["word"],
            "expected_answer": v["word"],
            "source_sentence": v["word"],
            "target_label": v["pos"],
            "target_pattern": v["reading"],
            "explanation": f"{v['word']}（{v['reading']}）：{v['meaning']}"[:46],
            "speak": v["word"],
        }
    return None


def generate_quiz_pack(lesson: str, mode: str = "fill_blank", count: int = 8) -> list[dict[str, Any]]:
    lid = normalize_lesson(lesson)
    sents = load_lesson_sentences(lid)
    vocab = load_lesson_vocab(lid)
    rng = random.Random()
    items: list[dict[str, Any]] = []

    if mode == "review":
        rq = read_json(STUDENTS_DIR / "default_student" / "review_queue.json", [])
        active_rq = [r for r in rq if isinstance(r, dict) and r.get("status") != "mastered"]
        for r in active_rq[:count]:
            wrong_sent = (r.get("sentence") or "").strip()
            lbl = (r.get("final_label") or r.get("predicted_label") or "错题复习").strip()
            diag = (r.get("final_diagnosis") or r.get("diagnosis") or "请选出正确表达").strip()
            if wrong_sent:
                for p, distractors in PARTICLE_CONFUSIONS.items():
                    if p in wrong_sent:
                        fixed = wrong_sent.replace(p, distractors[0], 1)
                        alt = wrong_sent.replace(p, distractors[1], 1)
                        opts = [fixed[:20], wrong_sent[:20], alt[:20]]
                        rng.shuffle(opts)
                        items.append({
                            "mode": "review",
                            "mode_label": "错题巩固",
                            "lesson": r.get("lesson") or lid,
                            "prompt": f"错题重做: {wrong_sent}"[:34],
                            "hint": diag[:28],
                            "options": opts,
                            "correct": opts.index(fixed[:20]) + 1,
                            "answer_sentence": fixed,
                            "expected_answer": fixed,
                            "source_sentence": fixed,
                            "target_label": lbl,
                            "target_pattern": lbl,
                            "explanation": f"错题诊断：{diag}"[:46],
                            "speak": fixed,
                        })
                        break

    pool = list(sents)
    rng.shuffle(pool)
    for row in pool:
        if len(items) >= count:
            break
        q = None
        if mode in {"fill_blank", "review"}:
            q = _make_blank_question(row, rng)
        elif mode == "correction":
            q = _make_correction_question(row, sents, rng)
        elif mode == "translate_zh_to_ja":
            q = _make_translation_question(row, sents, vocab, rng)
        if q:
            items.append(q)

    # Fill remaining with vocab/blank fallback if needed
    while len(items) < max(3, min(count, 6)):
        q = _make_translation_question(pool[len(items) % len(pool)] if pool else {}, sents, vocab, rng)
        if q:
            items.append(q)
        else:
            break

    return items


def record_quiz_submission(
    student_id: str,
    lesson: str,
    quiz_item: dict[str, Any],
    chosen_index: int,
) -> dict[str, Any]:
    """Record ESP32 quiz answer directly into minna-no-nihongo-agent student profile & review queue."""
    lid = normalize_lesson(lesson)
    options = quiz_item.get("options") or []
    correct_idx = int(quiz_item.get("correct") or 1)
    is_correct = chosen_index == correct_idx
    chosen_text = options[chosen_index - 1] if 1 <= chosen_index <= len(options) else ""
    expected_sent = quiz_item.get("answer_sentence") or chosen_text

    if quiz_item.get("mode") == "fill_blank" and "（　）" in str(quiz_item.get("prompt", "")):
        student_sentence = str(quiz_item["prompt"]).replace("（　）", chosen_text, 1)
    else:
        student_sentence = expected_sent if is_correct else chosen_text

    label = str(quiz_item.get("target_label") or "语法练习")
    pattern = str(quiz_item.get("target_pattern") or "")
    diagnosis = "表达自然，未发现明显基础语法错误。" if is_correct else f"ESP32练习误选「{chosen_text}」，正确应为「{options[correct_idx - 1] if 1 <= correct_idx <= len(options) else expected_sent}」。"

    try:
        if str(AGENT_ROOT) not in sys.path:
            sys.path.insert(0, str(AGENT_ROOT))
        from app.student_profile import ensure_student_profile, log_interaction
        from app.review_manager import record_error_if_needed

        ensure_student_profile(student_id, "japanese", "minna_no_nihongo", lid)
        session_id = f"esp32-{uuid.uuid4().hex[:12]}"
        log_interaction(
            student_id=student_id,
            session_id=session_id,
            sentence=student_sentence,
            predicted_label=label,
            confidence=0.98 if is_correct else 0.65,
            diagnosis=diagnosis,
            use_ollama=False,
            subject="japanese",
            course="minna_no_nihongo",
            lesson=lid,
            agent_response_summary=str(quiz_item.get("explanation") or ""),
            ann_label=label,
            ann_confidence=0.98 if is_correct else 0.65,
            final_label=label,
            final_diagnosis=diagnosis,
            archive_status="safe",
            arbitration_reason="esp32_interactive_quiz",
            rule_diagnosis=diagnosis,
            gemma_suggested_label="",
            exercise_mode=str(quiz_item.get("mode") or "fill_blank"),
            exercise_prompt=str(quiz_item.get("prompt") or ""),
            expected_answer=str(quiz_item.get("expected_answer") or expected_sent),
            source_sentence=str(quiz_item.get("source_sentence") or expected_sent),
            source_lesson=lid,
            target_label=label,
            target_pattern=pattern,
            answer_exact_match=is_correct,
        )
        if not is_correct:
            record_error_if_needed(
                student_id=student_id,
                session_id=session_id,
                sentence=student_sentence,
                predicted_label=label,
                diagnosis=diagnosis,
                subject="japanese",
                course="minna_no_nihongo",
                lesson=lid,
                ann_label=label,
                final_label=label,
                final_diagnosis=diagnosis,
                archive_status="safe",
            )
    except Exception as exc:
        return {"ok": False, "is_correct": is_correct, "error": str(exc)}

    return {"ok": True, "is_correct": is_correct, "diagnosis": diagnosis}


def get_student_summary(student_id: str = "default_student") -> dict[str, Any]:
    sdir = STUDENTS_DIR / student_id
    mastery = read_json(sdir / "mastery.json", {})
    progress = read_json(sdir / "progress.json", {})
    rq = read_json(sdir / "review_queue.json", [])
    recs = read_json(GRAPHS_DIR / "recommendations.json", {})

    total_interactions = 0
    correct_total = 0
    mastered_labels = 0
    if isinstance(mastery, dict):
        for v in mastery.values():
            if isinstance(v, dict):
                total_interactions += int(v.get("total_count") or 0)
                correct_total += int(v.get("correct_count") or 0)
                if int(v.get("mastery_level") or 0) >= 3:
                    mastered_labels += 1

    accuracy = int(round(100.0 * correct_total / max(1, total_interactions))) if total_interactions > 0 else 100
    pending_reviews = sum(1 for r in rq if isinstance(r, dict) and r.get("status") != "mastered") if isinstance(rq, list) else 0

    rec_list = recs.get("recommendations") if isinstance(recs, dict) else []
    gnn_focus = rec_list[0].get("target_label", "判断句") if rec_list else "地点へ行きます"
    gnn_action = rec_list[0].get("recommended_action", "做 3 道替换练习") if rec_list else "保持每日词句与听力练习"

    return {
        "student_id": student_id,
        "current_lesson": normalize_lesson(progress.get("current_lesson") or "006"),
        "total_interactions": total_interactions,
        "accuracy": accuracy,
        "mastered_labels": mastered_labels,
        "pending_reviews": pending_reviews,
        "gnn_focus": str(gnn_focus)[:14],
        "gnn_action": str(gnn_action)[:28],
    }


def build_lesson_bundle(
    lesson_num: int,
    student_id: str = "default_student",
) -> dict[str, Any]:
    """Build a complete self-contained lesson bundle (all 3 flashcard categories, all 4 quiz modes, and all audio tracks)."""
    from audio_engine import get_lesson_tracks

    lesson_num = max(1, min(25, int(lesson_num)))
    lid = f"{lesson_num:03d}"
    cards_by_cat = {
        cat: [
            {
                "type": c["type"],
                "tag": c["tag"],
                "front": c["front"],
                "sub": c["sub"],
                "back": c["back"],
                "detail": c["detail"],
                "speak": c["speak"],
            }
            for c in build_flashcards(lid, category=cat, limit=24)
        ]
        for cat in ("vocab", "grammar", "sentence")
    }
    quizzes_by_mode = {
        mode: generate_quiz_pack(lid, mode=mode, count=8)
        for mode in ("fill_blank", "correction", "translate_zh_to_ja", "review")
    }
    tracks = [
        {
            "index": t["index"],
            "title": t["title"],
            "section": t["section"],
            "duration": t["duration"],
        }
        for t in get_lesson_tracks(lid)
    ]
    return {
        "lesson": lesson_num,
        "cards_by_cat": cards_by_cat,
        "quizzes_by_mode": quizzes_by_mode,
        "cards": cards_by_cat["vocab"],
        "quizzes": quizzes_by_mode["fill_blank"],
        "tracks": tracks,
    }


def build_device_snapshot(
    lesson_num: int = 6,
    flash_cat: str = "vocab",
    quiz_mode: str = "fill_blank",
    student_id: str = "default_student",
    audio_cmd: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import datetime as dt

    lesson_num = max(1, min(25, int(lesson_num)))
    bundle = build_lesson_bundle(lesson_num=lesson_num, student_id=student_id)
    summary = get_student_summary(student_id=student_id)
    cards = bundle["cards_by_cat"].get(flash_cat) or bundle["cards_by_cat"]["vocab"]
    quizzes = bundle["quizzes_by_mode"].get(quiz_mode) or bundle["quizzes_by_mode"]["fill_blank"]
    payload: dict[str, Any] = {
        "epoch": int(dt.datetime.now(dt.timezone.utc).timestamp()),
        "lesson": lesson_num,
        "flash_cat": flash_cat,
        "quiz_mode": quiz_mode,
        "cards_by_cat": bundle["cards_by_cat"],
        "quizzes_by_mode": bundle["quizzes_by_mode"],
        "cards": cards,
        "quizzes": quizzes,
        "tracks": bundle["tracks"],
        "summary": summary,
    }
    if audio_cmd:
        payload["audio_cmd"] = audio_cmd
    return payload

