#!/usr/bin/env python3
"""lp-test-deletion-watcher — catch the recurring test-file deleter.

Background: LP regression tests under ~/.hermes have been deleted 10+ times
across sessions. The old theory (Hermes kernel reaps .py files in its cwd)
is DEAD: lp_tests_canonical/ is NOT in the kernel cwd, yet its copies vanish
too. The surviving boundary is: files under ~/.hermes vanish; MEGA survives.

This watcher uses only stdlib (poll on directory mtimes + contents), needs
no privileges, and writes an append-only log with:
  - every add/remove/rename of test_*.py in the watched dirs
  - a process-tree snapshot at the moment of each event (so the log names
    the likely culprits: kernel PIDs, cron jobs, hermes processes)

Run: nohup python3 lp-test-deletion-watcher.py &   (or as a systemd user unit)
Log: ~/.hermes/lp_test_deletion_log.jsonl  (and MEGA mirror, batched)
"""
import json
import os
import subprocess
import time

WATCH_DIRS = [
    "/home/tydynrain/.hermes/scripts/tests",
    "/home/tydynrain/.hermes/lp_tests_canonical",
    "/home/tydynrain/MEGA/Hermes-Backups/lp_tests_durable",
]
LOG = "/home/tydynrain/.hermes/lp_test_deletion_log.jsonl"
MEGA_LOG = "/home/tydynrain/MEGA/Hermes-Backups/lp_test_deletion_log.jsonl"
POLL_S = 2.0


def snapshot_procs():
    """Compact process census at event time: pid, ppid, cmd (truncated)."""
    out = []
    try:
        # ps walks all our processes; keep it cheap
        res = subprocess.run(
            ["ps", "-o", "pid=,ppid=,args=", "-u", os.environ.get("USER", "tydynrain")],
            capture_output=True, text=True, timeout=10)
        for line in res.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3:
                pid, ppid, args = parts
                out.append({"pid": int(pid), "ppid": int(ppid), "cmd": args[:160]})
    except Exception:
        pass
    return out


def scan(dirs):
    state = {}
    for d in dirs:
        try:
            files = {}
            for name in sorted(os.listdir(d)):
                if name.endswith(".py") and name.startswith("test_"):
                    p = os.path.join(d, name)
                    try:
                        st = os.stat(p)
                        files[name] = (st.st_mtime, st.st_size)
                    except OSError:
                        files[name] = None
            state[d] = files
        except FileNotFoundError:
            state[d] = None  # the directory itself is GONE — big signal
    return state


def emit(event, detail, procs, full_census=False):
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, "detail": detail}
    if procs is not None:
        rec["procs_seen"] = len(procs)
        if full_census:
            # 2026-10-08: keyword filtering hid the real deleter once already —
            # on DELETION events log the COMPLETE census. The one file that
            # matters is the one naming the culprit; disk is cheap.
            rec["full_procs"] = procs
        else:
            suspects = [p for p in procs if any(
                k in p["cmd"] for k in ("hermes", "kernel", "cron", "python", "cleanup",
                                        "sanitation", "prune", "watchdog"))]
            rec["suspects"] = suspects[:25]
    line = json.dumps(rec, ensure_ascii=False)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        os.chmod(LOG, 0o600)
    except OSError:
        pass
    try:
        with open(MEGA_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def main():
    emit("watcher_start", {"dirs": WATCH_DIRS}, [])
    prev = scan(WATCH_DIRS)
    while True:
        time.sleep(POLL_S)
        cur = scan(WATCH_DIRS)
        procs = None
        for d in WATCH_DIRS:
            c, p = cur.get(d), prev.get(d)
            if c is None and p is not None:
                procs = procs or snapshot_procs()
                emit("directory_gone", {"dir": d}, procs)
                continue
            if p is None or c is None:
                continue
            for name in p:
                if name not in c:
                    procs = procs or snapshot_procs()
                    emit("file_deleted", {"dir": d, "file": name}, procs,
                         full_census=True)
            for name in c:
                if name not in p:
                    procs = procs or snapshot_procs()
                    emit("file_created", {"dir": d, "file": name}, procs)
        prev = cur


if __name__ == "__main__":
    main()
