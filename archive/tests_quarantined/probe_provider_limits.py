#!/usr/bin/env python3
"""Read provider allowance/limit state WITHOUT consuming inference quota.

WHY
---
Tydyn: "juggling providers and their limits has been challenging". The 50/day
OpenRouter discovery showed the number is usually KNOWABLE - we were inferring
it from failed jobs instead of reading it. Several providers expose a balance
or allowance endpoint that costs nothing. Use those first.

A completions call is the LAST resort: on a scarce provider it spends quota
just to learn a number. So this script prefers, in order:
  1. GET <base>/key or /balance or /credits or /usage   (free, authoritative)
  2. GET <base>/models                                  (free, tells reachability)
  3. NO completion call at all unless explicitly forced.

Only prints metadata. Never prints a credential value.

USAGE: probe_provider_limits.py [--force-completion]
"""
import json, os, sys, urllib.request, urllib.error

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

def get(url, key, timeout=25):
    h = {"Accept": "application/json", "User-Agent": UA}
    if key:
        h["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")

def show(name, base, key_env, paths):
    key = ENV.get(key_env, "")
    print(f"\n  {name}  (key_env={key_env}, {'SET' if key else 'MISSING'})")
    if not key:
        print("    no credential - cannot query authenticated allowance")
        return
    for p in paths:
        url = base.rstrip("/") + p
        try:
            status, body = get(url, key)
        except urllib.error.HTTPError as e:
            print(f"    {p:<14} HTTP {e.code}")
            continue
        except Exception as e:
            print(f"    {p:<14} {type(e).__name__}")
            continue
        print(f"    {p:<14} HTTP {status}  {body[:150]}")

PROVIDERS = [
    ("kilocode",     "https://api.kilo.ai/api/gateway",  "KILOCODE_API_KEY",
     ["/v1/key", "/v1/credits", "/v1/usage", "/v1/balance"]),
    ("llm7",         "https://api.llm7.io",              "HERMES_CUSTOM_API_LLM7_IO_API_KEY",
     ["/v1/credits", "/v1/balance", "/v1/usage", "/v1/key"]),
    ("nvidia",       "https://integrate.api.nvidia.com","NVIDIA_API_KEY",
     ["/v1/credits", "/v1/organizations/me", "/v1/users/me"]),
    ("orcarouter",   "https://orcarouter.ai",           "HERMES_CUSTOM_API_ORCAROUTER_AI_API_KEY",
     ["/api/credits", "/api/balance", "/api/v1/credits", "/api/usage"]),
    ("aihubmix",     "https://aihubmix.com",            "HERMES_CUSTOM_AIHUBMIX_COM_API_KEY",
     ["/v1/credits", "/api/user/self/usage", "/v1/balance"]),
    ("inferera",     "https://api.inferera.com",         "HERMES_CUSTOM_API_INFERERA_COM_API_KEY",
     ["/v1/credits", "/v1/balance", "/v1/usage"]),
]

if __name__ == "__main__":
    print("=" * 68)
    print("PROVIDER ALLOWANCE READOUT  (free endpoints only, no completions)")
    print("=" * 68)
    for name, base, key_env, paths in PROVIDERS:
        show(name, base, key_env, paths)
