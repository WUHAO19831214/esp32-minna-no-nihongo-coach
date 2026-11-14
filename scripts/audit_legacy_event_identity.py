"""Cross-check the complete legacy submission sequence against raw device speech/source events."""
from collections import Counter
import csv
import datetime as dt
import json
from pathlib import Path
import shutil

from repair_esp32_history import ROOT, dataset, rebuild_mastery


def audit(student_id="default_student"):
    directory = dataset.STUDENTS_DIR / student_id
    marker = directory / "grounded_event_identity_20261024.json"
    if marker.exists():
        return {"already_audited": True, **json.loads(marker.read_text())}
    logs = dataset._student_interactions(student_id)
    records = [r for r in logs if r.get("arbitration_reason") == "esp32_interactive_quiz"]
    events = []
    for line in (ROOT / "evidence/bridge.log").read_text(errors="replace").splitlines():
        if "[ESP32 Event] " in line:
            try:
                event = json.loads(line.split("[ESP32 Event] ", 1)[1])
                if event.get("type") == "quiz_answer":
                    events.append(event)
            except ValueError:
                pass
    assert len(events) == len(records) == 16, "Legacy sequence is incomplete; do not guess event associations"
    assert all(r.get("exercise_mode") == e.get("quiz_mode") for r, e in zip(records, events))
    mastery = dataset.read_json(directory / "mastery.json", {})
    counts = Counter(r.get("final_label") or r.get("predicted_label") for r in logs if r.get("archive_status", "safe") == "safe")
    assert dict(counts) == {label: item["total_count"] for label, item in mastery.items()}
    assert all(not item.get("review_count") and not item.get("mastered_count") for item in mastery.values())
    mismatches = []
    for record, event in zip(records, events):
        matched = record.get("source_sentence") == event.get("speak")
        record.update(device_event_id=event["id"], device_source_sentence=event.get("speak"),
                      device_question_identity="matched" if matched else "mismatch")
        if not matched:
            record.update(archive_status="needs_review", migration_reason="Mac graded a different question from the source reported by the device")
            mismatches.append(record)
    assert all(r["device_question_identity"] == "matched" for r in records if r.get("answer_exact_match") is True), "A claimed correct legacy result needs separate review"
    backup = ROOT / "backups" / ("event-identity-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True)
    for name in ("interaction_log.jsonl", "mastery.json", "review_queue.json", "error_log.csv", "progress.json"):
        shutil.copy2(directory / name, backup / name)
    for record in records:
        session = dataset.AGENT_ROOT / "data/sessions" / (record["session_id"] + ".jsonl")
        if session.exists():
            (backup / "sessions").mkdir(exist_ok=True)
            shutil.copy2(session, backup / "sessions" / session.name)
            session.write_text(json.dumps(record, ensure_ascii=False) + "\n")
    queue = dataset.read_json(directory / "review_queue.json", [])
    invalid = {r["session_id"] for r in logs if r.get("archive_status") == "needs_review"}
    for item in queue:
        match = next((r for r in mismatches if r["sentence"] == item["sentence"] and r["timestamp"] == item.get("created_at")), None)
        if match:
            item.update(status="needs_review", quarantine_reason=match["migration_reason"],
                        device_event_id=match["device_event_id"])
    rebuilt = rebuild_mastery(logs)
    for name, value in (("mastery.json", rebuilt), ("review_queue.json", queue)):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    (directory / "interaction_log.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in logs))
    path = directory / "error_log.csv"
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        fields, errors = reader.fieldnames, list(reader)
    for item in errors:
        if item.get("session_id") in invalid:
            item["status"] = "needs_review"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(errors)
    progress = dataset.read_json(directory / "progress.json", {})
    progress["active_error_count"] = sum(r.get("status") in {"new", "reviewing"} for r in queue)
    (directory / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n")
    result = dict(device_events_checked=16, mismatched_questions=len(mismatches),
                  quarantined_total=len(invalid), valid_interactions=sum(i["total_count"] for i in rebuilt.values()),
                  valid_reviews=progress["active_error_count"], backup=str(backup))
    marker.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (ROOT / "evidence/grounded-20261024/legacy-event-identity.json").write_text(json.dumps({"result": result, "comparisons": [{"event_id": r["device_event_id"], "mac_source": r["source_sentence"], "device_source": r["device_source_sentence"], "identity": r["device_question_identity"]} for r in records]}, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False))
