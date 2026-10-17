#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Rung 4: HE token transfer (0.01 HH -> tydynrain) via active-key custom_json."""
import json
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
h = Hive(node=["https://api.hive.blog", "https://rpc.ecency.com"],
         keys={"hermes-sandbox": acct["active"]})

payload = {
    "contractName": "tokens",
    "contractAction": "transfer",
    "contractPayload": {
        "symbol": "HH",
        "quantity": "0.01",
        "to": "tydynrain",
        "memo": "Hive L2 transfer test — hermes-sandbox (active key, vault-stored)"
    }
}

result = h.custom_json(
    id="ssc-mainnet-hive",
    json_data=payload,
    required_auths=["hermes-sandbox"],
)
print(json.dumps(result, indent=1, default=str)[:400])
