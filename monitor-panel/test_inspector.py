import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from stratum_inspector import Inspector


class InspectorProtocolTest(unittest.TestCase):
    def test_relay_config_has_unique_ports(self):
        config = json.loads((Path(__file__).resolve().parent / "stratum-inspector.json").read_text(encoding="utf-8"))
        ports = [relay["listen_port"] for relay in config["relays"]]
        self.assertEqual(len(ports), len(set(ports)))
        self.assertEqual(len(ports), 15)
        pools = {}
        for relay in config["relays"]:
            pools.setdefault(relay["pool_id"], []).append(relay)
        self.assertEqual(set(pools), {"hashhut", "litecoinpool", "viabtc", "f2pool", "longpool"})
        self.assertTrue(all(len(relays) == 3 for relays in pools.values()))
        self.assertTrue(all(sorted(item["priority"] for item in relays) == [1, 2, 3] for relays in pools.values()))
        protected = [relay for relay in config["relays"] if 9999 <= relay["listen_port"] <= 10003]
        self.assertEqual(
            {(item["listen_port"], item["upstream_host"]) for item in protected},
            {
                (9999, "eu.pool.hash-hut.net"),
                (10001, "ru.pool.hash-hut.net"),
                (10002, "by.pool.hash-hut.net"),
            },
        )
        self.assertTrue(all(item.get("external") is True for item in protected))
        via_hosts = {item["upstream_host"] for item in pools["viabtc"]}
        self.assertEqual(via_hosts, {"ltc.viabtc.io", "ltc.viabtc.top", "ltc.viabtc.cc"})

    def test_authorize_submit_and_accept(self):
        inspector = Inspector()
        connection = {
            "id": "1",
            "source_ip": "192.0.2.10",
            "source_port": 50000,
            "connected_at": 1,
            "worker": "",
            "agent": "TestMiner/1.0",
            "difficulty": 0.0,
            "pending": {},
            "authorized_worker": "",
            "jobs": set(),
        }
        inspector.connections["1"] = connection
        inspector.from_miner(connection, {"id": 1, "method": "mining.authorize", "params": ["wallet.worker1", "secret"]})
        inspector.from_pool(connection, {"id": None, "method": "mining.set_difficulty", "params": [1024]})
        inspector.from_miner(connection, {"id": 2, "method": "mining.submit", "params": ["wallet.worker1", "job", "x"]})
        inspector.from_pool(connection, {"id": 2, "result": True, "error": None})

        state = inspector.state()
        worker = state["workers"][0]
        self.assertEqual(worker["name"], "wallet.worker1")
        self.assertEqual(worker["submitted"], 1)
        self.assertEqual(worker["accepted"], 1)
        self.assertEqual(worker["rejected"], 0)
        self.assertNotIn("secret", repr(state))

    def test_worker_and_job_anomalies(self):
        inspector = Inspector()
        connection = {
            "id": "2", "source_ip": "192.0.2.20", "source_port": 50001,
            "connected_at": 1, "worker": "", "agent": "Miner", "difficulty": 1,
            "pending": {}, "authorized_worker": "", "jobs": set(),
        }
        inspector.from_miner(connection, {"id": 1, "method": "mining.authorize", "params": ["owner.worker", "x"]})
        inspector.from_pool(connection, {"id": None, "method": "mining.notify", "params": ["job-1"]})
        inspector.from_miner(connection, {"id": 2, "method": "mining.submit", "params": ["other.worker", "job-2"]})
        kinds = {item["type"] for item in inspector.state()["anomalies"]}
        self.assertEqual(kinds, {"worker_mismatch"})

    def test_same_worker_connections_are_merged_with_details(self):
        inspector = Inspector()
        for connection_id, source_port in (("1", 50001), ("2", 50002), ("3", 50003)):
            connection = {
                "id": connection_id, "source_ip": "192.0.2.30", "source_port": source_port,
                "connected_at": 100, "worker": "", "agent": f"Board/{connection_id}",
                "difficulty": 1, "pending": {}, "authorized_worker": "", "jobs": set(),
            }
            inspector.connections[connection_id] = connection
            inspector.from_miner(connection, {"id": connection_id, "method": "mining.authorize", "params": ["owner.same", "x"]})
        with patch("stratum_inspector.time.time", return_value=200):
            state = inspector.state()
        self.assertEqual(len(state["workers"]), 1)
        self.assertEqual(state["workers"][0]["name"], "owner.same")
        self.assertEqual(state["workers"][0]["active"], 3)
        self.assertEqual(len(state["workers"][0]["details"]), 3)

    def test_worker_lifecycle_offline_invalid_and_retained_cleanup(self):
        inspector = Inspector()
        connection = {
            "id": "9", "source_ip": "192.0.2.90", "source_port": 50900,
            "connected_at": 100, "worker": "", "agent": "Board/9",
            "difficulty": 1, "pending": {}, "authorized_worker": "", "jobs": set(),
        }
        with patch("stratum_inspector.time.time", return_value=100):
            inspector.from_miner(connection, {"id": 1, "method": "mining.authorize", "params": ["owner.old", "x"]})
        inspector.workers["owner.old"]["active_connections"].clear()
        connection["disconnected_at"] = 200
        inspector.workers["owner.old"]["last_seen"] = 200
        with patch("stratum_inspector.time.time", return_value=200 + 901):
            self.assertEqual(inspector.state()["workers"][0]["status"], "offline")
        with patch("stratum_inspector.time.time", return_value=200 + 86401):
            self.assertEqual(inspector.state()["workers"][0]["status"], "invalid")
        with patch("stratum_inspector.time.time", return_value=200 + 604801):
            self.assertEqual(inspector.state()["workers"], [])
            self.assertNotIn("owner.old", inspector.workers)

    def test_disconnected_connection_details_are_bounded(self):
        inspector = Inspector()
        with patch("stratum_inspector.MAX_DISCONNECTED_DETAILS", 5), \
             patch("stratum_inspector.time.time", return_value=1000):
            for number in range(40):
                connection_id = str(number)
                connection = {
                    "id": connection_id, "source_ip": "192.0.2.40", "source_port": 51000 + number,
                    "connected_at": 900, "worker": "", "agent": "Miner", "difficulty": 1,
                    "pending": {"old": {"started": 1}}, "authorized_worker": "", "jobs": {"job"},
                    "disconnected_at": 950 + number,
                }
                inspector.attach_worker(connection, "owner.flappy")
                worker = inspector.workers["owner.flappy"]
                worker["active_connections"].discard(connection_id)
                inspector.prune_worker_connections(worker, 1000)
            state = inspector.state()
        self.assertEqual(len(state["workers"][0]["details"]), 5)

    def test_unanswered_share_requests_are_bounded(self):
        inspector = Inspector()
        connection = {
            "id": "pending", "source_ip": "192.0.2.41", "source_port": 52000,
            "connected_at": 1, "worker": "", "agent": "Miner", "difficulty": 1,
            "pending": {}, "authorized_worker": "owner.pending", "jobs": set(),
        }
        with patch("stratum_inspector.MAX_PENDING_SHARES", 8):
            for number in range(30):
                inspector.from_miner(connection, {"id": number, "method": "mining.submit",
                    "params": ["owner.pending", "job", "nonce"]})
        self.assertEqual(len(connection["pending"]), 8)

    def test_hashrate_samples_are_aggregated_into_bounded_time_buckets(self):
        inspector = Inspector()
        connection = {
            "id": "shares", "source_ip": "192.0.2.42", "source_port": 52001,
            "connected_at": 1, "worker": "", "agent": "Miner", "difficulty": 1,
            "pending": {}, "authorized_worker": "owner.shares", "jobs": set(),
            "submitted": 0, "accepted": 0, "rejected": 0,
            "latency_total_ms": 0, "latency_samples": 0,
        }
        inspector.attach_worker(connection, "owner.shares")
        with patch("stratum_inspector.time.time", return_value=1000):
            for number in range(1000):
                connection["pending"][json.dumps(number)] = {
                    "started": 999, "difficulty": 2, "worker": "owner.shares"
                }
                inspector.from_pool(connection, {"id": number, "result": True, "error": None})
        self.assertEqual(len(inspector.accepted_events["owner.shares"]), 1)
        self.assertEqual(inspector.accepted_events["owner.shares"][0][1], 2000)

    def test_worker_source_history_is_bounded(self):
        inspector = Inspector()
        with patch("stratum_inspector.MAX_WORKER_SOURCES", 8):
            for number in range(40):
                connection = {"id": str(number), "source_ip": f"192.0.2.{number + 1}",
                    "source_port": 53000 + number, "connected_at": 1, "worker": "", "agent": "Miner",
                    "difficulty": 1, "pending": {}, "authorized_worker": "", "jobs": set()}
                inspector.attach_worker(connection, "owner.sources")
        self.assertEqual(len(inspector.workers["owner.sources"]["sources"]), 8)

    def test_disconnect_history_is_written_without_credentials(self):
        inspector = Inspector()
        with tempfile.TemporaryDirectory() as folder, \
             patch("stratum_inspector.DISCONNECT_HISTORY_DIR", Path(folder)):
            connection = {
                "source_ip": "192.0.2.50", "source_port": 53000, "public_port": 11301,
                "worker": "owner.archive", "agent": "Miner/1", "connected_at": 100,
                "disconnected_at": 160, "submitted": 2, "accepted": 1, "rejected": 1,
                "latency_total_ms": 40, "latency_samples": 2, "last_share": "test",
                "last_error": "test error",
            }
            inspector.archive_disconnect(connection)
            files = list(Path(folder).glob("disconnect-*.jsonl"))
            self.assertEqual(len(files), 1)
            record = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(record["worker"], "owner.archive")
            self.assertEqual(record["duration_seconds"], 60)
            self.assertNotIn("password", repr(record).lower())


if __name__ == "__main__":
    unittest.main()
