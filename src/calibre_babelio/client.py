"""HTTP client and anti-bot layer for Babelio."""

from __future__ import annotations

import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .errors import (
    _LOGIN_FAILED_MESSAGE,
    _TOKEN_EXPIRED_MESSAGE,
    BabelioBlocked,
    BabelioLoginFailed,
    CircuitBreakerOpen,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_BASE_URL = "https://www.babelio.com"
_LOGIN_URL = f"{_BASE_URL}/connection.php"
_LOGIN_POST_URL = f"{_LOGIN_URL}?r=1"
_ACCEPT_LANGUAGE = "fr-FR,fr;q=0.9"
_COOKIE_NAME = "jstsToken"
_COOKIE_DOMAIN = "www.babelio.com"
_BODY_ENCODING = "iso-8859-1"

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_DEFAULT_MIN_INTERVAL = 1.2
_DEFAULT_SEARCH_INTERVAL = 0.0
_DEFAULT_BLOCK_THRESHOLD = 3
_DEFAULT_COOLDOWN = 23 * 3600.0
_CIRCUIT_LOCKFILE_NAME = "calibre_babelio_circuit_breaker.lock"

_HTTP_FORBIDDEN = 403

# Babelio returns HTTP 200 on failed login with `<p class="auth-error" role="alert">...`.
_LOGIN_ERROR_RE = re.compile(rb'class=["\'][^"\']*\bauth-error\b')

# Amazon image URLs encode a size in a modifier between the id and extension, e.g.
# `...._SX318_BO1,204,203,200_.jpg`; dropping it yields the full-resolution original.
_AMAZON_SIZE_RE = re.compile(r"\._S[XY]\d+_.*?(\.[A-Za-z]+)$")


def _full_resolution_url(url: str) -> str:
    """Strip an Amazon image size modifier for full-res. No-op on Babelio `/couv/` URLs."""
    return _AMAZON_SIZE_RE.sub(r"\1", url)


# mechanize's Browser is not thread-safe and Calibre hands every client the same per-plugin
# browser, so the request lock must be process-wide: a per-client lock would let workers
# surviving an aborted call race the next call's client through the same browser.
class _SharedRequestState:
    __slots__ = ("last_request_at", "last_search_at", "request_lock")

    def __init__(self) -> None:
        self.request_lock = threading.Lock()
        self.last_request_at = 0.0
        self.last_search_at: float | None = None


_SHARED_REQUEST_STATE = _SharedRequestState()


class _Response(Protocol):
    def read(self) -> bytes: ...


class BrowserProtocol(Protocol):
    def set_user_agent(self, newval: str) -> None: ...
    def set_header(self, header: str, value: str) -> None: ...
    def set_simple_cookie(self, name: str, value: str, domain: str, path: str = "/") -> None: ...
    def open(
        self,
        url: str,
        data: bytes | None = ...,
        timeout: float = ...,
        headers: Mapping[str, str] | None = ...,
    ) -> _Response: ...
    def geturl(self) -> str: ...


@dataclass(frozen=True, slots=True)
class FetchResult:
    body: bytes
    final_url: str


class ConnectionStatus(Enum):
    OK = "ok"
    AUTH_FAILED = "auth_failed"
    TOKEN_MISSING = "token_missing"
    TOKEN_EXPIRED = "token_expired"
    CIRCUIT_OPEN = "circuit_open"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ConnectionResult:
    status: ConnectionStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is ConnectionStatus.OK


class BabelioClient:
    def __init__(
        self,
        browser: BrowserProtocol,
        cookie: str,
        user_agent: str,
        *,
        username: str = "",
        password: str = "",
        min_interval: float = _DEFAULT_MIN_INTERVAL,
        search_interval: float = _DEFAULT_SEARCH_INTERVAL,
        lockfile_path: Path | None = None,
        block_threshold: int = _DEFAULT_BLOCK_THRESHOLD,
        cooldown: float = _DEFAULT_COOLDOWN,
        _clock: Callable[[], float] = time.monotonic,
        _sleep: Callable[[float], None] = time.sleep,
        _now_wall: Callable[[], float] = time.time,
        _shared: _SharedRequestState = _SHARED_REQUEST_STATE,
    ) -> None:
        self._browser = browser
        self._cookie = cookie.strip()
        self._user_agent = user_agent
        self._username = username.strip()
        self._password = password
        self._min_interval = min_interval
        self._search_interval = search_interval
        self._lockfile_path = lockfile_path or (
            Path(tempfile.gettempdir()) / _CIRCUIT_LOCKFILE_NAME
        )
        self._block_threshold = block_threshold
        self._cooldown = cooldown
        self._clock = _clock
        self._sleep = _sleep
        self._now_wall = _now_wall
        self._shared = _shared
        self._consecutive_blocks = 0
        self._logged_in = False
        self._lock = threading.Lock()
        self._setup_browser()

    def _setup_browser(self) -> None:
        self._browser.set_user_agent(self._user_agent)
        self._browser.set_header("Accept-Language", _ACCEPT_LANGUAGE)
        # Host-only on www.babelio.com, not broadened to .babelio.com.
        if self._cookie:
            self._browser.set_simple_cookie(_COOKIE_NAME, self._cookie, _COOKIE_DOMAIN)

    def _has_credentials(self) -> bool:
        return bool(self._username and self._password)

    def login(
        self,
        *,
        timeout: float = 30.0,
        record_blocks: bool = True,
        check_circuit: bool = True,
    ) -> None:
        if not self._has_credentials():
            return
        if check_circuit:
            self._check_circuit()
        with self._shared.request_lock:
            self._login_locked(timeout, record_blocks=record_blocks)
        self._on_success()

    def get_book_page(self, babelio_id: str, *, timeout: float = 30.0) -> FetchResult:
        return self._fetch(f"{_BASE_URL}/livres/{babelio_id}", None, {}, timeout)

    def get_author_bibliography(self, path: str, *, timeout: float = 30.0) -> FetchResult:
        return self._fetch(f"{_BASE_URL}{path}", None, {}, timeout)

    def search(
        self,
        terms: str,
        *,
        timeout: float = 30.0,
        record_blocks: bool = True,
        check_circuit: bool = True,
    ) -> FetchResult:
        data = urllib.parse.urlencode({"Recherche": terms}, encoding=_BODY_ENCODING).encode(
            _BODY_ENCODING
        )
        return self._fetch(
            f"{_BASE_URL}/recherche",
            data,
            {},
            timeout,
            record_blocks=record_blocks,
            check_circuit=check_circuit,
            is_search=True,
        )

    def fetch_image(self, url: str, *, timeout: float = 30.0) -> bytes:
        # Goes through the configured browser (cookie + UA), so Babelio-hosted /couv/ covers
        # behind the wall download too; the host-only cookie isn't sent to external CDNs.
        return self._fetch(_full_resolution_url(url), None, {}, timeout).body

    def get_full_summary(
        self, summary_type: int, obj_id: int, referer: str, *, timeout: float = 30.0
    ) -> FetchResult:
        data = urllib.parse.urlencode(
            {"type": summary_type, "id_obj": obj_id}, encoding=_BODY_ENCODING
        ).encode(_BODY_ENCODING)
        headers = {"X-Requested-With": "XMLHttpRequest", "Referer": referer}
        return self._fetch(f"{_BASE_URL}/aj_voir_plus_a.php", data, headers, timeout)

    def test_connection(self, *, timeout: float = 10.0) -> ConnectionResult:
        try:
            if self._has_credentials():
                self.login(timeout=timeout, record_blocks=False, check_circuit=False)
            self.search("test", timeout=timeout, record_blocks=False, check_circuit=False)
        except BabelioLoginFailed:
            return ConnectionResult(ConnectionStatus.AUTH_FAILED)
        except BabelioBlocked:
            return ConnectionResult(ConnectionStatus.TOKEN_EXPIRED)
        # Unreachable while the calls above pass check_circuit=False; kept defensively.
        except CircuitBreakerOpen as exc:  # pragma: no cover
            return ConnectionResult(ConnectionStatus.CIRCUIT_OPEN, str(exc))
        except Exception as exc:  # noqa: BLE001 - UI button must report any failure, never raise.
            return ConnectionResult(ConnectionStatus.ERROR, str(exc))
        return ConnectionResult(ConnectionStatus.OK)

    def _fetch(
        self,
        url: str,
        data: bytes | None,
        extra_headers: Mapping[str, str],
        timeout: float,
        *,
        record_blocks: bool = True,
        check_circuit: bool = True,
        is_search: bool = False,
    ) -> FetchResult:
        if check_circuit:
            self._check_circuit()
        with self._shared.request_lock:
            if self._has_credentials() and not self._logged_in and url.startswith(_BASE_URL):
                self._login_locked(timeout, record_blocks=record_blocks)
            result = self._open_raw(
                url, data, extra_headers, timeout, record_blocks=record_blocks, is_search=is_search
            )
        self._on_success()
        return result

    def _login_locked(self, timeout: float, *, record_blocks: bool) -> None:
        # Caller holds `self._shared.request_lock`.
        if self._logged_in:
            return
        # Prime the PHPSESSID cookie on the login page before submitting credentials.
        self._open_raw(_LOGIN_URL, None, {}, timeout, record_blocks=record_blocks)
        payload = urllib.parse.urlencode(
            {
                "Login": self._username,
                "Password": self._password,
                "sub_btn": "connexion",
                "ref": "",
            },
            encoding=_BODY_ENCODING,
        ).encode(_BODY_ENCODING)
        result = self._open_raw(
            _LOGIN_POST_URL,
            payload,
            {"Referer": _LOGIN_URL},
            timeout,
            record_blocks=record_blocks,
        )
        if _LOGIN_ERROR_RE.search(result.body) is not None:
            raise BabelioLoginFailed(_LOGIN_FAILED_MESSAGE)
        self._logged_in = True

    def _open_raw(
        self,
        url: str,
        data: bytes | None,
        extra_headers: Mapping[str, str],
        timeout: float,
        *,
        record_blocks: bool,
        is_search: bool = False,
    ) -> FetchResult:
        # Caller holds `self._shared.request_lock`.
        self._wait_rate_limit(is_search=is_search)
        try:
            response = self._browser.open(url, data, timeout, headers=extra_headers)
        except urllib.error.HTTPError as exc:
            if exc.code == _HTTP_FORBIDDEN:
                self._on_block(record_blocks)  # raises BabelioBlocked
            raise  # non-403 HTTPError surfaces unchanged
        return FetchResult(response.read(), self._browser.geturl())

    def _wait_rate_limit(self, *, is_search: bool = False) -> None:
        # Caller holds `request_lock`, which is what guards `last_request_at` / `last_search_at`.
        shared = self._shared
        now = self._clock()
        wait = max(0.0, self._min_interval - (now - shared.last_request_at))
        if is_search and shared.last_search_at is not None:
            wait = max(wait, self._search_interval - (now - shared.last_search_at))
        if wait > 0.0:
            self._sleep(wait)
        finished = self._clock()
        shared.last_request_at = finished
        if is_search:
            shared.last_search_at = finished

    def _check_circuit(self) -> None:
        # Another thread can unlink the lockfile between any two filesystem calls.
        try:
            mtime = self._lockfile_path.stat().st_mtime
        except FileNotFoundError:
            return
        age = self._now_wall() - mtime
        if age < self._cooldown:
            raise CircuitBreakerOpen(self._cooldown - age)
        self._lockfile_path.unlink(missing_ok=True)
        with self._lock:
            self._consecutive_blocks = 0

    def _on_success(self) -> None:
        with self._lock:
            self._consecutive_blocks = 0

    def _on_block(self, record_blocks: bool) -> None:
        if record_blocks:
            with self._lock:
                self._consecutive_blocks += 1
                if self._consecutive_blocks >= self._block_threshold:
                    self._lockfile_path.touch()
                    self._consecutive_blocks = 0
        raise BabelioBlocked(_TOKEN_EXPIRED_MESSAGE)
