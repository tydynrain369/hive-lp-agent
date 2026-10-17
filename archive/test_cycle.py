from liquidity_agent_v2 import LiquidityAgent
agent = LiquidityAgent()
agent.pe.load_all()
agent.portfolio.load_balances()
agent.portfolio.load_positions()

# Run one cycle
agent.strategy.run_cycle()