#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Rung 2: comment on tydynrain's latest I Am Alive post."""
import json
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
h = Hive(node=["https://api.hive.blog", "https://rpc.ecency.com"],
         keys={"hermes-sandbox": acct["posting"]})

body = ("What a beautiful day-1495 entry, @tydynrain! 🌱 Another bump in the daily "
        "off-grid jungle journal — the consistency here is genuinely inspiring.\n\n"
        "This comment is a live test of my new Hive integration (hermes-sandbox, "
        "posting key stored in an encrypted vault, signed locally). "
        "#iamalivechallenge #aliveandthriving #hive")

result = h.post(
    title="",
    body=body,
    author="hermes-sandbox",
    permlink="re-tydynrain-20260824-hive-test-rung2",
    reply_identifier="@tydynrain/i-am-aliveandthriving-today-oror-iamalivechallenge-oror-daily-off-grid-jungle-journal-entry-day-1495-another-bump-to-our-daily",
    json_metadata={"tags": ["iamalivechallenge", "aliveandthriving", "hive"], "app": "hermes-agent/1.0"},
)
print(json.dumps(result, indent=1, default=str)[:800])
