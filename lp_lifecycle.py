#!/usr/bin/env python3
"""LP Agent Pool Lifecycle Filter -- Exclude EOL and problematic pools from consideration.

2026-10-05: Built to exclude FORGE (EOL Jun 2025) and any pool with known end-of-life.
Also filters out operator-issued tokens from dividend scoring.
"""

from datetime import datetime, date
from typing import Dict, List, Optional, Set, Tuple
import json
import os


# Pool lifecycle database -- pools with known end-of-life dates
POOL_LIFECYCLE = {
    "SWAP.HIVE:FORGE": {
        "eol_date": "2025-06-01",
        "reason": "Project announced shutdown June 2025",
        "source": "project announcement",
    },
    # Add more as discovered
}

# Operator-issued tokens -- tokens created/managed by Tydyn or associates
OPERATOR_ISSUED_TOKENS = {
    "HOP",
    "IDD",
    "WRD",
    "POKEMON",
    "BEATCZ",
    "PEPECASH",
    "SWAP.PEPE",
    "SWAP.PEPET",
    # Add more as confirmed
}

# Self-issued tokens by Tydyn
SELF_ISSUED_TOKENS = {
    "HOP",
    "IDD",
    "WRD",
    "PEPECASH",  # suspected
    # "HOP/IDD/WRD created by Tydyn and coder friend in Belgium"
}

# Tokens with no Hive-Engine market (untradeable on DEX)
UNLISTED_TOKENS = {
    "BBHO",
    "FRESA",
    "HOP",
    "IDD",
    "WRD",
    "POKEMON",
    "BEATCZ",
    "PEPECASH",
    "SWAP.PEPE",
    "SWAP.PEPET",
}

# 2026-10-05: COMPROMISED bridged tokens. SWAP.MATIC, SWAP.USDT and other
# off-chain-linked SWAP tokens were RECENTLY HACKED and drained of their
# backing value. The chain cannot reveal this — reserves may still display
# numbers, pools still exist, APRs still compute — but the underlying asset
# is worthless. Tydyn: "I used to be in those pools, and they were quite
# good, now I avoid them." Operator-supplied knowledge with provenance;
# these must NEVER be expansion destinations and their pools must be
# filtered from consideration entirely.
#
# 2026-10-05 UPDATE — full list confirmed by Tydyn (this conversation):
# compromised: SWAP.MATIC, SWAP.USDT, SWAP.ETH, SWAP.PEPE, SWAP.PEPET,
#              SWAP.DFY, SWAP.STATE
# confirmed CLEAN (do NOT flag): SWAP.BLURT, SWAP.BTC, SWAP.LTC "among
#              others" — being bridged is NOT sufficient to be compromised;
#              only tokens confirmed drained belong here.
COMPROMISED_TOKENS = {
    # --- Operator-named (Tydyn, 2026-10-05) ---
    "SWAP.MATIC",
    "SWAP.USDT",
    "SWAP.ETH",
    "SWAP.PEPE",
    "SWAP.PEPET",
    "SWAP.DFY",
    "SWAP.STATE",
    # --- Chain-corroborated (pending Tydyn's ratification, 2026-10-05) ---
    # Discovered via tokens.contract issuer field: ALL 7 operator-named tokens
    # plus these 25 were re-issued under the issuer account "he-nuked" —
    # while every confirmed-clean bridge carries issuer btc-swap or
    # graphene-swap. 100% concordance on both sides. The re-issue account's
    # own name records the nuke event. Veto any of these by naming it.
    "SWAP.AAVE", "SWAP.AXS", "SWAP.BAT", "SWAP.BNB", "SWAP.BUSD",
    "SWAP.BZB", "SWAP.CRV", "SWAP.CUB", "SWAP.DAI", "SWAP.ENJ",
    "SWAP.ETHER", "SWAP.GALA", "SWAP.GIFU", "SWAP.KELP", "SWAP.LINK",
    "SWAP.POLY", "SWAP.PUMP", "SWAP.SIGNA", "SWAP.SKP", "SWAP.SLP",
    "SWAP.SOL", "SWAP.TRUMP", "SWAP.UNI", "SWAP.USDC", "SWAP.VG",
}

# 2026-10-05: Operator-supplied AVOID list — tokens whose pools must never
# be expansion destinations. NOT compromised/hacked — these are dead or
# defunct projects and tokens Tydyn actively avoids in pools.
# Tydyn will supply more exact names later ("among some others that I'll
# list when I have their exact names") — add as confirmed, never guess.
AVOID_TOKENS = {
    "BXT":       "dead/defunct project (formerly good pools)",
    "LASSECASH": "dead/defunct project (formerly good pools)",
    "BRO":       "operator avoid — pools to avoid",
    "LGN":       "operator avoid — pools to avoid",
    "LSTR":      "operator avoid — pools to avoid",
    "SURGE":     "operator avoid — pools to avoid",
    # 2026-10-17 (Tydyn): delisted off-chain-linked bridge tokens. SWAP.WAX
    # and SWAP.EOS were DELISTED a while ago — the bridge listing ended, so
    # there is no exit back to the native chain even though the HE-side
    # book/pool/reserve numbers still compute. Distinct from COMPROMISED
    # (backing drained): delisted means the door home is closed, not that
    # the vault was emptied. Pools pairing them must never be destinations.
    "SWAP.WAX":  "delisted off-chain-linked bridge token (no exit to native chain)",
    "SWAP.EOS":  "delisted off-chain-linked bridge token (no exit to native chain)",
}

# 2026-10-05: Operator-CONFIRMED-GOOD tokens. Several carry "dead project"
# narratives — SIM's project ended, DVS/DHEDGE/POB now have use only in
# pools — but Tydyn explicitly confirmed these are STILL GOOD (SIM's pools
# are "surprisingly good" despite the project ending; the other three are
# explicitly "still good"). They must NEVER be filtered by any future
# dead-project heuristic. POB underpins live positions and the
# SWAP.BTC:POB expand candidate — regressions here are live-money bugs.
OPERATOR_CONFIRMED_GOOD = {
    "SIM",     # project ended, pools still surprisingly good
    "DVS",     # pool-only utility, confirmed still good
    "DHEDGE",  # pool-only utility, confirmed still good
    "POB",     # pool-only utility, confirmed still good
}


class PoolLifecycleFilter:
    """Filters pools based on lifecycle status and token provenance."""

    def __init__(self, lifecycle_db=None, operator_tokens=None,
                 self_issued=None, unlisted=None, compromised=None, avoid=None):
        self.lifecycle_db = lifecycle_db or POOL_LIFECYCLE
        self.operator_tokens = operator_tokens or OPERATOR_ISSUED_TOKENS
        self.self_issued = self_issued or SELF_ISSUED_TOKENS
        self.unlisted = unlisted or UNLISTED_TOKENS
        self.compromised = compromised if compromised is not None else COMPROMISED_TOKENS
        self.avoid = avoid if avoid is not None else AVOID_TOKENS

    def is_eol(self, pool_id, check_date=None):
        """Check if pool has reached end-of-life."""
        if pool_id not in self.lifecycle_db:
            return False
        eol_str = self.lifecycle_db[pool_id].get("eol_date")
        if not eol_str:
            return False
        try:
            eol = datetime.strptime(eol_str, "%Y-%m-%d").date()
            check = check_date or date.today()
            return check >= eol
        except ValueError:
            return False

    def is_operator_issued(self, token):
        """Check if token is operator-issued (by Tydyn or associates)."""
        return token in self.operator_tokens

    def is_self_issued(self, token):
        """Check if token is self-issued by Tydyn."""
        return token in self.self_issued

    def is_unlisted(self, token):
        """Check if token has no Hive-Engine market."""
        return token in self.unlisted

    def is_dividend_eligible(self, token, pool_id=None):
        """
        Determine if a token's dividends should be scored.
        Returns (eligible, reason).

        Check order MATTERS for audit trail clarity: compromised is the
        most severe operator-supplied verdict, so it must be reported even
        when a token also happens to be unlisted (SWAP.PEPE/SWAP.PEPET are
        both — the audit note must say COMPROMISED, not merely "no market").
        AVOID is operator-supplied but NOT an impairment: avoid-pools must
        never be destinations, but held positions there are not exit-forced
        either — they just never score. COMPROMISED + EOL are the impairment
        classes that force EXIT on held positions in lp_decision.decide().
        """
        if token in self.compromised:
            return False, f"{token} is a COMPROMISED bridged token (backing drained)"
        if token in self.avoid:
            return False, (f"{token} is operator-AVOID "
                           f"({self.avoid.get(token, 'dead/defunct or avoided')})")
        if self.is_self_issued(token):
            return False, f"{token} is self-issued (created/managed by operator)"
        if self.is_unlisted(token):
            return False, f"{token} has no Hive-Engine market (no exit path)"
        if pool_id and self.is_eol(pool_id):
            return False, f"pool {pool_id} has reached end-of-life"
        return True, "eligible"

    def filter_expand_candidates(self, candidates):
        """Filter expand candidates by lifecycle and token provenance.

        2026-10-17: rewritten to check BOTH tokens of each pair via
        is_dividend_eligible — the old body read only pool_id.split(":")[0]
        (the BASE side), so a bad QUOTE side slipped through exactly the way
        SWAP.HIVE:SWAP.MATIC slipped past the first decision-layer fix
        (2026-10-05): same bug, different site. It also lacked the AVOID
        check entirely. A candidate entry may still pin a specific "token"
        key; when present that token is checked FIRST (an explicit claim
        about the asset), then the full pair.
        """
        filtered = []
        for c in candidates:
            pool_id = c.get("pool") or c.get("to_pool")
            eligible, reason = True, "eligible"
            checks = []
            explicit = c.get("token")
            if explicit:
                checks.append(explicit)
            if pool_id and ":" in pool_id:
                checks.extend(t for t in pool_id.split(":") if t and t != explicit)
            for tok in checks:
                eligible, reason = self.is_dividend_eligible(tok, pool_id)
                if not eligible:
                    break
            c["_lifecycle_eligible"] = eligible
            c["_lifecycle_reason"] = reason
            if eligible:
                filtered.append(c)
            else:
                print(f"  FILTERED {pool_id}: {reason}")
        return filtered


def load_lifecycle_config(path=None):
    """Load lifecycle config from JSON file."""
    if not path:
        path = os.path.join(os.path.dirname(__file__), "lp_lifecycle.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def save_lifecycle_config(config, path=None):
    """Save lifecycle config to JSON file."""
    if not path:
        path = os.path.join(os.path.dirname(__file__), "lp_lifecycle.json")
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


if __name__ == "__main__":
    f = PoolLifecycleFilter()
    print("FORGE EOL:", f.is_eol("SWAP.HIVE:FORGE"))
    print("CENT:POB EOL:", f.is_eol("CENT:POB"))
    print("FORGE operator:", f.is_operator_issued("FORGE"))
    print("HOP operator:", f.is_operator_issued("HOP"))
    print("FORGE eligible:", f.is_dividend_eligible("FORGE", "SWAP.HIVE:FORGE"))
    print("PIXYDUST eligible:", f.is_dividend_eligible("PIXYDUST", "HSBIDAO:PIXYDUST"))
