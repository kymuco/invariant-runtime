from __future__ import annotations

import concurrent.futures

import pytest

from tests.helpers_http import http_json, http_json_expect_continue_raw


def _base_cmd_create(ttl: int = 10):
    return {"cmd": "create_execution", "policy": "ephemeral", "ttl_seconds": ttl}


def test_http_health_smoke(daemon_server):
    host, port = daemon_server.server_address
    r = http_json(host, port, "GET", "/v1/health")
    assert r.status == 200, r.body
    j = r.json()
    assert j["ok"] is True
    assert "boot_id" in j
    assert "started_at" in j
    assert "agent_run_worker" in j
    assert isinstance(j["agent_run_worker"], dict)
    assert j["agent_run_worker"].get("enabled") is False
    assert "agent_run_alerting" in j
    assert isinstance(j["agent_run_alerting"], dict)
    assert j["agent_run_alerting"].get("enabled") is False
    # request_id РѕР±СЏР·Р°С‚РµР»РµРЅ РґР»СЏ РєРѕСЂСЂРµР»СЏС†РёРё РІ РїСЂРѕРґРµ
    assert "request_id" in j


def test_http_rejects_wrong_content_type(daemon_server):
    host, port = daemon_server.server_address
    # РЅР°РјРµСЂРµРЅРЅРѕ СЃС‚Р°РІРёРј text/plain
    r = http_json(
        host,
        port,
        "POST",
        "/v1/handle",
        {"cmd": "list_executions"},
        headers={"Content-Type": "text/plain"},
    )
    assert r.status == 400, r.body
    j = r.json()
    assert j["ok"] is False
    assert j["error"] == "bad_request"


def test_http_max_body_bytes_enforced(daemon_server):
    host, port = daemon_server.server_address

    # РґРµР»Р°РµРј С‚РµР»Рѕ Р±РѕР»СЊС€Рµ Р»РёРјРёС‚Р° (1MiB РІ С„РёРєСЃС‚СѓСЂРµ) вЂ” РЅРѕ С‚СѓС‚ РґРѕСЃС‚Р°С‚РѕС‡РЅРѕ Рё РјРµРЅСЊС€Рµ,
    # РµСЃР»Рё РІС‹ Р·Р°С…РѕС‚РёС‚Рµ РїР°СЂР°РјРµС‚СЂРёР·РѕРІР°С‚СЊ max_body_bytes РІ С„РёРєСЃС‚СѓСЂРµ.
    big = {"cmd": "execution_plugin_call", "junk": "x" * (2 * 1024 * 1024)}

    # IMPORTANT: on Windows http.client is flaky for Expect: 100-continue.
    # Use raw socket flow to avoid WinError 10053.
    r = http_json_expect_continue_raw(host, port, "/v1/handle", big, timeout=5.0)
    assert r.status in (400, 413), (r.status, r.body[:200])
    j = r.json()
    assert j["ok"] is False
    assert j["error"] in ("bad_request", "payload_too_large")
    assert "Body too large" in j["message"]


def test_http_shutdown_requires_token(daemon_server):
    host, port = daemon_server.server_address

    r = http_json(host, port, "POST", "/v1/shutdown", {})
    assert r.status == 403, r.body

    r2 = http_json(host, port, "POST", "/v1/shutdown", {}, headers={"Authorization": "Bearer secret"})
    assert r2.status == 200, r2.body
    assert r2.json()["ok"] is True


@pytest.mark.stress
def test_http_busy_rejection_under_load(daemon_server):
    """
    Р РµР°Р»СЊРЅС‹Р№ СЃС‚СЂРµСЃСЃ:
    - max_concurrent_requests=8 (РІ С„РёРєСЃС‚СѓСЂРµ)
    - Р·Р°РєРёРґС‹РІР°РµРј Р±РѕР»СЊС€Рµ РїР°СЂР°Р»Р»РµР»СЊРЅС‹С… Р·Р°РїСЂРѕСЃРѕРІ, С‡РµРј Р»РёРјРёС‚
    - РѕР¶РёРґР°РµРј, С‡С‚Рѕ С‡Р°СЃС‚СЊ Р±СѓРґРµС‚ 503 busy (Р±С‹СЃС‚СЂРѕ, РїСЂРµРґСЃРєР°Р·СѓРµРјРѕ), Р° С‡Р°СЃС‚СЊ 200.
    """
    host, port = daemon_server.server_address

    # РїРѕРґРіРѕС‚РѕРІРєР°: create_execution + get handle РЅР° sleep-plugin
    r = http_json(host, port, "POST", "/v1/handle", {"cmd": "create_execution", "policy": "ephemeral", "ttl_seconds": 20})
    assert r.status == 200, r.body
    exec_id = r.json()["execution"]

    r2 = http_json(host, port, "POST", "/v1/handle", {"cmd": "execution_plugin_get", "execution": exec_id, "plugin": "tests:sleep"})
    assert r2.status == 200, r2.body
    handle = r2.json()["handle"]

    def call_sleep(i: int):
        return http_json(
            host,
            port,
            "POST",
            "/v1/handle",
            {"cmd": "execution_plugin_call", "execution": exec_id, "handle": handle, "method": "sleep", "args": [0.08]},
            timeout=5.0,
        )

    n = 80
    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as ex:
        resps = list(ex.map(call_sleep, range(n)))

    codes = [r.status for r in resps]
    ok200 = codes.count(200)
    busy503 = codes.count(503)

    # Р’ РїСЂРѕРґРµ РЅРѕСЂРјР°Р»СЊРЅР°СЏ РєР°СЂС‚РёРЅР° РїРѕРґ РїРµСЂРµРіСЂСѓР·РѕРј:
    # - С‡Р°СЃС‚СЊ РїСЂРёРЅСЏС‚Р° (200)
    # - С‡Р°СЃС‚СЊ РѕС‚РІРµСЂРіРЅСѓС‚Р° РїСЂРµРґСЃРєР°Р·СѓРµРјРѕ (503 busy)
    assert ok200 > 0, {"codes_sample": codes[:20]}
    assert busy503 > 0, {"ok200": ok200, "busy503": busy503, "codes_sample": codes[:20]}

    # busy РѕС‚РІРµС‚С‹ РґРѕР»Р¶РЅС‹ СЃРѕРґРµСЂР¶Р°С‚СЊ request_id + (РІ РёРґРµР°Р»Рµ) Retry-After
    busy = [x for x in resps if x.status == 503]
    j0 = busy[0].json()
    assert j0["ok"] is False and j0["error"] == "busy"
    assert "request_id" in j0

