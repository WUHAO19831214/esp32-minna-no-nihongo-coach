"""Explicit, backed-up data/script deployment. Never activates a UI or restarts hardware."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
from device import OWN_MAIN, REMOTE, deploy_scripts, foreground_state, request, stop_app


def remote_bytes(target):
    try:
        return request("/files" + target)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def main():
    state = foreground_state()
    assert state["reachable"], "Cannot verify foreground ownership"
    if state["owner"] == "minna":
        assert stop_app()["ok"], "Cannot confirm Japanese UI stopped"
    backup = ROOT / "backups" / ("curriculum-device-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True)
    files = sorted((ROOT / "app/lessons").glob("L*.json"))
    files += [ROOT / "app/scripts" / n for n in ("main.lua", "model.lua", "render.lua", "receiver.lua")]
    # Back up exact current device bytes before touching any file.
    for local in files:
        rel = local.relative_to(ROOT / "app")
        target = REMOTE + "/" + rel.as_posix()
        raw = remote_bytes(target)
        saved = backup / rel
        saved.parent.mkdir(parents=True, exist_ok=True)
        if raw is None:
            saved.with_name(saved.name + ".missing").write_text("File absent before this deployment\n")
        else:
            saved.write_bytes(raw)
    for attempt in range(3):
        try:
            assert deploy_scripts()["ok"]
            break
        except (OSError, urllib.error.HTTPError):
            if attempt == 2:
                raise
            time.sleep(0.3)
    manifest = []
    for local in files:
        rel = local.relative_to(ROOT / "app").as_posix()
        target = REMOTE + "/" + rel
        raw = local.read_bytes()
        if rel.startswith("lessons/") and remote_bytes(target) != raw:
            for attempt in range(3):
                try:
                    result = json.loads(request("/api/files/upload?" + urllib.parse.urlencode({"path": target}),
                                                raw, "POST", "application/octet-stream", timeout=25))
                    assert result.get("ok"), result
                    break
                except (OSError, urllib.error.HTTPError) as exc:
                    # An embedded HTTP response can fail after the write. The
                    # authoritative result is the exact bytes read back.
                    if remote_bytes(target) == raw:
                        break
                    if attempt == 2:
                        raise RuntimeError("Upload failed: " + target) from exc
                    time.sleep(0.6 * (attempt + 1))
        assert request("/files" + target) == raw, "Byte verification failed: " + target
        manifest.append({"file": rel, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    after = foreground_state()
    assert after["reachable"] and after["slots_used"] <= 4
    assert not any(j["path"] == OWN_MAIN for j in after["jobs"]), "Deploy must not activate the Japanese UI"
    evidence = ROOT / "evidence/grounded-20261024"
    evidence.mkdir(parents=True, exist_ok=True)
    report = {"ok": True, "backup": str(backup), "files": manifest,
              "before": state, "after": after}
    (evidence / "device-deploy.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"ok": True, "byte_verified_files": len(manifest), "backup": str(backup),
                      "foreground_after": after["owner"], "slots_after": after["slots_used"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
