#!/usr/bin/env python3
"""LP Agent Operational Constraints -- Hard Guardrails

2026-10-05: Encoded from Tydyn's operational rules. These are HARD constraints,
not preferences. Violations block the action entirely.
"""

STAKING_THRESHOLDS = {
    "CENT": 20000,
    "PIMP": 5000,
    "BBHO": 20000,  # corrected per Tydyn (fact 262)
    # BBH deliberately absent - cannot be staked; dividends are HOLDING-based (min ~10)
}

GLOBAL_STAKING_THRESHOLDS = {
    "CENT": 20000,
    "PIMP": 5000,
    "BBHO": 20000,  # corrected per Tydyn (fact 262)
    # BBH deliberately absent - cannot be staked; dividends are HOLDING-based (min ~10)
}

ACCOUNT_CONSTRAINTS = {
    "aliveandthriving": {
        "ALIVE": {
            "max_lp_fraction": 0.10,
            "min_liquid_balance": 5000,
            "purpose": "payments, donations, contest prizes",
        },
        "ALIVEM": {
            "max_lp_fraction": 0.10,
            "min_liquid_balance": 5000,
            "purpose": "payments, donations, contest prizes",
        },
        "BEE": {
            "max_lp_fraction": 0.05,
            "min_liquid_balance": 10000,
            "purpose": "creating new ALIVE and HEARTBEAT pools",
            "pool_creation_reserve": True,
        },
    },
    "heartbeatvault01": {
        "BEE": {
            "dividend_program": "lbi-divs (SWAP.HBD daily on BEE balance)",
            "max_lp_fraction": 0.50,
            "dividend_bonus": True,
        },
    },
    "heartbeatonhive": {
        "BEE": {
            "max_lp_fraction": 0.30,
        },
        "HSBIDAO": {
            "max_lp_fraction": 0.30,
        },
        "PIXYDUST": {
            "max_lp_fraction": 0.30,
        },
    },
    "tydynrain": {
        "CENT": {
            "staking_threshold": 20000,
            "max_lp_fraction": 0.50,
        },
        "PIMP": {
            "staking_threshold": 5000,
            "max_lp_fraction": 0.50,
        },
        "BBHO": {
            "staking_threshold": 2000,
            "max_lp_fraction": 0.50,
        },
        "BBH": {
            "holding_dividend_threshold": 10,  # BBH cannot be staked; ~10 held earns weekly dividends
            "max_lp_fraction": 0.50,
        },
        "HSBIDAO": {
            "max_lp_fraction": 0.30,
        },
        "PIXYDUST": {
            "max_lp_fraction": 0.30,
        },
    },
}

GLOBAL_STAKING_THRESHOLDS = {
    "CENT": 20000,
    "PIMP": 5000,
    "BBHO": 20000,  # corrected per Tydyn (fact 262)
    # BBH deliberately absent - cannot be staked; dividends are HOLDING-based (min ~10)
}

ACCOUNT_CONSTRAINTS = {
    "aliveandthriving": {
        "ALIVE": {
            "max_lp_fraction": 0.10,
            "min_liquid_balance": 5000,
            "purpose": "payments, donations, contest prizes",
        },
        "ALIVEM": {
            "max_lp_fraction": 0.10,
            "min_liquid_balance": 5000,
            "purpose": "payments, donations, contest prizes",
        },
        "BEE": {
            "max_lp_fraction": 0.05,
            "min_liquid_balance": 10000,
            "purpose": "creating new ALIVE and HEARTBEAT pools",
            "pool_creation_reserve": True,
        },
    },
    "heartbeatvault01": {
        "BEE": {
            "dividend_program": "lbi-divs (SWAP.HBD daily on BEE balance)",
            "max_lp_fraction": 0.50,
            "dividend_bonus": True,
        },
    },
    "heartbeatonhive": {
        "BEE": {
            "max_lp_fraction": 0.30,
        },
        "HSBIDAO": {
            "max_lp_fraction": 0.30,
        },
        "PIXYDUST": {
            "max_lp_fraction": 0.30,
        },
    },
    "tydynrain": {
        "CENT": {
            "staking_threshold": 20000,
            "max_lp_fraction": 0.50,
        },
        "PIMP": {
            "staking_threshold": 5000,
            "max_lp_fraction": 0.50,
        },
        "BBHO": {
            "staking_threshold": 2000,
            "max_lp_fraction": 0.50,
        },
        "BBH": {
            "holding_dividend_threshold": 10,  # BBH cannot be staked; ~10 held earns weekly dividends
            "max_lp_fraction": 0.50,
        },
        "HSBIDAO": {
            "max_lp_fraction": 0.30,
        },
        "PIXYDUST": {
            "max_lp_fraction": 0.30,
        },
    },
}

GLOBAL_STAKING_THRESHOLDS = {
    "CENT": 20000,
    "PIMP": 5000,
    "BBHO": 20000,  # corrected per Tydyn (fact 262)
    # BBH deliberately absent - cannot be staked; dividends are HOLDING-based (min ~10)
}

ACCOUNT_CONSTRAINTS = {
    "aliveandthriving": {
        "ALIVE": {
            "max_lp_fraction": 0.10,
            "min_liquid_balance": 5000,
            "purpose": "payments, donations, contest prizes",
        },
        "ALIVEM": {
            "max_lp_fraction": 0.10,
            "min_liquid_balance": 5000,
            "purpose": "payments, donations, contest prizes",
        },
        "BEE": {
            "max_lp_fraction": 0.05,
            "min_liquid_balance": 10000,
            "purpose": "creating new ALIVE and HEARTBEAT pools",
            "pool_creation_reserve": True,
        },
    },
    "heartbeatvault01": {
        "BEE": {
            "dividend_program": "lbi-divs (SWAP.HBD daily on BEE balance)",
            "max_lp_fraction": 0.50,
            "dividend_bonus": True,
        },
    },
    "heartbeatonhive": {
        "BEE": {
            "max_lp_fraction": 0.30,
        },
        "HSBIDAO": {
            "max_lp_fraction": 0.30,
        },
        "PIXYDUST": {
            "max_lp_fraction": 0.30,
        },
    },
    "tydynrain": {
        "CENT": {
            "staking_threshold": 20000,
            "max_lp_fraction": 0.50,
        },
        "PIMP": {
            "staking_threshold": 5000,
            "max_lp_fraction": 0.50,
        },
        "BBHO": {
            "staking_threshold": 2000,
            "max_lp_fraction": 0.50,
        },
        "BBH": {
            "holding_dividend_threshold": 10,  # BBH cannot be staked; ~10 held earns weekly dividends
            "max_lp_fraction": 0.50,
        },
        "HSBIDAO": {
            "max_lp_fraction": 0.30,
        },
        "PIXYDUST": {
            "max_lp_fraction": 0.30,
        },
    },
}

GLOBAL_STAKING_THRESHOLDS = {
    "CENT": 20000,
    "PIMP": 5000,
    "BBHO": 20000,  # corrected per Tydyn (fact 262)
    # BBH deliberately absent - cannot be staked; dividends are HOLDING-based (min ~10)
}

ACCOUNT_CONSTRAINTS = {
    "aliveandthriving": {
        "ALIVE": {
            "max_lp_fraction": 0.10,
            "min_liquid_balance": 5000,
            "purpose": "payments, donations, contest prizes",
        },
        "ALIVEM": {
            "max_lp_fraction": 0.10,
            "min_liquid_balance": 5000,
            "purpose": "payments, donations, contest prizes",
        },
        "BEE": {
            "max_lp_fraction": 0.05,
            "min_liquid_balance": 10000,
            "purpose": "creating new ALIVE and HEARTBEAT pools",
            "pool_creation_reserve": True,
        },
    },
    "heartbeatvault01": {
        "BEE": {
            "dividend_program": "lbi-divs (SWAP.HBD daily on BEE balance)",
            "max_lp_fraction": 0.50,
            "dividend_bonus": True,
        },
    },
    "heartbeatonhive": {
        "BEE": {
            "max_lp_fraction": 0.30,
        },
        "HSBIDAO": {
            "max_lp_fraction": 0.30,
        },
        "PIXYDUST": {
            "max_lp_fraction": 0.30,
        },
    },
    "tydynrain": {
        "CENT": {
            "staking_threshold": 20000,
            "max_lp_fraction": 0.50,
        },
        "PIMP": {
            "staking_threshold": 5000,
            "max_lp_fraction": 0.50,
        },
        "BBHO": {
            "staking_threshold": 2000,
            "max_lp_fraction": 0.50,
        },
        "BBH": {
            "holding_dividend_threshold": 10,  # BBH cannot be staked; ~10 held earns weekly dividends
            "max_lp_fraction": 0.50,
        },
        "HSBIDAO": {
            "max_lp_fraction": 0.30,
        },
        "PIXYDUST": {
            "max_lp_fraction": 0.30,
        },
    },
}

GLOBAL_STAKING_THRESHOLDS = {
    "CENT": 20000,
    "PIMP": 5000,
    "BBHO": 20000,  # corrected per Tydyn (fact 262)
    # BBH deliberately absent - cannot be staked; dividends are HOLDING-based (min ~10)
}

ACCOUNT_RISK_BUDGETS = {
    "tydynrain": {
        "risk_budget": "moderate",
        "max_drawdown": 0.10,
        "max_lp_per_position": 0.10,
    },
    "aliveandthriving": {
        "risk_budget": "conservative",
        "max_drawdown": 0.05,
        "max_lp_per_position": 0.05,
    },
    "heartbeatvault01": {
        "risk_budget": "moderate",
        "max_drawdown": 0.08,
        "max_lp_per_position": 0.08,
    },
    "heartbeatonhive": {
        "risk_budget": "conservative",
        "max_drawdown": 0.03,
        "max_lp_per_position": 0.03,
    },
}

CROSS_ACCOUNT_COORDINATION = {
    "global_caps": True,
    "staking_thresholds_global": True,
    "bee_pool_creation_reserve_local": "aliveandthriving",
    "alive_alivem_payment_reserve_local": "aliveandthriving",
}

ACCOUNT_RISK_BUDGETS = {
    "tydynrain": {
        "risk_budget": "moderate",
        "max_drawdown": 0.10,
        "max_lp_per_position": 0.10,
    },
    "aliveandthriving": {
        "risk_budget": "conservative",
        "max_drawdown": 0.05,
        "max_lp_per_position": 0.05,
    },
    "heartbeatvault01": {
        "risk_budget": "moderate",
        "max_drawdown": 0.08,
        "max_lp_per_position": 0.08,
    },
    "heartbeatonhive": {
        "risk_budget": "conservative",
        "max_drawdown": 0.03,
        "max_lp_per_position": 0.03,
    },
}
