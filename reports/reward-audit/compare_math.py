"""Read-only offline arithmetic audit. All writes confined to this report directory."""
import copy
import json
import subprocess
from pathlib import Path
from decimal import Decimal

HERE = Path(__file__).resolve().parent
ns = {}
exec(compile((HERE / 'lp_reward_math_audited.py').read_text(), 'lp_reward_math_audited.py', 'exec'), ns)
estimate = ns['estimate_batch']
now = 1789650309000
tick = 86400000
params = {'distTickHours':'24','processQueryLimit':1000,'maxTransferLimit':50}
base = {'name':'baseline_synthetic','at_ms':now,'params':params,'precisions':{'TEST':8},
 'batch':{'_id':1,'strategy':'pool','active':True,'numTicksLeft':7,'lastTickTime':now-tick,
          'tokenPair':'X:Y','excludeAccount':[],'bonusCurve':{'numPeriods':'100','periodBonusPct':'1'},
          'tokenBalances':[{'symbol':'TEST','quantity':'1234.56789012'}]},
 'positions':[{'_id':1,'account':'hermes-sandbox','tokenPair':'X:Y','shares':'1.234567890123456789','timeFactor':now-tick*3},
              {'_id':2,'account':'other','tokenPair':'X:Y','shares':'9.876543210987654321','timeFactor':now-tick*50}]}
fixtures=[copy.deepcopy(base)]
for name,age in [('immature',tick-1),('exact_tick',tick),('tick_plus_one',tick+1),('fractional_timeFactor',tick*3+0.25),('bonus_cap',tick*200)]:
 f=copy.deepcopy(base);f['name']=name;f['positions'][0]['timeFactor']=now-age;fixtures.append(f)
f=copy.deepcopy(base);f['name']='numeric_periods_curve_disabled';f['batch']['bonusCurve']['numPeriods']=100;fixtures.append(f)
f=copy.deepcopy(base);f['name']='excluded_denominator';f['batch']['excludeAccount']=['other'];fixtures.append(f)
f=copy.deepcopy(base);f['name']='rounded_zero';f['batch']['tokenBalances'][0]['quantity']='0.00000001';fixtures.append(f)
f=copy.deepcopy(base);f['name']='queued_payment';f['params']['maxTransferLimit']=1;f['positions'].reverse();f['positions'][0]['_id']=1;f['positions'][1]['_id']=2;fixtures.append(f)
f=copy.deepcopy(base);f['name']='zero_pool_guard_difference';f['poolShares']='0';fixtures.append(f)
snapshot=json.loads((HERE/'earlier_snapshot_296.json').read_text())
batch=snapshot['batches'][0]
# Known precision entries from the earlier saved token table, not guessed.
source=json.loads(Path(snapshot['source']).read_text())
precisions={r['symbol']:r['precision'] for c in source['rpc_calls'] if c['params'].get('contract')=='tokens' and c['params'].get('table')=='tokens' for r in c['result']}
fixtures.append({'name':'earlier_live_snapshot_at_calculation_time_NOT_exact_reconciliation','at_ms':now,
 'params':snapshot['params'][0][0],'precisions':precisions,'batch':batch,'positions':snapshot['positions']})
(HERE/'math_fixtures.json').write_text(json.dumps(fixtures,indent=2))
p = subprocess.run(['node',str(HERE/'offline_authority_runner.js')],input=json.dumps(fixtures),text=True,capture_output=True,check=True,cwd=HERE)
node=json.loads(p.stdout)
(HERE/'offline_authority_results.json').write_text(json.dumps(node,indent=2))
results=[]
for f,j in zip(fixtures,node['outputs']):
 py=estimate(f['batch'],f['positions'],f['params'],f['precisions'],'hermes-sandbox',f['at_ms'])
 immediate=[e['data'] for e in j['events'] if e['event']=='payment' and e['data']['account']=='hermes-sandbox']
 queued=[a for pending in j['pending'] for a in pending['accounts'] if a['account']=='hermes-sandbox']
 deployed={a['symbol']:Decimal(a['quantity']) for a in immediate+queued}
 predicted={a['symbol']:Decimal(a['payout_share']) for a in py['entitlements'] or [] if Decimal(a['payout_share'])>0}
 results.append({'name':f['name'],'entitlement_match':deployed==predicted,'python':py,'deployed_immediate':immediate,'deployed_queued':queued,'deployed_errors':j['errors']})
(HERE/'math_comparison.json').write_text(json.dumps(results,indent=2))
print(json.dumps({'cases':len(results),'matches':sum(r['entitlement_match'] for r in results),
 'summary':[{'name':r['name'],'match':r['entitlement_match'],'immediate':r['deployed_immediate'],'queued':r['deployed_queued'],'errors':r['deployed_errors']} for r in results]},indent=2))
