"""Bounded process-local single-use dispatch capabilities; never serialize grants."""
import secrets
import threading
import time
from collections import OrderedDict


class CapabilityStore:
    def __init__(self, *, max_entries=1024, ttl=300):
        self._entries = OrderedDict()
        self._lock = threading.Lock()
        self.max_entries, self.ttl = max_entries, ttl

    def issue(self, session_id, invocation_key, args_hash, policy_hash) -> str:
        nonce = secrets.token_hex(16)
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            self._entries[nonce] = (session_id, invocation_key, args_hash, policy_hash, now + self.ttl)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return nonce

    def _expire(self, now):
        for nonce, entry in list(self._entries.items()):
            if entry[-1] <= now:
                del self._entries[nonce]

    def consume(self, nonce, session_id, invocation_key, args_hash, *, policy_hash=None) -> bool:
        with self._lock:
            self._expire(time.monotonic())
            entry = self._entries.get(nonce)
            if entry is None or entry[:3] != (session_id, invocation_key, args_hash):
                return False
            if policy_hash is not None and entry[3] != policy_hash:
                return False
            del self._entries[nonce]
            return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

