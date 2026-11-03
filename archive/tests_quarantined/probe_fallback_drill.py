#!/usr/bin/env python3
"""Prove the fallback ladder actually WALKS. Reachability of each rung is not
the property that matters on an expiry date — automatic failover is.

2026-10-04 finding: a 7-rung chain sat under `model.fallback_providers` and was
inert. `hermes config check` passed, every rung answered individually, and
`hermes fallback list` reported "No fallback providers configured". The runtime
reads TOP-LEVEL `config["fallback_providers"]`
(hermes_cli/fallback_config.py::get_fallback_chain). So the ladder had never
once been tested as a ladder.

This drill fails the PRIMARY deliberately and asserts a lower rung answers.
Run it after ANY ladder edit, and after every Hermes update.

USAGE: probe_fallback_drill.py [n_drills]
"""
import json, os, re, subprocess, sys, time

HERMES = "/home/tydynrain/.local/bin/hermes"
PRIMARY_DEAD = "does-not-exist-xyz-drill"

def run(prompt, model, provider, timeout=280):
    t = time.time()
    p = subprocess.run([HERMES, "-m", model, "--provider", provider, "-z", prompt],
                       capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out.strip(), round(time.time() - t, 1)


# ── provider-diversity guard ────────────────────────────────────────────────
def check_provider_diversity():
    """Adjacent rungs must not share a provider.

    A provider-wide failure (429, 5xx, outage, key revocation) kills EVERY rung
    on that provider at the same instant. Two adjacent same-provider rungs
    therefore burn the failover window re-attempting the same dead route, and
    the request never reaches a genuinely different provider. With a 7-rung
    chain this is not a theoretical concern.

    Set by Tydyn 2026-10-04 after the same-provider ordering was spotted.
    """
    # Do NOT assume this script's interpreter has PyYAML. The hive-agent venv
    # and several managed envs do not; /usr/bin/python3 does. Resolve one that
    # can actually parse the config rather than dying on the import.
    import subprocess, shutil, glob
    cfg = None
    candidates = ["/usr/bin/python3"] + sorted(glob.glob(
        "/home/tydynrain/.hermes/installs/*/environments/*/bin/python3"))
    for exe in candidates:
        if not os.path.exists(exe):
            continue
        probe = subprocess.run([exe, "-c", "import yaml"], capture_output=True)
        if probe.returncode == 0:
            dump = subprocess.run([exe, "-c",
                "import yaml,json,sys;"
                "d=yaml.safe_load(open('/home/tydynrain/.hermes/config.yaml')) or {};"
                "print(json.dumps(d.get('fallback_providers') or "
                "(d.get('model') or {}).get('fallback_providers') or []))"],
                capture_output=True, text=True)
            if dump.returncode == 0 and dump.stdout.strip():
                cfg = json.loads(dump.stdout.strip())
                break
    if cfg is None:
        print("    FAIL  no interpreter with PyYAML could read config.yaml")
        return 1
    chain = cfg
    print(f"  provider-diversity guard: {len(chain)} rungs")

    if not chain:
        print("    FAIL  chain is empty or in the wrong key")
        return 1

    bad = [(i + 1, chain[i]["provider"]) for i in range(len(chain) - 1)
           if chain[i].get("provider") == chain[i + 1].get("provider")]
    for n, prov in bad:
        print(f"    FAIL  rungs {n} and {n+1} are both '{prov}'")
    if bad:
        print("    Adjacent same-provider rungs are wasted under a provider-wide failure.")
        return 1

    providers = [c.get("provider") for c in chain]
    print(f"    OK    order: {' -> '.join(providers)}")
    print(f"    OK    {len(set(providers))} distinct providers across {len(chain)} rungs")
    return 0


def main(n=2):
    print("=" * 66)
    print("FALLBACK DRILL - does the chain fail over automatically?")
    print("=" * 66)

    lst = subprocess.run([HERMES, "fallback", "list"], capture_output=True,
                         text=True, timeout=120).stdout
    m = re.search(r"Fallback chain \((\d+) entries?\)", lst)
    count = int(m.group(1)) if m else 0
    if not count:
        print("  FAIL: hermes fallback list reports NO CHAIN.")
        print("        Is the chain under top-level 'fallback_providers'?")
        print("        (hermes_cli/fallback_config.py::get_fallback_chain)")
        return 1
    print(f"  chain visible to runtime: {count} entries")

    diversity_rc = check_provider_diversity()
    if diversity_rc:
        print()
        return 1
    print()

    token = "FALLBACK_DRILL_OK"
    failures = 0
    for i in range(n):
        rc, out, secs = run(f"Reply with exactly: {token}", PRIMARY_DEAD, "nvidia")
        ok = token in out
        why = "" if ok else out.replace("\n", " ")[:150]
        print(f"  drill {i+1}/{n}: {'PASS' if ok else 'FAIL'}  ({secs}s)  {why}")
        if not ok:
            failures += 1
        time.sleep(4)

    print()
    print("  RESULT:", "all drills passed" if not failures else f"{failures} drill(s) FAILED")
    return 1 if (failures or diversity_rc) else 0

if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2))
