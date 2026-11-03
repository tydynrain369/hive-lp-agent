#!/usr/bin/env python3
"""
Hive RPC Health Checker — queries PeakD's Node Beacon for live node status.

Usage:
  python3 hive-rpc-health.py              # JSON output to stdout
  python3 hive-rpc-health.py --top 3      # show top N nodes
  python3 hive-rpc-health.py --cron       # cron-friendly: exit 0 always, print compact

The Beacon (beacon.peakd.com/api/nodes) is the community-maintained live
registry of Hive RPC endpoints. It reports scores, versions, feature support,
app success/fail counts, and last block height — all updated every ~10s.

This script wraps it for agent use: filters to healthy nodes, ranks by
score, and outputs a compact JSON array agents can consume directly.

Why: api.hive.blog is a single point of failure for the entire Hive ecosystem.
Every wallet, dApp, and RPC-dependent tool goes dark if that one node blinks.
This script provides a live fallback ladder instead of a hard-coded URL.
"""

import json
import sys
import urllib.request
import urllib.error
from typing import Any

BEACON_URL = "https://beacon.peakd.com/api/nodes"
DEFAULT_MIN_SCORE = 80


def fetch_beacon(timeout: int = 15) -> list[dict[str, Any]]:
    """Fetch the node list from PeakD Beacon. Raises on failure."""
    req = urllib.request.Request(
        BEACON_URL,
        headers={"User-Agent": "hive-rpc-health/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def rank_nodes(nodes: list[dict[str, Any]], min_score: int = DEFAULT_MIN_SCORE) -> list[dict[str, Any]]:
    """Filter to healthy nodes, sort by score descending, then by name."""
    healthy = [n for n in nodes if n.get("score", 0) >= min_score]
    healthy.sort(key=lambda n: (-n.get("score", 0), n.get("name", "")))
    return healthy


def compact(node: dict[str, Any]) -> dict[str, Any]:
    """Reduce a beacon node entry to the fields agents actually need."""
    return {
        "name": node.get("name"),
        "endpoint": node.get("endpoint"),
        "score": node.get("score"),
        "version": node.get("version"),
        "last_block": node.get("lastBlock"),
        "features": node.get("features", []),
        "apps_ok": node.get("apps", {}).get("success", 0),
        "apps_fail": node.get("apps", {}).get("fail", 0),
    }


def main() -> int:
    min_score = DEFAULT_MIN_SCORE
    top_n: int | None = None
    cron_mode = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--cron":
            cron_mode = True
        elif a == "--top" and i + 1 < len(args):
            top_n = int(args[i + 1])
            i += 1
        elif a.startswith("--min-score="):
            min_score = int(a.split("=", 1)[1])
        i += 1

    try:
        nodes = fetch_beacon()
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        if cron_mode:
            print(f"BEACON_UNREACHABLE: {e}", file=sys.stderr)
            return 1
        print(json.dumps({"error": str(e), "beacon_url": BEACON_URL}, indent=2))
        return 1

    ranked = rank_nodes(nodes, min_score)
    compact_nodes = [compact(n) for n in ranked]

    if top_n is not None:
        compact_nodes = compact_nodes[:top_n]

    if cron_mode:
        # Compact one-line-per-node format for cron logs
        for n in compact_nodes:
            print(f"{n['name']} score={n['score']} v={n['version']} block={n['last_block']}")
        print(f"HEALTHY_NODES={len(compact_nodes)} (min_score>={min_score}, total_beacon={len(nodes)})", file=sys.stderr)
        return 0

    print(json.dumps(compact_nodes, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())