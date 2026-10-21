"""Small, capture-scoped HTTP proxy that cannot connect to private networks."""

from __future__ import annotations

import ipaddress
import select
import socket
import socketserver
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

MAX_CONNECTIONS = 16
MAX_HEADER_BYTES = 32768
SOCKET_TIMEOUT = 5
IDLE_TIMEOUT = 10
TUNNEL_TIMEOUT = 45


class ProxyDenied(ValueError):
    pass


def open_public_connection(host: str, port: int) -> socket.socket:
    """Resolve once, reject all non-public answers, then connect to a pinned IP."""
    if not host or not 1 <= port <= 65535:
        raise ProxyDenied("Invalid destination")
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers:
        raise ProxyDenied("Destination did not resolve")
    for _family, _kind, _protocol, _canonical, endpoint in answers:
        address = ipaddress.ip_address(endpoint[0])
        if not address.is_global:
            raise ProxyDenied("Non-public destination")
    last_error = None
    for family, kind, protocol, _canonical, endpoint in answers:
        connection = socket.socket(family, kind, protocol)
        connection.settimeout(SOCKET_TIMEOUT)
        try:
            # endpoint contains the validated numeric IP, never the hostname.
            connection.connect(endpoint)
            return connection
        except OSError as exc:
            connection.close()
            last_error = exc
    raise OSError("Could not connect to destination") from last_error


class _CaptureProxy(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = MAX_CONNECTIONS

    def __init__(self):
        self.stopped = threading.Event()
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.connections = set()
        self.connection_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _ProxyRequest)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def track(self, connection):
        with self.connection_lock:
            self.connections.add(connection)

    def untrack(self, connection):
        with self.connection_lock:
            self.connections.discard(connection)

    def close_connections(self):
        self.stopped.set()
        with self.connection_lock:
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()


class _ProxyRequest(socketserver.BaseRequestHandler):
    def handle(self):
        upstream = None
        started = False
        self.server.track(self.request)
        try:
            if self.server.stopped.is_set():
                return
            self.request.settimeout(SOCKET_TIMEOUT)
            header = bytearray()
            header_deadline = time.monotonic() + SOCKET_TIMEOUT
            while b"\r\n\r\n" not in header:
                remaining = header_deadline - time.monotonic()
                if remaining <= 0:
                    raise ProxyDenied("Proxy header timeout")
                self.request.settimeout(remaining)
                chunk = self.request.recv(min(4096, MAX_HEADER_BYTES - len(header)))
                if not chunk:
                    raise ProxyDenied("Invalid proxy request")
                header.extend(chunk)
                if len(header) >= MAX_HEADER_BYTES:
                    raise ProxyDenied("Proxy request too large")
            self.request.settimeout(SOCKET_TIMEOUT)
            head, pending = bytes(header).split(b"\r\n\r\n", 1)
            request_line, *headers = head.split(b"\r\n")
            method, target, version = request_line.decode("ascii").split(" ")
            if version not in {"HTTP/1.0", "HTTP/1.1"}:
                raise ProxyDenied("Unsupported HTTP version")
            if method == "CONNECT":
                destination = urlsplit("//" + target)
                if destination.path or destination.query or destination.fragment:
                    raise ProxyDenied("Invalid tunnel destination")
                port = destination.port or 443
            else:
                destination = urlsplit(target)
                if destination.scheme != "http" or method not in {
                    "GET",
                    "HEAD",
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                    "OPTIONS",
                }:
                    raise ProxyDenied("Unsupported proxy request")
                port = destination.port or 80
            if destination.username is not None or destination.password is not None:
                raise ProxyDenied("Credentials in destination")
            upstream = open_public_connection(destination.hostname, port)
            self.server.track(upstream)
            if method == "CONNECT":
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                started = True
            else:
                path = destination.path or "/"
                if destination.query:
                    path += "?" + destination.query
                hostname = destination.hostname
                host = f"[{hostname}]" if ":" in hostname else hostname
                if port != 80:
                    host += f":{port}"
                forwarded = []
                for line in headers:
                    name, separator, _value = line.partition(b":")
                    if not separator or name.strip() != name:
                        raise ProxyDenied("Invalid HTTP header")
                    if name.lower() not in {
                        b"host",
                        b"connection",
                        b"proxy-connection",
                        b"proxy-authorization",
                    }:
                        forwarded.append(line)
                forwarded = [
                    f"{method} {path} HTTP/1.1".encode("ascii"),
                    f"Host: {host}".encode("ascii"),
                    b"Connection: close",
                    *forwarded,
                ]
                upstream.sendall(b"\r\n".join(forwarded) + b"\r\n\r\n")
                started = True
            if pending:
                upstream.sendall(pending)
            self.relay(upstream)
        except (OSError, ValueError, UnicodeError):
            if not started:
                try:
                    self.request.sendall(
                        b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                except OSError:
                    pass
        finally:
            if upstream is not None:
                self.server.untrack(upstream)
                upstream.close()
            self.server.untrack(self.request)

    def relay(self, upstream):
        readers = [self.request, upstream]
        expires = time.monotonic() + TUNNEL_TIMEOUT
        last_activity = time.monotonic()
        while readers and not self.server.stopped.is_set():
            now = time.monotonic()
            if now >= expires or now - last_activity >= IDLE_TIMEOUT:
                return
            ready, _, _ = select.select(readers, [], [], 0.25)
            for source in ready:
                destination = upstream if source is self.request else self.request
                chunk = source.recv(65536)
                if not chunk:
                    readers.remove(source)
                    destination.shutdown(socket.SHUT_WR)
                    continue
                destination.sendall(chunk)
                last_activity = time.monotonic()


@contextmanager
def snapshot_proxy():
    server = _CaptureProxy()
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.close_connections()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
