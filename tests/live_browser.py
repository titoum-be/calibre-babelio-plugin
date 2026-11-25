from __future__ import annotations

import http.client
import http.cookiejar
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class UrllibBrowser:
    def __init__(self) -> None:
        self._headers: dict[str, str] = {}
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))
        self._final_url = ""

    def set_user_agent(self, newval: str) -> None:
        self._headers["User-Agent"] = newval

    def set_header(self, header: str, value: str) -> None:
        self._headers[header] = value

    def set_simple_cookie(self, name: str, value: str, domain: str, path: str = "/") -> None:
        cookie = http.cookiejar.Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith("."),
            path=path,
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": ""},
            rfc2109=False,
        )
        self._jar.set_cookie(cookie)

    def open(
        self,
        url: str,
        data: bytes | None = None,
        timeout: float = 30.0,
        headers: Mapping[str, str] | None = None,
    ) -> http.client.HTTPResponse:
        merged = dict(self._headers)
        if headers:
            merged.update(headers)
        request = urllib.request.Request(url, data=data, headers=merged)
        response = self._opener.open(request, timeout=timeout)
        if not isinstance(response, http.client.HTTPResponse):
            raise TypeError("expected an HTTP response from Babelio")
        final = getattr(response, "url", None)
        self._final_url = final if isinstance(final, str) else url
        return response

    def geturl(self) -> str:
        return self._final_url
