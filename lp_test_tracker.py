"""
Progressive Liquidity Discovery tracker.

Persistent SQLite state for liquidity productivity tests:
- deploy test liquidity at an escalating level
- measure earnings (fees + rewards) over a test window (default 24h)
- success  -> pool marked 'productive' (graduates; agent treats as proven pool)
- failure  -> next test must be >= prev_level * escalation_factor (1.5x)
- repeated failure (max_failures) -> pool on 'hold' for hold_days (default 90), then retest

DB: ~/.hermes/lp_tests/lp_tests.db
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

DB_DIR = os.path.expanduser("~/.hermes/lp_tests")
DB_PATH = os.path.join(DB_DIR, "lp_tests.db")

# ---- config defaults (cron script can override via its own config dict) ----
DEFAULTS = {
    "escalation_factor": 1.5,
    "test_window_hours": 24,
    "max_failures": 4,          # consecutive failures before hold
    "hold_days": 90,
    "min_test_usd": 0.01,        # minimum test deployment (fallback for pools without token config)
    "max_test_usd": 25.0,       # cap a single test deployment
    # NEW: token-quantity-based test config (overrides USD when present)
    # Format: { "pool_id": {"token_symbol": "BASE", "min_quantity": 0.001, "escalation_factor": 1.5} }
    "token_test_config": {},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS lp_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool TEXT NOT NULL,                -- 'BASE:QUOTE'
    level_usd REAL NOT NULL,           -- USD value deployed for this test (legacy/compat)
    level_quantity REAL,               -- token quantity deployed for this test (NEW)
    token_symbol TEXT,                 -- which token the quantity refers to (NEW)
    deployed_at TEXT NOT NULL,         -- ISO utc
    check_after TEXT NOT NULL,         -- ISO utc (deployed_at + window)
    checked_at TEXT,                   -- ISO utc when outcome recorded
    shares REAL,                       -- LP shares deployed
    baseline_json TEXT,                -- snapshot of relevant state at deploy time
    result_json TEXT,                  -- measured earnings etc at check time
    outcome TEXT,                      -- testing|success|failed|hold
    fail_streak INTEGER DEFAULT 0,
    hold_until TEXT,                   -- ISO utc, set when outcome='hold'
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_lp_tests_pool ON lp_tests(pool);
CREATE INDEX IF NOT EXISTS idx_lp_tests_state ON lp_tests(outcome);
"""


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


class LPTestTracker:
    def __init__(self, config=None, db_path=DB_PATH):
        self.config = dict(DEFAULTS)
        if config:
            self.config.update(config)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- pool state ----------
    def pool_state(self, pool):
        """Return dict with current state for a pool:
        {status: untested|testing|productive|hold, active_test, last_level, fail_streak, productive_since}
        """
        cur = self.conn.execute(
            "SELECT * FROM lp_tests WHERE pool=? ORDER BY id DESC", (pool,))
        rows = cur.fetchall()
        if not rows:
            return {"status": "untested", "active_test": None,
                    "last_level": None, "fail_streak": 0}

        # active (unchecked) test?
        active = next((r for r in rows if r["outcome"] == "testing"), None)
        # last completed outcomes
        completed = [r for r in rows if r["outcome"] in ("success", "failed", "hold")]
        last_completed = completed[0] if completed else None

        # hold still in effect?
        hold_row = next((r for r in rows if r["outcome"] == "hold"
                         and r["hold_until"]
                         and datetime.fromisoformat(r["hold_until"]) > _now()), None)
        if hold_row and not active:
            return {"status": "hold", "active_test": None,
                    "last_level": completed[0]["level_usd"] if completed else None,
                    "fail_streak": completed[0]["fail_streak"] if completed else 0,
                    "hold_until": hold_row["hold_until"]}

        # productive if most recent completed outcome is success and no later fail
        if last_completed and last_completed["outcome"] == "success":
            return {"status": "productive", "active_test": active and dict(active),
                    "last_level": last_completed["level_usd"],
                    "fail_streak": 0,
                    "productive_since": last_completed["checked_at"]}

        if active:
            return {"status": "testing", "active_test": dict(active),
                    "last_level": active["level_usd"],
                    "fail_streak": last_completed["fail_streak"] if last_completed else 0}

        # otherwise: failed recently → ready for next escalated test
        return {"status": "failed", "active_test": None,
                "last_level": last_completed["level_usd"] if last_completed else None,
                "fail_streak": last_completed["fail_streak"] if last_completed else 0}

    # ---------- lifecycle ----------
    def start_test(self, pool, level_usd, level_quantity=None, token_symbol=None, shares=None, baseline=None):
        now = _now()
        check_after = now + timedelta(hours=self.config["test_window_hours"])
        cur = self.conn.execute(
            """INSERT INTO lp_tests
               (pool, level_usd, level_quantity, token_symbol, deployed_at, check_after, shares, baseline_json, outcome)
               VALUES (?,?,?,?,?,?,?,?, 'testing')""",
            (pool, level_usd, level_quantity, token_symbol, _iso(now), _iso(check_after),
             shares, json.dumps(baseline or {})))
        self.conn.commit()
        return cur.lastrowid

    def due_tests(self):
        now = _iso(_now())
        cur = self.conn.execute(
            "SELECT * FROM lp_tests WHERE outcome='testing' AND check_after<=?", (now,))
        return [dict(r) for r in cur.fetchall()]

    def record_result(self, test_id, earned_core_amount, measured, notes=""):
        """earned_core_amount: float. >0 => success; else failed/hold per streak."""
        row = self.conn.execute(
            "SELECT * FROM lp_tests WHERE id=?", (test_id,)).fetchone()
        if not row or row["outcome"] != "testing":
            return None
        if earned_core_amount is None or (measured or {}).get("measurement_status") == "unmeasured":
            # Retain testing state: absence of evidence is not a failed trial.
            self.conn.execute(
                "UPDATE lp_tests SET result_json=?, notes=? WHERE id=?",
                (json.dumps(measured or {}), notes, test_id))
            self.conn.commit()
            return {"outcome": "unmeasured", "fail_streak": row["fail_streak"],
                    "hold_until": row["hold_until"]}
        now = _now()
        if earned_core_amount and earned_core_amount > 0:
            outcome = "success"
            fail_streak = 0
            hold_until = None
        else:
            prev = self.conn.execute(
                "SELECT fail_streak FROM lp_tests WHERE pool=? AND outcome IN ('failed','hold') ORDER BY id DESC LIMIT 1",
                (row["pool"],)).fetchone()
            fail_streak = (prev["fail_streak"] if prev else 0) + 1
            if fail_streak >= self.config["max_failures"]:
                outcome = "hold"
                hold_until = _iso(now + timedelta(days=self.config["hold_days"]))
            else:
                outcome = "failed"
                hold_until = None

        self.conn.execute(
            """UPDATE lp_tests SET checked_at=?, result_json=?, outcome=?,
               fail_streak=?, hold_until=?, notes=? WHERE id=?""",
            (_iso(now), json.dumps(measured or {}), outcome,
             fail_streak, hold_until, notes, test_id))
        self.conn.commit()
        return {"outcome": outcome, "fail_streak": fail_streak, "hold_until": hold_until}

    def next_test_level(self, pool):
        """USD level for the next test of this pool. None if not testable now."""
        st = self.pool_state(pool)
        if st["status"] in ("testing", "hold"):
            return None
        min_lvl = self.config["min_test_usd"]
        if st["status"] == "productive":
            return None  # proven, no test needed
        last = st.get("last_level")
        if last is None:
            level = min_lvl
        else:
            level = max(min_lvl, last * self.config["escalation_factor"])
        return min(level, self.config["max_test_usd"])

    def next_test_quantity(self, pool, price_engine=None):
        """
        Token quantity for the next test of this pool.
        Uses token_test_config if available for the pool, otherwise falls back to USD-based.
        Returns (quantity, token_symbol, level_usd) or (None, None, None) if not testable.
        """
        st = self.pool_state(pool)
        if st["status"] in ("testing", "hold"):
            return None, None, None
        if st["status"] == "productive":
            return None, None, None  # proven, no test needed
        
        token_config = self.config.get("token_test_config", {}).get(pool)
        if token_config:
            token_symbol = token_config.get("token_symbol")
            min_quantity = token_config.get("min_quantity", 100)
            escalation = token_config.get("escalation_factor", self.config["escalation_factor"])
            
            # Get last completed test for this pool
            cur = self.conn.execute(
                "SELECT level_quantity, token_symbol FROM lp_tests WHERE pool=? AND outcome IN ('success','failed','hold') ORDER BY id DESC LIMIT 1",
                (pool,))
            last_row = cur.fetchone()
            # sqlite3.Row or tuple depending on row_factory; handle both
            if last_row and last_row[0] and last_row[1] == token_symbol:
                quantity = max(min_quantity, last_row[0] * escalation)
            else:
                quantity = min_quantity
            
            # Also return USD equivalent for compatibility (using current price if available)
            level_usd = None
            if price_engine and token_symbol:
                price = price_engine.token_prices.get(token_symbol, 0)
                if price > 0:
                    level_usd = quantity * price
            
            return quantity, token_symbol, level_usd
        
        # Fallback to USD-based
        level_usd = self.next_test_level(pool)
        if level_usd is None:
            return None, None, None
        return None, None, level_usd

    def summary(self):
        cur = self.conn.execute("SELECT pool, outcome, fail_streak, level_usd, hold_until FROM lp_tests ORDER BY id")
        return [dict(r) for r in cur.fetchall()]


def measure_lp_test_earnings(agent, test_record):
    """Legacy snapshots cannot establish pool-attributed income.

    Pool fee counters (even when available) are not our earnings; wallet deltas
    are not reconciled rewards. Never add unlike raw token amounts or treat a
    missing observation as zero. An attribution adapter is required before this
    path can produce a numeric result. Keep old snapshots untouched.
    """
    return None, {
        "measurement_status": "unmeasured",
        "reason": "pool-attributed fee and reward evidence unavailable",
        "pool": test_record["pool"],
        "fees_earned_base": None,
        "fees_earned_quote": None,
        "rewards_earned": None,
        "total_rewards_earned": None,
        "current_fees_base": None,
        "current_fees_quote": None,
    }


if __name__ == "__main__":
    t = LPTestTracker()
    print("DB:", t.db_path)
    for pool in ("SWAP.HIVE:WAIV", "SWAP.HIVE:BEE"):
        st = t.pool_state(pool)
        nxt = t.next_test_level(pool)
        print(f"{pool}: state={st['status']}, next_test_level={nxt}")
