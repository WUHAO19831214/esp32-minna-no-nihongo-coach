"""One-time, backed-up repair of the historical ESP32 success-text mismatch.

Refuses to reconstruct mastery unless all existing counts can be accounted for
by the interaction log and no separate review counters would be lost.
"""
from collections import Counter
import csv
import datetime as dt
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
import dataset


def rebuild_mastery(logs):
    rebuilt = {}
    for r in logs:
        if r.get("archive_status", "safe") != "safe":
            continue
        label = r.get("final_label") or r.get("predicted_label")
        item = rebuilt.setdefault(label, dict(total_count=0, correct_count=0, error_count=0,
                                              review_count=0, mastered_count=0, confidence_sum=0.0))
        item["total_count"] += 1
        item["correct_count" if r["is_correct_input"] else "error_count"] += 1
        item["confidence_sum"] += float(r.get("ann_confidence") or r.get("confidence") or 0)
        item["last_seen"] = r["timestamp"]
    for item in rebuilt.values():
        item["avg_confidence"] = round(item.pop("confidence_sum") / item["total_count"], 4)
        c, e = item["correct_count"], item["error_count"]
        item["mastery_level"] = 5 if c >= 4 and e == 0 else 4 if c >= 3 and e <= 1 else 2 if e else 1 if c else 0
    return rebuilt


def repair(student_id="default_student"):
    directory = dataset.STUDENTS_DIR / student_id
    marker = directory / "grounded_history_repair_20261024.json"
    if marker.exists():
        return {"already_repaired": True, **json.loads(marker.read_text())}
    logs = dataset._student_interactions(student_id)
    mastery = dataset.read_json(directory / "mastery.json", {})
    counts = Counter(r.get("final_label") or r.get("predicted_label") for r in logs if r.get("archive_status", "safe") == "safe")
    assert dict(counts) == {label: item["total_count"] for label, item in mastery.items()}, "Mastery/log coverage mismatch; manual review required"
    assert all(not item.get("review_count") and not item.get("mastered_count") for item in mastery.values()), "Existing review outcomes need a separate migration"
    sources = {r["sentence"] for n in range(1, 26) for r in dataset.load_lesson_sentences(str(n))}
    corrected = quarantined = 0
    invalid_sessions = set()
    changed = []
    for record in logs:
        if record.get("arbitration_reason") != "esp32_interactive_quiz":
            continue
        source = record.get("source_sentence")
        if source not in sources:
            record["archive_status"] = "needs_review"
            record["migration_reason"] = "legacy ESP32 answer has no textbook source"
            invalid_sessions.add(record["session_id"])
            quarantined += 1
            changed.append(record)
        elif record.get("answer_exact_match") is True and record.get("is_correct_input") is False:
            assert record["sentence"] == source, "A claimed correct answer does not reproduce its source"
            record["legacy_diagnosis"] = record.get("diagnosis")
            record["legacy_is_correct_input"] = False
            record.update(diagnosis="未发现明显基础错误。", final_diagnosis="未发现明显基础错误。",
                          rule_diagnosis="未发现明显基础错误。", is_correct_input=True,
                          migration_reason="ESP32 success text did not match NO_ERROR_DIAGNOSIS")
            corrected += 1
            changed.append(record)
    rebuilt = rebuild_mastery(logs)
    backup = ROOT / "backups" / ("history-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True)
    for name in ("interaction_log.jsonl", "mastery.json", "error_log.csv", "progress.json"):
        if (directory / name).exists():
            shutil.copy2(directory / name, backup / name)
    for record in changed:
        session = dataset.AGENT_ROOT / "data/sessions" / (record["session_id"] + ".jsonl")
        if session.exists():
            (backup / "sessions").mkdir(exist_ok=True)
            shutil.copy2(session, backup / "sessions" / session.name)
            session.write_text(json.dumps(record, ensure_ascii=False) + "\n")
    (directory / "interaction_log.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in logs))
    (directory / "mastery.json").write_text(json.dumps(rebuilt, ensure_ascii=False, indent=2) + "\n")
    error_path = directory / "error_log.csv"
    with error_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        fields = reader.fieldnames
        errors = list(reader)
    for item in errors:
        if item.get("session_id") in invalid_sessions:
            item["status"] = "needs_review"
    with error_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(errors)
    progress = dataset.read_json(directory / "progress.json", {})
    queue = dataset.read_json(directory / "review_queue.json", [])
    progress["active_error_count"] = sum(r.get("status") in {"new", "reviewing"} for r in queue)
    (directory / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n")
    result = dict(corrected_success_records=corrected, quarantined_records=quarantined,
                  valid_interactions=sum(i["total_count"] for i in rebuilt.values()), backup=str(backup))
    marker.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(repair(), ensure_ascii=False))
