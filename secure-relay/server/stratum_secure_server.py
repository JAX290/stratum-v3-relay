#!/usr/bin/env python3
"""TLS ingress for the Stratum V3 relay.

The client opens a TLS connection and sends an HTTP CONNECT-like request.  The
request is inside TLS.  After authentication both sides switch to a raw byte
stream, preserving the original Stratum protocol for the existing inspector.
"""

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import signal
import ssl
from pathlib import Path


CONFIG_FILE = Path(os.getenv("SECURE_RELAY_CONFIG", "/etc/stratum-secure-relay.json"))
V3_CONFIG_FILE = Path(os.getenv("V3_CONFIG_FILE", "/etc/stratum-v3.json"))
MAX_HEADER = 8192
INTERNAL_START = 20000


def load_config():
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    required = ("listen_host", "listen_port", "certificate", "private_key", "token")
    missing = [name for name in required if not data.get(name)]
    if missing:
        raise ValueError("missing secure relay settings: " + ", ".join(missing))
    if len(str(data["token"])) < 32:
        raise ValueError("secure relay token must contain at least 32 characters")
    return data


def route_map():
    config = json.loads(V3_CONFIG_FILE.read_text(encoding="utf-8"))
    internal = {}
    for offset, endpoint in enumerate(config.get("endpoints", [])):
        if endpoint.get("enabled", True):
            internal[endpoint["id"]] = INTERNAL_START + offset
    routes = {}
    for group in config.get("port_groups", []):
        for public_port, endpoint_id in zip(group.get("ports", []), group.get("endpoint_ids", [])):
            if endpoint_id in internal:
                routes[int(public_port)] = internal[endpoint_id]
    for route in config.get("fixed_routes", []):
        endpoint_id = route.get("endpoint_id")
        if endpoint_id in internal:
            routes[int(route["port"])] = internal[endpoint_id]
    return routes


async def close_writer(writer):
    if writer is None:
        return
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError, ssl.SSLError):
        pass


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass


def parse_request(raw):
    text = raw.decode("iso-8859-1")
    lines = text.split("\r\n")
    request = lines[0].split()
    if len(request) != 3 or request[0] != "CONNECT" or request[2] != "HTTP/1.1":
        raise ValueError("invalid request line")
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            raise ValueError("invalid header")
        headers[name.strip().lower()] = value.strip()
    prefix = "/relay/v1/"
    if not request[1].startswith(prefix):
        raise ValueError("invalid path")
    port = int(request[1][len(prefix):])
    authorization = headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise PermissionError("missing token")
    return port, authorization[7:], headers


def proxy_header(headers, peer, internal_port):
    candidate = headers.get("x-miner-ip", "")
    try:
        address = ipaddress.ip_address(candidate)
        if address.version != 4:
            raise ValueError("IPv6 client is not supported by this build")
        source_ip = str(address)
    except ValueError:
        source_ip = str(peer[0])
        ipaddress.ip_address(source_ip)
    try:
        source_port = int(headers.get("x-miner-port", peer[1]))
    except (TypeError, ValueError):
        source_port = int(peer[1])
    if not 1 <= source_port <= 65535:
        source_port = 1
    return f"PROXY TCP4 {source_ip} 127.0.0.1 {source_port} {internal_port}\r\n".encode("ascii")


class SecureRelay:
    def __init__(self, config):
        self.config = config
        self.token = str(config["token"])
        self.semaphore = asyncio.Semaphore(int(config.get("max_connections", 1000)))

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername") or ("0.0.0.0", 1)
        upstream_writer = None
        async with self.semaphore:
            try:
                raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
                if len(raw) > MAX_HEADER:
                    raise ValueError("header too large")
                port, supplied_token, headers = parse_request(raw)
                if not hmac.compare_digest(supplied_token, self.token):
                    raise PermissionError("invalid token")
                internal_port = route_map().get(port)
                if not internal_port:
                    raise ValueError("route is not enabled")
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", internal_port), timeout=10
                )
                upstream_writer.write(proxy_header(headers, peer, internal_port))
                await upstream_writer.drain()
                writer.write(b"HTTP/1.1 200 Connection Established\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                logging.info("relay connected source=%s miner=%s port=%s", peer[0], headers.get("x-miner-ip", "?"), port)
                tasks = (asyncio.create_task(pipe(reader, upstream_writer)),
                         asyncio.create_task(pipe(upstream_reader, writer)))
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*done, *pending, return_exceptions=True)
            except PermissionError:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                logging.warning("rejected unauthenticated connection source=%s", peer[0])
            except (ValueError, OSError, ssl.SSLError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
                logging.info("connection closed source=%s reason=%s", peer[0], exc)
            finally:
                await close_writer(upstream_writer)
                await close_writer(writer)


async def main():
    config = load_config()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config["certificate"], config["private_key"])
    relay = SecureRelay(config)
    server = await asyncio.start_server(relay.handle, config["listen_host"], int(config["listen_port"]), ssl=context)
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    logging.info("secure relay listening on %s", addresses)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            loop.add_signal_handler(getattr(signal, name), stop.set)
    async with server:
        await stop.wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
