"""Read-only live diagnostic. Public RPC only; no key loading, executor, or tracker."""
import ast
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
import argparse
import math
import sys
sys.path.insert(0, '/home/tydynrain/.hermes/scripts')
from lp_reward_math import estimate_batch
from decimal import Decimal, ROUND_DOWN

SOURCE = Path('/home/tydynrain/.hermes/scripts')
OUT = Path('/home/tydynrain/hive-agent-test/reports')

def definitions(file, names, scope):
    tree = ast.parse(file.read_text())
    selected = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    assert {n.name for n in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(file), 'exec'), scope)


def run():
    scope = dict(dataclass=dataclass, field=field, Dict=Dict, List=List, Optional=Optional,
                 Tuple=Tuple, Request=Request, urlopen=urlopen, json=json,
                 math=math, Decimal=Decimal, ROUND_DOWN=ROUND_DOWN, datetime=datetime,
                 HE_RPC='https://api.hive-engine.com/rpc/contracts')
    definitions(SOURCE/'liquidity_agent_v2.py',
                ['he','he_all','mark_amount','complete_sum','TokenBalance','LPPosition','PoolData','PriceEngine','PortfolioManager'], scope)
    definitions(SOURCE/'cron_lp_agent.py', ['discover_rewards','manage_positions'], scope)
    he = scope['he']
    original_he = he
    calls = []
    def readonly_he(method, params):
        assert method in ('find', 'getContract'), 'Non-read RPC denied'
        result = original_he(method, params)
        calls.append({'method':method, 'params':params, 'result':result})
        return result
    scope['he'] = readonly_he
    pe = scope['PriceEngine']()
    portfolio = scope['PortfolioManager'](pe)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    log = OUT/f'planner-diagnostic-{stamp}.txt'
    with log.open('w') as output, contextlib.redirect_stdout(output):
        print('LIVE READ-ONLY PLANNER DIAGNOSTIC', stamp)
        print('Account: hermes-sandbox. Corrected indicative valuation; no sale quote claimed.')
        print('No keys loaded; no agent executor; no tracker or database writes.')
        pe.load_all()
        portfolio.load_balances()
        portfolio.load_positions()
        for pair, position in portfolio.positions.items():
            print(f'[VALUATION] {pair}: token_price_mark={position.value_usd!r} timeFactor={position.time_factor}')
        print('VALUATION SUMMARY:', json.dumps(portfolio.valuation_summary()))
        print(f'Loaded positions: {len(portfolio.positions)}')
        rewards = scope['discover_rewards'](pe, portfolio)
        estimates = []
        for pair in portfolio.positions:
            pool = pe.pools.get(pair)
            if not pool or pool.total_shares <= 0:
                continue
            participants = scope['he_all']('marketpools', 'liquidityPositions', {'tokenPair': pair})
            seen = set()
            for info in rewards.get(pair, []):
                if info['type'] != 'distribution' or info['batch_id'] in seen:
                    continue
                seen.add(info['batch_id'])
                batch = info['batch']
                # Condition on the earliest nominal next tick, never label as guaranteed.
                # Participant snapshot is sequential public RPC, not atomic block state.
                at_ms = int(Decimal(str(batch['lastTickTime'])) + Decimal(str(info['distribution_params']['distTickHours'])) * 3600000)
                at_ms = max(at_ms, int(datetime.now(timezone.utc).timestamp()*1000))
                result = estimate_batch(batch, participants, info['distribution_params'], pe.token_precisions, portfolio.account, at_ms)
                result['token_pair'] = pair
                estimates.append(result)
                print('[CONDITIONAL TICK]', pair, batch['_id'], result['status'], json.dumps(result['entitlements']))
        agent = SimpleNamespace(pe=pe,portfolio=portfolio)
        actions = scope['manage_positions'](agent, rewards, diagnostics=True)
        print('RESULT:', json.dumps(actions))
        print('Read-only diagnostic complete. No transactions submitted.')
    snapshot = OUT/f'planner-snapshot-{stamp}.json'
    snapshot.write_text(json.dumps({'timestamp_utc':stamp,'account':'hermes-sandbox','rpc_calls':calls,'actions':actions,'candidate_screen':agent.candidate_screen,'pool_assessment':agent.pool_assessment,'conditional_tick_estimates':estimates,'valuation':portfolio.valuation_summary()}, indent=2))
    print(log.read_text())
    print('LOG:', log)
    print('SNAPSHOT:', snapshot)

if __name__ == '__main__':
    run()
