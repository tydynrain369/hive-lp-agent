#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""
Cron job for autonomous LP management.
Runs every 6 hours to rebalance and compound.
"""
import sys
sys.path.insert(0, '/home/tydynrain/hive-agent-test')

from liquidity_agent_v2 import LiquidityAgent

def main():
    print("=== HERMES LP AGENT CRON RUN ===")
    agent = LiquidityAgent()
    agent.pe.load_all()
    agent.portfolio.load_balances()
    agent.portfolio.load_positions()
    
    total = agent.portfolio.total_value()
    idle = agent.portfolio.idle_value()
    print(f"Portfolio: ${total:.4f} (idle: ${idle:.4f})")
    
    # Rank pools
    ranked = agent.analyzer.rank_pools(min_liquidity=500)
    print(f"Top opportunities: {len(ranked)} pools ranked")
    for i, r in enumerate(ranked[:3]):
        print(f"  {i+1}. {r['pool']} Score: {r['total_score']:.3f} APR: {r['est_apr']:.1f}% Liq: ${r['liquidity_usd']:,.0f} CanEnter: {r['can_enter']}")
    
    # Execute strategy
    if idle > 0.001:
        agent.strategy._execute_strategy(ranked[:5])
        print("Strategy executed")
    else:
        print("No idle capital to deploy")
    
    # Re-check
    agent.portfolio.load_balances()
    agent.portfolio.load_positions()
    new_total = agent.portfolio.total_value()
    print(f"Post-run portfolio: ${new_total:.4f} (Δ: ${new_total-total:+.4f})")

if __name__ == "__main__":
    main()