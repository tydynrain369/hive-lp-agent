"""General-intermediate swap routing over the Hive-Engine pool graph.

WHY THIS EXISTS
  PriceEngine.find_path() only tries an intermediate of HIVE or SWAP.HIVE:

      direct = get_pool(from, to)
      if direct: return [direct]
      for mid in ["HIVE", "SWAP.HIVE"]:
          if get_pool(from, mid) and get_pool(mid, to): return [...]

  Hive-Engine is layer 2 and SWAP.HIVE is the L2 representation of HIVE (the
  L1 HIVE token has no pool at all), so those two are the hub. But a community
  token can reach the hub through its own chain — verified example supplied by
  Tydyn: HSBIDAO -> LBI -> SWAP.HIVE. find_path reports NONE for that, which is
  wrong and would cause the route guard to refuse performable reallocations.

  This searches the pool graph for ANY intermediate, bounded by hop count and
  visited set. It is read-only: it plans routes, it does not execute swaps.

NOTES
  - get_pool() already tries both orientations, so the adjacency built here is
    order-independent. An earlier note claiming it was order-sensitive was
    wrong; do not reintroduce that.
  - Returns [] when no route exists within the hop bound. That still means
    "no route within N hops", not "no route exists" — say which bound was used.
"""
from collections import deque
from typing import Dict, List, Optional


def build_adjacency(pools) -> Dict[str, List[str]]:
    """token -> set of tokens it has a pool with.

    `pools` may be the PriceEngine.pools mapping (pool_id -> PoolData) or any
    iterable of "BASE:QUOTE" strings. PoolData objects are accepted too by
    reading their .base/.quote attributes.
    """
    adj: Dict[str, set] = {}

    def add(a: str, b: str):
        if a and b and a != b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)

    if hasattr(pools, "items"):
        items = pools.items()
    else:
        items = ((p, None) for p in pools)

    for key, val in items:
        base = quote = None
        if isinstance(key, str) and ":" in key:
            base, quote = key.split(":", 1)
        if base is None and val is not None:
            base = getattr(val, "base", None)
            quote = getattr(val, "quote", None)
        if base and quote:
            add(str(base), str(quote))
    return {k: sorted(v) for k, v in adj.items()}


def find_general_path(adjacency: Dict[str, List[str]], from_sym: str,
                      to_sym: str, max_hops: int = 3) -> List[str]:
    """Breadth-first search over the pool graph.

    Returns a list of symbols INCLUDING both endpoints, e.g.
        ['HSBIDAO', 'LBI', 'SWAP.HIVE']
    Returns [] when no route exists within max_hops.
    """
    if not from_sym or not to_sym:
        return []
    if from_sym == to_sym:
        return []
    if from_sym not in adjacency or to_sym not in adjacency:
        return []

    # BFS layer by layer, tracking the actual path taken.
    frontier = {from_sym: [from_sym]}
    visited = {from_sym}
    for _ in range(max_hops):
        nxt = {}
        for node, path in frontier.items():
            for nb in adjacency.get(node, ()):
                if nb in visited:
                    continue
                candidate = path + [nb]
                if nb == to_sym:
                    return candidate
                nxt[nb] = candidate
        if not nxt:
            break
        visited.update(nxt)
        frontier = nxt
    return []


def reachable_from(adjacency, token: str, target: str, max_hops: int = 3):
    """Convenience: bool plus the path, for the route guard."""
    p = find_general_path(adjacency, token, target, max_hops=max_hops)
    return (bool(p), p)


def explain_routability(adjacency, tokens, target: str, max_hops: int = 3):
    """Per-token route verdict, suitable for a decision artifact."""
    out = {}
    for t in tokens:
        ok, path = reachable_from(adjacency, t, target, max_hops=max_hops)
        out[t] = {
            "routable": ok,
            "path": path,
            "hops": (len(path) - 1) if path else None,
        }
    return out