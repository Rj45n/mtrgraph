"""S3 client with AWS SigV4 signing (stdlib only).

Compatible with any S3-compatible storage: AWS, MinIO, Scaleway, OVH Cloud
Object Storage, Clever Cloud Cellar, Free Pro, etc.

For full IAM/STS/SSO support, install boto3 and use the BotoClient class.
"""
from __future__ import annotations

import hashlib
import hmac
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlparse

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@dataclass
class S3Result:
    operation: str
    endpoint: str
    bucket: str | None
    key: str | None
    label: str | None

    duration_ms: float | None = None
    http_status: int | None = None
    bytes_transferred: int = 0
    error: str | None = None
    response_summary: str | None = None
    resolved_ip: str | None = None

    # Per-stage timings (cold start = no keep-alive)
    dns_ms: float | None = None
    tcp_ms: float | None = None
    tls_ms: float | None = None
    ttfb_ms: float | None = None

    headers: dict = field(default_factory=dict)


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    k_date = _sign(("AWS4" + secret).encode("utf-8"), datestamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


def _canonical_uri(parsed_url) -> str:
    """Canonical URI for SigV4. Each path segment URL-encoded, slashes preserved."""
    path = parsed_url.path or "/"
    # S3 SigV4 spec: encode each path segment but keep '/' separators.
    return "/" + "/".join(quote(seg, safe="~") for seg in path.split("/")[1:])


def _canonical_query(parsed_url) -> str:
    """Canonical query string per SigV4: decode any pre-existing percent-encoding
    in the URL, then re-encode strictly per the SigV4 rules (no double-encoding).
    """
    if not parsed_url.query:
        return ""
    pairs = []
    for kv in parsed_url.query.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
        else:
            k, v = kv, ""
        # Unquote first to get the raw value, then quote per SigV4 (no safe chars
        # other than the RFC 3986 unreserved set, which `quote` keeps by default
        # plus '~' which we add explicitly).
        pairs.append((quote(unquote(k), safe="~"), quote(unquote(v), safe="~")))
    pairs.sort()
    return "&".join(f"{k}={v}" for k, v in pairs)


def sign_request(
    method: str,
    url: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str = "s3",
    body: bytes = b"",
    extra_headers: dict | None = None,
    session_token: str | None = None,
) -> dict:
    """Return the headers (incl. Authorization) for a SigV4-signed request."""
    if not access_key or not secret_key:
        raise ValueError("access_key and secret_key are required")
    # Defensive: strip whitespace (tab/space/newline) from credentials.
    # Copy-paste from terminals can inject leading/trailing whitespace which
    # silently corrupts SigV4 signing.
    access_key = access_key.strip()
    secret_key = secret_key.strip()
    region = (region or "").strip()
    if session_token:
        session_token = session_token.strip()
    parsed = urlparse(url)
    if not parsed.scheme:
        raise ValueError(f"URL must include scheme (https://): got {url!r}")
    if not parsed.hostname:
        raise ValueError(f"URL has no hostname: got {url!r}")
    host = parsed.hostname
    if parsed.port and parsed.port not in (80, 443):
        host = f"{host}:{parsed.port}"

    now = datetime.now(timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")

    payload_hash = hashlib.sha256(body).hexdigest()

    headers: dict[str, str] = {
        "host": host,
        "x-amz-date": amzdate,
        "x-amz-content-sha256": payload_hash,
    }
    if session_token:
        headers["x-amz-security-token"] = session_token
    if extra_headers:
        for k, v in extra_headers.items():
            headers[k.lower()] = v

    canonical_uri = _canonical_uri(parsed)
    canonical_qs = _canonical_query(parsed)

    signed_header_names = sorted(headers.keys())
    canonical_headers = "".join(f"{k}:{headers[k].strip()}\n" for k in signed_header_names)
    signed_headers = ";".join(signed_header_names)

    canonical_request = "\n".join([
        method.upper(),
        canonical_uri,
        canonical_qs,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    credential_scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amzdate,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    signing_key = _signing_key(secret_key, datestamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers["authorization"] = auth
    return headers


def _ms(start: float) -> float:
    return (time.monotonic() - start) * 1000.0


def _http_request(
    method: str,
    url: str,
    headers: dict,
    body: bytes = b"",
    timeout: float = 30.0,
    read_body: bool = False,
    max_body_bytes: int = 5 * 1024 * 1024,
) -> tuple[int, dict, bytes, dict[str, float], str | None]:
    """Send HTTP(S) request, return (status, headers, body, timings, error).

    timings keys: dns_ms, tcp_ms, tls_ms, ttfb_ms, resolved_ip
    """
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    use_tls = parsed.scheme == "https"
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    timings: dict = {"dns_ms": None, "tcp_ms": None, "tls_ms": None, "ttfb_ms": None, "resolved_ip": None}

    try:
        t0 = time.monotonic()
        addrs = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        timings["dns_ms"] = _ms(t0)
        family, sock_type, proto, _, sockaddr = addrs[0]
        timings["resolved_ip"] = sockaddr[0]
    except socket.gaierror as e:
        return 0, {}, b"", timings, f"dns: {e}"

    sock = socket.socket(family, sock_type, proto)
    sock.settimeout(timeout)
    ssock: socket.socket | ssl.SSLSocket = sock

    try:
        try:
            t0 = time.monotonic()
            sock.connect(sockaddr)
            timings["tcp_ms"] = _ms(t0)
        except (socket.timeout, OSError) as e:
            return 0, {}, b"", timings, f"tcp: {e}"

        if use_tls:
            try:
                ctx = ssl.create_default_context()
                t0 = time.monotonic()
                ssock = ctx.wrap_socket(sock, server_hostname=host)
                timings["tls_ms"] = _ms(t0)
            except (ssl.SSLError, socket.timeout, OSError) as e:
                return 0, {}, b"", timings, f"tls: {e}"

        # Build request
        lines = [f"{method.upper()} {path} HTTP/1.1"]
        if "content-length" not in {k.lower() for k in headers}:
            lines.append(f"Content-Length: {len(body)}")
        if "connection" not in {k.lower() for k in headers}:
            lines.append("Connection: close")
        if "user-agent" not in {k.lower() for k in headers}:
            lines.append("User-Agent: mtrgraph-s3/0.1")
        for k, v in headers.items():
            # Capitalize for nicer logs (HTTP is case-insensitive anyway).
            lines.append(f"{k.title()}: {v}")
        request_head = "\r\n".join(lines).encode("ascii") + b"\r\n\r\n"

        try:
            t0 = time.monotonic()
            ssock.sendall(request_head + body)
            first = ssock.recv(8192)
            timings["ttfb_ms"] = _ms(t0)
            if not first:
                return 0, {}, b"", timings, "empty response"

            # Read until end of headers
            buf = bytearray(first)
            while b"\r\n\r\n" not in buf:
                chunk = ssock.recv(8192)
                if not chunk:
                    break
                buf.extend(chunk)

            head_end = buf.index(b"\r\n\r\n")
            head_bytes = bytes(buf[:head_end])
            head_lines = head_bytes.split(b"\r\n")
            status_line = head_lines[0].decode("iso-8859-1", "replace")
            parts = status_line.split(" ", 2)
            status = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
            resp_headers: dict[str, str] = {}
            for hl in head_lines[1:]:
                if b":" in hl:
                    k, _, v = hl.partition(b":")
                    resp_headers[k.decode("ascii", "replace").lower()] = v.decode("iso-8859-1", "replace").strip()

            body_buf = bytes(buf[head_end + 4:])
            if read_body:
                while len(body_buf) < max_body_bytes:
                    chunk = ssock.recv(min(65536, max_body_bytes - len(body_buf)))
                    if not chunk:
                        break
                    body_buf += chunk

            return status, resp_headers, body_buf, timings, None
        except (socket.timeout, OSError) as e:
            return 0, {}, b"", timings, f"http: {e}"
    finally:
        try:
            ssock.close()
        except OSError:
            pass


def _parse_endpoint(endpoint: str, bucket: str | None, key: str | None, query: str = "") -> str:
    """Build the full URL. Path-style: https://endpoint/bucket/key?query

    Auto-prefixes https:// if no scheme. Raises ValueError on malformed input.
    """
    if not endpoint:
        raise ValueError("endpoint is required")
    endpoint = endpoint.strip()
    if "://" not in endpoint:
        endpoint = "https://" + endpoint
    endpoint = endpoint.rstrip("/")
    parts = [endpoint]
    if bucket:
        parts.append(bucket)
    if key:
        parts.append(key.lstrip("/"))
    url = "/".join(parts)
    if not bucket:
        url += "/"
    if query:
        url += "?" + query
    return url


def _summarize_xml(body: bytes, max_keys_shown: int = 5) -> str:
    """Quick & dirty XML summary for ListBucket response."""
    text = body.decode("utf-8", "replace")
    import re
    keys = re.findall(r"<Key>([^<]+)</Key>", text)
    is_truncated = "<IsTruncated>true</IsTruncated>" in text
    count = len(keys)
    sample = ", ".join(keys[:max_keys_shown])
    if count > max_keys_shown:
        sample += f", … +{count - max_keys_shown}"
    return (
        f"{count} keys{' (truncated)' if is_truncated else ''}"
        + (f" — {sample}" if sample else "")
    )


def _exec_s3(
    operation: str,
    method: str,
    endpoint: str,
    bucket: str | None,
    key: str | None,
    access_key: str,
    secret_key: str,
    region: str,
    body: bytes = b"",
    extra_headers: dict | None = None,
    query: str = "",
    session_token: str | None = None,
    timeout: float = 30.0,
    read_body: bool = False,
    label: str | None = None,
) -> S3Result:
    overall = time.monotonic()
    try:
        url = _parse_endpoint(endpoint, bucket, key, query)
    except ValueError as e:
        return S3Result(
            operation=operation, endpoint=endpoint, bucket=bucket, key=key,
            label=label, error=f"endpoint: {e}",
        )
    try:
        headers = sign_request(
            method, url, access_key, secret_key, region,
            body=body, extra_headers=extra_headers, session_token=session_token,
        )
    except ValueError as e:
        return S3Result(
            operation=operation, endpoint=endpoint, bucket=bucket, key=key,
            label=label, error=f"config: {e}",
        )
    except Exception as e:
        return S3Result(
            operation=operation, endpoint=endpoint, bucket=bucket, key=key,
            label=label, error=f"sign: {type(e).__name__}: {e}",
        )

    status, resp_headers, resp_body, timings, err = _http_request(
        method, url, headers, body=body, timeout=timeout, read_body=read_body,
    )
    duration = _ms(overall)

    result = S3Result(
        operation=operation, endpoint=endpoint, bucket=bucket, key=key, label=label,
        duration_ms=duration, http_status=status if status else None,
        bytes_transferred=len(resp_body) if method == "GET" else len(body),
        error=err,
        resolved_ip=timings.get("resolved_ip"),
        dns_ms=timings.get("dns_ms"), tcp_ms=timings.get("tcp_ms"),
        tls_ms=timings.get("tls_ms"), ttfb_ms=timings.get("ttfb_ms"),
        headers=resp_headers,
    )

    if err:
        return result

    if operation == "list" and status == 200:
        result.response_summary = _summarize_xml(resp_body)
    elif operation == "get" and status == 200:
        result.response_summary = (
            f"{len(resp_body)} bytes" +
            (f" · type={resp_headers.get('content-type','?')}" if resp_headers else "")
        )
    elif operation == "head" and status == 200:
        result.response_summary = (
            f"size={resp_headers.get('content-length','?')} · "
            f"etag={resp_headers.get('etag','?')}"
        )
    elif operation == "put" and 200 <= status < 300:
        result.response_summary = f"{len(body)} bytes uploaded · etag={resp_headers.get('etag','?')}"
    elif operation == "delete" and 200 <= status < 300:
        result.response_summary = "deleted"
    elif status >= 400:
        # Try to extract error code from XML
        import re
        m = re.search(rb"<Code>([^<]+)</Code>", resp_body)
        msg = re.search(rb"<Message>([^<]+)</Message>", resp_body)
        result.response_summary = (
            (m.group(1).decode("utf-8", "replace") if m else f"HTTP {status}")
            + (f": {msg.group(1).decode('utf-8', 'replace')}" if msg else "")
        )

    return result


# ─── High-level operations ────────────────────────────────────────────────


def list_bucket(endpoint, bucket, access_key, secret_key, region, prefix="", max_keys=1000, **kw):
    query = f"list-type=2&max-keys={max_keys}"
    if prefix:
        query += f"&prefix={quote(prefix, safe='')}"
    return _exec_s3(
        "list", "GET", endpoint, bucket, None,
        access_key, secret_key, region,
        query=query, read_body=True, **kw,
    )


def head_object(endpoint, bucket, key, access_key, secret_key, region, **kw):
    return _exec_s3(
        "head", "HEAD", endpoint, bucket, key,
        access_key, secret_key, region, **kw,
    )


def get_object(endpoint, bucket, key, access_key, secret_key, region, **kw):
    return _exec_s3(
        "get", "GET", endpoint, bucket, key,
        access_key, secret_key, region, read_body=True, **kw,
    )


def put_object(endpoint, bucket, key, body: bytes, access_key, secret_key, region,
               content_type="application/octet-stream", **kw):
    return _exec_s3(
        "put", "PUT", endpoint, bucket, key,
        access_key, secret_key, region,
        body=body, extra_headers={"Content-Type": content_type}, **kw,
    )


def delete_object(endpoint, bucket, key, access_key, secret_key, region, **kw):
    return _exec_s3(
        "delete", "DELETE", endpoint, bucket, key,
        access_key, secret_key, region, **kw,
    )
