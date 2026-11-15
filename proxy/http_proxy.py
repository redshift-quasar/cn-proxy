"""Restricted HTTP forward proxy: GET/HEAD, explicit configured backend only.

One request per connection. No CONNECT, request bodies, or TLS interception.
The existing custom TCP protocol remains available on its original listener.
"""
from contextlib import closing
import http.client
import re
import socket
import time
from urllib.parse import urlsplit

try:
    from .classifier import inspect as inspect_content
except ImportError:
    from classifier import inspect as inspect_content

MAX_RESPONSE_BYTES = 1024 * 1024
HOP_HEADERS = {'connection', 'proxy-connection', 'keep-alive', 'te', 'trailer',
               'transfer-encoding', 'upgrade', 'proxy-authorization',
               'proxy-authenticate'}
TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class RequestError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def reply(conn, status, body, head=False, headers=(), content_length=None):
    if isinstance(body, str):
        body = body.encode('utf-8')
    reason = http.client.responses.get(status, 'Error')
    # 204 and 304 have no message body; do not manufacture a length for them.
    no_body = status in {204, 304}
    lines = [f'HTTP/1.1 {status} {reason}', 'Connection: close']
    if not no_body:
        lines.append(f'Content-Length: {len(body) if content_length is None else content_length}')
    lines += [f'{key}: {value}' for key, value in headers]
    if not any(key.lower() == 'content-type' for key, _ in headers):
        lines.append('Content-Type: text/plain; charset=utf-8')
    conn.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode('latin-1')
                 + (b'' if head or no_body else body))


def read_request(conn, settings):
    deadline = time.monotonic() + settings['request_timeout_seconds']
    data = b''
    limit = settings['max_header_bytes']
    while b'\r\n\r\n' not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout()
        conn.settimeout(remaining)
        chunk = conn.recv(min(4096, limit + 1 - len(data)))
        if not chunk:
            raise RequestError(400, 'Incomplete request')
        data += chunk
        if len(data) > limit:
            raise RequestError(431, 'Request headers too large')
    header, extra = data.split(b'\r\n\r\n', 1)
    if extra:
        raise RequestError(400, 'Request bodies and pipelining are not supported')
    lines = header.decode('latin-1').split('\r\n')
    fields = lines[0].split(' ')
    if len(fields) != 3 or fields[2] not in {'HTTP/1.0', 'HTTP/1.1'}:
        raise RequestError(400, 'Malformed request line')
    method, target, version = fields
    if method not in {'GET', 'HEAD'}:
        raise RequestError(405, 'Only GET and HEAD are supported; CONNECT is unavailable')
    headers = {}
    for line in lines[1:]:
        key, sep, value = line.partition(':')
        if not sep or not TOKEN.fullmatch(key) or any(ord(c) < 32 and c != '\t' or ord(c) == 127 for c in value):
            raise RequestError(400, 'Malformed header')
        key = key.lower()
        if key in headers:
            raise RequestError(400, 'Duplicate headers are not supported')
        headers[key] = value.strip()
    if version == 'HTTP/1.1' and not headers.get('host'):
        raise RequestError(400, 'Host header required')
    if 'transfer-encoding' in headers or headers.get('content-length', '0') != '0':
        raise RequestError(400, 'Request bodies are not supported')
    if any(ord(c) <= 32 or ord(c) >= 127 for c in target):
        raise RequestError(400, 'Invalid request target')
    return method, target, headers


def handle_http(conn, address, policy, server_host, protection, logger):
    ip = address[0]
    head = False
    request_complete = False
    with conn:
        try:
            method, target, headers = read_request(conn, protection.settings())
            request_complete = True
            head = method == 'HEAD'
            try:
                url = urlsplit(target)
                port = url.port if url.port is not None else 80
                valid = (url.scheme == 'http' and url.hostname == server_host
                         and url.username is None and url.password is None and not url.fragment)
            except ValueError:
                raise RequestError(400, 'Malformed destination URL') from None
            if not valid:
                raise RequestError(403, 'Destination is outside the configured backend')
            rules, settings = policy.snapshot()
            services = [service for service, p in rules if p == port]
            if len(services) != 1:
                raise RequestError(403, 'Destination must match one explicitly configured service')
            service = services[0]
            action = rules[(service, port)]
            if action == 'DENY':
                reply(conn, 403, 'DENIED: service policy', head)
                logger.info('HTTP_DENY | client=%s | service=%s', ip, service)
                return
            connection_tokens = {x.strip().lower() for x in headers.get('connection', '').split(',')}
            forwarded = {k: v for k, v in headers.items()
                         if k not in HOP_HEADERS | connection_tokens | {'host', 'content-length', 'accept-encoding'}}
            forwarded.update({'Host': f'{server_host}:{port}', 'Connection': 'close',
                              'Accept-Encoding': 'identity'})
            path = url.path or '/'
            if url.query:
                path += '?' + url.query
            timeout = settings['protection']['request_timeout_seconds']
            with closing(http.client.HTTPConnection(server_host, port, timeout=timeout)) as upstream:
                upstream.request(method, path, headers=forwarded)
                response = upstream.getresponse()
                if response.status < 200:
                    raise RequestError(502, 'Unsupported informational backend response')
                response_headers = response.getheaders()
                lengths = response.headers.get_all('Content-Length', [])
                if (len(lengths) > 1 or (lengths and not re.fullmatch(r'[0-9]+', lengths[0]))
                        or (lengths and response.getheader('Transfer-Encoding'))):
                    raise RequestError(502, 'Ambiguous backend response framing')
                transfer = response.getheader('Transfer-Encoding', '').lower()
                if transfer and transfer != 'chunked':
                    raise RequestError(502, 'Unsupported backend transfer encoding')
                expected = response.length
                deadline = time.monotonic() + timeout
                chunks, size = [], 0
                while response.fp is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise socket.timeout()
                    # HTTPConnection can detach its socket when Connection: close.
                    response.fp.raw._sock.settimeout(remaining)
                    chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RequestError(502, 'Backend response exceeds proxy limit')
                body = b''.join(chunks)
                if expected is not None and size != expected:
                    raise RequestError(502, 'Truncated backend response')
                status = response.status
            if action == 'AUTO' and not head:
                if status == 206 or response.getheader('Content-Range'):
                    raise RequestError(502, 'Partial responses cannot be inspected safely')
                encoding = response.getheader('Content-Encoding', 'identity').lower()
                if encoding != 'identity':
                    raise RequestError(502, 'Encoded response cannot be inspected')
                try:
                    text = body.decode('utf-8')
                except UnicodeDecodeError:
                    raise RequestError(502, 'AUTO inspection requires UTF-8 content') from None
                verdict = inspect_content(text, settings['on_sensitive'])
                if verdict.action == 'BLOCK':
                    reply(conn, 403, 'BLOCKED: sensitive content detected', head)
                    logger.warning('HTTP_AUTO_BLOCK | client=%s | service=%s | found=%s', ip, service, verdict.labels)
                    return
                body = verdict.text.encode('utf-8')
                logger.info('HTTP_INSPECT | client=%s | service=%s | verdict=%s', ip, service, verdict.action)
            connection_tokens = {x.strip().lower() for x in response.getheader('Connection', '').split(',')}
            excluded = HOP_HEADERS | connection_tokens | {'content-length'}
            if action == 'AUTO':
                excluded |= {'etag', 'content-md5', 'digest', 'content-encoding'}
            if any(not TOKEN.fullmatch(k) or any(ord(c) < 32 and c != '\t' or ord(c) == 127 for c in v)
                   for k, v in response_headers):
                raise RequestError(502, 'Malformed backend response header')
            outgoing = [(k, v) for k, v in response_headers if k.lower() not in excluded]
            head_length = response.getheader("Content-Length") if head else None
            if head_length is not None and not head_length.isdigit():
                raise RequestError(502, "Invalid backend content length")
            reply(conn, status, body, head, outgoing, head_length)
            logger.info('HTTP_FORWARD | client=%s | service=%s | action=%s | status=%s', ip, service, action, status)
        except RequestError as exc:
            if exc.status in {400, 403, 405, 431}:
                protection.violation(ip)
            logger.warning('HTTP_REJECT | client=%s | status=%s | reason=%s', ip, exc.status, exc.message)
            try:
                reply(conn, exc.status, exc.message, head)
            except OSError:
                pass
        except socket.timeout:
            # A slow backend is not misconduct by the requesting client.
            if not request_complete:
                protection.violation(ip)
            logger.warning('HTTP_TIMEOUT | client=%s | phase=%s', ip,
                           'backend' if request_complete else 'request')
            try:
                reply(conn, 504, 'Request or backend timed out', head)
            except OSError:
                pass
        except (OSError, http.client.HTTPException) as exc:
            logger.warning('HTTP_BACKEND_ERROR | client=%s | error=%s', ip, exc)
            try:
                reply(conn, 502, 'Backend unavailable or invalid HTTP response', head)
            except OSError:
                pass
