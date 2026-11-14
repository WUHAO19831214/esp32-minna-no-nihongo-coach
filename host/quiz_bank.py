"""Source-backed questions. No LLM or random particle replacement on the answer path."""
from __future__ import annotations

import hashlib
import json
import random
import re
from difflib import SequenceMatcher
from typing import Any

from textbook_parser.grounded_exercises import (
    BLANK, CONTENT_VERSION, answer_variants, grammar_target, recall_target,
)

MAX_SENTENCE = 38  # two full lines on the 480px display, including option prefix


def question_id(question: dict[str, Any]) -> str:
    keys = ("mode", "lesson", "prompt", "options", "correct", "source_sentence", "review_error_id")
    body = {k: question.get(k) for k in keys}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256((CONTENT_VERSION + raw).encode()).hexdigest()[:20]


def finalize(q: dict[str, Any]) -> dict[str, Any]:
    q["content_version"] = CONTENT_VERSION
    q["question_id"] = question_id(q)
    validate_question(q)
    return q


def validate_question(q: dict[str, Any]) -> None:
    options = q.get("options", [])
    if len(options) != 3 or len(set(options)) != 3:
        raise ValueError("Question needs three distinct, complete options")
    if any(not opt or len(opt) > MAX_SENTENCE for opt in options):
        raise ValueError("Option exceeds display budget; never truncate an answer")
    if not 1 <= q.get("correct", 0) <= 3:
        raise ValueError("Invalid correct index")
    correct = options[q["correct"] - 1]
    source = q.get("source_sentence", "")
    if q.get("answer_sentence") != source or q.get("speak") != source:
        raise ValueError("Answer and speech must preserve the source")
    if BLANK in q.get("prompt", ""):
        filled = q["prompt"].replace(BLANK, q["expected_answer"], 1)
        if filled != source:
            raise ValueError("Cloze does not reconstruct its source")
        if correct != q.get("answer_option"):
            raise ValueError("Cloze display and answer disagree")
    elif correct != source:
        raise ValueError("Selected answer differs from source")
    if len(q.get("prompt", "")) > 48:
        raise ValueError("Prompt exceeds display budget")


def _base(row: dict[str, str], mode: str) -> dict[str, Any]:
    s = row["sentence"]
    return {
        "mode": mode, "lesson": row["lesson"], "source_sentence": s,
        "answer_sentence": s, "speak": s, "expected_answer": s,
        "target_label": row["label"], "target_pattern": row["pattern"],
        "source_ref": row.get("source_ref", ""),
        "source_section": row.get("source_section", ""),
    }


def make_question(row: dict[str, str], mode: str, rng: random.Random) -> dict[str, Any] | None:
    s = row["sentence"]
    if not s or len(s) > MAX_SENTENCE:
        return None
    target = grammar_target(s)
    recall = False
    if target is None and mode == "fill_blank" and row.get("meaning"):
        # Explicit source recall, not a claim that another tense is ungrammatical.
        target = recall_target(s)
        recall = target is not None
    if target is None:
        return None
    q = _base(row, mode)
    q["target_label"] = target.label
    q["target_pattern"] = "／".join(target.answers)
    q["hint"] = (row.get("meaning") or "请选择符合语法的表达")[:26]
    q["explanation"] = target.explanation
    q["edit_span"] = [target.start, target.end]
    if mode == "fill_blank":
        q["mode_label"] = "原句填空" if recall else "语法填空"
        q["prompt"] = s[:target.start] + BLANK + s[target.end:]
        token = s[target.start:target.end]
        answer_option = "／".join(target.answers)
        options = [answer_option, *target.wrongs]
        q.update(expected_answer=token, answer_option=answer_option,
                 accepted_answers=list(target.answers))
    else:
        q["mode_label"] = "纠错练习"
        corrupted = s[:target.start] + target.wrongs[0] + s[target.end:]
        alternative = s[:target.start] + target.wrongs[1] + s[target.end:]
        if max(len(corrupted), len(alternative)) > MAX_SENTENCE:
            return None
        q["prompt"] = "改错: " + corrupted
        q["wrong_sentence"] = corrupted
        answer_option = s
        options = [s, corrupted, alternative]
    rng.shuffle(options)
    q["options"] = options
    q["correct"] = options.index(answer_option) + 1
    return finalize(q)


def _meaning_key(text: str) -> str:
    return re.sub(r"[\s。，、！？!？“”‘’\"'…—（）()、]", "", text)


def translation_question(row: dict[str, str], rows: list[dict[str, str]], rng: random.Random) -> dict[str, Any] | None:
    s, meaning = row["sentence"], row.get("meaning", "")
    if not meaning or meaning == s or len(s) > MAX_SENTENCE or len(meaning) > 38:
        return None
    accepted = answer_variants(s)
    key = _meaning_key(meaning)
    others = []
    for other in rows:
        sentence, translation = other["sentence"], other.get("meaning", "")
        k = _meaning_key(translation)
        if (not k or sentence in accepted or len(sentence) > MAX_SENTENCE or sentence in others
                or not key or key in k or k in key
                or SequenceMatcher(None, key, k).ratio() > 0.45
                or SequenceMatcher(None, s, sentence).ratio() > 0.72):
            continue
        others.append(sentence)
    if len(others) < 2:
        return None
    options = [s, *rng.sample(others, 2)]
    rng.shuffle(options)
    q = _base(row, "translate_zh_to_ja")
    q.update(mode_label="原句理解", prompt="中译日: " + meaning, hint="选择符合中文原意的教材句",
             options=options, correct=options.index(s) + 1,
             explanation="对应教材表达：" + s)
    return finalize(q)


def resolve_review(item: dict[str, Any], rows: list[dict[str, str]], interactions: list[dict[str, Any]]) -> dict[str, str] | None:
    """Recover only an explicitly recorded, source-backed answer. Never edit a wrong sentence."""
    by_sentence = {r["sentence"]: r for r in rows}
    for field in ("source_sentence", "correct_sentence"):
        row = by_sentence.get(item.get(field, ""))
        if row:
            return row
    # Legacy items lacked correct_sentence, but log_interaction already saved source_sentence.
    matches = [r for r in interactions if r.get("sentence") == item.get("sentence")
               and r.get("diagnosis") == item.get("diagnosis")
               and r.get("source_sentence") in by_sentence]
    sources = {r["source_sentence"] for r in matches}
    if len(sources) == 1:
        return by_sentence[next(iter(sources))]
    return None


def review_question(item: dict[str, Any], row: dict[str, str], rows: list[dict[str, str]], rng: random.Random) -> dict[str, Any] | None:
    source, wrong = row["sentence"], item.get("sentence", "")
    # The photographed legacy error is a topic error. Retest that exact edit,
    # not a different particle somewhere else in the sentence.
    if (source.startswith("わたしは") and wrong.startswith(("わたしに", "わたしへ"))
            and source[4:] == wrong[4:] and len(source) <= MAX_SENTENCE):
        options = [source, wrong, "わたしを" + source[4:]]
        rng.shuffle(options)
        q = _base(row, "review")
        q.update(prompt="改错: " + wrong, hint=row.get("meaning", "")[:26],
                 options=options, correct=options.index(source) + 1,
                 target_pattern="わたしは", explanation="这里用「わたしは」提示主题；原句的其余部分保持完整。")
    else:
        q = make_question(row, "correction", rng) or translation_question(row, rows, rng)
    if q:
        q.update(mode="review", mode_label="错题巩固", review_error_id=item["error_id"])
        return finalize(q)
    return None
