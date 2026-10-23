"""BROADCAST-ONCE semantics for nectar Hive instances (the ops #222/#223 guard).

Root cause (2026-10-17 live lesson): nectar's node pool auto-retries failed
RPC calls and auto-switches nodes — by design (thanks, TheCrazyGM), and
exactly right for READS. For BROADCASTS it is a hazard: a failure AFTER the
node already accepted the transaction re-sends the SAME signed broadcast —
the double-broadcast that spent both deploy leg budgets into CENT as
sidechain ops #222/#223.

Empirical send-count matrix (nectar 1.0.7, hermetic probe, 2026-10-22):

  budgets (num_retries, num_retries_call)   timeout-class   empty-reply-class
  (0, 0)                                      1 send        UNBOUNDED re-send
  (0, 5)                                      1 send        UNBOUNDED re-send
  (2, 5)                                      3 sends      UNBOUNDED re-send
  (100, 5) library default                  101 sends      UNBOUNDED re-send

Two failure classes, two different mechanics, hence TWO guard layers:

1. KWARGS LAYER — ``num_retries=0`` (and ``num_retries_call=0`` for
   tidiness). Transport-class failures (timeout, 5xx, connection reset —
   the ops #222/#223 class) are bounded by the per-NODE budget in
   nectarapi/node.py sleep_and_check_retries; zeroing it makes the first
   failure raise NumRetriesReached after exactly one send. Verified
   end-to-end through the real Hive constructor.

2. WRAPPER LAYER — ``bind_send_once(rpc)``. The per-CALL budget
   (num_retries_call) is INERT for the empty-reply class in this nectar
   version: nectarapi/pool.py's mark_node_failed/rotate interplay resets the
   very counter the budget check reads ("Retry RPC Call on node ... (0/0)"),
   so an empty-reply storm re-sends the payload indefinitely regardless of
   the budget. The wrapper counts payloads at the request_send seam: a
   re-send of the SAME payload raises BroadcastResendRefused, which the
   retry machinery treats as a transport error — and with the node budget
   at 0 the call then exits after exactly one real send. Within-call
   retries carry identical payload bytes (the request id is allocated once
   per method call in GrapheneRPC.__getattr__), while legitimate sequential
   calls always differ, so identical bytes = auto-retry, reliably.

The existing per-step pattern — broadcast -> settle -> INDEPENDENT
fresh-RPC verify — then decides what actually happened before any new
action. A failed tick is retried whole, with fresh pre-state, by the next
scheduler tick. At the $0.05/action cap the cost of a failed tick is
negligible; a double-spend is not.

Use ``broadcast_once_hive()`` ONLY on instances whose traffic is
broadcasts. Read-heavy instances (load_positions, dashboards, feed
queries) keep the library's failover untouched.
"""


class BroadcastResendRefused(Exception):
    """Raised by the send-once wrapper when nectar's retry machinery tries to
    re-send a payload that was already sent once in this call."""


_WRAPPER_TAG = "_broadcast_once_bound"


def bind_send_once(rpc) -> bool:
    """Wrap ``rpc.request_send`` so the SAME payload can never be sent twice.

    Idempotent. Returns True when a wrapper was installed (or was already
    present), False when the object shape is not recognized.

    NOTE: the bound-tag MUST be read from the instance ``__dict__``.
    GrapheneRPC.__getattr__ maps ANY unknown attribute name to a generated
    RPC-method closure, so ``getattr(rpc, tag, False)`` is always truthy
    there and would make this function silently never bind.
    """
    if rpc is None or not hasattr(rpc, "request_send"):
        return False
    if _WRAPPER_TAG in getattr(rpc, "__dict__", {}):
        return True

    original = rpc.request_send
    last = {"payload": None}

    def _send_once(payload, *args, **kwargs):
        if last["payload"] is not None and payload == last["payload"]:
            raise BroadcastResendRefused(
                "auto-retry re-send of an already-broadcast payload refused "
                "(ops #222/#223 guard); the verify layer decides what happened")
        last["payload"] = payload
        return original(payload, *args, **kwargs)

    rpc.request_send = _send_once
    try:
        rpc.__dict__[_WRAPPER_TAG] = True
    except Exception:  # noqa: BLE001 - wrapper still active either way
        pass
    return True


def broadcast_once_hive(**hive_kwargs):
    """Construct a nectar Hive instance with BROADCAST-ONCE retry semantics.

    All normal Hive() arguments (node, keys, nobroadcast, ...) pass through.
    Applies both guard layers: zero retry budgets (kwargs) and the
    send-once wrapper (hive.rpc is constructed eagerly by nectar when not
    offline). Explicit non-zero retry budgets are refused — a broadcast
    instance with auto-retry re-enabled is the exact defect this module
    exists to prevent.
    """
    from nectar import Hive

    for key in ("num_retries", "num_retries_call"):
        val = hive_kwargs.setdefault(key, 0)
        if val != 0:
            raise ValueError(
                f"broadcast_once_hive: {key}={val!r} would re-enable auto-retry "
                f"on a broadcast instance — the ops #222/#223 double-broadcast "
                f"guard requires 0"
            )
    hive = Hive(**hive_kwargs)
    bind_send_once(getattr(hive, "rpc", None))
    return hive


def is_broadcast_once(hive_instance) -> bool:
    """True when the instance carries BOTH guard layers. False when the shape
    is unrecognized — never raises."""
    try:
        rpc = getattr(hive_instance, "rpc", None)
        nodes = getattr(rpc, "nodes", None)
        if nodes is None:
            return False
        budgets_ok = (getattr(nodes, "num_retries", None) == 0
                     and getattr(nodes, "num_retries_call", None) == 0)
        # Instance __dict__, NOT getattr: GrapheneRPC.__getattr__ fabricates a
        # truthy RPC-method closure for any unknown attribute name.
        wrapper_ok = _WRAPPER_TAG in getattr(rpc, "__dict__", {})
        return budgets_ok and wrapper_ok
    except Exception:  # noqa: BLE001 - a check must never crash a deploy
        return False
