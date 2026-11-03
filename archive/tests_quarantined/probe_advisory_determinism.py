#!/usr/bin/env python3
"""Per-case determinism probe for the LP advisory adjudicator.

Runs ONE case N times so we can tell a stable pass from a lucky one.
Whole-battery runs conflate cases: a 7/7 can hide a case that fails half
the time while another passes half the time.
"""
import sys, json, urllib.request, time
sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
import lp_advisory as A

key = ""
for l in open("/home/tydynrain/.hermes/.env", encoding="utf-8", errors="replace"):
    if l.strip().startswith("NVIDIA_API_KEY"):
        key = l.split("=", 1)[1].strip(); break

A.PORT = "https://integrate.api.nvidia.com/v1"
A.MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
A.TIMEOUT = 120
_real = urllib.request.urlopen
def post(p):
    r = urllib.request.Request(A.PORT + "/chat/completions", data=json.dumps(p).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(r, timeout=A.TIMEOUT) as resp:
        return json.loads(resp.read())
A._post = post
A.available = lambda: True

def dec(verdicts, **kw):
    return {"verdicts": verdicts,
            "recommended_reallocation": kw.get("realloc"),
            "expand_candidate": kw.get("expand"),
            "exit_candidates": kw.get("exits", []),
            "notes": kw.get("notes", []),
            "threshold_multiples": kw.get("mults", {})}

CASES = {
 "case1_sound": ("sound", dec({"POOL:A": {"action": "consolidate", "reason": "pool APR 0.04% is too low to be worth concentrating here - release this liquidity to a more productive pool", "fee_apr_pct": 0.04, "reward_token_count": 13},
                      "POOL:B": {"action": "expand", "reason": "only 6.4x from earning; pool APR 5.0% is a viable concentration candidate (ranked against other candidates below)", "fee_apr_pct": 5.0, "reward_token_count": 20}},
                     realloc={"from_pool": "POOL:A", "to_pool": "POOL:B", "rationale": "release liquidity from consolidate POOL:A and redeploy to POOL:B (highest viability-weighted score)"},
                     expand="POOL:B", notes=["release (consolidate) candidates: POOL:A", "expand candidate: POOL:B"], mults={"POOL:A": 300.0, "POOL:B": 6.4}),
                   {"POOL:A": {"fee_apr_pct": 0.04}, "POOL:B": {"fee_apr_pct": 5.0}}, "CORROBORATE"),
 "case2_rubberstamp": ("rubberstamp", dec({"POOL:A": {"action": "hold", "reason": "position is productive, keep it here", "fee_apr_pct": 0.04, "reward_token_count": 0}}, realloc=None, expand=None,
                     notes=["portfolio already in near-optimal configuration given evidence"]),
                   {}, "INSUFFICIENT_CHALLENGE"),
 "case5_zombie": ("zombie", dec({"POOL:HEARTBEAT": {"action": "exit", "reason": "no measurable fees AND no active reward distributions", "fee_apr_pct": 0.003, "reward_token_count": 0}}, realloc=None, expand=None, notes=["exit candidates: POOL:HEARTBEAT"],
                     mults={"POOL:HEARTBEAT": 18.8}),
                   {"POOL:HEARTBEAT": {"fee_apr_pct": 1.18}}, "CHALLENGE"),
}


def _cli():
    """Per-case determinism CLI. Kept in a function so this file can also be
    IMPORTED for replay_pairs() without executing the probe loop."""
    name = sys.argv[1]; n = int(sys.argv[2])
    out = []
    for i in range(n):
        _label, payload, evidence, _exp = CASES[name]
        try:
            r = A.adjudicate(payload, evidence)
            out.append(r.get("verdict"))
        except Exception as e:
            out.append("EXC:" + type(e).__name__)
        print("   %s #%d -> %s" % (name, i+1, out[-1]), flush=True)
    print("JSON", json.dumps(out))


if __name__ == "__main__":
    _cli()

def replay_pairs(n=4):
    """Adjudicate REAL decision+recommendation pairs, oldest first.

    Synthetic cases prove the subject can reason about a shape we designed.
    These are the artifacts the unattended job actually reads, so they are the
    only evidence that matters for promoting the layer.
    """
    import glob, json, urllib.request, os, sys
    sys.path.insert(0, "/home/tydynrain/.hermes/scripts")

    key = ""
    for l in open("/home/tydynrain/.hermes/.env", encoding="utf-8", errors="replace"):
        if l.strip().startswith("NVIDIA_API_KEY"):
            key = l.split("=", 1)[1].strip(); break

    import lp_advisory as A
    A.PORT = "https://integrate.api.nvidia.com/v1"
    A.MODEL = os.environ.get("REPLAY_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
    A.TIMEOUT = 150
    def post(p):
        r = urllib.request.Request(A.PORT + "/chat/completions", data=json.dumps(p).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        with urllib.request.urlopen(r, timeout=A.TIMEOUT) as resp:
            return json.loads(resp.read())
    A._post = post; A.available = lambda: True

    decs = sorted(x for x in glob.glob("/home/tydynrain/.hermes/lp_tests/decisions/decision-*.json")
                  if "advisory" not in x)
    recs = sorted(glob.glob("/home/tydynrain/.hermes/scripts/reports/recommendation-*.json"))
    pairs = []
    for d in decs:
        date = d.split("decision-")[1].split(".")[0]
        match = [r for r in recs if r.endswith(date + ".json")]
        if match:
            pairs.append((d, match[0]))

    print(f"\n=== REPLAY: {min(n, len(pairs))} REAL decision artifacts ===")
    print(f"  model: {A.MODEL}\n")
    for d, r in pairs[:n]:
        try:
            dec = json.load(open(d))
            rec = json.load(open(r))
            adv = A.adjudicate(dec, rec.get("verdicts") or {})
            leaks = "LEAK!" if adv.get("possible_quantity_leak") else "no-leak"
            print(f"  {d.split('/')[-1]:26s} {str(adv.get('verdict')):14s} ok={str(adv.get('ok')):5s} {adv.get('ms')}ms {leaks}")
            print(f"      {(adv.get('reason') or adv.get('error') or '')[:190]}")
        except Exception as e:
            print(f"  {d.split('/')[-1]:26s} ERROR {type(e).__name__}: {str(e)[:90]}")
