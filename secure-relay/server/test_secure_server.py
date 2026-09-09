import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import stratum_secure_server as secure


class SecureServerTests(unittest.TestCase):
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

    def test_route_map_matches_v3_ports_to_internal_endpoint(self):
        config = {
            "endpoints": [
                {"id": "disabled", "enabled": False},
                {"id": "active", "enabled": True},
                {"id": "backup", "enabled": True},
            ],
            "port_groups": [{"ports": [9999, 10001], "endpoint_ids": ["active", "backup"]}],
            "fixed_routes": [{"port": 11001, "endpoint_id": "backup"}],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "v3.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch.object(secure, "V3_CONFIG_FILE", path):
                self.assertEqual(secure.route_map(), {9999: 20001, 10001: 20002, 11001: 20002})

    def test_proxy_header_preserves_miner_lan_address(self):
        value = secure.proxy_header(
            {"x-miner-ip": "192.168.10.25", "x-miner-port": "45678"},
            ("198.51.100.4", 50000),
            20002,
        )
        self.assertEqual(value, b"PROXY TCP4 192.168.10.25 127.0.0.1 45678 20002\r\n")

    def test_missing_token_is_rejected(self):
        with self.assertRaises(PermissionError):
            secure.parse_request(b"CONNECT /relay/v1/9999 HTTP/1.1\r\nHost: relay\r\n\r\n")


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
        relay = secure.SecureRelay({"token": "a" * 64, "max_connections": 10})
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


if __name__ == "__main__":
    unittest.main()
