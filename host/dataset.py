"""Bridge to local-teaching-agent/minna-no-nihongo-agent (Lessons 001-025, Quiz Generator, and Student Profile)."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
from pathlib import Path
import random
import re
import sys
import uuid
from typing import Any

AGENT_ROOT = Path(os.environ.get(
    "MINNA_AGENT_ROOT",
    str(Path.home() / "LocalProjects/Codex/macmini/local-teaching-agent/minna-no-nihongo-agent"),
)).expanduser().resolve()
MATERIALS_DIR = AGENT_ROOT / "data" / "materials" / "japanese" / "minna_no_nihongo"
STRUCTURED_DIR = AGENT_ROOT / "data" / "structured" / "japanese" / "minna_no_nihongo"
STUDENTS_DIR = AGENT_ROOT / "data" / "students"
GRAPHS_DIR = AGENT_ROOT / "data" / "graphs"

if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
from quiz_bank import (
    CONTENT_VERSION, finalize, make_question, question_id, resolve_review,
    review_question, translation_question, validate_question,
)


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
    source_path = STRUCTURED_DIR / f"lesson_{lid}" / "examples.csv"
    rows = read_csv(source_path)
    if not rows:
        source_path = MATERIALS_DIR / f"lesson_{lid}" / "sentences.csv"
        rows = read_csv(source_path)
    out = []
    seen = set()
    for row_index, r in enumerate(rows, start=2):
        sent = (r.get("sentence") or r.get("text") or "").strip()
        meaning = (r.get("meaning") or r.get("translation") or r.get("explanation") or "").strip()
        label = (r.get("label") or "例句").strip()
        pattern = (r.get("pattern") or "").strip()
        if not sent or sent in seen or len(sent) < 3:
            continue
        seen.add(sent)
        out.append({
            "sentence": sent,
            "source_ref": f"{source_path.relative_to(AGENT_ROOT)}:{row_index}",
            "source_section": r.get("source_section", ""),
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


def _student_interactions(student_id: str) -> list[dict[str, Any]]:
    path = STUDENTS_DIR / student_id / "interaction_log.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
        except ValueError:
            continue
    return records


def _review_due(item: dict[str, Any]) -> bool:
    if item.get("status") not in {"new", "reviewing"}:
        return False
    try:
        due = dt.datetime.fromisoformat(item.get("next_review_at") or "")
        if due.tzinfo is None:
            return False
        return due <= dt.datetime.now(dt.timezone.utc)
    except ValueError:
        return item.get("status") == "new"


def generate_quiz_pack(lesson: str, mode: str = "fill_blank", count: int = 8,
                       student_id: str = "default_student", *,
                       include_student_reviews: bool = True) -> list[dict[str, Any]]:
    lid = normalize_lesson(lesson)
    sents = load_lesson_sentences(lid)
    # Repeatable across the offline bundle and every host rebuild.
    rng = random.Random(f"{CONTENT_VERSION}:{student_id}:{lid}:{mode}")
    pool = list(sents)
    rng.shuffle(pool)
    items: list[dict[str, Any]] = []
    if mode == "review":
        queue = read_json(STUDENTS_DIR / student_id / "review_queue.json", []) if include_student_reviews else []
        interactions = _student_interactions(student_id) if include_student_reviews and queue else []
        for item in queue if isinstance(queue, list) else []:
            if not _review_due(item):
                continue
            if normalize_lesson(item.get("lesson") or lid) != lid:
                continue
            row = resolve_review(item, sents, interactions)
            if row:
                q = review_question(item, row, sents, rng)
                if q:
                    items.append(q)
            if len(items) >= count:
                break
        # Explicitly labelled textbook revision when no verified error is available.
        if not items:
            for row in pool:
                q = make_question(row, "fill_blank", rng)
                if q:
                    q.update(mode="review", mode_label="教材复习")
                    items.append(finalize(q))
                if len(items) >= count:
                    break
        return items[:count]

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in pool:
        q = (translation_question(row, sents, rng) if mode == "translate_zh_to_ja"
             else make_question(row, mode, rng) if mode in {"fill_blank", "correction"} else None)
        if q:
            groups.setdefault(q["target_label"] + ":" + q["target_pattern"], []).append(q)
    # Cover multiple grammar points before repeating a single point.
    while any(groups.values()) and len(items) < count:
        for group in groups.values():
            if group and len(items) < count:
                items.append(group.pop(0))
    return items


def resolve_quiz_event(event: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """A question index alone cannot identify an offline/randomized question."""
    qid = event.get("question_id")
    if not qid:
        return None
    lid = normalize_lesson(event.get("lesson") or "")
    mode = event.get("quiz_mode")
    candidates = list((snapshot.get("quizzes_by_mode") or {}).get(mode) or [])
    bundle_path = Path(__file__).resolve().parents[1] / "app/lessons" / f"L{int(lid):02d}.json"
    bundle = read_json(bundle_path, {})
    candidates.extend((bundle.get("quizzes_by_mode") or {}).get(mode) or [])
    for q in candidates:
        if q.get("question_id") == qid and q.get("lesson") == lid and q.get("mode") == mode:
            try:
                validate_question(q)
            except ValueError:
                continue
            if question_id(q) == qid:
                return q
    return None


def record_quiz_submission(
    student_id: str,
    lesson: str,
    quiz_item: dict[str, Any],
    chosen_index: int,
) -> dict[str, Any]:
    """Record ESP32 quiz answer directly into minna-no-nihongo-agent student profile & review queue."""
    lid = normalize_lesson(lesson)
    try:
        validate_question(quiz_item)
        if (quiz_item.get("question_id") != question_id(quiz_item)
                or quiz_item.get("lesson") != lid
                or not 1 <= chosen_index <= 3
                or quiz_item.get("source_sentence") not in {
                    r["sentence"] for r in load_lesson_sentences(lid)
                }):
            raise ValueError("Unknown, stale or mismatched question")
    except (ValueError, TypeError):
        return {"ok": False, "error": "unverified_question", "recorded": False}
    options = quiz_item.get("options") or []
    correct_idx = int(quiz_item.get("correct") or 1)
    is_correct = chosen_index == correct_idx
    chosen_text = options[chosen_index - 1] if 1 <= chosen_index <= len(options) else ""
    expected_sent = quiz_item.get("answer_sentence") or chosen_text

    if "（　）" in str(quiz_item.get("prompt", "")):
        # A grouped を／が option is one correct choice, not a literal Japanese particle.
        student_sentence = expected_sent if is_correct else str(quiz_item["prompt"]).replace("（　）", chosen_text, 1)
    else:
        student_sentence = expected_sent if is_correct else chosen_text

    label = str(quiz_item.get("target_label") or "语法练习")
    pattern = str(quiz_item.get("target_pattern") or "")
    diagnosis = "未发现明显基础错误。" if is_correct else f"ESP32练习误选「{chosen_text}」，正确应为「{options[correct_idx - 1] if 1 <= correct_idx <= len(options) else expected_sent}」。"

    if quiz_item.get("review_error_id"):
        queue = read_json(STUDENTS_DIR / student_id / "review_queue.json", [])
        review_item = next((r for r in queue if r.get("error_id") == quiz_item["review_error_id"]), None)
        if not review_item or not _review_due(review_item):
            return {"ok": True, "is_correct": is_correct, "recorded": False, "reason": "review_not_due"}

    try:
        if str(AGENT_ROOT) not in sys.path:
            sys.path.insert(0, str(AGENT_ROOT))
        from app.student_profile import ensure_student_profile, log_interaction
        from app.review_manager import record_error_if_needed, update_review_result

        ensure_student_profile(student_id, "japanese", "minna_no_nihongo", lid)
        session_id = f"esp32-{uuid.uuid4().hex[:12]}"
        log_interaction(
            student_id=student_id,
            session_id=session_id,
            sentence=student_sentence,
            predicted_label=label,
            confidence=0.98 if is_correct else 0.65,
            diagnosis=diagnosis,
            update_mastery_stats=not bool(quiz_item.get("review_error_id")),
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
        if quiz_item.get("review_error_id"):
            update_review_result(
                student_id=student_id, error_id=quiz_item["review_error_id"],
                is_correct=is_correct, label=label, confidence=1.0,
                session_id=session_id, lesson=lid,
            )
        elif not is_correct:
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
                correct_sentence=expected_sent,
                source_sentence=quiz_item["source_sentence"],
                question_id=quiz_item["question_id"],
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
    pending_reviews = sum(1 for r in rq if isinstance(r, dict) and r.get("status") in {"new", "reviewing"}) if isinstance(rq, list) else 0

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
    *,
    include_student_reviews: bool = True,
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
        mode: generate_quiz_pack(lid, mode=mode, count=8, student_id=student_id,
                                 include_student_reviews=include_student_reviews)
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
        "content_version": CONTENT_VERSION,
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
    quizzes = bundle["quizzes_by_mode"].get(quiz_mode, [])
    payload: dict[str, Any] = {
        "content_version": CONTENT_VERSION,
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

