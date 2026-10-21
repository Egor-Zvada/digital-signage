import http.client
import json
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from core.services import snapshot_proxy as proxy_module


@pytest.fixture
def public_site(monkeypatch):
    """Route only this test's public hostname to a local origin; real policy stays for others."""
    hits = []

    class Origin(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            hits.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/private")
                self.end_headers()
                return
            data = json.dumps({"host": self.headers.get("Host"), "path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    thread = threading.Thread(
        target=origin.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    real_connect = proxy_module.open_public_connection

    def connect(host, port):
        if host == "public.example":
            return socket.create_connection(origin.server_address, timeout=2)
        return real_connect(host, port)

    monkeypatch.setattr(proxy_module, "open_public_connection", connect)
    try:
        yield origin, hits
    finally:
        origin.shutdown()
        origin.server_close()
        thread.join(timeout=2)


def proxy_request(proxy, target, *, method="GET", body=None):
    address = urlsplit(proxy)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=2)
    try:
        connection.request(method, target, body=body)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_public_http_request_preserves_host_path_and_post_body(public_site):
    with proxy_module.snapshot_proxy() as proxy:
        status, _, body = proxy_request(proxy, "http://public.example/page?x=1")
        assert status == 200
        assert json.loads(body) == {"host": "public.example", "path": "/page?x=1"}
        status, _, body = proxy_request(
            proxy, "http://public.example/submit", method="POST", body=b"snapshot request body"
        )
        assert status == 200
        assert body == b"snapshot request body"


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1/private",
        "http://[::1]/private",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.110.222/",
        "http://10.0.0.1/",
        "http://user:password@public.example/",
        "file:///etc/passwd",
    ],
)
def test_private_resources_and_local_urls_are_denied(target):
    with proxy_module.snapshot_proxy() as proxy:
        status, _, body = proxy_request(proxy, target)
        assert status == 403
        assert body == b""


def test_redirect_to_loopback_is_denied_before_contacting_private_origin(public_site):
    _origin, hits = public_site
    with proxy_module.snapshot_proxy() as proxy:
        status, headers, _ = proxy_request(proxy, "http://public.example/redirect")
        assert status == 302
        status, _, _ = proxy_request(proxy, headers["Location"])
        assert status == 403
    assert hits == ["/redirect"]


def test_page_resources_cannot_connect_to_private_network(public_site):
    origin, hits = public_site
    with proxy_module.snapshot_proxy() as proxy:
        assert proxy_request(proxy, "http://public.example/page")[0] == 200
        assert proxy_request(proxy, f"http://127.0.0.1:{origin.server_port}/private")[0] == 403
    assert hits == ["/page"]


def test_dns_answers_are_pinned_before_connecting(monkeypatch):
    resolved = []
    connected = []

    def resolve(host, port, **_kwargs):
        resolved.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", port))]

    class Connection:
        def settimeout(self, timeout):
            assert timeout > 0

        def connect(self, address):
            connected.append(address)

    monkeypatch.setattr(proxy_module.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(proxy_module.socket, "socket", lambda *_args: Connection())

    proxy_module.open_public_connection("rebinding.example", 443)

    assert resolved == [("rebinding.example", 443)]
    assert connected == [("1.1.1.1", 443)]


def test_mixed_public_and_private_dns_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(
        proxy_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ],
    )

    def unexpected_connect(*_args):
        pytest.fail("Private/mixed DNS must be rejected before creating a connection")

    monkeypatch.setattr(proxy_module.socket, "socket", unexpected_connect)

    with pytest.raises(proxy_module.ProxyDenied):
        proxy_module.open_public_connection("rebinding.example", 443)


def test_https_connect_is_opaque_and_capture_shutdown_closes_tunnels(monkeypatch):
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            while chunk := self.request.recv(1024):
                self.request.sendall(chunk)

    origin = socketserver.TCPServer(("127.0.0.1", 0), Echo)
    thread = threading.Thread(
        target=origin.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    real_connect = proxy_module.open_public_connection
    monkeypatch.setattr(
        proxy_module,
        "open_public_connection",
        lambda host, port: socket.create_connection(origin.server_address, timeout=2)
        if host == "public.example"
        else real_connect(host, port),
    )
    connection = None
    try:
        with proxy_module.snapshot_proxy() as proxy:
            address = urlsplit(proxy)
            connection = socket.create_connection((address.hostname, address.port), timeout=2)
            connection.sendall(
                b"CONNECT public.example:443 HTTP/1.1\r\nHost: public.example:443\r\n\r\n"
            )
            assert connection.recv(1024) == b"HTTP/1.1 200 Connection Established\r\n\r\n"
            opaque_tls_record = b"\x16\x03\x01opaque-tls-record"
            connection.sendall(opaque_tls_record)
            assert connection.recv(1024) == opaque_tls_record
        assert connection.recv(1024) == b""
    finally:
        if connection is not None:
            connection.close()
        origin.shutdown()
        origin.server_close()
        thread.join(timeout=2)


def test_connect_to_private_address_is_denied():
    with proxy_module.snapshot_proxy() as proxy:
        address = urlsplit(proxy)
        with socket.create_connection((address.hostname, address.port), timeout=2) as connection:
            connection.sendall(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            assert connection.recv(1024).startswith(b"HTTP/1.1 403 Forbidden")


def test_connection_limit_rejects_excess_clients_without_starting_more_threads(monkeypatch):
    monkeypatch.setattr(proxy_module, "MAX_CONNECTIONS", 2)
    clients = []
    with proxy_module.snapshot_proxy() as proxy:
        address = urlsplit(proxy)
        try:
            for _ in range(2):
                connection = socket.create_connection((address.hostname, address.port), timeout=2)
                connection.sendall(b"GET ")
                clients.append(connection)
            with socket.create_connection((address.hostname, address.port), timeout=2) as excess:
                try:
                    assert excess.recv(1024) == b""
                except ConnectionResetError:
                    pass
        finally:
            for connection in clients:
                connection.close()


def test_oversized_headers_are_rejected():
    with proxy_module.snapshot_proxy() as proxy:
        address = urlsplit(proxy)
        with socket.create_connection((address.hostname, address.port), timeout=2) as connection:
            connection.sendall(b"GET http://public.example/ HTTP/1.1\r\nX-Large: " + b"x" * 32768)
            assert connection.recv(1024).startswith(b"HTTP/1.1 403 Forbidden")
