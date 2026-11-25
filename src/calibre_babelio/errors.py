"""Typed exceptions and shared user-facing messages for the Babelio client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:

    def _(text: str) -> str: ...
    def load_translations() -> None: ...


load_translations()

_TOKEN_EXPIRED_MESSAGE = (
    "Babelio blocked the request (HTTP 403): if jstsToken is configured, copy a fresh one from "
    "your browser (DevTools → Application → Cookies → www.babelio.com → jstsToken) into plugin "
    "settings."
)

_TOKEN_MISSING_MESSAGE = (
    "No Babelio jstsToken cookie is configured: copy one from your browser "
    "(DevTools → Application → Cookies → www.babelio.com → jstsToken) into plugin settings."
)

_LOGIN_FAILED_MESSAGE = (
    "Babelio rejected the configured username or password: check your credentials in plugin "
    "settings."
)


def cookie_expired_message() -> str:
    return _(
        "Babelio blocked the request (HTTP 403): paste a fresh jstsToken cookie in the plugin "
        "settings (Preferences → Metadata download → Babelio → Configure)."
    )


def token_missing_message() -> str:
    return _(
        "No Babelio jstsToken cookie is configured: paste one in the plugin settings "
        "(Preferences → Metadata download → Babelio → Configure) before importing."
    )


def login_failed_message() -> str:
    return _(
        "Babelio rejected the configured username or password: check your credentials in the "
        "plugin settings (Preferences → Metadata download → Babelio → Configure)."
    )


def circuit_open_message() -> str:
    return _("Babelio access is temporarily blocked to avoid an IP ban; try again later.")


class BabelioBlocked(Exception):
    """Raised on HTTP 403: Babelio challenged or rejected the request."""


class BabelioTokenMissing(BabelioBlocked):
    """Raised when no `jstsToken` is configured and one is required."""


class BabelioLoginFailed(Exception):
    """Raised when Babelio rejects the configured username or password."""


class CircuitBreakerOpen(Exception):
    """Raised while the breaker is open; `remaining` is seconds until auto-recovery."""

    def __init__(self, remaining: float) -> None:
        self.remaining = remaining
        hours = remaining / 3600.0
        super().__init__(
            f"Babelio access is temporarily blocked to avoid an IP ban; "
            f"try again in ~{hours:.1f} h."
        )


def message_for_error(error: BaseException) -> str:
    if isinstance(error, CircuitBreakerOpen):
        return circuit_open_message()
    if isinstance(error, BabelioLoginFailed):
        return login_failed_message()
    if isinstance(error, BabelioTokenMissing):
        return token_missing_message()
    return cookie_expired_message()
