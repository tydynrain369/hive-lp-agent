import os

os.environ['LP_TEST_LIVE'] = '1'
os.environ['LP_TEST_MAX_USD'] = '0.05'
os.environ['HERMES_LIVE_TEST'] = 'bbh_remove_once'

from liquidity_agent_v2 import LiquidityAgent
from lp_decision import decide
from lp_executor_test import execute_from_decision
from cron_lp_agent import discover_rewards

agent = LiquidityAgent()
agent.pe.load_all()
agent.portfolio.load_balances()
agent.portfolio.load_positions()

reward_discovery = discover_rewards(agent.pe, agent.portfolio)
decision = decide(agent.pe, agent.portfolio, reward_discovery)
actions = execute_from_decision(agent, decision)
print(actions)
