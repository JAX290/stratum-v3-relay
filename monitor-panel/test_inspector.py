import unittest
import json
from pathlib import Path
from unittest.mock import patch

from stratum_inspector import Inspector


class InspectorProtocolTest(unittest.TestCase):
    def test_relay_config_has_unique_ports(self):
        config = json.loads(Path("stratum-inspector.json").read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main()
