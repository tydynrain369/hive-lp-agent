#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""
Hive Liquidity Agent v2 — Autonomous DeFi agent for @hermes-sandbox
Full price discovery, pool health scoring, autonomous execution.
"""
import json
import os
import time
import logging
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

from lp_route_cost import SWAP_FEE   # ONE fee constant (0.25%); no cycle

logging.basicConfig(level=logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# ─── Config ──────────────────────────────────────────────────────────
HIVE_RPC = "https://api.deathwing.me"
HE_RPC = "https://api.hive-engine.com/rpc/contracts"

# 2026-11-14 (Tydyn's call): the primary HE endpoint black-holes reads for
# hours at a stretch. Rotation ring, official endpoints FIRST (fully
# compatible query surface), then the beacon-scored COMMUNITY nodes
# (enginerpc, actifit, c0ff33a, atexoras, kanibot, dtools — all known,
# trusted community operators; they live at /contracts, not /rpc/contracts,
# and they 403 the default python UA, so every call carries an honest
# agent User-Agent). Verified with REAL POSTs via the real client path:
# a TCP connect proves nothing — these edges tarpit non-browser-shaped
# raw requests. CAUTION encoded below: a community mirror returned
# result:null for a find on a table the official node serves (incomplete
# sync) — a null on a FIND is treated as NODE FAILURE (rotate), never as
# an empty answer, because a fabricated empty would read as "no pools".
HE_NODES = [
    {"base": "https://api.hive-engine.com", "path": "/rpc/contracts"},
    {"base": "https://api2.hive-engine.com", "path": "/rpc/contracts"},
    {"base": "https://enginerpc.com", "path": "/contracts"},
    {"base": "https://herpc.actifit.io", "path": "/contracts"},
    {"base": "https://he.c0ff33a.uk", "path": "/contracts"},
    {"base": "https://v6-he.atexoras.com:2083", "path": "/contracts"},
    {"base": "https://herpc.kanibot.com", "path": "/contracts"},
    {"base": "https://herpc.dtools.dev", "path": "/contracts"},
]
HE_USER_AGENT = "hive-lp-agent/2.1 (Hermes autonomous operator; contact: hive @tydynrain)"
HE_NODE_STATE_PATH = os.path.expanduser("~/.hermes/lp_tests/he_node.json")


# 2026-11-15 (Fable round 6): the process ALWAYS starts at the official
# pair — no cross-tick resume. A mirror that answered yesterday must not
# outrank the official pair today, and a stale persisted index (left by a
# smoke test, as happened) would silently pin every tick to a volunteer
# node. The persisted file is now diagnostics only.
_HE_NODE_IDX = [0]   # mutable box: process-wide sticky index
_HE_LOCK = threading.Lock()          # guards the box + persist (round 6, F9)
_HE_RING_DEAD = [False]              # circuit breaker: one full-ring failure


def _he_node_persist():
    try:
        os.makedirs(os.path.dirname(HE_NODE_STATE_PATH), exist_ok=True)
        tmp = HE_NODE_STATE_PATH + ".tmp"
        with _HE_LOCK:
            idx = _HE_NODE_IDX[0]
        with open(tmp, "w") as fh:
            json.dump({"index": idx,
                       "node": HE_NODES[idx]["base"]}, fh)
        os.replace(tmp, HE_NODE_STATE_PATH)
    except Exception:
        pass   # memory is an optimization; rotation works without it
# 2026-10-22: the account NAME for public reads. It is already public in
# this codebase (docstring, load_balances query) and in our own artifacts
# (fee_snapshots rows); only the KEYS are secret and they stay in the vault.
# Public reads must never open the vault (pinned: test_position_valuation's
# open-forbidden patch). Env-overridable for the multi-account future.
ACCOUNT = os.environ.get("LP_ACCOUNT", "hermes-sandbox")

VAULT_PATH = "/mnt/vault/Hermes-Keys/hermes-sandbox.json"
SCAN_INTERVAL = 300  # 5 min

# Target pools we want to actively manage (all relevant token pairs for our wallet)
TARGET_POOLS = [
    "HSBIDAO:PIXYDUST",
    "ALIVE:HEARTBEAT",
    "SWAP.HIVE:ALIVE",
    "SWAP.HIVE:BBH",
    "SWAP.HIVE:PHOTO",
    "SWAP.HIVE:PIZZA",
    "SWAP.HIVE:CENT",
]

# Zombie pools - zero fees, zero/low rewards, should be exited
ZOMBIE_POOLS = {"SWAP.HIVE:BEE", "SWAP.HIVE:POB", "POB:SWAP.HIVE", "BEE:SWAP.HIVE",
                "ATF:SWAP.HIVE", "SWAP.HIVE:ATF"}

# Tokens we want to track for unstaking schedule
UNSTAKING_SCHEDULE = {
    "PIZZA": 28,
    "PHOTO": 91,
    "HEARTBEAT": 3,
    "CENT": 28,
    "BEE": 40,
}

# Token importance weights for reallocation decisions
# Tier 1=10 (Protocol/Infrastructure), Tier 2=8 (Tribe Core), Tier 3=5 (Allied), Tier 4=2, Tier 5=1
TOKEN_IMPORTANCE = {
    # Tier 1: Protocol/Infrastructure - Always useful
    "BEE": 10, "WORKERBEE": 10,
    # Tier 2: Tribe Core - Our pools, we control contracts
    "ALIVE": 10, "ALIVEM": 10, "HEARTBEAT": 10,
    # Tier 3: Allied Ecosystems - Supported projects
    "LBI": 8, "PIXYDUST": 8, "UNICOIN": 8, "GLITTER": 8,
    "CENT": 8, "CENTG": 8, "TRADE": 8, "TRIBE": 8, "SHORT": 8,
    "BBH": 8, "BBHM": 8, "BBHO": 8,
    "PEPE": 8, "PPDLR": 8, "PPBT": 8,
    "PIMP": 8, "AEGIS": 8,
    # ATF REMOVED - project ended
    # Tier 4: Other Hive Engine tokens
    "HSBIDAO": 5, "POB": 5, "SWAP.HIVE": 5,
    "PHOTO": 5, "PIZZA": 5, "BBH": 5,
}

# Dead/ended projects - should not be considered for new liquidity
DEAD_PROJECTS = {"ATF"}  # ATF ended

# Reallocation target pools with tier and notes
REALLOC_TARGETS = {
    "SWAP.HIVE:CENT": {"tier": 1, "notes": "CENT mining rewards (1000 CENT/hr = 24K/day) + daily LP rewards", "reward_tokens": ["CENT"], "compound_accel": True, "daily_reward_usd": 0},
    "ALIVE:SWAP.HIVE": {"tier": 2, "notes": "Core tribe liquidity, rewards: ALIVE, BBH, FRESA, PEPENTROPY, TOOFUCKEH, PEPE, GAMER, MEME, BBHO, etc. (~$0.13/day)", "reward_tokens": ["ALIVE", "BBH", "BBHO", "FRESA", "PEPENTROPY", "TOOFUCKEH", "PEPE", "GAMER", "MEME", "IDD", "HOP", "WRD", "BEER", "PEPECASH", "SWAP.PEPET", "SWAP.PEPE", "HUESO", "LOLZ", "ALIVEM", "POKEMON"], "compound_accel": False, "daily_reward_usd": 0.13},
    "ALIVE:HEARTBEAT": {"tier": 2, "notes": "Fees crossed + allied tokens (BBH, PIXYDUST)", "reward_tokens": ["BBH", "PIXYDUST"], "compound_accel": True, "daily_reward_usd": 0},
    "ALIVE:ALIVEM": {"tier": 2, "notes": "Governance stack (PEPE rewards)", "reward_tokens": ["PEPE"], "compound_accel": True, "daily_reward_usd": 0},
    "ALIVE:LBI": {"tier": 2, "notes": "Newest ally pool (rewards to be discovered)", "reward_tokens": [], "compound_accel": True, "daily_reward_usd": 0},
    "HSBIDAO:PIXYDUST": {"tier": 3, "notes": "1.35K/day rewards, PIXYDUST ecosystem", "reward_tokens": ["PIXYDUST"], "compound_accel": True, "daily_reward_usd": 1350},
    "CENT:POB": {"tier": 2, "notes": "Dual yield (fees + rewards), CENT ecosystem", "reward_tokens": ["CENT"], "compound_accel": True, "daily_reward_usd": 0},
}

# Our pool fees data (from Tribaldex - manual entry)
OUR_POOL_FEES = {
    "CENT:POB": {
        "token_a": "CENT", "token_a_fee": 0.00929195,
        "token_b": "POB", "token_b_fee": 0.00127011,
        "threshold_crossed": True
    },
    "SWAP.HIVE:POB": {
        "token_a": "SWAP.HIVE", "token_a_fee": 0.0,
        "token_b": "POB", "token_b_fee": 0.0,
        "threshold_crossed": False
    },
    "SWAP.HIVE:BEE": {
        "token_a": "SWAP.HIVE", "token_a_fee": 0.0,
        "token_b": "BEE", "token_b_fee": 0.0,
        "threshold_crossed": False
    },
    "ALIVE:HEARTBEAT": {
        "token_a": "ALIVE", "token_a_fee": 0.00000113,
        "token_b": "HEARTBEAT", "token_b_fee": 0.00003545,
        "threshold_crossed": True
    },
    "SWAP.HIVE:ALIVE": {
        "token_a": "SWAP.HIVE", "token_a_fee": 0.0,
        "token_b": "ALIVE", "token_b_fee": 0.0,
        "threshold_crossed": False
    },
    "HSBIDAO:PIXYDUST": {
        "token_a": "HSBIDAO", "token_a_fee": 0.0,
        "token_b": "PIXYDUST", "token_b_fee": 0.0,
        "threshold_crossed": False
    },
}

# ─── RPC Helper ──────────────────────────────────────────────────────────
def _he_request(node, p):
    data = json.dumps(p).encode()
    resp = None
    last_err = None
    for attempt in range(3):
        try:
            req = Request(node["base"] + node["path"], data=data,
                          headers={"Content-Type": "application/json",
                                   "User-Agent": HE_USER_AGENT})
            with urlopen(req, timeout=30) as r:
                resp = json.load(r)
            break
        except Exception as e:   # read-only: retry with backoff
            last_err = e
            time.sleep(1 + attempt)
    if resp is None:
        raise RuntimeError(f"HE RPC failed after 3 attempts: {last_err}")
    if "error" in resp:
        raise RuntimeError(f"RPC error: {resp['error']}")
    return resp


# 2026-11-14 (live incident, round 5): the faulthandler watchdog caught
# he()'s own read hanging >20 minutes DESPITE urlopen(timeout=30) — an
# api.hive-engine.com node that accepts the connection and never answers
# (the "provider goes silent under load" pattern), and this ssl read path
# does not honor the socket timeout. Defense that CANNOT be ignored: run
# each call in a daemon thread and JOIN WITH A HARD CAP. A hung call
# abandons its thread (the watchdog kills the process at 20 min anyway)
# and raises like any other transient error — retries pick it up.
_CALL_TIMEOUT_S = 60.0


def _call_bounded(node, p):
    box = {}

    def _run():
        try:
            box.setdefault("r", _he_request(node, p))
        except Exception as _te:   # fast failure: surface it, don't leak
            box.setdefault("err", _te)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=_CALL_TIMEOUT_S)
    if t.is_alive():
        raise RuntimeError(
            f"HE RPC exceeded {_CALL_TIMEOUT_S:.0f}s (no response) "
            f"— abandoned hung read")
    if "r" not in box:
        raise RuntimeError("HE RPC call failed before returning")
    return box["r"]


def _rpc_rotated(p, method):
    """One RPC against the sticky node; rotate to the next on failure.

    The 2026-11-14 "black-hole windows" turned out (Fable round 6) to be
    largely OUR OWN double-pagination loop — but per-node wedges are real
    (verified live), so rotation stays. Official endpoints FIRST: the
    process starts at index 0 every tick (no cross-tick resume — a mirror
    that answered yesterday must not outrank the official pair today),
    advances the sticky index on failure, persists only on success.
    A community mirror returned result:null for a find on a table the
    official node serves (incomplete sync) — for a FIND, a null result is
    NODE FAILURE (rotate), never an empty answer: a fabricated empty
    would read as "no pools" downstream. CIRCUIT BREAKER (Fable round 6,
    budget nesting): after ONE full-ring failure this process raises
    immediately on every later call — one bounded ring sweep per tick,
    not one per call site. The mutation of the shared index is
    lock-guarded (the phase-budgeted assessment thread and the main
    thread both call he()); races can then only skip a node, never
    corrupt it.
    """
    if _HE_RING_DEAD[0]:
        raise RuntimeError(
            "HE RPC: the whole node ring already failed this process "
            "(circuit breaker) — not retrying")
    last_err = None
    for _ in range(len(HE_NODES)):
        with _HE_LOCK:
            node = HE_NODES[_HE_NODE_IDX[0]]
        try:
            resp = _call_bounded(node, p)
            result = resp.get("result") if isinstance(resp, dict) else None
            if method == "find" and "result" in resp and result is None:
                raise RuntimeError("node returned null for a find "
                                   "(incomplete mirror) — not an answer")
            _he_node_persist()   # diagnostics: remember the node that answered
            return resp
        except Exception as e:
            last_err = e
            logging.getLogger(__name__).warning(
                "HE node %s failed (%s) — rotating to next",
                node["base"], type(e).__name__)
            with _HE_LOCK:
                _HE_NODE_IDX[0] = (_HE_NODE_IDX[0] + 1) % len(HE_NODES)
    _HE_RING_DEAD[0] = True
    raise last_err if last_err else RuntimeError("HE RPC: no nodes")


def he(method: str, params: dict):
    """HE contract call with PAGINATION + read-retry.

    2026-11-13 (Opus P1-11, round 3): a single limit-1000 page silently
    dropped every row beyond it — a truncated pool list is how released
    amounts read empty and how a real pool vanishes from valuation. Reads
    are side-effect free, so retrying transient failures is safe; broadcasts
    NEVER go through here (they use broadcast_once_hive, which must not
    retry).

    2026-11-14 (Fable round 5, LIVE INCIDENT): ONLY list-table 'find'
    paginates. findOne/getContract return DICTS — the old path did
    rows.extend(dict), which appends the dict's KEYS, handing every dict
    caller a list of strings that crashed on .get(). That took the 23:00
    tick down at cron_lp_agent's first findOne. Non-'find' results are
    returned untouched; a 'find' that somehow yields a non-list is also
    returned untouched rather than exploded into keys.

    2026-11-15 (Fable round 6, the REAL root cause of the "black-hole"
    incident): DOUBLE PAGINATION. he() overwrote the caller's offset with
    its own, always starting at 0 and returning the WHOLE table — so an
    adapter that ALSO paginates (lp_position_assessment.rows(), offset
    len(result)) saw a full table as every "page", never hit the short-
    page stop, and looped forever on any table >= 1000 rows (tokens/
    tokens). Every "landscape read exceeded 360s" tick and the original
    8h50m hang were this loop, not a dead node. CONTRACT: if the caller
    supplies "offset", he() is a SINGLE-PAGE reader — one call at that
    exact offset, page returned as-is (caller owns pagination). Without
    an offset he() owns pagination — but all pages of one he() call come
    from ONE node: a mid-pagination failure rotates and RESTARTS from
    offset 0 on the next node, so pages are never stitched across nodes
    with different sync heads.
    """
    payload = {"jsonrpc": "2.0", "method": method,
               "params": dict(params), "id": 1}

    if method != "find":
        return _rpc_rotated(payload, method).get("result") or {}

    if "offset" in params:
        # Caller-managed pagination: one page at the caller's offset,
        # verbatim. he() must NOT overwrite it — that was the infinite loop.
        resp = _rpc_rotated(payload, method)
        return resp.get("result", []) or []

    page_limit = int(params.get("limit", 1000) or 1000)
    # 2026-11-15 (Fable round 7, A-4): the breaker must gate this loop's
    # ENTRY too — the loop calls _call_bounded directly, so after a full-ring
    # failure each paginated find still burned one 60s node attempt before
    # noticing. One bounded sweep per process, then silence.
    if _HE_RING_DEAD[0]:
        raise RuntimeError(
            "HE RPC: the whole node ring already failed this process "
            "(circuit breaker) — not retrying")
    last_err: Exception | None = None
    for _ in range(len(HE_NODES)):
        with _HE_LOCK:
            node = HE_NODES[_HE_NODE_IDX[0]]
        rows: list = []
        offset = 0
        try:
            while True:
                payload["params"]["offset"] = offset
                resp = _call_bounded(node, payload)
                result = resp.get("result") if isinstance(resp, dict) else None
                if "result" in resp and result is None:
                    raise RuntimeError("node returned null for a find "
                                       "(incomplete mirror) — not an answer")
                page = resp.get("result", []) or []
                if not isinstance(page, list):
                    return page      # contract changed shape: never explode keys
                rows.extend(page)
                if len(page) < page_limit:
                    _he_node_persist()   # whole table read: node proven good
                    return rows
                offset += len(page)
                if offset > 100_000:   # hard stop: a table this big is a bug
                    raise RuntimeError(
                        f"pagination exceeded 100k rows on {method}")
        except Exception as e:
            last_err = e
            logging.getLogger(__name__).warning(
                "HE node %s failed mid-pagination (%s) — rotating and "
                "restarting the table read from offset 0",
                node["base"], type(e).__name__)
            with _HE_LOCK:
                _HE_NODE_IDX[0] = (_HE_NODE_IDX[0] + 1) % len(HE_NODES)
            if _HE_RING_DEAD[0]:
                raise last_err
    _HE_RING_DEAD[0] = True
    raise last_err if last_err else RuntimeError("HE RPC: no nodes")

# ─── Data Classes ────────────────────────────────────────────────────
@dataclass
class TokenBalance:
    symbol: str
    balance: float = 0.0
    staked: float = 0.0
    price_usd: float = 0.0
    # 2026-10-22 spec restore: value_usd is the LIQUID value only
    # (balance x price); staked value is reported separately — folding
    # stake into value_usd overstated deployable capital (pinned:
    # test_liquid_excludes_stake).
    value_usd: float = 0.0
    staked_value_usd: float = 0.0

@dataclass
class LPPosition:
    pool_id: str
    base: str
    quote: str
    shares: float
    base_deposited: float
    quote_deposited: float
    # Optional[float]: None = value UNKNOWN (missing/invalid USD price) —
    # never silently 0 (pinned: test_missing_price_is_unknown).
    value_usd: Optional[float] = None
    apr: float = 0.0
    # 2026-10-07: chain timeFactor (ms) of our liquidity-position row — the
    # timestamp the position was last touched (deposit/top-up rebase it).
    # The distribution contract's age bonus is computed from (now - timeFactor),
    # so the decision layer needs it to score bonus dilution on top-ups.
    time_factor_ms: float = 0.0

@dataclass
class PoolData:
    pool_id: str
    base: str
    quote: str
    base_reserve: float
    quote_reserve: float
    base_price: float
    quote_price: float
    volume_lifetime: float
    fees_lifetime: float
    apr: float
    liquidity_usd: float
    total_shares: float = 0.0
    base_quantity: float = 0.0
    quote_quantity: float = 0.0
    # 2026-10-17: raw 24h volumes in each side's own token units (contract
    # fields baseVolume/quoteVolume). Kept raw so the USD pass can convert
    # honestly once prices exist (volume_lifetime itself is recomputed there).
    base_volume: float = 0.0
    quote_volume: float = 0.0

    @property
    def fee_observation_status(self) -> str:
        """2026-10-22 spec restore (pinned: test_unknown_fee_has_explicit_status).
        Pool-level data carries NO longitudinal fee observation — that
        evidence is account-scoped in the fee snapshot store. 'unknown' is
        the honest answer at this layer; a missing status must never read
        as 'measured'."""
        return "unknown"

# ─── Price Engine ───────────────────────────────────────────────────
class PriceEngine:
    def __init__(self):
        self.pools: Dict[str, PoolData] = {}
        # Symbols to attempt market pricing for even though they appear in no
        # pool: reward tokens from active distributions and wallet holdings.
        # Populated by the caller BEFORE load_all(); a pool-less token cannot
        # be discovered from pool ids, so without this it is invisible.
        self.extra_price_candidates: set = set()
        # What the caller ASKED us to price. Kept so a partial fill can be
        # named explicitly instead of being hidden behind a success-shaped count.
        self.requested_price_candidates: set = set()
        # Pool ids the portfolio currently holds — the ONLY pools whose
        # pair/reward tokens are worth pricing.
        self.held_pools: set = set()
        self.token_prices: Dict[str, float] = {}
        self.token_precisions: Dict[str, int] = {}  # symbol -> precision
        # round 8 (B-3): symbols whose precision the CHAIN confirmed (vs a
        # defaults fallback) — pass 2 skips only these.
        self._precision_confirmed: set = set()
        # 2026-10-02: liquidity verdicts for tokens priced from the market
        # order book rather than from pool reserves. Declared here so a caller
        # can read it even when load_all() has not run or market pricing failed.
        self.market_liquidity: Dict[str, dict] = {}
        self._loaded = False

    def _load_token_precisions(self, extra_symbols=None):
        """Load token precision from the HE tokens table, BATCHED ($in).

        2026-11-15 (Fable round 7, JOB B): the hardcoded 16-symbol list left
        TRIBE, PEPE, PEPENTROPY (and any future destination) precision-unknown
        — round_amount refused, plan_idle_deploy refused, and the ladder's
        TOP destination could never deploy. The loader now takes any symbol
        set: the bootstrap list below, PLUS every token discovered in the
        loaded pools, the managed targets, and the caller's extras — one $in
        query instead of one call per symbol. Still fail-closed downstream:
        a symbol the chain does not know simply stays unknown.
        """
        symbols = set(extra_symbols or [])
        symbols.update([
            "ALIVE", "HEARTBEAT", "BBH", "POB", "CENT", "PHOTO", "PIZZA",
            "BEE", "HSBIDAO", "PIXYDUST", "UNICOIN", "SWAP.HIVE", "HIVE",
            "PSX", "DEC", "WEED"
        ])
        # 2026-11-15 (Fable round 8, B-3): skip only CHAIN-CONFIRMED symbols.
        # The defaults dict installs fallback precisions after a failed query;
        # skipping those would let invented precision stand in for chain truth
        # even after the node recovers.
        symbols -= getattr(self, "_precision_confirmed", set())
        if not symbols:
            return
        try:
            rows = he("find", {
                "contract": "tokens", "table": "tokens",
                "query": {"symbol": {"$in": sorted(symbols)}},
                "limit": 1000
            }) or []
            for row in rows:
                sym = row.get("symbol")
                if sym and row.get("precision") is not None:
                    self.token_precisions[sym] = int(row["precision"])
                    self._precision_confirmed.add(sym)
        except Exception as _e:
            # 2026-11-15 (Fable round 8, B-2): silence here made the armed
            # tick's outcome illegible — a refused $in query left symbols
            # unknown and the deploy refused without a why.
            print(f"  PriceEngine: precision batch query FAILED "
                  f"({type(_e).__name__}: {str(_e)[:120]}) — "
                  f"{len(symbols)} symbol(s) remain precision-unknown; "
                  f"deploys into them will refuse")
        # Defaults for tokens not in the list
        defaults = {
            "SWAP.HIVE": 8, "HIVE": 3, "HBD": 3,
            "BEE": 8, "POB": 8, "CENT": 8, "PIXYDUST": 8,
            "ALIVE": 4, "HEARTBEAT": 8, "BBH": 2, 
            "PHOTO": 2, "PIZZA": 2, "HSBIDAO": 3,
            "UNICOIN": 8, "PSX": 8, "DEC": 8, "WEED": 8
        }
        for sym, prec in defaults.items():
            self.token_precisions.setdefault(sym, prec)

    def round_amount(self, symbol: str, amount: float) -> float:
        """Floor-round to the token's precision.

        2026-10-22 spec restore (pinned: test_floor_never_overspends,
        test_unknown_precision_not_invented): a deploy sized by rounding UP
        can exceed the wallet balance — floor, never round-half. And an
        unknown symbol REFUSES rather than inventing precision 8: an
        unpriced token yields a refusal that NAMES the token, never a
        fabricated quantity.
        """
        prec = self.token_precisions.get(symbol)
        if prec is None:
            raise ValueError(
                f"unknown precision for {symbol!r}: refusing to size "
                f"capital against an invented precision")
        import math
        return math.floor(amount * 10 ** prec) / 10 ** prec

    def load_all(self):
        """Load all pools and compute prices from reserves, anchored to SWAP.HIVE."""
        if self._loaded:
            return
        
        # Load token precisions first
        self._load_token_precisions()
        
        raw = he("find", {
            "contract": "marketpools", "table": "pools", "query": {}, "limit": 1000
        })
        
        # First pass: build pool map
        for p in (raw or []):
            try:
                token_pair = p.get("tokenPair", "")
                if ":" not in token_pair:
                    continue
                base, quote = token_pair.split(":", 1)
                
                base_reserve = float(p.get("baseQuantity", 0))
                quote_reserve = float(p.get("quoteQuantity", 0))
                if base_reserve == 0 or quote_reserve == 0:
                    continue
                
                pool_id = f"{base}:{quote}"
                # basePrice is quote-per-base (e.g., SWAP.HIVE per CENT)
                # quotePrice is base-per-quote (e.g., CENT per SWAP.HIVE)
                base_price_in_quote = float(p.get("basePrice", quote_reserve / base_reserve))
                quote_price_in_base = float(p.get("quotePrice", base_reserve / quote_reserve))
                
                volume = float(p.get("baseVolume", 0)) * base_price_in_quote + float(p.get("quoteVolume", 0)) * quote_price_in_base
                # 2026-11-13 (Opus N3/P1-13): ONE fee constant, the verified
                # deployed value (tradeFeeMul=0.9975 = 0.25%). The old 0.3%
                # here disagreed with lp_route_cost's 0.1% — three modules,
                # three answers.
                try:
                    from lp_route_cost import SWAP_FEE as _SWAP_FEE
                    fees = volume * float(_SWAP_FEE)
                except Exception:
                    fees = volume * 0.0025
                
                apr = 0.0
                if volume > 0:
                    liquidity_usd = base_reserve * base_price_in_quote + quote_reserve * quote_price_in_base
                    if liquidity_usd > 0:
                        apr = (fees / liquidity_usd) * 365 * 100
                
                self.pools[pool_id] = PoolData(
                    pool_id=pool_id, base=base, quote=quote,
                    base_reserve=base_reserve, quote_reserve=quote_reserve,
                    base_price=base_price_in_quote, quote_price=quote_price_in_base,
                    volume_lifetime=volume, fees_lifetime=fees, apr=apr,
                    liquidity_usd=base_reserve * base_price_in_quote + quote_reserve * quote_price_in_base,
                    total_shares=float(p.get("totalShares", 0)),
                    base_quantity=base_reserve,
                    quote_quantity=quote_reserve,
                    base_volume=float(p.get("baseVolume", 0)),
                    quote_volume=float(p.get("quoteVolume", 0))
                )
            except Exception:
                continue

        # 2026-11-15 (Fable round 7, JOB B): SECOND precision pass — every
        # token the loaded pools, the managed targets, and the wallet can
        # touch. The bootstrap list alone left the ladder's top destination
        # (SWAP.HIVE:TRIBE) precision-unknown and undeployable.
        _discovered = set()
        for _p in self.pools.values():
            _discovered.add(_p.base)
            _discovered.add(_p.quote)
        for _pair in TARGET_POOLS:
            _discovered.update(_pair.split(":"))
        self._load_token_precisions(_discovered)

        # ===== Build USD price map via BFS from anchor tokens =====
        # ANCHOR: Use the SWAP.HIVE:SWAP.HBD pool as the live price oracle
        # This gives us the market-derived HIVE price, not the internal witness rate
        swap_hive_hbd = self.pools.get("SWAP.HIVE:SWAP.HBD")
        if swap_hive_hbd and swap_hive_hbd.base_price > 0:
            hive_usd = swap_hive_hbd.base_price * 1.0  # SWAP.HBD ≈ $1.00
            print(f"  Price anchor: HIVE = ${hive_usd:.6f} (from SWAP.HIVE:SWAP.HBD pool)")
        else:
            # P0-10 (Opus round 3): a missing anchor pool means the chain
            # read failed — REFUSE to price rather than fabricating $0.05.
            # SWAP.HIVE/HIVE stay ABSENT from token_prices: every consumer
            # treats an absent price as unknown (deploys refuse, valuations
            # read unpriced) — never a fabricated anchor. The stale
            # SWAP.BTC/ETH/LTC/DOGE constants are gone with it: those tokens
            # now price only from real pool routes, else they are unpriced.
            hive_usd = None
            print("  Price anchor: SWAP.HIVE:SWAP.HBD pool MISSING — HIVE "
                  "price UNKNOWN this tick (no fabricated fallback); "
                  "price-dependent actions will refuse")
        
        anchors = {
            "SWAP.HBD": 1.0,
            "HBD": 1.0,
        }
        if hive_usd is not None:
            anchors["SWAP.HIVE"] = hive_usd
            anchors["HIVE"] = hive_usd
        # Initialize with anchors
        for tok, price in anchors.items():
            if tok not in self.token_prices or self.token_prices[tok] == 0:
                self.token_prices[tok] = price
        
        # ===== Derive token prices from the deepest-liquidity pool vs an already-priced token =====
        # Strategy: for each unknown token, find ALL pools containing it where the OTHER side
        # is already priced. Compute the token's USD price from reserve ratios. Commit the
        # figure from the DEEPEST-known-side pool (deepest-liquidity, not a
        # median — the pre-2026-10-22 last-write-wins loop was
        # iteration-order dependent; now order-independent, pinned by
        # test_order_independent_deeper_known_side).
        #
        # For a pool (A, B) with reserves (rA, rB):
        #   A's implied price = (rB / rA) * price(B)   [if B is known]
        #   B's implied price = (rA / rB) * price(A)   [if A is known]
        
        def _derive_prices():
            changed_any = False
            # 2026-10-22 determinism restore (pinned:
            # test_order_independent_deeper_known_side): the previous
            # last-write-wins loop was silently iteration-order dependent —
            # a token priced from two pools got whichever pool the dict
            # reached last, contradicting the deepest-liquidity comment.
            # Collect ALL candidates for this round, then commit the
            # deepest-known-side figure per symbol: order-independent and
            # matching the documented intent.
            candidates = {}
            for pool in self.pools.values():
                base, quote = pool.base, pool.quote
                rB, rQ = pool.base_reserve, pool.quote_reserve
                if rB <= 0 or rQ <= 0:
                    continue
                b_known = self.token_prices.get(base, 0)
                q_known = self.token_prices.get(quote, 0)

                # derive quote from base: quote_usd = (rB * base_usd) / rQ
                if b_known > 0 and q_known == 0:
                    price = (rB * b_known) / rQ
                    weight = rB * b_known   # USD depth of the known side
                    cur = candidates.get(quote)
                    if cur is None or weight > cur[0]:
                        candidates[quote] = (weight, price)

                # derive base from quote: base_usd = (rQ * quote_usd) / rB
                elif q_known > 0 and b_known == 0:
                    price = (rQ * q_known) / rB
                    weight = rQ * q_known
                    cur = candidates.get(base)
                    if cur is None or weight > cur[0]:
                        candidates[base] = (weight, price)

            for sym, (_weight, price) in candidates.items():
                if price > 0:
                    self.token_prices[sym] = price
                    changed_any = True
            return changed_any
        
        # Iterate until no more prices can be derived (max 10 rounds)
        for _ in range(10):
            if not _derive_prices():
                break
        
        # Sanity clamp: reject prices that are clearly wrong
        # Only clamp if price is absurdly high (>$1M) or negative
        for sym in list(self.token_prices.keys()):
            p = self.token_prices[sym]
            if p > 1_000_000 or p < 0:
                print(f"  PriceEngine: clamped {sym} from ${p:.8f} to 0 (out of range)")
                self.token_prices[sym] = 0
        
        # 2026-10-02: fill pool-less tokens from the market order book.
        #
        # Every price above is derived from POOL RESERVES, so a token that has a
        # real market but no pool at all is left at 0. That silently made ten
        # reward tokens (BBHO, FRESA, HOP, IDD, WRD, POKEMON, BEATCZ, PEPECASH,
        # SWAP.PEPE, SWAP.PEPET) look unpriced, which in turn made
        # ALIVE:HEARTBEAT and ALIVE:SWAP.HIVE look dividend-free.
        #
        # Tydyn: "there are markets for all layer-2 Hive-Engine tokens,
        # regardless of whether there are any buy or sell orders, but not all
        # tokens have pools."
        #
        # The market price is the HIGHEST BID (what a sale realises), not
        # lastPrice (a stale print), and the bid/ask spread is retained as a
        # liquidity measure. Pool-derived prices are never overwritten.
        # Candidate list: the fallback iterates only keys ALREADY in the map,
        # and a pool-less token is never a key there. So build the candidate set
        # explicitly — every symbol seen anywhere we know about.
        # BOUNDED, NOT GLOBAL (2026-10-02 second fix). The previous set was
        # token_prices keys + every pool id + extras = 213 symbols, i.e. 213
        # sequential HTTP round-trips inside a one-shot load_all(). The run was
        # cut short again, still missing the tokens that motivated the change.
        # Price only what a decision about THIS portfolio consumes:
        #   - pool pair tokens of held pools (what we could deposit into)
        #   - reward tokens of held pools (what we would earn)
        #   - wallet holdings (what we already own)
        # Everything else stays unpriced, which is correct and cheap.
        _candidates = set(self.extra_price_candidates or ())
        try:
            for _pid in (self.held_pools or ()):
                _candidates.update(t for t in str(_pid).split(":") if t)
        except Exception:
            pass
        try:
            from lp_market_price import enrich_price_map
            _before = len([s for s, p in self.token_prices.items() if p == 0])
            self.token_prices, _added, _still = enrich_price_map(
                self.token_prices, symbols=sorted(_candidates))
            self.market_liquidity = _added
            if _added:
                _tight = sum(1 for m in _added.values() if m["liquidity"] == "tight")
                _illiq = sum(1 for m in _added.values() if not m["sizeable"])
                print(f"  PriceEngine: market-filled {len(_added)} of "
                      f"{len(_candidates)} candidates "
                      f"({_tight} tight, {_illiq} not sizeable for exit)")
            # NAME any requested token we could not price. A quiet partial fill
            # behind a success-shaped summary is how BBHO/FRESA/HOP went missing
            # while the log still read "198 filled".
            _missed = [s for s in (self.requested_price_candidates or ())
                       if not self.token_prices.get(s)]
            if _missed:
                print(f"  PriceEngine: WARNING {len(_missed)} requested token(s) "
                      f"still unpriced: {', '.join(sorted(_missed)[:12])}")
        except Exception as e:  # noqa: BLE001
            self.market_liquidity = {}
            print(f"  PriceEngine: market pricing unavailable ({type(e).__name__})")

        # 2026-10-02: the realisable-dividend gate needs a bid/ask spread for
        # EVERY token a distribution is paid in, not only the pool-less ones we
        # just filled. Pool-priced tokens have a price but no spread reading, so
        # their yield was UNKNOWN and could never count as income — the gate
        # was inert for exactly the tokens it exists to judge.
        _gate_syms = set(self.extra_price_candidates or ())
        try:
            from lp_market_price import market_price as _mp
            for _s in sorted(_gate_syms):
                if _s in self.market_liquidity or self.token_prices.get(_s):
                    _m = _mp(_s)
                    if _m:
                        self.market_liquidity.setdefault(_s, _m)
        except Exception as e:  # noqa: BLE001
            print(f"  PriceEngine: spread lookup for gate failed ({type(e).__name__})")

        # Report how many stayed unknown
        unknown = [s for s, p in self.token_prices.items() if p == 0]
        if unknown:
            print(f"  PriceEngine: {len(self.pools)} pools, {len(self.token_prices)} tokens priced, {len(unknown)} unknown")
        else:
            print(f"  PriceEngine: {len(self.pools)} pools, {len(self.token_prices)} tokens priced")

        # ===== SECOND PASS (2026-10-17): honest USD liquidity/volume/APR =====
        # The first pass computed liquidity_usd and volume_lifetime BEFORE any USD
        # price existed, as base_reserve * base_price_in_quote + quote_reserve *
        # quote_price_in_base — a sum of MIXED TOKEN UNITS (CENT-units plus
        # TRIBE-units), never USD. Downstream consumers read garbage:
        # CENT:TRIBE carried "$6.4M" against a real ~$746 TVL; the
        # min-liquidity gate passed $84 pools as $1.4M; the reward-per-HIVE
        # scoring term (2026-10-10) was INERT because its pool_tvl_map input
        # was this unit-babble. Recompute everything now that the USD price
        # map is complete. Convention preserved: volume counts BOTH sides
        # (each swap adds its size to baseVolume and quoteVolume alike).
        # A pool with EITHER side unpriced gets TVL 0.0 — UNKNOWN, never a
        # half-honest sum — matching the standing rule that a missing TVL
        # is not a small TVL (rph then skips the term, liquidity gates then
        # exclude the pool: both conservative and honest).
        _fixed = _unknown_tvl = 0
        for _pool in self.pools.values():
            _b_usd = self.token_prices.get(_pool.base) or 0.0
            _q_usd = self.token_prices.get(_pool.quote) or 0.0
            if _b_usd > 0 and _q_usd > 0:
                _pool.liquidity_usd = (_pool.base_reserve * _b_usd
                                       + _pool.quote_reserve * _q_usd)
                _pool.volume_lifetime = (_pool.base_volume * _b_usd
                                    + _pool.quote_volume * _q_usd)
                # 2026-11-14 (Fable round 5): this second pass still hardcoded
                # the pre-fee-fix 0.003, overwriting the SWAP_FEE (0.25%)
                # first-pass value for every priced pool. One fee constant.
                _pool.fees_lifetime = _pool.volume_lifetime * SWAP_FEE
                _pool.apr = ((_pool.fees_lifetime / _pool.liquidity_usd)
                             * 365 * 100) if _pool.liquidity_usd > 0 else 0.0
                _fixed += 1
            else:
                _pool.liquidity_usd = 0.0
                _pool.volume_lifetime = 0.0
                _pool.fees_lifetime = 0.0
                _pool.apr = 0.0
                _unknown_tvl += 1
        if _unknown_tvl:
            print(f"  PriceEngine: USD pass priced {_fixed} pools' TVL; "
                  f"{_unknown_tvl} left at 0 (a side token unpriced)")

        self._loaded = True

    def get_pool(self, base: str, quote: str) -> Optional[PoolData]:
        return self.pools.get(f"{base}:{quote}") or self.pools.get(f"{quote}:{base}")

    def exit_liquidity(self, symbols):
        """Total exit liquidity per symbol, in HIVE (2026-10-07, PsyopDaily
        mechanics verified against two of the site's published anchors).

        exit_liquidity(T) = order-book bid depth  +  max counter-side pool
        reserve, where:
          - bid depth    = sum(price * quantity) over market.buyBook rows for
            T (site anchor: WIT = 0.2432 HIVE — exact match);
          - pool depth   = the SWAP.HIVE-side (or deepest counter-side)
            reserve of the largest pool containing T — what a sale into the
            pool would actually pay out (site anchor: REVX = 0.2656 HIVE,
            exactly the SWAP.HIVE baseQuantity of SWAP.HIVE:REVX — NOT the
            pool's TVL, which would overstate depth ~2x in the DANGEROUS
            direction: approving an unexitable pool).
        ANCHOR EXEMPTION (caught by the live gate, 2026-10-07): SWAP.HIVE and
        SWAP.HBD are the L1 wrappers — their exit is the CHAIN BRIDGE
        (SWAP.HIVE → native HIVE at 1:1), not a market. Both probes
        legitimately read ~0 for them (SWAP.HIVE is the universal quote, so
        nobody bids FOR it and pool sales pay the other side), which without
        this exemption would false-positive-block every SWAP.HIVE pair from
        expansion — half the ecosystem. Anchors are therefore exempt from
        the floor: they appear in the map as None with this documented
        meaning (market-unbounded), which the gate correctly does not gate.
        Returns {symbol: depth_in_HIVE or None}. None = UNKNOWN (probe
        failure) or MARKET-UNBOUNDED (anchor) — never zero-as-unknown; the
        caller must treat None as not-gated, not as below-floor.
        Scoped by the caller to exactly the symbols the decision consumes
        (held-pool pair tokens + reward tokens) — one buyBook query per
        symbol, bounded by that set.
        """
        from lp_market_price import bid_depth_hive
        ANCHOR_EXIT_TOKENS = {"SWAP.HIVE", "SWAP.HBD"}
        out: Dict[str, Optional[float]] = {}
        for s in sorted(set(symbols or [])):
            if s in ANCHOR_EXIT_TOKENS:
                out[s] = None  # chain-bridge exit, market-unbounded — see docstring
                continue
            book = bid_depth_hive(s)
            pool_depth = None
            best = None
            for p in self.pools.values():
                if s not in (p.base, p.quote):
                    continue
                # counter-side reserve: what the sale of T lands as.
                # Orientation: if T is base, you receive quote; and vice versa.
                counter = p.quote_quantity if s == p.base else p.base_quantity
                # depth must be HIVE-denominated: only counter == SWAP.HIVE
                # is a direct reading; a non-HIVE counter is not exit depth
                # without a conversion, so it does not count as pool depth.
                counter_sym = p.quote if s == p.base else p.base
                if counter_sym == "SWAP.HIVE":
                    if best is None or counter > best:
                        best = counter
            if best is not None:
                pool_depth = best
            if book is None and pool_depth is None:
                out[s] = None
            else:
                out[s] = (book or 0.0) + (pool_depth or 0.0)
        return out

    def find_path(self, from_sym: str, to_sym: str) -> List[PoolData]:
        """Find swap path (max 2 hops)."""
        if from_sym == to_sym:
            return []
        direct = self.get_pool(from_sym, to_sym)
        if direct:
            return [direct]
        # 2-hop via HIVE
        for mid in ["HIVE", "SWAP.HIVE"]:
            p1 = self.get_pool(from_sym, mid)
            p2 = self.get_pool(mid, to_sym)
            if p1 and p2:
                return [p1, p2]
        return []

    def estimate_swap_out(self, amount_in: float, path: List[PoolData]) -> float:
        amt = amount_in
        for pool in path:
            if pool.base == "HIVE" or pool.quote == "HIVE":
                # Simplified constant product
                if amt < pool.base_reserve * 0.1:  # <10% impact
                    amt = amt * pool.base_price * 0.997
                else:
                    return 0
            else:
                amt = amt * 0.997 * 0.9  # rough with fees
        return amt

# ─── Portfolio Manager ──────────────────────────────────────────────
class PortfolioManager:
    def __init__(self, price_engine: PriceEngine):
        self.pe = price_engine
        self.balances: Dict[str, TokenBalance] = {}
        self.positions: Dict[str, LPPosition] = {}
        # 2026-10-22: the position-assessment orchestration reads the
        # account name from the portfolio it is handed.
        self.account = ACCOUNT

    def load_balances(self):
        raw = he("find", {
            "contract": "tokens", "table": "balances",
            "query": {"account": ACCOUNT}, "limit": 200
        })
        for b in raw:
            sym = b.get("symbol", "")
            bal = float(b.get("balance", 0))
            staked = float(b.get("stake", 0))
            price = self.pe.token_prices.get(sym, 0)
            # 2026-10-22 spec restore: LIQUID value only in value_usd;
            # staked value reported separately (pinned: test_liquid_excludes_stake).
            self.balances[sym] = TokenBalance(sym, bal, staked, price,
                                              bal * price, staked * price)

    def load_positions(self):
        """Load LP positions via the PUBLIC RPC — reads never open the key
        vault (pinned: test_position_valuation's open-forbidden patch; the
        nectarengine rewrite loaded keys just to read public rows). Reload
        REPLACES: stale positions must not survive (pinned:
        test_reload_removes_stale_positions). Missing/invalid USD prices
        read as UNKNOWN (None), never 0."""
        import math
        self.positions = {}
        rows = he("find", {
            "contract": "marketpools", "table": "liquidityPositions",
            "query": {"account": ACCOUNT}, "limit": 200
        })
        for p in rows:
            try:
                token_pair = p.get("tokenPair", "")
                if ":" not in token_pair:
                    continue
                base, quote = token_pair.split(":", 1)
                shares = float(p.get("shares", 0))

                # Get deposited amounts from pool data
                pool = self.pe.get_pool(base, quote)
                base_dep = 0.0
                quote_dep = 0.0
                if pool and pool.total_shares > 0:
                    base_dep = shares / pool.total_shares * pool.base_quantity
                    quote_dep = shares / pool.total_shares * pool.quote_quantity

                val = None
                apr = 0.0
                if pool:
                    # 2026-09-30 (kept): pool.base_price/quote_price are
                    # exchange ratios, NOT USD — value from the validated
                    # USD price map, per token. A missing price is UNKNOWN
                    # (None), never 0 (which would silently understate).
                    p_base = self.pe.token_prices.get(base)
                    p_quote = self.pe.token_prices.get(quote)
                    valid_base = p_base is not None and p_base > 0 and math.isfinite(p_base)
                    valid_quote = p_quote is not None and p_quote > 0 and math.isfinite(p_quote)
                    if valid_base and valid_quote:
                        assert p_base is not None and p_quote is not None
                        val = (base_dep * p_base) + (quote_dep * p_quote)
                    else:
                        val = None
                        missing = [t for t, v, ok in
                                   ((base, p_base, valid_base),
                                    (quote, p_quote, valid_quote)) if not ok]
                        print(f"  Warning: no valid USD price for {missing} in "
                              f"{token_pair} — value UNKNOWN, reported as None")
                    apr = pool.apr

                pid = f"{base}:{quote}"
                self.positions[pid] = LPPosition(pid, base, quote, shares, base_dep, quote_dep, val, apr,
                                                  time_factor_ms=float(p.get("timeFactor", 0) or 0))
            except Exception as e:
                print(f"  Warning: Failed to parse position {p}: {e}")
                continue

    def total_value(self):
        """None when ANY position's value is unknown — an unknown is not a
        zero and must not be folded into a completed-looking total (pinned:
        test_unknown_total_is_not_complete_value)."""
        total = sum(tb.value_usd for tb in self.balances.values())
        for p in self.positions.values():
            if p.value_usd is None:
                return None
            total += p.value_usd
        return total

    def valuation_summary(self):
        """2026-10-22 spec restore (pinned by test_position_valuation): the
        report must say WHICH positions are unpriced instead of implying
        uniform coverage."""
        return {"total_usd": self.total_value(),
                "unpriced_positions": sorted(
                    pid for pid, p in self.positions.items()
                    if p.value_usd is None),
                "position_count": len(self.positions),
                "balance_count": len(self.balances)}

    def lp_value(self) -> float:
        known = [p.value_usd for p in self.positions.values()
                 if p.value_usd is not None]
        return sum(known)

    def idle_value(self) -> float:
        # 2026-10-22 spec restore: pure sum — dust is NOT filtered from the
        # valuation (pinned: test_dust_is_not_filtered_from_idle; the 1e-4
        # display filter belongs to printing, not to measurement).
        return sum(tb.value_usd for tb in self.balances.values())

# ─── Pool Analyzer ──────────────────────────────────────────────────
class PoolAnalyzer:
    def __init__(self, price_engine: PriceEngine, portfolio: PortfolioManager):
        self.pe = price_engine
        self.portfolio = portfolio

    def score_pool(self, pool: PoolData) -> dict:
        """Score pool 0-1 for LP attractiveness."""
        liq = pool.liquidity_usd
        apr = pool.apr
        vol_liq = pool.volume_lifetime / liq if liq > 0 else 0
        
        # Liquidity score (0-1): want deep pools
        liq_score = min(liq / 10000, 1.0)
        
        # APR score (0-1): diminishing returns
        apr_score = min(apr / 100, 1.0)
        
        # Volume/liquidity = turnover (0-1): healthy trading
        turn_score = min(vol_liq * 10, 1.0)
        
        # Impermanent loss risk: lower for stable-correlated pairs
        stable_pairs = {("HIVE", "SWAP.HIVE"), ("SWAP.HIVE", "HIVE")}
        il_risk = 0.1 if (pool.base, pool.quote) in stable_pairs or (pool.quote, pool.base) in stable_pairs else 0.5
        
        # Can we enter? Need both tokens
        has_base = self.portfolio.balances.get(pool.base, TokenBalance(pool.base)).balance > 0.0001
        has_quote = self.portfolio.balances.get(pool.quote, TokenBalance(pool.quote)).balance > 0.0001
        can_enter = has_base and has_quote
        
        total = 0.3 * liq_score + 0.3 * apr_score + 0.2 * turn_score + 0.2 * (1 - il_risk)
        
        return {
            "pool": pool.pool_id,
            "total_score": total,
            "liq_score": liq_score,
            "apr_score": apr_score,
            "turn_score": turn_score,
            "il_risk": il_risk,
            "est_apr": apr,
            "liquidity_usd": liq,
            "can_enter": can_enter
        }

    def rank_pools(self, min_liquidity: float = 100) -> List[dict]:
        ranked = []
        for pool in self.pe.pools.values():
            if pool.liquidity_usd < min_liquidity:
                continue
            # Skip dead projects
            if pool.base in DEAD_PROJECTS or pool.quote in DEAD_PROJECTS:
                continue
            ranked.append(self.score_pool(pool))
        ranked.sort(key=lambda x: x["total_score"], reverse=True)
        return ranked

# ─── Reallocation Planner ────────────────────────────────────────────


# ─── Live gate ──────────────────────────────────────────────────────
class LiveNotArmed(RuntimeError):
    """Raised instead of signing: live broadcasting was never armed."""


def live_armed() -> tuple:
    """Is live broadcasting armed? -> (bool, why-not)

    Signing requires BOTH, deliberately:
      1. LP_TEST_LIVE == "1" in the environment (exported by run_lp_agent.sh
         only when the AUTONOMY_ON flag exists), and
      2. ~/.hermes/lp_tests/AUTONOMY_ON present on disk.

    2026-11-03 root cause: a bare `pytest -q` from the tree root collected
    archive/test_cycle.py — a file named test_*.py with unguarded module-level
    code whose run_cycle() executed ON IMPORT and broadcast real market buys
    and add_liquidity calls. The ledger never saw them because the executions
    log is HOME-expanded and test runs sandbox HOME. The key was never meant
    to be reachable from a test process, so this chokepoint refuses instead of
    trusting every caller to check (audit: lp_chain_audit.py).
    """
    import os
    if os.environ.get("LP_TEST_LIVE") != "1":
        return False, "LP_TEST_LIVE != 1"
    if not os.path.exists(os.path.expanduser("~/.hermes/lp_tests/AUTONOMY_ON")):
        return False, "AUTONOMY_ON flag absent"
    return True, "armed"


# ─── Execution Engine ───────────────────────────────────────────────
class ExecutionEngine:
    def __init__(self, price_engine: PriceEngine, portfolio: PortfolioManager):
        self.pe = price_engine
        self.portfolio = portfolio
        # LIVE GATE: never load the key and never build a signing instance
        # unless live is deliberately armed. Read-only callers still work;
        # every broadcast method refuses with LiveNotArmed.
        self.armed, self._not_armed_why = live_armed()
        self.acct = json.load(open(VAULT_PATH)) if self.armed else None
        self.hive = None
        if self.armed:
            from lp_broadcast_guard import broadcast_once_hive
            # BROADCAST-ONCE: self.hive feeds ONLY swap/add/remove broadcasts
            # (Market at :1030, LiquidityPool at :1047/:1066) — never reads —
            # so RPC failures must raise, never auto-retry (lp_broadcast_guard.py).
            self.hive = broadcast_once_hive(node=[HIVE_RPC], keys={self.acct["account"]: self.acct["active"]})

    def _require_armed(self, what: str) -> None:
        if not self.armed:
            raise LiveNotArmed(f"{what} refused: live not armed ({self._not_armed_why})")

    def _log_broadcast(self, action: str, tx_result, detail: dict) -> None:
        """Self-ledgering: everything that leaves this class is recorded with
        its trx_id, so reconciliation can never again disagree because a caller
        forgot to log (lp_chain_audit.py)."""
        rec = {"logged_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action,
               "live": True, "pipeline": "liquidity_agent_v2",
               "detail": detail, "tx_result": tx_result}
        try:
            import lp_executor_test as _le
            _le._log(rec)                   # canonical writer (owns the path)
        except Exception as e:              # never mask a trade — but be loud
            print(f"    !! LEDGER WRITE FAILED ({e}): {json.dumps(rec)[:400]}")

# 2026-11-15 (Fable round 6, triage item 5-cheap): ExecutionEngine's capless
# add_liquidity/remove_liquidity are DELETED. No live callers remained (the
# real paths are TestExecutor.remove_liquidity / the realloc executor, both
# chokepointed and capped); remove_liquidity here never wrote the ledger and
# took ABSOLUTE lp_tokens where the contract wants a PERCENTAGE — the exact
# backwards-unit class that put us in the 08:20 incident. Dead capability is
# attack surface: the only broadcast primitives left are chokepointed.

# ─── Strategy Engine ────────────────────────────────────────────────


class LiquidityAgent:
    def __init__(self):
        self.pe = PriceEngine()
        self.portfolio = PortfolioManager(self.pe)
        self.analyzer = PoolAnalyzer(self.pe, self.portfolio)
        # 2026-10-22 spec restore (pinned: test_cron_dry_run): the executor
        # loads the KEY VAULT in its constructor, so a dry-run tick must
        # never construct it — lazy properties, constructed only on first
        # actual use by the live paths.
        self._executor = None
        # 2026-11-13 (Opus review N4): the `strategy` property (StrategyEngine)
        # was deleted with its class — it was a live-capable, capless trading
        # engine whose only consumer (hive_lp_agent.py) is archived. Any new
        # broadcast path must go through live_armed() + the cap +
        # broadcast_once_hive.

    @property
    def executor(self):
        if self._executor is None:
            self._executor = ExecutionEngine(self.pe, self.portfolio)
        return self._executor

