import json
import socket
import threading
import time
import unittest
from pathlib import Path

import pool_discovery as discovery
from pool_discovery import (Candidate, candidates_from_config, coin_tokens, coverage_report,
    deduplicate, matches_coin, parse_candidate_lines, pick_diverse, split_host_port,
    summarize)

HERE = Path(__file__).resolve().parent


class FakeStratumPool:
    """A loopback Stratum server that answers mining.subscribe / mining.authorize.

    probe_once deliberately refuses non-public addresses, so tests inject a resolver
    that returns a loopback address and connect to this fixture instead.
    """

    def __init__(self, authorize_result=True, send_garbage=False):
        self.authorize_result = authorize_result
        self.send_garbage = send_garbage
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self.port = self._server.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self._server.settimeout(0.2)
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection):
        with connection:
            connection.settimeout(2.0)
            buffer = b""
            try:
                if self.send_garbage:
                    connection.sendall(b"this is not json at all\n")
                    return
                # Keep serving until the client goes away: closing right after the
                # subscribe reply would reset the connection before an authorize check.
                while True:
                    while b"\n" not in buffer:
                        chunk = connection.recv(65536)
                        if not chunk:
                            return
                        buffer += chunk
                    raw, buffer = buffer.split(b"\n", 1)
                    try:
                        request = json.loads(raw)
                    except ValueError:
                        continue
                    if request.get("method") == "mining.subscribe":
                        reply = {"id": request["id"], "result": [[["mining.set_difficulty", "s"]], "extranonce"],
                                 "error": None}
                    elif request.get("method") == "mining.authorize":
                        reply = {"id": request["id"], "result": self.authorize_result,
                                 "error": None if self.authorize_result else [24, "unauthorized", None]}
                    else:
                        reply = {"id": request.get("id"), "result": None, "error": [20, "unknown", None]}
                    connection.sendall((json.dumps(reply) + "\n").encode())
            except (OSError, ValueError):
                return

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._server.close()


def loopback_resolver(host, port):
    """Stands in for resolve_host: production rejects loopback, the fixture needs it."""
    return ["127.0.0.1"]


# probe_once connects to candidate.host, so the fixture candidate must use a loopback
# literal. The public-address guard is covered separately by its own test.
LOOPBACK_HOST = "127.0.0.1"


class PoolDiscoveryParsingTest(unittest.TestCase):
    def test_split_host_port_forms(self):
        self.assertEqual(split_host_port("pool.example.com", 3333), ("pool.example.com", 3333))
        self.assertEqual(split_host_port("pool.example.com:8080", 3333), ("pool.example.com", 8080))
        self.assertEqual(split_host_port("  POOL.Example.COM:99  ", 3333), ("pool.example.com", 99))
        self.assertEqual(split_host_port("[2001:db8::1]:4444", 3333), ("2001:db8::1", 4444))
        with self.assertRaises(ValueError):
            split_host_port("", 3333)
        with self.assertRaises(ValueError):
            split_host_port("pool.example.com:notaport", 3333)

    def test_parse_candidate_lines_skips_comments_and_reads_metadata(self):
        text = """
        # a comment
        newpool.example.com:3333 pool=NewPool region=亚洲

        other.example.com pool=Other coins=LTC/DOGE
        """
        candidates = parse_candidate_lines(text)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].host, "newpool.example.com")
        self.assertEqual(candidates[0].port, 3333)
        self.assertEqual(candidates[0].pool, "NewPool")
        self.assertEqual(candidates[0].region, "亚洲")
        self.assertEqual(candidates[1].port, 3333, "a bare host must fall back to the default port")

    def test_parse_candidate_lines_rejects_malformed_input(self):
        with self.assertRaises(ValueError):
            parse_candidate_lines("pool.example.com:3333 nonsense-without-equals")

    def test_coin_tokens_accepts_slash_comma_and_case(self):
        self.assertEqual(coin_tokens("LTC/DOGE"), {"LTC", "DOGE"})
        self.assertEqual(coin_tokens("ltc,doge"), {"LTC", "DOGE"})
        self.assertEqual(coin_tokens(""), set())

    def test_matches_coin_requires_a_declared_coin(self):
        declared = Candidate("P", "R", "a.example.com", 1, coins="LTC/DOGE")
        silent = Candidate("P", "R", "b.example.com", 1, coins="")
        self.assertTrue(matches_coin(declared, "ltc"))
        self.assertTrue(matches_coin(declared, "DOGE"))
        self.assertFalse(matches_coin(declared, "BTC"))
        self.assertFalse(matches_coin(silent, "LTC"),
            "an endpoint that declares no coins is unknown, not a match")

    def test_deduplicate_keeps_first_of_each_host_port(self):
        rows = [Candidate("A", "", "x.example.com", 1), Candidate("B", "", "x.example.com", 1),
                Candidate("C", "", "x.example.com", 2)]
        self.assertEqual([item.pool for item in deduplicate(rows)], ["A", "C"])

    def test_parse_candidate_lines_marks_the_source(self):
        candidates = parse_candidate_lines("a.example.com:1", source="newpools.txt")
        self.assertEqual(candidates[0].source, "newpools.txt")


class PoolDiscoveryRankingTest(unittest.TestCase):
    def make_candidate(self, pool, host, port=3333):
        return Candidate(pool, "R", host, port, coins="LTC")

    def test_summarize_reports_success_and_latency(self):
        candidate = self.make_candidate("P", "a.example.com")
        samples = {candidate: [(10.0, 20.0), (12.0, 24.0)]}
        attempts = {candidate.key: 3}
        row = summarize(samples, attempts)[0]
        self.assertTrue(row["tcp_ok"])
        self.assertTrue(row["stratum_ok"])
        self.assertEqual(row["success_percent"], 66.7)
        self.assertEqual(row["stratum_median_ms"], 22.0)
        self.assertIsNotNone(row["jitter_ms"])

    def test_summarize_marks_total_failure(self):
        candidate = self.make_candidate("P", "dead.example.com")
        row = summarize({candidate: []}, {candidate.key: 2})[0]
        self.assertFalse(row["tcp_ok"])
        self.assertFalse(row["stratum_ok"])
        self.assertEqual(row["success_percent"], 0.0)
        self.assertIsNone(row["stratum_p95_ms"])

    def test_pick_diverse_prefers_one_endpoint_per_pool(self):
        rows = [
            {"pool": "A", "host": "a1", "stratum_ok": True, "stratum_p95_ms": 1, "jitter_ms": 0, "tcp_p95_ms": 1},
            {"pool": "A", "host": "a2", "stratum_ok": True, "stratum_p95_ms": 2, "jitter_ms": 0, "tcp_p95_ms": 2},
            {"pool": "B", "host": "b1", "stratum_ok": True, "stratum_p95_ms": 3, "jitter_ms": 0, "tcp_p95_ms": 3},
            {"pool": "C", "host": "c1", "stratum_ok": True, "stratum_p95_ms": 4, "jitter_ms": 0, "tcp_p95_ms": 4},
        ]
        chosen = pick_diverse(rows)
        self.assertEqual([row["pool"] for row in chosen], ["A", "B", "C"],
            "regional hosts of one pool must not fill the slots ahead of other pools")

    def test_pick_diverse_tops_up_when_fewer_pools_than_slots(self):
        rows = [
            {"pool": "A", "host": "a1", "stratum_ok": True, "stratum_p95_ms": 1, "jitter_ms": 0, "tcp_p95_ms": 1},
            {"pool": "A", "host": "a2", "stratum_ok": True, "stratum_p95_ms": 2, "jitter_ms": 0, "tcp_p95_ms": 2},
        ]
        self.assertEqual(len(pick_diverse(rows)), 2)

    def test_pick_diverse_ignores_unreachable_endpoints(self):
        rows = [
            {"pool": "A", "host": "a1", "stratum_ok": False, "stratum_p95_ms": None, "jitter_ms": 0, "tcp_p95_ms": None},
            {"pool": "B", "host": "b1", "stratum_ok": True, "stratum_p95_ms": 5, "jitter_ms": 0, "tcp_p95_ms": 5},
        ]
        self.assertEqual([row["pool"] for row in pick_diverse(rows)], ["B"])


class PoolDiscoveryProbeTest(unittest.TestCase):
    """Exercises the real socket path against a loopback Stratum fixture."""

    def test_probe_once_confirms_a_stratum_pool(self):
        pool = FakeStratumPool()
        try:
            candidate = Candidate("P", "R", LOOPBACK_HOST, pool.port)
            tcp_ms, stratum_ms = discovery.probe_once(candidate, timeout=3.0, resolver=loopback_resolver)
            self.assertGreaterEqual(tcp_ms, 0)
            self.assertGreaterEqual(stratum_ms, tcp_ms)
        finally:
            pool.close()

    def test_probe_once_surfaces_an_authorize_rejection(self):
        pool = FakeStratumPool(authorize_result=False)
        try:
            candidate = Candidate("P", "R", LOOPBACK_HOST, pool.port)
            with self.assertRaises(OSError) as caught:
                discovery.probe_once(candidate, timeout=3.0, username="acct.rig", resolver=loopback_resolver)
            self.assertIn("authorize rejected", str(caught.exception))
        finally:
            pool.close()

    def test_probe_once_accepts_a_successful_authorize(self):
        pool = FakeStratumPool(authorize_result=True)
        try:
            candidate = Candidate("P", "R", LOOPBACK_HOST, pool.port)
            discovery.probe_once(candidate, timeout=3.0, username="acct.rig", resolver=loopback_resolver)
        finally:
            pool.close()

    def test_probe_once_rejects_a_non_stratum_listener(self):
        pool = FakeStratumPool(send_garbage=True)
        try:
            candidate = Candidate("P", "R", LOOPBACK_HOST, pool.port)
            with self.assertRaises(OSError):
                discovery.probe_once(candidate, timeout=1.0, resolver=loopback_resolver)
        finally:
            pool.close()

    def test_probe_once_refuses_non_public_addresses_by_default(self):
        pool = FakeStratumPool()
        try:
            candidate = Candidate("P", "R", "127.0.0.1", pool.port)
            with self.assertRaises(OSError) as caught:
                discovery.probe_once(candidate, timeout=1.0)
            self.assertIn("non-public", str(caught.exception))
        finally:
            pool.close()

    def test_probe_round_collects_samples_and_attempts(self):
        pool = FakeStratumPool()
        try:
            candidate = Candidate("P", "R", LOOPBACK_HOST, pool.port)
            samples, attempts = discovery.probe_round([candidate], 2, 2.0, "", "x", 2, True,
                                                     resolver=loopback_resolver)
            self.assertEqual(attempts[candidate.key], 2)
            self.assertEqual(len(samples[candidate]), 2)
        finally:
            pool.close()

    def test_probe_round_counts_a_dead_endpoint_as_attempted(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        candidate = Candidate("P", "R", LOOPBACK_HOST, dead_port)
        samples, attempts = discovery.probe_round([candidate], 1, 0.4, "", "x", 1, True,
                                                 resolver=loopback_resolver)
        self.assertEqual(attempts[candidate.key], 1)
        self.assertEqual(samples[candidate], [])


class PoolDiscoveryCatalogTest(unittest.TestCase):
    def test_bundled_catalog_loads_and_declares_coins(self):
        candidates = discovery.load_catalog(HERE / "pool-catalog.json")
        self.assertGreater(len(candidates), 0)
        self.assertTrue(all(item.host and item.port for item in candidates))
        self.assertTrue(any(matches_coin(item, "LTC") for item in candidates))
        self.assertTrue(any(matches_coin(item, "DOGE") for item in candidates))

    def test_missing_catalog_is_not_an_error(self):
        self.assertEqual(discovery.load_catalog(HERE / "does-not-exist.json"), [])

    def test_malformed_catalog_reports_clearly(self):
        path = HERE / "catalog-malformed-fixture.json"
        path.write_text("{not json", encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                discovery.load_catalog(path)
        finally:
            path.unlink(missing_ok=True)

    def test_config_candidates_come_from_the_shipped_default(self):
        candidates, config = candidates_from_config(HERE / "v3-config.json")
        self.assertIsNotNone(config)
        self.assertEqual(len(candidates), len(config["endpoints"]))
        self.assertTrue(all(item.source == "relay-config" for item in candidates))


class PoolDiscoveryCoverageTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((HERE / "v3-config.json").read_text(encoding="utf-8"))

    def test_shipped_default_reports_no_shared_pool(self):
        rows, shared = coverage_report(self.config)
        self.assertEqual(len(rows), 16, "4 dynamic groups plus 12 fixed routes")
        self.assertEqual(shared, {},
            "the shipped default must not concentrate multiple port groups on one pool")

    def test_concentrated_config_is_flagged(self):
        config = json.loads(json.dumps(self.config))
        for group in config["port_groups"]:
            group["endpoint_ids"] = list(config["port_groups"][0]["endpoint_ids"])
        _, shared = coverage_report(config)
        self.assertIn("Hash-Hut", shared)
        self.assertEqual(len(shared["Hash-Hut"]), 4)

    def test_fixed_routes_do_not_count_as_concentration(self):
        """A pool owning a fixed route plus one group is deliberate, not a hazard.

        LongPool is the pool that owns 11301-11303 and backs no dynamic group in the
        shipped default, so assigning it to exactly one group must stay quiet.
        """
        config = json.loads(json.dumps(self.config))
        _, shared_before = coverage_report(config)
        self.assertEqual(shared_before, {})
        config["port_groups"][3]["endpoint_ids"] = ["longpool-asia-8080", "longpool-asia-8880", "longpool-usw"]
        _, shared = coverage_report(config)
        self.assertEqual(shared, {}, "fixed ports are single-purpose and must not raise a warning")

    def test_two_groups_on_one_pool_are_flagged(self):
        config = json.loads(json.dumps(self.config))
        config["port_groups"][3]["endpoint_ids"] = list(config["port_groups"][0]["endpoint_ids"])
        _, shared = coverage_report(config)
        self.assertEqual(shared, {"Hash-Hut": ["private", "backup-3"]})

    def test_fixed_routes_appear_in_the_report_rows(self):
        rows, _ = coverage_report(self.config)
        ids = {row["id"] for row in rows}
        self.assertIn(":11001", ids)
        self.assertIn("private", ids)

    def test_empty_config_is_handled(self):
        self.assertEqual(coverage_report(None), ([], {}))


if __name__ == "__main__":
    unittest.main()
