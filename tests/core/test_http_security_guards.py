# tests/test_http_security_guards.py
from __future__ import annotations

import urllib.request
from http.client import HTTPMessage

import pytest

from invariant.exec.caps.http import HttpAccess, HttpHostDenied, _CheckedRedirect


def test_http_blocks_private_ip_literal_by_default() -> None:
    """
    SSRF guard: IP literal 127.0.0.1 должен быть запрещён, если allow_private_ips=False.
    (Без DNS, детерминированно.)
    """
    http = HttpAccess(
        allow_hosts=(),
        allow_any_host=True,     # иначе пустой allowlist = deny-all раньше, чем дойдём до IP checks
        allow_private_ips=False,
        allow_http=False,
    )

    with pytest.raises(HttpHostDenied):
        http._check_url("https://127.0.0.1/metadata")  # noqa: SLF001 (тестируем guard)


def test_redirect_handler_rechecks_target_host() -> None:
    """
    Защита от обхода allowlist через 30x:
    _CheckedRedirect должен вызывать _check_url(newurl) и падать на запрещённом хосте.
    """
    http = HttpAccess(
        allow_hosts=("example.com",),
        allow_redirects=True,
        allow_private_ips=True,  # чтобы не было DNS
    )

    h = _CheckedRedirect(http._check_url)
    req = urllib.request.Request(url="https://example.com/start", method="GET")

    with pytest.raises(HttpHostDenied):
        h.redirect_request(
            req=req,
            fp=None,
            code=302,
            msg="Found",
            headers=HTTPMessage(),
            newurl="https://evil.com/steal",
        )
