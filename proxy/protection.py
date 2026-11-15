"""Bounded, thread-safe per-source-IP admission shared by both listeners."""
from collections import deque
from dataclasses import dataclass, field
import threading
import time

DEFAULTS = {
    'requests_per_window': 20, 'window_seconds': 10,
    'max_connections_per_client': 5, 'max_connections_total': 100,
    'max_tracked_clients': 4096, 'block_after_violations': 5,
    'block_seconds': 30, 'request_timeout_seconds': 5,
    'max_header_bytes': 16384,
}


def parse_settings(value):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('protection must contain only documented settings')
    result = dict(DEFAULTS)
    for key, number in value.items():
        if type(number) is not int or not 1 <= number <= 1000000:
            raise ValueError(f'protection.{key} must be a positive integer <= 1000000')
        result[key] = number
    return result


@dataclass
class Client:
    requests: deque = field(default_factory=deque)
    active: int = 0
    violations: int = 0
    blocked_until: float = 0
    touched: float = 0


class Protection:
    def __init__(self, policy, clock=time.monotonic):
        self.policy, self.clock = policy, clock
        self.lock = threading.Lock()
        self.clients = {}
        self.active = 0

    def settings(self):
        return self.policy.snapshot()[1]['protection']

    def _strike(self, client, now, settings):
        client.violations += 1
        if client.violations >= settings['block_after_violations']:
            client.blocked_until = now + settings['block_seconds']
            client.violations = 0

    def admit(self, ip):
        settings, now = self.settings(), self.clock()
        with self.lock:
            # Prune idle state; never evict active connections or live bans.
            for key, item in list(self.clients.items()):
                if (not item.active and item.blocked_until <= now
                        and now - item.touched > max(settings['window_seconds'], settings['block_seconds'])):
                    del self.clients[key]
            if ip not in self.clients:
                if len(self.clients) >= settings['max_tracked_clients']:
                    return 'client capacity reached'
                self.clients[ip] = Client(touched=now)
            client = self.clients[ip]
            client.touched = now
            if now < client.blocked_until:
                return 'temporarily blocked'
            while client.requests and client.requests[0] <= now - settings['window_seconds']:
                client.requests.popleft()
            if client.active >= settings['max_connections_per_client']:
                self._strike(client, now, settings)
                return 'too many concurrent connections'
            if len(client.requests) >= settings['requests_per_window']:
                self._strike(client, now, settings)
                return 'request rate exceeded'
            if self.active >= settings['max_connections_total']:
                return 'proxy connection capacity reached'
            client.requests.append(now)
            client.active += 1
            self.active += 1
            return None

    def release(self, ip):
        with self.lock:
            self.clients[ip].active -= 1
            self.active -= 1

    def violation(self, ip):
        settings, now = self.settings(), self.clock()
        with self.lock:
            if ip in self.clients:
                self._strike(self.clients[ip], now, settings)
