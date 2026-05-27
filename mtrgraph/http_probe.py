"""HTTP timing probe — measures DNS / TCP / TLS / TTFB / total.

Pure stdlib (no extra deps). One socket per sample, connection closed each time
so we measure cold-start latency (which is what S3-style API clients pay).
"""
from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class HttpSample:
    sample_idx: int
    dns_ms: float | None
    tcp_ms: float | None
    tls_ms: float | None
    ttfb_ms: float | None
    total_ms: float | None
    status: int | None
    resolved_ip: str | None
    error: str | None
    # TLS metadata captured during handshake (only first sample matters for a
    # given URL — TLS doesn't change per-sample under normal operation)
    tls_version: str | None = None
    tls_cipher: str | None = None
    cert_subject_cn: str | None = None
    cert_issuer_cn: str | None = None
    cert_not_after: str | None = None
    cert_san_count: int | None = None
    # Response metadata captured from headers
    content_length: int | None = None
    content_type: str | None = None
    content_encoding: str | None = None
    server: str | None = None
    cache_status: str | None = None     # X-Cache header (HIT/MISS) — common in CDNs
    # Redirect chain — list of dicts {status, location} if redirects were followed
    redirect_chain: list | None = None
    final_url: str | None = None        # URL after redirect chain (None if no redirect)
    # Raw response headers (lower-case keyed). NOT persisted in DB, only available
    # in-process for callers that need them (synthetic transactions, etc.).
    response_headers: dict | None = None
    set_cookie_headers: list | None = None   # All Set-Cookie values (multi-value header)


def _ms(start: float) -> float:
    return (time.monotonic() - start) * 1000.0


def probe_once(
    url: str,
    method: str = "HEAD",
    timeout: float = 10.0,
    sample_idx: int = 0,
    user_agent: str = "mtrgraph/0.1",
    force_ip: str | None = None,
    follow_redirects: bool = False,
    max_redirects: int = 5,
    extra_headers: dict | None = None,
    body: bytes | str | None = None,
    _redirect_chain: list | None = None,
    _depth: int = 0,
) -> HttpSample:
    """Single HTTP probe. Returns timings in milliseconds.

    Connection is not reused — every call pays DNS+TCP+TLS to mirror what a
    cold S3 client / Lambda cold-start would see.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return HttpSample(sample_idx, None, None, None, None, None, None, None,
                          f"unsupported scheme: {parsed.scheme}")
    host = parsed.hostname
    if not host:
        return HttpSample(sample_idx, None, None, None, None, None, None, None,
                          "no hostname in URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    use_tls = parsed.scheme == "https"
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    resolved_ip: str | None = None
    overall_start = time.monotonic()

    # --- DNS (skipped if force_ip is set) ---
    if force_ip is not None:
        dns_ms = 0.0
        resolved_ip = force_ip
        try:
            socket.inet_pton(socket.AF_INET6, force_ip)
            family = socket.AF_INET6
            sockaddr: tuple = (force_ip, port, 0, 0)
        except OSError:
            family = socket.AF_INET
            sockaddr = (force_ip, port)
        sock_type = socket.SOCK_STREAM
        proto = 0
    else:
        try:
            t0 = time.monotonic()
            addrs = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            dns_ms = _ms(t0)
            family, sock_type, proto, _, sockaddr = addrs[0]
            resolved_ip = sockaddr[0]
        except socket.gaierror as e:
            return HttpSample(sample_idx, None, None, None, None, _ms(overall_start),
                              None, None, f"dns: {e}")

    sock = socket.socket(family, sock_type, proto)
    sock.settimeout(timeout)
    ssock: socket.socket | ssl.SSLSocket = sock

    try:
        # --- TCP connect ---
        try:
            t0 = time.monotonic()
            sock.connect(sockaddr)
            tcp_ms = _ms(t0)
        except (socket.timeout, OSError) as e:
            return HttpSample(sample_idx, dns_ms, None, None, None, _ms(overall_start),
                              None, resolved_ip, f"tcp: {e}")

        # --- TLS handshake ---
        tls_ms: float | None = None
        tls_meta: dict = {}
        if use_tls:
            try:
                ctx = ssl.create_default_context()
                t0 = time.monotonic()
                ssock = ctx.wrap_socket(sock, server_hostname=host)
                tls_ms = _ms(t0)
                # Capture TLS info (best-effort, never blocks)
                try:
                    tls_meta["tls_version"] = ssock.version()
                    c = ssock.cipher()
                    if c:
                        tls_meta["tls_cipher"] = c[0]
                    cert = ssock.getpeercert()
                    if cert:
                        subj = dict((x[0] for x in cert.get("subject", ())))
                        issuer = dict((x[0] for x in cert.get("issuer", ())))
                        tls_meta["cert_subject_cn"] = subj.get("commonName")
                        tls_meta["cert_issuer_cn"] = issuer.get("commonName")
                        tls_meta["cert_not_after"] = cert.get("notAfter")
                        sans = cert.get("subjectAltName", ())
                        tls_meta["cert_san_count"] = len(sans)
                except Exception:
                    pass
            except (ssl.SSLError, socket.timeout, OSError) as e:
                return HttpSample(sample_idx, dns_ms, tcp_ms, None, None,
                                  _ms(overall_start), None, resolved_ip, f"tls: {e}")

        # --- HTTP request + TTFB ---
        body_bytes = b""
        if body is not None:
            body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        lines = [
            f"{method} {path} HTTP/1.1",
            f"Host: {host}",
            f"User-Agent: {user_agent}",
            "Accept: */*",
            "Connection: close",
        ]
        if body_bytes:
            lines.append(f"Content-Length: {len(body_bytes)}")
        if extra_headers:
            for k, v in extra_headers.items():
                # Skip headers we already control
                if k.lower() in ("host", "user-agent", "connection", "content-length"):
                    continue
                lines.append(f"{k}: {v}")
        req = "\r\n".join(lines) + "\r\n\r\n"
        # ── Send request + read response (status line + headers) ──
        resp_headers: dict[str, str] = {}
        try:
            t0 = time.monotonic()
            ssock.sendall(req.encode("ascii") + body_bytes)
            first = ssock.recv(1)
            ttfb_ms = _ms(t0)
            if not first:
                return HttpSample(sample_idx, dns_ms, tcp_ms, tls_ms, ttfb_ms,
                                  _ms(overall_start), None, resolved_ip,
                                  "empty response")
            buf = bytearray(first)
            # Read until end of headers (CRLFCRLF) or 32 KB
            while b"\r\n\r\n" not in buf and len(buf) < 32768:
                chunk = ssock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
            head_end = buf.find(b"\r\n\r\n")
            head_bytes = bytes(buf[:head_end] if head_end >= 0 else buf)
            lines = head_bytes.split(b"\r\n")
            status_line = lines[0].decode("iso-8859-1", "replace")
            parts = status_line.split(" ", 2)
            status = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
            set_cookies: list[str] = []
            for line in lines[1:]:
                if b":" in line:
                    k, _, v = line.partition(b":")
                    name = k.decode("ascii", "replace").lower().strip()
                    val = v.decode("iso-8859-1", "replace").strip()
                    if name == "set-cookie":
                        set_cookies.append(val)
                    else:
                        resp_headers[name] = val
        except (socket.timeout, OSError) as e:
            return HttpSample(sample_idx, dns_ms, tcp_ms, tls_ms, None,
                              _ms(overall_start), None, resolved_ip, f"http: {e}")

        # ── Build sample with all captured response metadata ──
        cl_raw = resp_headers.get("content-length")
        try:
            content_length = int(cl_raw) if cl_raw is not None else None
        except ValueError:
            content_length = None
        sample = HttpSample(
            sample_idx, dns_ms, tcp_ms, tls_ms, ttfb_ms,
            _ms(overall_start), status, resolved_ip, None,
            tls_version=tls_meta.get("tls_version"),
            tls_cipher=tls_meta.get("tls_cipher"),
            cert_subject_cn=tls_meta.get("cert_subject_cn"),
            cert_issuer_cn=tls_meta.get("cert_issuer_cn"),
            cert_not_after=tls_meta.get("cert_not_after"),
            cert_san_count=tls_meta.get("cert_san_count"),
            content_length=content_length,
            content_type=resp_headers.get("content-type"),
            content_encoding=resp_headers.get("content-encoding"),
            server=resp_headers.get("server"),
            cache_status=resp_headers.get("x-cache") or resp_headers.get("cf-cache-status"),
            response_headers=resp_headers,
            set_cookie_headers=set_cookies or None,
        )

        # ── Follow redirects if asked and status is 3xx + has Location ──
        if (follow_redirects and status is not None and 300 <= status < 400
                and resp_headers.get("location") and _depth < max_redirects):
            chain = list(_redirect_chain or [])
            chain.append({"status": status, "location": resp_headers["location"]})
            next_url = resp_headers["location"]
            # Resolve relative redirect
            if next_url.startswith("/"):
                p = urlparse(url)
                next_url = f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "") + next_url
            elif not next_url.startswith(("http://", "https://")):
                next_url = url.rsplit("/", 1)[0] + "/" + next_url
            next_sample = probe_once(
                next_url, method=method, timeout=timeout,
                sample_idx=sample_idx, user_agent=user_agent,
                follow_redirects=True, max_redirects=max_redirects,
                _redirect_chain=chain, _depth=_depth + 1,
            )
            # Propagate redirect info to the final sample
            next_sample.redirect_chain = chain
            next_sample.final_url = next_url
            return next_sample

        return sample
    finally:
        try:
            ssock.close()
        except OSError:
            pass


def probe_many(
    url: str,
    count: int = 10,
    method: str = "HEAD",
    timeout: float = 10.0,
    interval: float = 0.5,
    force_ip: str | None = None,
    follow_redirects: bool = False,
) -> list[HttpSample]:
    out = []
    for i in range(count):
        out.append(probe_once(
            url, method=method, timeout=timeout,
            sample_idx=i + 1, force_ip=force_ip,
            follow_redirects=follow_redirects,
        ))
        if i + 1 < count:
            time.sleep(interval)
    return out


def aggregate(samples: list[HttpSample]) -> dict:
    """Return aggregated stats and status-code counts."""
    def stats(vals: list[float | None]) -> dict:
        clean = [v for v in vals if v is not None]
        if not clean:
            return {"avg": None, "best": None, "worst": None, "stddev": None, "n": 0}
        n = len(clean)
        avg = sum(clean) / n
        var = sum((x - avg) ** 2 for x in clean) / n
        return {
            "avg": avg, "best": min(clean), "worst": max(clean),
            "stddev": var ** 0.5, "n": n,
        }

    status_counts: dict[int | None, int] = {}
    for s in samples:
        status_counts[s.status] = status_counts.get(s.status, 0) + 1

    errors = sum(1 for s in samples if s.error)

    return {
        "dns": stats([s.dns_ms for s in samples]),
        "tcp": stats([s.tcp_ms for s in samples]),
        "tls": stats([s.tls_ms for s in samples]),
        "ttfb": stats([s.ttfb_ms for s in samples]),
        "total": stats([s.total_ms for s in samples]),
        "status_counts": status_counts,
        "errors": errors,
        "samples": len(samples),
    }


def status_summary(status_counts: dict[int | None, int]) -> str:
    """e.g. '200:28,503:2,err:1'"""
    parts = []
    for k, v in sorted(status_counts.items(), key=lambda kv: (kv[0] is None, kv[0])):
        if k is None:
            parts.append(f"err:{v}")
        else:
            parts.append(f"{k}:{v}")
    return ",".join(parts) or "-"
