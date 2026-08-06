#!/usr/bin/env python3
"""Local HTTP CONNECT/forward proxy with byte counters and upstream auth injection."""
from __future__ import annotations

import base64
import select
import socket
import socketserver
import threading
import time
from typing import Any, Optional
from urllib.parse import unquote, urlparse


def parse_proxy_url(proxy: str) -> dict[str, str]:
    raw = str(proxy or "").strip()
    out = {"scheme": "http", "host": "", "port": "", "user": "", "password": "", "url": raw}
    if not raw:
        return out
    if "://" not in raw and raw.count(":") >= 3:
        host, port, user, password = raw.split(":", 3)
        out.update(scheme="http", host=host, port=port, user=user, password=password)
        out["url"] = f"http://{user}:{password}@{host}:{port}"
        return out
    try:
        parsed = urlparse(raw if "://" in raw else f"http://{raw}")
        out["scheme"] = parsed.scheme or "http"
        out["host"] = parsed.hostname or ""
        out["port"] = str(parsed.port or (443 if (parsed.scheme or "") == "https" else 80))
        out["user"] = unquote(parsed.username or "")
        out["password"] = unquote(parsed.password or "")
        if out["user"]:
            out["url"] = f"{out['scheme']}://{out['user']}:{out['password']}@{out['host']}:{out['port']}"
        else:
            out["url"] = f"{out['scheme']}://{out['host']}:{out['port']}"
    except Exception:
        pass
    return out


def redact_proxy(proxy: str) -> str:
    s = str(proxy or "").strip()
    if not s:
        return ""
    if "://" not in s and s.count(":") >= 3:
        host, port, user, _password = s.split(":", 3)
        return f"http://{user}:***@{host}:{port}"
    return __import__("re").sub(r":([^:/@]+)@", r":***@", s)


class MeteredProxy:
    """127.0.0.1 HTTP proxy that forwards to an upstream proxy and counts bytes."""

    def __init__(self, upstream: str, *, listen_host: str = "127.0.0.1") -> None:
        self.upstream_raw = str(upstream or "").strip()
        self.upstream = parse_proxy_url(self.upstream_raw)
        self.listen_host = listen_host
        self.listen_port = 0
        self._server: Optional[socketserver.ThreadingTCPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.bytes_sent = 0  # client -> target (via upstream)
        self.bytes_recv = 0  # target -> client
        self.connections = 0
        self.active_connections = 0
        self.started_at = 0.0
        self.stopped = False
        self.last_error = ""

    @property
    def local_url(self) -> str:
        return f"http://{self.listen_host}:{self.listen_port}"

    @property
    def ready(self) -> bool:
        return bool(self._server and self.listen_port)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "bytes_sent": int(self.bytes_sent),
                "bytes_recv": int(self.bytes_recv),
                "bytes_total": int(self.bytes_sent + self.bytes_recv),
                "connections": int(self.connections),
                "active_connections": int(self.active_connections),
                "local_url": self.local_url if self.listen_port else "",
                "upstream": redact_proxy(self.upstream_raw or self.upstream.get("url") or ""),
                "last_error": self.last_error,
                "running": bool(self._server) and not self.stopped,
            }

    def _add(self, sent: int = 0, recv: int = 0) -> None:
        if sent or recv:
            with self._lock:
                self.bytes_sent += max(0, int(sent))
                self.bytes_recv += max(0, int(recv))

    def start(self) -> str:
        if not self.upstream.get("host"):
            raise ValueError("meter proxy requires upstream host")
        if self._server:
            return self.local_url

        up_host = self.upstream["host"]
        up_port = int(self.upstream["port"] or 80)
        user = self.upstream.get("user") or ""
        password = self.upstream.get("password") or ""
        auth_header = ""
        if user:
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            auth_header = f"Basic {token}"

        meter = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:  # noqa: C901
                with meter._lock:
                    meter.connections += 1
                    meter.active_connections += 1
                try:
                    first = self.rfile.readline(65535)
                    if not first:
                        return
                    line = first.decode("iso-8859-1", errors="replace").strip()
                    parts = line.split()
                    if len(parts) < 2:
                        return
                    method, target = parts[0].upper(), parts[1]
                    headers: list[bytes] = []
                    while True:
                        h = self.rfile.readline(65535)
                        if not h or h in (b"\r\n", b"\n"):
                            break
                        low = h.lower()
                        if low.startswith(b"proxy-authorization:") or low.startswith(b"proxy-connection:"):
                            continue
                        headers.append(h)

                    upstream = socket.create_connection((up_host, up_port), timeout=45)
                    try:
                        if method == "CONNECT":
                            req = (
                                f"CONNECT {target} HTTP/1.1\r\n"
                                f"Host: {target}\r\n"
                                f"{('Proxy-Authorization: ' + auth_header + chr(13) + chr(10)) if auth_header else ''}"
                                f"Proxy-Connection: keep-alive\r\n\r\n"
                            ).encode()
                            upstream.sendall(req)
                            meter._add(sent=len(req))
                            resp = b""
                            while b"\r\n\r\n" not in resp and len(resp) < 65535:
                                chunk = upstream.recv(4096)
                                if not chunk:
                                    break
                                resp += chunk
                            meter._add(recv=len(resp))
                            if b" 200 " not in resp.split(b"\r\n", 1)[0]:
                                self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                                return
                            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                            self._pipe(self.connection, upstream)
                        else:
                            rebuilt = [f"{method} {target} HTTP/1.1\r\n".encode()]
                            host_hdr_seen = False
                            for h in headers:
                                if h.lower().startswith(b"host:"):
                                    host_hdr_seen = True
                                rebuilt.append(h)
                            if not host_hdr_seen:
                                # best effort host from absolute URL
                                try:
                                    host_part = target.split("://", 1)[-1].split("/", 1)[0]
                                    rebuilt.append(f"Host: {host_part}\r\n".encode())
                                except Exception:
                                    pass
                            if auth_header:
                                rebuilt.append(f"Proxy-Authorization: {auth_header}\r\n".encode())
                            rebuilt.append(b"\r\n")
                            head = b"".join(rebuilt)
                            upstream.sendall(head)
                            meter._add(sent=len(head))
                            # body for non-CONNECT is rare for our use; still drain content-length if present
                            cl = 0
                            for h in headers:
                                if h.lower().startswith(b"content-length:"):
                                    try:
                                        cl = int(h.split(b":", 1)[1].strip() or 0)
                                    except Exception:
                                        cl = 0
                            left = cl
                            while left > 0:
                                chunk = self.rfile.read(min(65536, left))
                                if not chunk:
                                    break
                                upstream.sendall(chunk)
                                meter._add(sent=len(chunk))
                                left -= len(chunk)
                            self._pipe(self.connection, upstream)
                    finally:
                        try:
                            upstream.close()
                        except Exception:
                            pass
                except Exception as exc:
                    with meter._lock:
                        meter.last_error = str(exc)[:240]
                    try:
                        self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                    except Exception:
                        pass
                finally:
                    with meter._lock:
                        meter.active_connections = max(0, meter.active_connections - 1)

            def _pipe(self, client: socket.socket, upstream: socket.socket) -> None:
                client.setblocking(False)
                upstream.setblocking(False)
                sockets = [client, upstream]
                idle_deadline = time.time() + 300
                while True:
                    if time.time() > idle_deadline:
                        break
                    try:
                        r, _, x = select.select(sockets, [], sockets, 1.0)
                    except Exception:
                        break
                    if x:
                        break
                    if not r:
                        continue
                    idle_deadline = time.time() + 300
                    if client in r:
                        try:
                            data = client.recv(65536)
                        except Exception:
                            break
                        if not data:
                            break
                        try:
                            upstream.sendall(data)
                        except Exception:
                            break
                        meter._add(sent=len(data))
                    if upstream in r:
                        try:
                            data = upstream.recv(65536)
                        except Exception:
                            break
                        if not data:
                            break
                        try:
                            client.sendall(data)
                        except Exception:
                            break
                        meter._add(recv=len(data))

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        server = Server((self.listen_host, 0), Handler)
        self._server = server
        self.listen_port = int(server.server_address[1])
        self.started_at = time.time()
        self.stopped = False
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.3}, daemon=True)
        self._thread.start()
        return self.local_url

    def stop(self) -> dict[str, Any]:
        self.stopped = True
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        snap = self.snapshot()
        snap["running"] = False
        return snap
