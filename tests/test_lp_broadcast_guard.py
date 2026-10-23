"""Broadcast-once guard regression tests (the ops #222/#223 double-broadcast).

Hermetic (request_send is faked; construction is network-free). Pins:

  1. broadcast_once_hive() forces num_retries=0 / num_retries_call=0.
  2. It REFUSES to construct a broadcast instance with auto-retry re-enabled.
  3. Transport-class failures (the 2026-10-17 ops #222/#223 class) raise
     after EXACTLY ONE send under the zero budgets.
  4. Empty-reply failures are UNBOUNDED in nectar 1.0.7 even at zero budgets
     (the pool's mark-failed/rotate resets the counter the call-budget check
     reads — "(0/0)" forever). The send-once wrapper bounds that class to
     exactly one real send by refusing the identical re-send.
  5. Control: without the wrapper, the library re-sends an empty-replying
     payload unboundedly — pinned so a nectar upgrade that fixes the
     interplay (or breaks the wrapper) fails HERE loudly.
  6. The wrapper passes distinct payloads (legitimate sequential calls) and
     refuses identical bytes (auto-retry), and binds idempotently.
"""
import unittest
from unittest import mock

import httpx2

import lp_broadcast_guard as guard
from nectarapi.exceptions import NumRetriesReached
from nectarapi.graphenerpc import GrapheneRPC

CAP = 12  # escape hatch for unbounded library loops


class DeadNode:
    """A real GrapheneRPC against a dead URL whose request_send is faked.

    ``real_sends`` counts payloads that actually reached the fake transport
    (through the wrapper, if bound). The cap converts an unbounded library
    loop into a TimeoutException escape, which the zero node-budget turns
    into NumRetriesReached.
    """

    def __init__(self, num_retries, num_retries_call, mode, cap=CAP):
        self.rpc = GrapheneRPC("http://127.0.0.1:1/",
                               num_retries=num_retries,
                               num_retries_call=num_retries_call)
        self.real_sends = 0
        self.cap = cap

        def fake(payload, *a, **kw):
            self.real_sends += 1
            if self.real_sends >= self.cap:
                raise httpx2.TimeoutException("send-cap escape")
            if mode == "timeout":
                raise httpx2.TimeoutException("simulated timeout after accept")
            resp = mock.Mock()
            resp.text = ""          # empty-reply failure class
            return resp

        self.rpc.request_send = fake

    def rpcexec(self):
        return self.rpc.rpcexec({"jsonrpc": "2.0", "method": "x", "id": 1})


class TestBroadcastOnceHelper(unittest.TestCase):
    def test_forces_zero_budgets_through_to_hive(self):
        import nectar
        captured = {}

        class FakeHive:
            def __init__(self, **kw):
                captured.update(kw)

        with mock.patch.object(nectar, "Hive", FakeHive):
            guard.broadcast_once_hive(node=["http://x"], keys={"a": "b"})
        self.assertEqual(captured["num_retries"], 0)
        self.assertEqual(captured["num_retries_call"], 0)
        self.assertEqual(captured["node"], ["http://x"])
        self.assertEqual(captured["keys"], {"a": "b"})

    def test_refuses_auto_retry_kwargs(self):
        for bad in ({"num_retries": 5}, {"num_retries_call": 1}):
            with self.assertRaisesRegex(ValueError, "double-broadcast"):
                guard.broadcast_once_hive(node=["http://x"], **bad)

    def test_is_broadcast_once_true_only_with_both_layers(self):
        rpc = GrapheneRPC("http://127.0.0.1:1/",
                          num_retries=0, num_retries_call=0)
        holder = mock.Mock(spec=["rpc"])
        holder.rpc = rpc
        self.assertFalse(guard.is_broadcast_once(holder),
                         "budgets alone are not broadcast-once: the "
                         "empty-reply class is unbounded without the wrapper")
        guard.bind_send_once(rpc)
        self.assertTrue(guard.is_broadcast_once(holder))

    def test_is_broadcast_once_never_raises(self):
        self.assertFalse(guard.is_broadcast_once(object()))
        self.assertFalse(guard.is_broadcast_once(None))


class TestTransportClass(unittest.TestCase):
    """The ops #222/#223 class: node accepted, then the connection failed."""

    def test_sends_exactly_once_with_wrapper(self):
        node = DeadNode(0, 0, "timeout")
        guard.bind_send_once(node.rpc)
        with self.assertRaises(NumRetriesReached):
            node.rpcexec()
        self.assertEqual(node.real_sends, 1)

    def test_sends_exactly_once_from_budgets_alone(self):
        # The kwargs layer already bounds this class; the wrapper adds the
        # empty-reply class on top.
        node = DeadNode(0, 0, "timeout")
        with self.assertRaises(NumRetriesReached):
            node.rpcexec()
        self.assertEqual(node.real_sends, 1)


class TestEmptyReplyClass(unittest.TestCase):
    """The class the retry budgets CANNOT bound (nectar 1.0.7 counter-reset
    interplay) — the wrapper is the only bound."""

    def test_wrapper_bounds_to_exactly_one_real_send(self):
        node = DeadNode(0, 0, "empty")
        guard.bind_send_once(node.rpc)
        with self.assertRaises(NumRetriesReached):
            node.rpcexec()
        self.assertEqual(node.real_sends, 1,
                         "the empty-reply retry loop re-sent an already-"
                         "broadcast payload — the send-once wrapper is broken")

    def test_control_library_resends_unboundedly_without_wrapper(self):
        node = DeadNode(0, 0, "empty", cap=CAP)
        with self.assertRaises(NumRetriesReached):
            node.rpcexec()
        self.assertGreaterEqual(
            node.real_sends, CAP,
            "nectar's empty-reply loop terminated below the cap without the "
            "wrapper — the upstream counter-reset interplay may have been "
            "fixed. Re-verify guard.py's send-count matrix before trusting "
            "either layer.")


class TestSendOnceWrapper(unittest.TestCase):
    def _bound(self):
        rpc = GrapheneRPC("http://127.0.0.1:1/",
                         num_retries=0, num_retries_call=0)
        seen = []
        rpc.request_send = lambda payload, *a, **kw: seen.append(payload) or None
        assert guard.bind_send_once(rpc)
        return rpc, seen

    def test_identical_resend_refused(self):
        rpc, _ = self._bound()
        rpc.request_send(b"payload-1")
        with self.assertRaises(guard.BroadcastResendRefused):
            rpc.request_send(b"payload-1")

    def test_distinct_payloads_pass(self):
        rpc, seen = self._bound()
        rpc.request_send(b"payload-1")
        rpc.request_send(b"payload-2")
        self.assertEqual(seen, [b"payload-1", b"payload-2"])

    def test_binding_is_idempotent(self):
        rpc, _ = self._bound()
        self.assertTrue(guard.bind_send_once(rpc))
        rpc.request_send(b"a")
        rpc.request_send(b"b")     # must still pass: single wrapper layer
        self.assertEqual(guard.bind_send_once(rpc), True)

    def test_unrecognized_shape_returns_false(self):
        self.assertFalse(guard.bind_send_once(None))
        self.assertFalse(guard.bind_send_once(object()))


if __name__ == "__main__":
    unittest.main()
