from liquidity_agent_v2 import LiquidityAgent
agent = LiquidityAgent()
agent.pe.load_all()
agent.portfolio.load_balances()
agent.portfolio.load_positions()

# Check the pool
pool = agent.pe.get_pool('SWAP.HIVE', 'BEE')
if pool:
    print(f'Pool: {pool.pool_id}')
    print(f'  base_reserve: {pool.base_reserve:.2f}')
    print(f'  quote_reserve: {pool.quote_reserve:.2f}')
    print(f'  pool_ratio (base/quote): {pool.base_reserve/pool.quote_reserve:.6f}')
    print()
    print(f'  APR: {pool.apr:.2f}%')
    print(f'  liquidity_usd: ${pool.liquidity_usd:,.0f}')

# Check balances
base_bal = agent.portfolio.balances.get('SWAP.HIVE', None)
quote_bal = agent.portfolio.balances.get('BEE', None)
if base_bal:
    print(f'\nSWAP.HIVE balance: {base_bal.balance:.6f} (value: ${base_bal.value_usd:.4f})')
if quote_bal:
    print(f'BEE balance: {quote_bal.balance:.6f} (value: ${quote_bal.value_usd:.4f})')

# Calculate what would be deployed
if base_bal and quote_bal and pool:
    pool_ratio = pool.base_reserve / pool.quote_reserve
    max_base_from_quote = quote_bal.balance * 0.5 * pool_ratio
    max_quote_from_base = base_bal.balance * 0.5 / pool_ratio
    base_deploy = min(base_bal.balance * 0.5, max_base_from_quote, pool.base_reserve * 0.01)
    quote_deploy = min(quote_bal.balance * 0.5, max_quote_from_base, pool.quote_reserve * 0.01)
    actual_ratio = base_deploy / quote_deploy if quote_deploy > 0 else 0
    print(f'\nCalculated deploy:')
    print(f'  base_deploy: {base_deploy:.6f}')
    print(f'  quote_deploy: {quote_deploy:.6f}')
    print(f'  actual_ratio: {actual_ratio:.6f} (target: {pool_ratio:.6f})')
    print(f'  Match: {abs(actual_ratio - pool_ratio) < 0.0001}')