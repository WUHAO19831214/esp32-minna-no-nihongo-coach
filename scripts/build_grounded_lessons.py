"""Rebuild/audit all offline lessons without touching the ESP32. Optional backed-up review repair."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
import dataset
from quiz_bank import CONTENT_VERSION, resolve_review, validate_question


def repair_reviews(student_id: str) -> dict:
    directory = dataset.STUDENTS_DIR / student_id
    queue = dataset.read_json(directory / "review_queue.json", [])
    if not queue:
        return {"recovered": 0, "quarantined": 0}
    backup = ROOT / "backups" / ("grounded-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True)
    for name in ("review_queue.json", "error_log.csv", "interaction_log.jsonl", "mastery.json", "progress.json"):
        if (directory / name).exists():
            shutil.copy2(directory / name, backup / name)
    rows = [r for n in range(1, 26) for r in dataset.load_lesson_sentences(str(n))]
    logs = dataset._student_interactions(student_id)
    recovered = quarantined = 0
    for item in queue:
        if item.get("status") not in {"new", "reviewing"}:
            continue
        row = resolve_review(item, rows, logs)
        if row:
            item.update(correct_sentence=row["sentence"], source_sentence=row["sentence"],
                        lesson=row["lesson"], label=row["label"],
                        source_ref=row["source_ref"], content_version=CONTENT_VERSION)
            recovered += 1
        else:
            item.update(status="needs_review", quarantine_reason="Recorded answer has no matching textbook source",
                        content_version=CONTENT_VERSION)
            quarantined += 1
    # Do not silently rewrite historical interactions or mastery scores.
    (directory / "review_queue.json").write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n")
    return {"recovered": recovered, "quarantined": quarantined, "backup": str(backup)}


def build(student_id: str, *, lesson_dir: Path | None = None, include_student_reviews: bool = True) -> dict:
    report = {"content_version": CONTENT_VERSION, "lessons": [], "warnings": []}
    lesson_dir = lesson_dir or ROOT / "app/lessons"
    lesson_dir.mkdir(parents=True, exist_ok=True)
    for number in range(1, 26):
        lid = f"{number:03d}"
        rows = dataset.load_lesson_sentences(lid)
        bundle = dataset.build_lesson_bundle(number, student_id=student_id,
                                             include_student_reviews=include_student_reviews)
        for mode, questions in bundle["quizzes_by_mode"].items():
            for q in questions:
                validate_question(q)
                assert q["source_sentence"] in {r["sentence"] for r in rows}
                assert q["lesson"] == lid and q["mode"] == mode
        raw = (json.dumps(bundle, ensure_ascii=False) + "\n").encode()
        (lesson_dir / f"L{number:02d}.json").write_bytes(raw)
        imported = dataset.read_json(dataset.STRUCTURED_DIR / f"lesson_{lid}" / "structured_lesson.json", {})
        provenance = []
        for source in imported.get("source_files", []):
            path = Path(source)
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="replace")
                headers = re.findall(r"^lesson:\s*(\d+)", text, re.M)
                provenance.append({"path": str(path), "current_lessons": headers,
                                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                if lid not in [n.zfill(3) for n in headers]:
                    report["warnings"].append(f"L{number:02d}: original import path now contains another lesson; printed-edition verification pending")
        report["lessons"].append({
            "lesson": lid, "source_sentences": len(rows),
            "source_sections": sorted({r["source_section"] for r in rows}),
            "oversize_sentences_excluded_from_quiz": sum(len(r["sentence"]) > 38 for r in rows),
            "questions": {m: len(qs) for m, qs in bundle["quizzes_by_mode"].items()},
            "bundle_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "original_sources": provenance,
        })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--student", default="default_student")
    parser.add_argument("--repair-reviews", action="store_true")
    parser.add_argument("--public-output", type=Path, metavar="DIR",
                        help="Write shareable lesson bundles to DIR, without student history")
    args = parser.parse_args()
    if args.public_output and (args.repair_reviews or args.student != "default_student"):
        parser.error("--public-output cannot be combined with private student options")
    repair = repair_reviews(args.student) if args.repair_reviews else None
    report = build(args.student, lesson_dir=args.public_output,
                   include_student_reviews=not bool(args.public_output))
    if repair:
        report["review_repair"] = repair
    output = ROOT / "evidence/grounded-20261024"
    output.mkdir(parents=True, exist_ok=True)
    if not args.public_output:
        (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"ok": True, "lessons": len(report["lessons"]),
                      "questions": sum(sum(l["questions"].values()) for l in report["lessons"]),
                      "provenance_warnings": len(report["warnings"]), "review_repair": repair,
                      "report": str(output / "audit.json") if not args.public_output else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
