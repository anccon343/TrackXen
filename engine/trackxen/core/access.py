"""Optional PIN for the control page (Settings -> System), off until it's switched on.

When it's on, every device except this PC itself (opened as localhost or 127.0.0.1, unless "ask on
this PC too" is ticked) enters the PIN once and then keeps a random sign-in token in a cookie. Bitfocus Companion and other
tools send the PIN with every request in an X-TrackXen-PIN header instead.

The PIN is kept only as a salted PBKDF2 hash, in access.json next to the settings: never in
settings.json, so it can't leak through the settings API or a saved show file. Sign-in tokens
are kept hashed too. Five wrong PINs from one address make it wait 30 s, doubling each time up
to 15 minutes; every wrong PIN goes to the log.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path

COOKIE = "trackxen_session"
HEADER = "x-trackxen-pin"
ITERATIONS = 200_000  # PBKDF2-SHA256 rounds: ~0.1 s per check, slow for guessing
FREE_TRIES = 5
FIRST_WAIT, LONGEST_WAIT = 30.0, 900.0
HEADER_REMEMBER_S = 600.0  # a tool's correct header PIN isn't re-hashed on every button press
LOCAL = {"127.0.0.1", "::1"}


def valid_pin(pin: object) -> bool:
    return isinstance(pin, str) and pin.isdigit() and 4 <= len(pin) <= 8


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AccessGuard:
    def __init__(self, path: Path, log: Callable[[str, str], None] | None = None):
        self.path = path
        self.log = log or (lambda level, message: None)
        self._pin: dict | None = None  # {"salt", "hash", "iterations"} (hex)
        self._tokens: dict[str, float] = {}  # sha256 of a device's token -> when it signed in
        self.ask_local = False
        self._failures: dict[str, tuple[int, float]] = {}  # address -> (wrong PINs in a row, wait until)
        self._header_ok: dict[str, float] = {}  # sha256 of a correct header PIN -> when checked
        self._lock = threading.Lock()  # PINs are checked in several threads at once
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self._pin = data.get("pin")
            self._tokens = dict(data.get("tokens", {}))
            self.ask_local = bool(data.get("ask_local", False))

    @property
    def enabled(self) -> bool:
        return self._pin is not None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"pin": self._pin, "tokens": self._tokens, "ask_local": self.ask_local}, indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)

    # --- who may in ------------------------------------------------------------------------

    def needs_pin(self, address: str) -> bool:
        return self.enabled and (self.ask_local or address not in LOCAL)

    def signed_in(self, cookie: str | None) -> bool:
        return bool(cookie) and _digest(cookie) in self._tokens

    def allowed(self, address: str, cookie: str | None = None, header_pin: str | None = None) -> bool:
        """May this request in? A header PIN counts like a typed one (wrong ones make it wait)."""
        if not self.needs_pin(address) or self.signed_in(cookie):
            return True
        if header_pin is None:
            return False
        key = _digest(f"{self._pin['salt']}:{header_pin}")
        if time.monotonic() - self._header_ok.get(key, -HEADER_REMEMBER_S) < HEADER_REMEMBER_S:
            return True
        if self.check_pin(address, header_pin):
            self._header_ok[key] = time.monotonic()
            return True
        return False

    def wait_left(self, address: str) -> float:
        return max(0.0, self._failures.get(address, (0, 0.0))[1] - time.monotonic())

    def check_pin(self, address: str, pin: object) -> bool:
        """Compare a PIN (constant time); a wrong one counts towards the wait. Takes ~0.1 s.
        The try is counted before the slow comparison, so PINs sent all at once wait after five too."""
        with self._lock:
            if not self.enabled or self.wait_left(address) > 0:
                return False
            p = self._pin
            tries = self._failures.get(address, (0, 0.0))[0] + 1
            wait = min(LONGEST_WAIT, FIRST_WAIT * 2 ** (tries - FREE_TRIES)) if tries >= FREE_TRIES else 0.0
            self._failures[address] = (tries, time.monotonic() + wait)
        if valid_pin(pin) and hmac.compare_digest(
                hashlib.pbkdf2_hmac("sha256", str(pin).encode(), bytes.fromhex(p["salt"]), p["iterations"]).hex(),
                p["hash"]):
            with self._lock:
                self._failures.pop(address, None)
            return True
        self.log("warning", f"wrong PIN from {address}" + (f"; it has to wait {wait:.0f} s" if wait else ""))
        return False

    # --- signing in and out ------------------------------------------------------------------

    def sign_in(self, address: str, pin: object) -> str | None:
        """A new token for the device's cookie, or None for a wrong PIN."""
        if not self.check_pin(address, pin):
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[_digest(token)] = time.time()
            self._save()
        self.log("info", f"signed in with the PIN: {address}")
        return token

    def sign_out(self, cookie: str | None) -> None:
        with self._lock:
            if cookie and self._tokens.pop(_digest(cookie), None) is not None:
                self._save()

    def forget_devices(self, keep: str | None = None) -> int:
        """Sign out every device (except the one with token `keep`); returns how many."""
        with self._lock:
            kept = {k: v for k, v in self._tokens.items() if keep and k == _digest(keep)}
            gone = len(self._tokens) - len(kept)
            self._tokens = kept
            self._save()
        self.log("info", f"signed out {gone} device(s)")
        return gone

    def set_pin(self, new: str | None, ask_local: bool = False) -> str | None:
        """Switch the PIN on or change it (every device then signs in again), or off (None).
        Returns a token for the device that made the change, so it stays signed in."""
        if new is not None and not valid_pin(new):
            raise ValueError("the PIN must be 4 to 8 digits")
        if new is None:
            with self._lock:
                self._tokens, self._header_ok = {}, {}
                self._pin, self.ask_local = None, False
                self._save()
            self.log("info", "PIN switched off")
            return None
        salt = secrets.token_bytes(16)
        pin = {"salt": salt.hex(), "iterations": ITERATIONS,
               "hash": hashlib.pbkdf2_hmac("sha256", new.encode(), salt, ITERATIONS).hex()}
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._pin, self.ask_local = pin, ask_local
            self._tokens, self._header_ok = {_digest(token): time.time()}, {}
            self._save()
        self.log("info", "PIN set")
        return token

    def set_ask_local(self, on: bool) -> None:
        with self._lock:
            self.ask_local = bool(on) and self.enabled
            self._save()

    def status(self, address: str, cookie: str | None) -> dict:
        return {"enabled": self.enabled, "local": address in LOCAL, "ask_local": self.ask_local,
                "signed_in": self.signed_in(cookie), "devices": len(self._tokens),
                "needs_pin": self.needs_pin(address) and not self.signed_in(cookie),
                "wait_s": round(self.wait_left(address))}
