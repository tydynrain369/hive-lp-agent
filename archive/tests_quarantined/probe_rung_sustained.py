#!/usr/bin/env python3
"""Sustained-load probe for fallback-ladder candidate providers.

WHY THIS EXISTS
---------------
Single-shot probes ("does it answer once?") are not enough to qualify a
ladder rung. Tydyn's standing point: several of these providers have very
small DAILY allowances and rate-limit within a burst, so a provider that
answers one call and then 429s is worse than useless in a ladder - it looks
alive during setup and fails exactly when it is needed.

The 2026-09-27 OpenRouter incident is the precedent: probing burned the daily
free-models-per-day quota that production use then needed.

So a rung must clear TWO bars before it earns a place:
  1. reachability  - answers at all
  2. sustainability - holds up under a burst that plausibly approximates a
                       busy cron window, without rate limiting

This script measures both and prints a single verdict. It does not touch
config. It never logs a credential value.

USAGE
  probe_rung_sustained.py <label> <base_url> <model> <key_env> [burst]
  probe_rung_sustained.py --sequential KEYLESS_NAME   (for keyless endpoints)

Defaults: burst=8, gap=2.0s. Keep the burst small on purpose - we want to
learn the real allowance, not to exhaust it.
"""
import json, os, sys, time, urllib.request, urllib.error

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")

def load_env(path="/home/tydynrain/.hermes/.env"):
    env = {}
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env

ENV = load_env()

def one_call(url, model, key, timeout=90, extra_headers=None):
    """Return (ok, seconds, detail). Never raises."""
    h = {"Content-Type": "application/json", "User-Agent": UA, "Accept": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    if extra_headers:
        h.update(extra_headers)
    body = {"model": model,
            "messages": [{"role": "user", "content": "Say OK"}],
            # MUST be generous. Reasoning models spend the budget on
            # reasoning_content first and return EMPTY content if it runs out
            # — which reads as a total failure on a perfectly healthy model.
            # GLM-5.3-Flash at max_tokens=24 scored 0/8 this way while at 120
            # it answers in ~2.6s. Never probe a reasoning model on a tiny budget.
            "max_tokens": 1200}
    t0 = time.time()
    try:
        r = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h)
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        try:
            msg = (json.loads(raw).get("choices") or [{}])[0].get("message", {})
            content = (msg.get("content") or "").strip()
            if not content:
                # reasoning-only stop: visible content empty but tokens spent
                return False, time.time() - t0, "EMPTY_CONTENT(reasoning-only)"
            return True, time.time() - t0, content[:24]
        except Exception:
            return False, time.time() - t0, "UNPARSEABLE"
    except urllib.error.HTTPError as e:
        return False, time.time() - t0, f"HTTP {e.code}"
    except Exception as e:
        return False, time.time() - t0, type(e).__name__

def run(label, url, model, key_env=None, burst=8, gap=2.0, keyless=False):
    key = ENV.get(key_env, "") if key_env else ""
    if not keyless and not key:
        print(f"  {label:<26} SKIP   no credential for {key_env}")
        return {"label": label, "verdict": "NO_CREDENTIAL"}
    hdrs = {"User-Agent": UA}
    if keyless:
        # Some edges want a browser UA AND no auth header at all.
        pass
    print(f"  {label:<26} burst={burst} model={model}")
    ok_n = 0; lat = []; fails = []
    for i in range(burst):
        ok, sec, detail = one_call(url, model, key, extra_headers=hdrs)
        (ok_n := ok_n + 1) if ok else fails.append(f"#{i+1}:{detail}")
        if ok:
            lat.append(sec)
        print(f"      call {i+1}/{burst}  {'OK  ' if ok else 'FAIL'}  {sec:6.2f}s  {detail}")
        if i < burst - 1:
            time.sleep(gap)
    lat_sorted = sorted(lat)
    med = lat_sorted[len(lat_sorted)//2] if lat_sorted else 0.0
    mx = max(lat) if lat else 0.0
    print(f"    -> {ok_n}/{burst} ok, median {med:.2f}s, max {mx:.2f}s")
    if fails:
        print(f"    -> failures: {', '.join(fails)}")
    if ok_n == burst:
        v = "SUSTAINABLE"
    elif ok_n >= burst * 0.75:
        v = "MARGINAL"
    elif ok_n > 0:
        v = "FRAGILE"
    else:
        v = "DEAD"
    print(f"    -> VERDICT: {v}")
    return {"label": label, "ok": ok_n, "burst": burst, "median_s": round(med, 2),
            "max_s": round(mx, 2), "verdict": v, "fails": fails}

if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--sequential":
        # keyless endpoint, no Authorization header
        run(a[1], a[2], a[3], keyless=True,
            burst=int(a[4]) if len(a) > 4 else 6, gap=float(a[5]) if len(a) > 5 else 2.0)
    else:
        run(a[0], a[1], a[2], a[3],
            burst=int(a[4]) if len(a) > 4 else 8, gap=float(a[5]) if len(a) > 5 else 2.0)
