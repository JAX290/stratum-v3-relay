import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import stratum_secure_server as secure
import stratum_secure_monitor as monitor
import secure_relay_clients as credentials


class SecureServerTests(unittest.TestCase):
    def test_limited_client_action_is_delivered_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            actions = Path(temporary) / "actions.json"
            actions.write_text(json.dumps({"clients": {"mine-a": {"id": "a" * 32,
                "action": "diagnose", "expires": 2000}}, "results": {}}), encoding="utf-8")
            with patch.object(secure, "CLIENT_ACTION_FILE", actions):
                delivered = secure.take_client_action("mine-a", now=1000)
                repeated = secure.take_client_action("mine-a", now=1000)
            self.assertEqual(delivered, {"id": "a" * 32, "action": "diagnose"})
            self.assertIsNone(repeated)
            stored = json.loads(actions.read_text(encoding="utf-8"))
            self.assertEqual(stored["results"]["mine-a"]["status"], "delivered")

    def test_empty_client_list_is_valid_and_authenticates_nobody(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "relay.json"
            config.write_text(json.dumps({"listen_host": "0.0.0.0", "listen_port": 452,
                "certificate": "cert", "private_key": "key", "clients": []}))
            with patch.object(secure, "CONFIG_FILE", config):
                loaded = secure.load_config()
            self.assertEqual(loaded["clients"], [])
            with self.assertRaises(PermissionError):
                secure.authenticate(loaded, "a" * 64)

    def test_parse_authenticated_request(self):
        raw = (
            b"CONNECT /relay/v1/9999 HTTP/1.1\r\n"
            b"Host: relay.example\r\n"
            b"Authorization: Bearer abcdef\r\n"
            b"X-Miner-IP: 192.168.1.20\r\n\r\n"
        )
        port, token, headers = secure.parse_request(raw)
        self.assertEqual(port, 9999)
        self.assertEqual(token, "abcdef")
        self.assertEqual(headers["x-miner-ip"], "192.168.1.20")

    def test_health_request(self):
        port, token, headers = secure.parse_request(
            b"CONNECT /relay/v2/health HTTP/1.1\r\nAuthorization: Bearer abcdef\r\n\r\n"
        )
        self.assertIsNone(port)
        self.assertEqual(token, "abcdef")

    def test_only_loopback_watchdog_health_is_excluded_from_site_heartbeat(self):
        headers = {"x-health-origin": "vps-watchdog"}
        self.assertTrue(secure.is_local_watchdog(None, headers, ("127.0.0.1", 1234)))
        self.assertFalse(secure.is_local_watchdog(None, headers, ("198.51.100.1", 1234)))
        self.assertFalse(secure.is_local_watchdog(9999, headers, ("127.0.0.1", 1234)))

    def test_multiple_client_authentication(self):
        config = {"clients": [
            {"id": "mine-a", "name": "矿场A", "token": "a" * 64, "enabled": True},
            {"id": "mine-b", "name": "矿场B", "token": "b" * 64, "enabled": False},
        ]}
        self.assertEqual(secure.authenticate(config, "a" * 64)["id"], "mine-a")
        with self.assertRaises(PermissionError):
            secure.authenticate(config, "b" * 64)

    def test_route_map_matches_v3_ports_to_internal_endpoint(self):
        config = {
            "endpoints": [
                {"id": "disabled", "enabled": False},
                {"id": "active", "enabled": True},
                {"id": "backup", "enabled": True},
            ],
            "port_groups": [{"ports": [9999, 10001], "endpoint_ids": ["active", "backup"]}],
            "fixed_routes": [{"port": 11001, "endpoint_id": "backup"}],
            "canary_routes": [{"port": 11001, "source_ip": "192.168.1.20", "endpoint_id": "active"}],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "v3.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch.object(secure, "V3_CONFIG_FILE", path):
                self.assertEqual(secure.route_map(), {9999: 20001, 10001: 20002, 11001: 20002})
                self.assertEqual(secure.route_map("192.168.1.20")[11001], 20001)

    def test_proxy_header_preserves_miner_lan_address(self):
        value = secure.proxy_header(
            {"x-miner-ip": "192.168.10.25", "x-miner-port": "45678"},
            ("198.51.100.4", 50000),
            9999,
        )
        self.assertEqual(value, b"PROXY TCP4 192.168.10.25 127.0.0.1 45678 9999\r\n")

    def test_targeted_reconnect_only_closes_matching_miner(self):
        class Writer:
            def __init__(self): self.closed = False
            def close(self): self.closed = True
        with tempfile.TemporaryDirectory() as folder, patch.object(secure, "CONTROL_FILE", Path(folder) / "control.json"):
            relay = secure.SecureRelay({"clients": [], "state_file": str(Path(folder) / "sites.json")})
            first, second = Writer(), Writer()
            relay.connections = {1: {"writer": first, "port": 11301, "miner_ip": "192.168.1.20"},
                2: {"writer": second, "port": 11301, "miner_ip": "192.168.1.21"}}
            self.assertEqual(relay.disconnect_matching(11301, "192.168.1.20"), 1)
            self.assertTrue(first.closed)
            self.assertFalse(second.closed)

    def test_missing_token_is_rejected(self):
        with self.assertRaises(PermissionError):
            secure.parse_request(b"CONNECT /relay/v1/9999 HTTP/1.1\r\nHost: relay\r\n\r\n")

    def test_site_state_resets_stale_active_connections(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sites.json"
            path.write_text(json.dumps({"sites": {"mine-a": {"id": "mine-a", "active": 9}}}), encoding="utf-8")
            state = secure.SiteState(path)
            self.assertEqual(state.sites["mine-a"]["active"], 0)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["sites"]["mine-a"]["active"], 0)

    def test_site_state_write_failure_does_not_break_relay_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            state = secure.SiteState(Path(folder) / "sites.json")
            with patch.object(secure.tempfile, "mkstemp", side_effect=PermissionError("read-only filesystem")):
                self.assertFalse(state.write())
                state.update({"id": "mine-a", "name": "矿场A"}, ("198.51.100.4", 50000), force=True)
            self.assertEqual(state.sites["mine-a"]["last_ip"], "198.51.100.4")

    def test_site_state_groups_unique_miners_and_records_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sites.json"
            state = secure.SiteState(path)
            client = {"id": "mine-a", "name": "矿场A"}
            state.update(client, ("198.51.100.4", 50000), active_delta=1, miner_ip="192.168.1.20", client_version="2.2.0")
            state.update(client, ("198.51.100.4", 50001), active_delta=1, miner_ip="192.168.1.20")
            state.update(client, ("198.51.100.4", 50002), active_delta=1, miner_ip="192.168.1.21", force=True)
            self.assertEqual(state.sites["mine-a"]["miner_count"], 2)
            self.assertEqual(state.sites["mine-a"]["active"], 3)
            self.assertEqual(state.sites["mine-a"]["client_version"], "2.2.0")
            state.update(client, ("198.51.100.4", 50000), active_delta=-1, miner_ip="192.168.1.20")
            self.assertEqual(state.sites["mine-a"]["miner_count"], 2)
            state.update(client, ("198.51.100.4", 50001), active_delta=-1, miner_ip="192.168.1.20", force=True)
            self.assertEqual(state.sites["mine-a"]["miner_count"], 1)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["server_version"], secure.SERVER_VERSION)
            state.update(client, ("198.51.100.4", 50003), reported_miner_count=88, reported_connections=352, force=True)
            self.assertEqual(state.sites["mine-a"]["reported_miner_count"], 88)
            self.assertEqual(state.sites["mine-a"]["reported_connections"], 352)


class RelayFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_bidirectional_tunnel(self):
        async def echo_after_proxy(reader, writer):
            header = await reader.readline()
            self.assertTrue(header.startswith(b"PROXY TCP4 192.168.1.20 "))
            data = await reader.readexactly(13)
            writer.write(data)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream = await asyncio.start_server(echo_after_proxy, "127.0.0.1", 0)
        upstream_port = upstream.sockets[0].getsockname()[1]
        relay = secure.SecureRelay({"clients": [{"id": "test", "name": "测试", "token": "a" * 64}], "max_connections": 10,
                                    "state_file": str(Path(tempfile.gettempdir()) / "stratum-secure-test-state.json")})
        with patch.object(secure, "route_map", return_value={9999: upstream_port}):
            ingress = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
            ingress_port = ingress.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", ingress_port)
            writer.write(
                b"CONNECT /relay/v1/9999 HTTP/1.1\r\n"
                b"Host: relay\r\nAuthorization: Bearer " + b"a" * 64 +
                b"\r\nX-Miner-IP: 192.168.1.20\r\nX-Miner-Port: 4567\r\n\r\n"
            )
            await writer.drain()
            response = await reader.readuntil(b"\r\n\r\n")
            self.assertTrue(response.startswith(b"HTTP/1.1 200 "))
            writer.write(b"hello-stratum")
            await writer.drain()
            self.assertEqual(await reader.readexactly(13), b"hello-stratum")
            writer.close()
            await writer.wait_closed()
            ingress.close()
            upstream.close()
            await ingress.wait_closed()
            await upstream.wait_closed()


class MonitorTests(unittest.TestCase):
    def test_offline_and_recovery_transitions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.json"
            state = root / "sites.json"
            monitor_state = root / "monitor.json"
            event_file = root / "events.jsonl"
            config.write_text(json.dumps({"offline_after_seconds": 60, "clients": [
                {"id": "mine-a", "name": "矿场A", "enabled": True, "alert_enabled": True}
            ]}), encoding="utf-8")
            state.write_text(json.dumps({"sites": {"mine-a": {"last_seen": 100}}}), encoding="utf-8")
            monitor_state.write_text(json.dumps({"started_at": 0, "clients": {"mine-a": "online"}}), encoding="utf-8")
            with patch.object(monitor, "CONFIG_FILE", config), patch.object(monitor, "STATE_FILE", state), patch.object(monitor, "MONITOR_FILE", monitor_state), patch.object(monitor, "EVENT_FILE", event_file):
                events = monitor.check_once(now=200)
                self.assertEqual(events, [])
                events = monitor.check_once(now=230)
                self.assertEqual(len(events), 1)
                self.assertIn("离线", events[0])
                state.write_text(json.dumps({"sites": {"mine-a": {"last_seen": 235}}}), encoding="utf-8")
                events = monitor.check_once(now=240)
                self.assertEqual(events, [])
                events = monitor.check_once(now=270)
                self.assertEqual(len(events), 1)
                self.assertIn("恢复", events[0])
                records = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()]
                self.assertEqual([item["type"] for item in records], ["site_offline", "site_recovered"])

    def test_duplicate_transition_is_suppressed_after_state_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.json"
            state = root / "sites.json"
            monitor_state = root / "monitor.json"
            event_file = root / "events.jsonl"
            config.write_text(json.dumps({"offline_after_seconds": 60, "offline_confirm_checks": 1,
                "recovery_confirm_checks": 1, "notification_dedup_seconds": 600,
                "clients": [{"id": "mine-a", "name": "矿场A", "enabled": True, "alert_enabled": True}]}), encoding="utf-8")
            state.write_text(json.dumps({"sites": {"mine-a": {"last_seen": 100}}}), encoding="utf-8")
            monitor_state.write_text(json.dumps({"started_at": 0, "clients": {"mine-a": "online"}}), encoding="utf-8")
            with patch.object(monitor, "CONFIG_FILE", config), patch.object(monitor, "STATE_FILE", state), patch.object(monitor, "MONITOR_FILE", monitor_state), patch.object(monitor, "EVENT_FILE", event_file):
                self.assertEqual(len(monitor.check_once(now=200)), 1)
                monitor_state.write_text(json.dumps({"started_at": 0, "clients": {"mine-a": "online"}}), encoding="utf-8")
                self.assertEqual(monitor.check_once(now=230), [])
                records = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()]
                self.assertEqual([item["type"] for item in records], ["site_offline"])


class CredentialTests(unittest.TestCase):
    def test_legacy_token_migrates_without_changing_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"token": "a" * 64}), encoding="utf-8")
            with patch.object(credentials, "CONFIG_FILE", path):
                data = credentials.load()
                self.assertNotIn("token", data)
                self.assertEqual(data["clients"][0]["token"], "a" * 64)
                credentials.save(data)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["clients"][0]["id"], "default")


if __name__ == "__main__":
    unittest.main()
