"""Tests for the general-intermediate router.

The reference chain supplied by Tydyn — HSBIDAO -> LBI -> SWAP.HIVE — is the
case find_path() got wrong, so it is the anchor fixture here.

Run:
  /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest \
      tests.test_lp_router -v
"""
import sys
import unittest

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
from lp_router import build_adjacency, find_general_path, explain_routability


class TestAdjacency(unittest.TestCase):

    def test_builds_symmetric_edges(self):
        adj = build_adjacency(["CENT:POB"])
        self.assertEqual(adj["CENT"], ["POB"])
        self.assertEqual(adj["POB"], ["CENT"])

    def test_accepts_pool_id_mapping(self):
        adj = build_adjacency({"CENT:POB": None, "HSBIDAO:LBI": None})
        self.assertEqual(adj["HSBIDAO"], ["LBI"])
        self.assertIn("POB", adj["CENT"])

    def test_ignores_malformed_ids(self):
        adj = build_adjacency(["NO_COLON", "A:B", ":B", "A:"])
        self.assertEqual(sorted(adj.keys()), ["A", "B"])

    def test_self_pair_is_not_an_edge(self):
        adj = build_adjacency(["X:X"])
        self.assertEqual(adj, {})


class TestFindGeneralPath(unittest.TestCase):
    """TYDDYN'S CHAIN: HSBIDAO -> LBI -> SWAP.HIVE. find_path() says NONE."""

    GRAPH = ["HSBIDAO:LBI", "LBI:SWAP.HIVE", "HSBIDAO:PIXYDUST",
             "CENT:POB", "POB:SWAP.HIVE", "HSBIDAO:THREE", "SWAP.HIVE:CENT"]

    def setUp(self):
        self.adj = build_adjacency(self.GRAPH)

    def test_the_three_hop_chain_is_found(self):
        path = find_general_path(self.adj, "HSBIDAO", "SWAP.HIVE", max_hops=3)
        self.assertEqual(path, ["HSBIDAO", "LBI", "SWAP.HIVE"])

    def test_direct_pair_is_one_hop(self):
        self.assertEqual(find_general_path(self.adj, "CENT", "POB"), ["CENT", "POB"])

    def test_two_hop_chain(self):
        self.assertEqual(find_general_path(self.adj, "POB", "SWAP.HIVE"),
                         ["POB", "SWAP.HIVE"])

    def test_pixydust_reaches_hsbidao(self):
        """Tydyn's other correction: PIXYDUST -> HSBIDAO is a real route."""
        self.assertEqual(find_general_path(self.adj, "PIXYDUST", "HSBIDAO"),
                         ["PIXYDUST", "HSBIDAO"])

    def test_pixydust_reaches_hub_through_hsbidao(self):
        self.assertEqual(find_general_path(self.adj, "PIXYDUST", "SWAP.HIVE", max_hops=3),
                         ["PIXYDUST", "HSBIDAO", "LBI", "SWAP.HIVE"])

    def test_same_token_has_no_path(self):
        self.assertEqual(find_general_path(self.adj, "CENT", "CENT"), [])

    def test_unknown_token_has_no_path(self):
        self.assertEqual(find_general_path(self.adj, "NOPE", "CENT"), [])

    def test_disconnected_component_has_no_path(self):
        self.assertEqual(find_general_path(self.adj, "HSBIDAO", "CENT", max_hops=2), [])

    def test_hop_bound_is_respected(self):
        """With max_hops=1 the 3-hop chain must NOT be found."""
        self.assertEqual(find_general_path(self.adj, "HSBIDAO", "SWAP.HIVE", max_hops=1), [])

    def test_no_infinite_loop_on_cycles(self):
        """A cycle must terminate rather than spin."""
        cyc = build_adjacency(["A:B", "B:C", "C:A", "C:SWAP.HIVE"])
        path = find_general_path(cyc, "A", "SWAP.HIVE", max_hops=6)
        self.assertTrue(path)
        self.assertEqual(path[0], "A")
        self.assertEqual(path[-1], "SWAP.HIVE")

    def test_path_is_contiguous(self):
        """Every consecutive pair on the path must share a pool."""
        path = find_general_path(self.adj, "PIXYDUST", "SWAP.HIVE", max_hops=3)
        for a, b in zip(path, path[1:]):
            self.assertIn(b, self.adj[a],
                          f"{a}->{b} on path {path} is not an edge")


class TestExplain(unittest.TestCase):

    def test_reports_hop_counts(self):
        adj = build_adjacency(["HSBIDAO:LBI", "LBI:SWAP.HIVE"])
        out = explain_routability(adj, ["HSBIDAO", "ORPHAN"], "SWAP.HIVE", max_hops=3)
        self.assertTrue(out["HSBIDAO"]["routable"])
        self.assertEqual(out["HSBIDAO"]["hops"], 2)
        self.assertFalse(out["ORPHAN"]["routable"])
        self.assertIsNone(out["ORPHAN"]["hops"])


if __name__ == "__main__":
    unittest.main(verbosity=2)