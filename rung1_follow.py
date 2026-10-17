#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Rung 1: follow custom_json via hermes-sandbox posting key from LUKS vault."""
import json
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))

h = Hive(node=["https://api.hive.blog", "https://rpc.ecency.com"],
         keys={"hermes-sandbox": acct["posting"]})

result = h.custom_json(
    id="follow",
    json_data=["follow", {"follower": "hermes-sandbox",
                          "following": "tydynrain",
                          "what": ["blog"]}],
    required_posting_auths=["hermes-sandbox"],
)
print(json.dumps(result, indent=1, default=str)[:1200])
