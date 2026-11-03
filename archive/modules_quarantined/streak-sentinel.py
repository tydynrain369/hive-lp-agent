#!/usr/bin/env python3
"""
# Streak Sentinel — post-publish daily-post guard for @tydynrain.
#
# 2026-10-26 REGIME CHANGE: Tydyn now writes his daily post in the morning and
# schedules it for 14:05 HST (00:05 UTC) via the platform post scheduler, so
# each post lands at the START of its UTC day. Recovery buffer ~24h: a missed
# scheduled publish can be recovered any time before ~13:59 HST the NEXT day
# (end of the UTC day). The guard therefore runs AFTER the scheduled publish:
# the hard alarm fires at 14:30 HST and verifies the post actually landed.
# The legacy pre-cutoff design (12:00 soft nudge + 13:30 hard alarm against a
# same-day 14:00 HST cutoff) is retired; the 12:00 job stays paused.
#
# Account: @tydynrain (Tydyn's own daily-post streak). Corrected 2026-10-26
# from @aliveandthriving, which also posts daily but is a different account.
# Override with STREAK_ACCOUNT= for a second guard.
#
# Queries the Hive condenser API for the account's most recent top-level
# post. If no post is from today (HST), it escalates:
#   1. Desktop notification via notify-send (Omarchy bar)
#   2. Telegram message via `hermes send -t telegram:<channel> -q`
#
# Idempotent: safe to run manually at any time. Before the scheduled publish
# time it exits cleanly (nothing to verify yet). Reads config from env vars
# with the same defaults hermes-alert uses.
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

ACCOUNT = os.environ.get("STREAK_ACCOUNT", "tydynrain")
PUBLISH_HOUR = int(os.environ.get("STREAK_PUBLISH_HOUR", "14"))    # 2:05 PM HST
PUBLISH_MINUTE = int(os.environ.get("STREAK_PUBLISH_MINUTE", "5"))  # scheduled publish
HST = timezone(timedelta(hours=-10))
TELEGRAM_TARGET = os.environ.get("TELEGRAM_HOME_CHANNEL", "-1003782411547")
HERMES_BIN = os.environ.get("HERMES_BIN",
                            "/home/tydynrain/.hermes/hermes-agent/.hermes/bin/hermes")

# Only api.hive.blog survives — 13 alternatives tested 2026-10-26 (403 /
# timeout / DNS-dead). Do not add fallback endpoints without re-testing.
ENDPOINTS = [
    "https://api.hive.blog",
]


def now_hst() -> datetime:
    return datetime.now(HST)


def hive_rpc(method: str, params: list):
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params
    }).encode()
    last_err = None
    for endpoint in ENDPOINTS:
        try:
            req = urllib.request.Request(
                endpoint, data=payload, headers={"Content-Type": "application/json"})
            resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
            if isinstance(resp, list):
                return resp[0]
            if "error" in resp:
                last_err = f"{endpoint}: {resp['error']}"
                continue
            return resp.get("result", resp)
        except Exception as exc:  # noqa: BLE001
            last_err = f"{endpoint}: {exc}"
            continue
    raise RuntimeError(f"all Hive endpoints failed: {last_err}")


def latest_post(account: str) -> dict | None:
    """Newest TOP-LEVEL post by the account (replies and reblogs excluded).

    Primary: condenser_api.get_discussions_by_blog returns the account's blog
    (own posts + reblogs, newest first). Filter author==account to drop
    reblogs (they carry the original author) and take the first hit. This is
    independent of account op-rate.

    Fallback: condenser_api.get_account_history[account, -1, 1000]. Three
    traps verified live on 2026-10-27 make that call unfit as the primary:
      * the returned window is ordered OLDEST-FIRST (ascending op index) —
        scanning forward latches onto the STALEST comment in the window and
        false-alarms even when today's post is live; scan REVERSED.
      * the window contains comment ops authored by OTHER users (replies on
        the account's content land in its history) — without author AND
        parent_author filters, someone else's reply satisfies the guard and
        masks a real miss (silent false-OK, the worst failure direction).
      * the fixed-size op window shrinks as engagement grows (@tydynrain's
        last 1000 ops spanned only 25.6h; at the old limit of 50, just 1.2h
        — which cannot even reach back to yesterday's post at 14:30).
    """
    # Primary: blog listing (op-rate independent).
    try:
        blog = hive_rpc("condenser_api.get_discussions_by_blog",
                        [{"tag": account, "limit": 10}])
        if isinstance(blog, list):
            for d in blog:
                if not isinstance(d, dict):
                    continue
                if d.get("author") != account:
                    continue  # reblog: carries the original author
                return {
                    "permlink": d.get("permlink", ""),
                    "timestamp": d.get("created", 0),
                    "block": d.get("block", 0),
                }
    except Exception:  # noqa: BLE001
        pass  # fall through to the history scan

    # Fallback: newest-first history scan, own top-level posts only.
    result = hive_rpc("condenser_api.get_account_history",
                      [account, -1, 1000])
    if not isinstance(result, list) or not result:
        return None
    for pair in reversed(result):
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        entry = pair[1]
        if not isinstance(entry, dict):
            continue
        op = entry.get("op")
        if not isinstance(op, list) or len(op) < 2 or op[0] != "comment":
            continue
        op_value = op[1]
        if not isinstance(op_value, dict):
            continue
        if op_value.get("author") != account:
            continue  # someone else's reply on this account's content
        if op_value.get("parent_author"):
            continue  # reply, not a top-level post
        return {
            "permlink": op_value.get("permlink", ""),
            "timestamp": entry.get("timestamp", 0),
            "block": entry.get("block", 0),
        }
    return None


def ts_to_hst(ts) -> datetime:
    """Condenser timestamps are ISO-8601 strings (e.g. '2026-10-26T03:52:15'),
    not Unix ints — parse them as such.

    Hive timestamps are always UTC. If the string has no timezone suffix
    (the common case — Hive returns '2026-10-26T03:52:15' with no Z),
    fromisoformat returns a NAIVE datetime. Calling .astimezone() on a naive
    datetime treats it as the system's local time — which on this machine is
    already HST, so the conversion is a no-op and the date comes out wrong.
    Always attach UTC explicitly for naive strings.
    """
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(HST)
    if isinstance(ts, str):
        ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # Hive timestamps are UTC
        return dt.astimezone(HST)
    raise TypeError(f"unparseable timestamp: {ts!r}")


def main() -> int:
    now = now_hst()
    today = now.date()
    publish_at = f"{PUBLISH_HOUR:02d}:{PUBLISH_MINUTE:02d}"

    # Nothing to verify before the scheduled publish time — a manual or
    # early run must not read "no post today" as a miss.
    expected_at = now.replace(hour=PUBLISH_HOUR, minute=PUBLISH_MINUTE,
                              second=0, microsecond=0)
    if now < expected_at:
        print(f"ℹ️ Scheduled publish for today is {publish_at} HST — "
              f"not due yet. Nothing to verify.")
        return 0

    try:
        post = latest_post(ACCOUNT)
    except Exception as exc:  # noqa: BLE001
        # API failure is a sentinel fault, not a streak miss — escalate loudly.
        msg = (f"⚠️ Streak Sentinel: Hive API unreachable ({exc}). "
               f"Cannot verify whether @{ACCOUNT} posted today.")
        _escalate(msg, urgent=True)
        return 1

    if not post or not post.get("permlink"):
        msg = (f"🚨 STREAK ALARM — no posts found for @{ACCOUNT} on the chain. "
               f"The scheduled {publish_at} HST publish did not land. "
               f"Recovery: publish manually before ~14:00 HST tomorrow "
               f"(end of the UTC day) to keep the streak.")
        _escalate(msg, urgent=True)
        return 1

    post_time = ts_to_hst(post["timestamp"])
    if post_time.date() == today:
        # Live today — no action. Print for the log and exit clean.
        print(f"✅ @{ACCOUNT} posted today: "
              f"https://peakd.com/@{ACCOUNT}/{post['permlink']} "
              f"({post_time.strftime('%H:%M')} HST) — streak safe.")
        return 0

    # Scheduled publish did not land — this is the miss.
    hours_ago = (now - post_time).total_seconds() / 3600
    msg = (f"🚨 STREAK ALARM — @{ACCOUNT} has not posted today (HST). "
           f"The scheduled {publish_at} HST publish did not land on-chain. "
           f"Most recent post is {hours_ago:.1f}h old "
           f"({post_time.strftime('%Y-%m-%d %H:%M')} HST). "
           f"Recovery: publish manually before ~14:00 HST tomorrow "
           f"(end of the UTC day) to keep the streak.")
    _escalate(msg, urgent=True)
    return 1


def _escalate(text: str, urgent: bool) -> None:
    title = "🚨 Streak Sentinel" if urgent else "⏰ Streak Sentinel"
    # 1. Desktop notification
    subprocess.run(["notify-send", title, text],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 2. Telegram (best-effort; don't fail the whole run if this drops)
    try:
        subprocess.run(
            [HERMES_BIN, "send", "-t", f"telegram:{TELEGRAM_TARGET}", "-q", text],
            check=False, timeout=60,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    sys.exit(main())
