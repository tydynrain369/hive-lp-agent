#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Rung 3: upvote tydynrain's Day-1495 post at 1% weight (sandbox has ~0 HP, so symbolic)."""
import json
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
h = Hive(node=["https://api.hive.blog", "https://rpc.ecency.com"],
         keys={"hermes-sandbox": acct["posting"]})

result = h.vote(
    weight=1.0,  # percent; 1% test vote
    identifier="@tydynrain/i-am-aliveandthriving-today-oror-iamalivechallenge-oror-daily-off-grid-jungle-journal-entry-day-1495-another-bump-to-our-daily",
    account="hermes-sandbox",
)
print(json.dumps(result, indent=1, default=str)[:500])
