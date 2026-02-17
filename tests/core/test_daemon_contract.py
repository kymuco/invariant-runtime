from __future__ import annotations

import inspect


def test_daemon_handler_does_not_depend_on_try_acquire():
    from invariant.ops.daemon import JsonAPIHandler

    src = inspect.getsource(JsonAPIHandler._dispatch)
    assert "try_acquire" not in src, (
        "Concurrency gating must live in InvariantHTTPServer.process_request(), "
        "not inside JsonAPIHandler._dispatch()."
    )


def test_jsonapi_send_json_supports_optional_headers_kwarg():
    from invariant.ops.daemon import JsonAPIHandler

    sig = inspect.signature(JsonAPIHandler._send_json)
    # В _dispatch у вас вызывается _send_json(..., headers=...)
    assert "headers" in sig.parameters, (
        "JsonAPIHandler._dispatch calls _send_json(..., headers=...). "
        "Add headers: Optional[dict] parameter to _send_json, or stop passing it."
    )
