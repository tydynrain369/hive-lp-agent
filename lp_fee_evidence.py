"""Read-only, account/pool-attributed LP fee evidence collector.

Attribution model: the deployed marketpools contract (hash prefix
fd6d750418df...) folds swap fees into pool reserves (constant-product growth);
there is NO per-account fee ledger. Per-account realized fees are therefore
only measurable LONGITUDINALLY: account share x reserve growth between two
snapshots, and only while the share base (pool totalShares and the account's
position shares) is constant. Nothing is fabricated: unknowns are null +
explicit status. Pure integer/Fraction arithmetic; no Decimal, no floats.
"""
import json
import os
from pathlib import Path
from fractions import Fraction

# Known-good deployed marketpools code hashes (verified 2026-09-17).
KNOWN_CONTRACT_HASHES = {
    'fd6d750418df6b23de96fa0ba8cb006828dcbc272d566c6847204b552a41004c':
        'marketpools with tradeFeeMul in swap outputs; verified 2026-09-17',
}
KNOWN_GOOD_PREFIXES = ('fd6d750418df',)


def _parse_amount(s):
    """Exact parse of a non-negative decimal token amount string to Fraction."""
    try:
        f = Fraction(str(s))
    except (ValueError, ZeroDivisionError) as e:
        raise ValueError(f'invalid amount {s!r}: {e}')
    if f < 0:
        raise ValueError(f'negative amount {s!r}')
    return f


def _fmt_amount(fr, precision):
    """Fraction -> exact decimal string at token precision (truncated toward
    zero like contract ROUND_DOWN on magnitudes). Integer-string math only."""
    if not isinstance(precision, int) or isinstance(precision, bool) or not 0 <= precision <= 8:
        raise ValueError('invalid precision')
    scale = 10 ** precision
    neg = fr < 0
    n, d = abs(fr.numerator), fr.denominator
    scaled = n * scale
    q, r = divmod(scaled, d)
    ip, fp = divmod(q, scale)
    if precision == 0:
        out = str(ip)
    else:
        frac = str(fp).rjust(precision, '0').rstrip('0')
        out = str(ip) if not frac else f'{ip}.{frac}'
    return ('-' + out) if neg and q else out


def _fmt_reserve(fr):
    """Reserve values to plain exact decimal string (Fraction, no exponent)."""
    n, d = fr.numerator, fr.denominator
    q, r = divmod(abs(n), d)
    if r == 0:
        return str(n // d)
    digits = []
    while r:
        r *= 10
        dig, r = divmod(r, d)
        digits.append(str(dig))
    s = f'{q}.{"".join(digits)}'.rstrip('0').rstrip('.')
    return ('-' + s) if n < 0 else s


def _hash_status(contract_hash):
    # 2026-11-15 (Fable round 6): a MISSING hash is not a verified one. The
    # old None -> 'ok' was fail-open: a findOne returning null (community
    # mirror, or {}) made code_hash None and snapshots proceeded UNVERIFIED
    # against an unknown contract. Unknown = unverified = fail closed.
    if contract_hash is None:
        return 'unverified-no-codehash'
    if contract_hash in KNOWN_CONTRACT_HASHES:
        return 'ok'
    if any(str(contract_hash).startswith(p) for p in KNOWN_GOOD_PREFIXES):
        return 'ok'
    return 'contract_unverified'


def snapshot_fee_state(rpc, account, pair, at_ms):
    """Record one storable snapshot. rpc(contract, table, query) -> list."""
    base = {'pool': pair, 'account': account, 'at_ms': at_ms,
            'measurement_status': 'snapshot_recorded'}
    try:
        pools = rpc('marketpools', 'pools', {'tokenPair': pair})
    except Exception as e:
        return {**base, 'status': 'unavailable', 'reason': f'rpc failure: {e}',
                'share': None}
    if not pools:
        return {**base, 'status': 'unavailable',
                'reason': f'pool {pair} not found', 'share': None}
    p = pools[0]
    try:
        base_sym, quote_sym = p['tokenPair'].split(':')
        precision = int(p['precision'])
        reserves = {base_sym: _parse_amount(p['baseQuantity']),
                    quote_sym: _parse_amount(p['quoteQuantity'])}
        total_shares = _parse_amount(p['totalShares'])
    except (KeyError, ValueError) as e:
        return {**base, 'status': 'unavailable',
                'reason': f'malformed pool record: {e}', 'share': None}
    try:
        positions = rpc('marketpools', 'liquidityPositions', {'account': account})
    except Exception as e:
        return {**base, 'status': 'unavailable', 'reason': f'rpc failure: {e}',
                'share': None}
    pos = None
    for cand in positions:
        if cand.get('tokenPair') in (None, pair):
            pos = cand
            break
    share = None
    reason = None
    if pos is None:
        reason = f'no liquidity position for {account} in {pair}; share unknown'
        pos_shares = None
    else:
        pos_shares = _parse_amount(pos['shares'])
        share = Fraction(0) if total_shares == 0 else pos_shares / total_shares
    out = {**base, 'status': 'ok',
           'tokenPair': p['tokenPair'], 'precision': precision,
           'total_shares': str(total_shares) if total_shares.denominator != 1
           else str(total_shares.numerator),
           'position_shares': (str(pos_shares) if pos_shares is not None else None),
           'share': ({'num': str(share.numerator), 'den': str(share.denominator)}
                     if share is not None else None),
           'reserves': {s: _fmt_reserve(r) for s, r in reserves.items()},
           'volumes': {'baseVolume': str(p.get('baseVolume', '0')),
                       'quoteVolume': str(p.get('quoteVolume', '0'))}}
    if reason:
        out['reason'] = reason
    return out


def diff_fee_snapshots(before, after):
    """Attribute per-token reserve growth to the account share. Fails closed:
    ANY change in pool totalShares or account position shares makes fee income
    not isolatable (mint/burn events move reserves too)."""
    out = {'pool': after.get('pool') or before.get('pool'),
           'account': after.get('account') or before.get('account'),
           'window': {'from_ms': before.get('at_ms'), 'to_ms': after.get('at_ms')}}
    if before.get('status') != 'ok' or after.get('status') != 'ok':
        return {**out, 'status': 'unmeasured',
                'reason': 'snapshot unavailable; cannot diff',
                'attributable_fees': None}
    if (before['total_shares'] != after['total_shares'] or
            before['position_shares'] != after['position_shares']):
        return {**out, 'share_before': before['share'], 'share_after': after['share'],
                'status': 'unmeasured',
                'reason': 'share base changed; fee growth not isolatable',
                'attributable_fees': None}
    if before['share'] is None:
        return {**out, 'status': 'unmeasured',
                'reason': 'no account position; nothing attributable',
                'attributable_fees': None}
    share = Fraction(int(before['share']['num']), int(before['share']['den']))
    precision = after['precision']
    # 2026-09-30 CORRECTION — this was NOT measuring fees.
    # It took the growth in each side's RESERVE and called it attributable
    # fees. In a constant-product pool reserves move from trades, arbitrage
    # and price change as well as from fees, so reserve delta is a NET
    # reserve movement, not fee income. The proof was in the output: it
    # reported NEGATIVE fees (CENT:POB our CENT = -1.344331, ALIVE:HEARTBEAT
    # our ALIVE = -0.007382). A fee can never be negative; the negative leg
    # was the swap/price movement netting against the positive leg.
    #
    # Fee income is only isolated from VOLUME: volume traded x fee rate, and
    # that is the same basis measure_fee_apr() already uses correctly
    # (dVol * FEE_RATE * our_share). So rather than invent a second, wrong
    # estimator, report the reserve movement honestly as what it is, and
    # leave fee attribution to the volume-based path.
    volumes_before = before.get('volumes') or {}
    volumes_after = after.get('volumes') or {}
    reserve_movement = {}
    for sym, after_amt in after['reserves'].items():
        growth = Fraction(after_amt) - Fraction(before['reserves'][sym])
        reserve_movement[sym] = {
            'net_reserve_delta': _fmt_amount(growth, precision),
            'our_share_of_delta': _fmt_amount(growth * share, precision),
        }
    fee_basis = 'volume x fee_rate (see measure_fee_apr); reserve deltas are NOT fees'
    return {**out, 'status': 'measured', 'share': before['share'],
            'total_shares': after['total_shares'],
            'reserves_before': before['reserves'],
            'reserves_after': after['reserves'],
            'volumes_before': volumes_before,
            'volumes_after': volumes_after,
            'reserve_movement': reserve_movement,
            'attributable_fees': None,
            'fee_basis': fee_basis,
            'note': ('reserve delta is net of trades, arbitrage and price '
                     'movement; it cannot be used as fee income. Use the '
                     'volume-based measurement instead.')}


DEFAULT_SNAPSHOT_STORE = os.path.expanduser('~/.hermes/lp_tests/fee_snapshots.jsonl')


# Retention policy for the append-only snapshot store.
#
# The reader only ever consults the MOST RECENT prior row for a (pool, account)
# pair, so keeping the last N rows per pair preserves diff semantics exactly.
# A flat time-based cut does NOT: it can drop the newest prior row for a pair
# and silently downgrade fee attribution from 'measured' to 'snapshot_recorded'
# (unmeasured), with no error anywhere. Rotate per-pair, never by timestamp.
SNAPSHOT_RETAIN_PER_PAIR = int(os.environ.get('LP_SNAPSHOT_RETAIN_PER_PAIR', '720'))
# Rotate only when the store grows past this; ordinary hourly ticks never trip it.
SNAPSHOT_ROTATE_BYTES = int(os.environ.get('LP_SNAPSHOT_ROTATE_BYTES',
                                           str(8 * 1024 * 1024)))


def _read_snapshot_rows(store):
    """ONE pass over the store -> ({(pool, account): [row, ...]}, unparsable).

    Rows keep append order within a key. A corrupt line is counted and skipped:
    never parsed as truth, never allowed to abort the read.
    """
    rows_by_key = {}
    unparsable = 0
    if not store.exists():
        return rows_by_key, unparsable
    for line in store.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            unparsable += 1
            continue
        key = (row.get('pool'), (row.get('snapshot') or {}).get('account'))
        rows_by_key.setdefault(key, []).append(row)
    return rows_by_key, unparsable


def _rotate_in_place(store, rows_by_key, keep, dry_run=False):
    """Keep the last `keep` rows per key; atomically replace the store."""
    kept, dropped = [], 0
    for key in sorted(rows_by_key, key=lambda k: (str(k[0]), str(k[1]))):
        rows = rows_by_key[key]
        if keep > 0:
            kept.extend(rows[-keep:])
            dropped += max(0, len(rows) - keep)
        else:
            dropped += len(rows)
    before_bytes = store.stat().st_size if store.exists() else 0
    payload = ''.join(json.dumps(r) + '\n' for r in kept)
    report = {'store': str(store), 'keep_per_pair': keep,
              'rows_before': sum(len(v) for v in rows_by_key.values()),
              'rows_after': len(kept), 'rows_dropped': dropped,
              'pairs': len(rows_by_key),
              'bytes_before': before_bytes, 'bytes_after': len(payload.encode()),
              'dry_run': bool(dry_run)}
    if dry_run or dropped == 0:
        return report
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_name(store.name + '.rotating')
    tmp.write_text(payload)
    os.replace(tmp, store)   # atomic: a reader never observes a partial store
    return report


def rotate_fee_snapshots(*, store_path=None, keep_per_pair=None, dry_run=False):
    """Manual / dry-run entry point. Default keeps SNAPSHOT_RETAIN_PER_PAIR/pair."""
    store = Path(store_path or DEFAULT_SNAPSHOT_STORE)
    rows_by_key, unparsable = _read_snapshot_rows(store)
    rep = _rotate_in_place(store, rows_by_key,
                           SNAPSHOT_RETAIN_PER_PAIR if keep_per_pair is None
                           else keep_per_pair, dry_run=dry_run)
    rep['unparsable_skipped'] = unparsable
    return rep


def record_fee_snapshots(rpc, account, positions, *, store_path=None, at_ms,
                         contract_hash=None):
    """Snapshot each held pool, diff vs the most recent prior snapshot.

    positions: iterable of pool ids or mapping of pool ids. Never raises on
    RPC failure (fail-closed 'unavailable'). contract_hash is verified against
    the known-good deployed set; unrecognized hash skips snapshots entirely.
    Returns {'results': {pool: {...}}} — JSON-serializable, exact amounts.

    2026-11-05: the prior lookup used to re-read the ENTIRE store once PER PAIR
    (O(pairs x rows)). At 24k rows x every held pool that — not disk — is the
    real cost of this file. One read per call now builds every pair's prior, and
    the same read feeds a threshold-gated per-pair rotation.
    """
    from lp_fee_evidence import (snapshot_fee_state, diff_fee_snapshots,
                                 KNOWN_GOOD_PREFIXES, KNOWN_CONTRACT_HASHES)
    store = Path(store_path or DEFAULT_SNAPSHOT_STORE)
    pairs = sorted(positions) if positions else []
    # 2026-11-15 (Fable round 6): same fail-closed rule as _hash_status —
    # a None hash (findOne null -> {}) is UNVERIFIED, never silently ok.
    hs = ('ok' if (contract_hash is not None
                   and (contract_hash in KNOWN_CONTRACT_HASHES
                        or any(str(contract_hash).startswith(p)
                               for p in KNOWN_GOOD_PREFIXES)))
          else ('unverified-no-codehash' if contract_hash is None
                else 'contract_unverified'))
    # Single read for the whole call: builds every pair's prior snapshot, and is
    # the input to threshold rotation below.
    rows_by_key, unparsable = _read_snapshot_rows(store)
    priors = {k: v[-1].get('snapshot') for k, v in rows_by_key.items()
              if v and v[-1].get('snapshot')}
    rotation = None
    try:
        if store.exists() and store.stat().st_size > SNAPSHOT_ROTATE_BYTES:
            rotation = _rotate_in_place(store, rows_by_key,
                                        SNAPSHOT_RETAIN_PER_PAIR)
    except Exception as e:   # maintenance must never break a live run
        rotation = {'error': f'{type(e).__name__}: {e}'}
    results = {}
    for pair in pairs:
        if hs != 'ok':
            results[pair] = {'status': hs, 'reason': 'unrecognized marketpools '
                             f'contract hash {contract_hash}; snapshots skipped'}
            continue
        snap = snapshot_fee_state(rpc, account, pair, at_ms)
        if snap.get('status') != 'ok':
            results[pair] = {'status': 'unavailable',
                             'reason': snap.get('reason', 'snapshot failed')}
            continue
        prior = priors.get((pair, account))
        store.parent.mkdir(parents=True, exist_ok=True)
        with open(store, 'a') as f:
            f.write(json.dumps({'pool': pair, 'account': account,
                                'at_ms': at_ms, 'snapshot': snap}) + '\n')
        entry = {'status': 'snapshot_recorded', 'snapshot': snap, 'diff': None}
        if prior is not None:
            entry['diff'] = diff_fee_snapshots(prior, snap)
            if entry['diff'].get('status') == 'measured':
                entry['status'] = 'measured'
        results[pair] = entry
    out = {'results': results}
    # Surface the maintenance facts so a caller (and the cron artifact) can see
    # that a rotation ran and that corrupt lines were skipped, rather than
    # inferring it from a change in file size.
    if unparsable:
        out['unparsable_lines_skipped'] = unparsable
    if rotation is not None:
        out['rotation'] = rotation
    return out


def collect_fee_evidence(rpc, account, pool_pairs, *, at_ms, contract_hash=None):
    """Per-pool evidence dict for account. rpc callable injected; tests use
    fakes, production wires api.hive-engine.com. Read-only: no transactions."""
    result = {}
    for pair in pool_pairs:
        entry = {'pool': pair, 'account': account, 'at_ms': at_ms}
        hs = _hash_status(contract_hash)
        if contract_hash is not None:
            entry['contract_hash'] = contract_hash
            entry['contract_hash_verified'] = hs == 'ok'
        if hs != 'ok':
            result[pair] = {**entry, 'status': hs, 'share': None,
                            'reason': f'contract hash {contract_hash} not in known-good set',
                            'attributable_fees': None}
            continue
        try:
            snap = snapshot_fee_state(rpc, account, pair, at_ms)
        except Exception as e:
            result[pair] = {**entry, 'status': 'unavailable',
                            'reason': f'snapshot error: {e}',
                            'share': None, 'attributable_fees': None}
            continue
        result[pair] = {**entry,
                        'status': snap['status'] if snap['status'] != 'ok' else 'ok',
                        'measurement_status': snap['measurement_status'],
                        'share': snap['share'],
                        'snapshot': snap,
                        'attributable_fees': None,
                        'reason': snap.get('reason',
                                           'single snapshot; diff with a later snapshot to attribute fees')}
    return result
