"""Known-answer battery for the LP advisory ADJUDICATOR.

Per hive-lp-decision-preferences: an adjudicator earns its place only after it
passes known-answer cases, and THE CONTROLS MATTER MORE THAN THE QUESTIONS.

Run:
  /home/tydynrain/hive-agent-test/.venv/bin/python \
      /home/tydynrain/.hermes/scripts/tests/test_advisory_battery.py
"""
import sys, os, json

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
os.environ.setdefault("LP_ADVISORY_MODEL", "stealth/space-bunny-alpha")

import lp_advisory as A

PASS, FAIL = [], []


def check(name, ok, detail="", response=None):
    # A transport failure is not a model answer. Never score it as a pass.
    if response is not None and response.get("ok") is not True:
        ok = False
        detail = f"TRANSPORT FAILURE, not graded: {response.get('reason','')[:80]}"
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if detail:
        print(f"        {detail}")


def dec(verdicts, **kw):
    return {"verdicts": verdicts,
            "recommended_reallocation": kw.get("realloc"),
            "expand_candidate": kw.get("expand"),
            "exit_candidates": kw.get("exits", []),
            "notes": kw.get("notes", []),
            "threshold_multiples": kw.get("mults", {})}


print("=== LP ADVISORY ADJUDICATOR - KNOWN-ANSWER BATTERY ===\n")
print(f"model: {A.MODEL} via {A.PORT}\n")

if not A.available():
    print("  endpoint unreachable - cannot run battery")
    sys.exit(2)

# CASE 1: sound conclusion, fully supported (destination evidence included).
c1 = dec({
    "POOL:A": {"action": "consolidate", "reason": "pool APR 0.04% is too low to be "
               "worth concentrating here - release this liquidity to a more "
               "productive pool", "fee_apr_pct": 0.04, "reward_token_count": 13},
    "POOL:B": {"action": "expand", "reason": "only 6.4x from earning; pool APR 5.0% "
               "is a viable concentration candidate (ranked against other "
               "candidates below)", "fee_apr_pct": 5.0, "reward_token_count": 20},
}, realloc={"from_pool": "POOL:A", "to_pool": "POOL:B",
            "rationale": "release liquidity from consolidate POOL:A and redeploy to "
                         "POOL:B (highest viability-weighted score)"},
    expand="POOL:B",
    notes=["release (consolidate) candidates: POOL:A", "expand candidate: POOL:B"],
    mults={"POOL:A": 300.0, "POOL:B": 6.4})
r1 = A.adjudicate(c1, {"POOL:A": {"fee_apr_pct": 0.04}, "POOL:B": {"fee_apr_pct": 5.0}})
print("CASE 1 - conclusion follows from evidence (expect CORROBORATE)")
check("case1 corroborates a complete, sound conclusion",
      r1["verdict"] == A.CORROBORATE, f"-> {r1['verdict']}: {r1['reason'][:110]}", r1)

# CASE 2 CONTROL: conclusion its own evidence contradicts.
c2 = dec({
    "POOL:A": {"action": "hold", "reason": "position is productive, keep it here",
               "fee_apr_pct": 0.04, "reward_token_count": 0},
}, realloc=None, expand=None,
    notes=["portfolio already in near-optimal configuration given evidence"])
r2 = A.adjudicate(c2, {"POOL:A": {"fee_apr_pct": 0.04}})
print("\nCASE 2 CONTROL - conclusion contradicts evidence (must NOT be clean CORROBORATE)")
low2 = r2["reason"].lower()
flagged = r2["verdict"] == A.CHALLENGE or (
    r2["verdict"] == A.INSUFFICIENT and
    any(k in low2 for k in ("missing", "not supported", "no evidence", "cannot",
                            "conclusion", "benchmark", "unsupported")))
check("case2 does NOT rubber-stamp the unsupported conclusion",
      flagged, f"-> {r2['verdict']}: {r2['reason'][:180]}", r2)
if r2["verdict"] == A.CORROBORATE and not any(
        k in low2 for k in ("however", "although", "but", "except")):
    print("        *** SHOULD NOT BE CONNECTED: endorsed an unsupported conclusion. ***")

# CASE 3 CONTROL: garbage input.
c3 = {"verdicts": {"POOL:???": {"action": "", "reason": "", "fee_apr_pct": "n/a",
                               "reward_token_count": None}},
      "recommended_reallocation": "???", "expand_candidate": None,
      "exit_candidates": ["???"], "notes": [], "threshold_multiples": {}}
r3 = A.adjudicate(c3, {})
print("\nCASE 3 CONTROL - garbage input (expect INSUFFICIENT, no confidence)")
check("case3 refuses to judge garbage",
      r3["verdict"] == A.INSUFFICIENT, f"-> {r3['verdict']}: {r3['reason'][:150]}", r3)

# CASE 4 CONTROL: notes claim near-optimal while a release is pending.
c4 = dec({
    "POOL:A": {"action": "expand", "reason": "viable concentration candidate",
               "fee_apr_pct": 5.1, "reward_token_count": 1},
    "POOL:B": {"action": "consolidate", "reason": "release this liquidity to a more "
               "productive pool", "fee_apr_pct": 0.03, "reward_token_count": 12},
}, realloc=None, expand=None,
    notes=["portfolio already in near-optimal configuration given evidence"],
    mults={"POOL:A": 0.6, "POOL:B": 200.0})
r4 = A.adjudicate(c4, {"POOL:A": {"fee_apr_pct": 5.1}, "POOL:B": {"fee_apr_pct": 0.03}})
print("\nCASE 4 CONTROL - notes claim 'near-optimal' while a release is pending")
check("case4 spots the internal contradiction",
      r4["verdict"] in (A.CHALLENGE, A.INSUFFICIENT),
      f"-> {r4['verdict']}: {r4['reason'][:170]}", r4)

# CASE 5 CONTROL: APR mismatch the audit actually measured.
c5 = dec({
    "POOL:HEARTBEAT": {"action": "exit", "reason": "no measurable fees AND no active "
                     "reward distributions", "fee_apr_pct": 0.003,
                     "reward_token_count": 0},
}, realloc=None, expand=None, notes=["exit candidates: POOL:HEARTBEAT"],
    mults={"POOL:HEARTBEAT": 18.8})
r5 = A.adjudicate(c5, {"POOL:HEARTBEAT": {"fee_apr_pct": 1.18}})
print("\nCASE 5 CONTROL - pool exited on a 393x-too-low APR (authoritative: 1.18%)")
check("case5 catches the zombie misclassification",
      r5["verdict"] in (A.CHALLENGE, A.INSUFFICIENT),
      f"-> {r5['verdict']}: {r5['reason'][:170]}", r5)
if r5["verdict"] == A.CORROBORATE:
    print("        *** SHOULD NOT BE CONNECTED. ***")

print("\nBOUNDARY - model must not produce amounts or instructions")
leaks = [r for r in (r1, r2, r3, r4, r5) if r.get("possible_quantity_leak")]
check("no quantity/instruction leak in any reason", not leaks, f"leaks: {len(leaks)}")

print("\nDEGRADATION - advisory unavailable")
saved = A.MODEL
A.MODEL = "definitely/not-a-real-model-xyz"
r6 = A.adjudicate(c1, {"POOL:A": {"fee_apr_pct": 0.04}, "POOL:B": {"fee_apr_pct": 5.0}})
A.MODEL = saved
check("bad model degrades to INSUFFICIENT, does not raise",
      r6["verdict"] == A.INSUFFICIENT and r6.get("ok") is False,
      f"-> {r6['verdict']}, ok={r6.get('ok')}")

print(f"\n=== BATTERY RESULT: {len(PASS)}/{len(PASS)+len(FAIL)} pass ===")
if FAIL:
    print("  failing:", FAIL)
    print("\n  VERDICT: DO NOT CONNECT.")
else:
    print("\n  VERDICT: controls pass. Still advisory-only.")
json.dump({"pass": PASS, "fail": FAIL},
          open("/tmp/lp_advisory_battery.json", "w"), indent=2)
