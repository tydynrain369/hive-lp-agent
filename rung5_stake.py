#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Rung 5: stake 0.01 HH (active key custom_json, tokens contract stake action)."""
import json
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
h = Hive(node=["https://api.hive.blog", "https://rpc.ecency.com"],
         keys={"hermes-sandbox": acct["active"]})

result = h.custom_json(
    id="ssc-mainnet-hive",
    json_data={"contractName": "tokens", "contractAction": "stake",
               "contractPayload": {"symbol": "HH", "quantity": "0.01"}},
    required_auths=["hermes-sandbox"],
)
print(json.dumps(result, indent=1, default=str)[:300])
