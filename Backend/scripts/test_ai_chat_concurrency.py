"""
Offline tests for /api/ai/chat event-loop safety (audit item H3).

  - a slow, blocking run_assistant does not stall unrelated async requests on the same event loop
  - run_assistant runs off the event-loop thread
  - response format and error mapping are unchanged ({"reply": ...}, 400 / 503 / 502 / 500, 401 without token)

Authentication is mocked (app.main.authenticate_access_token) and run_assistant is mocked, so Groq and
Wialon are never contacted. Outbound `requests` calls are blocked by OfflineGuard and outbound httpx
calls (used by the Groq SDK) are blocked here. Run from Backend/:
    python scripts/test_ai_chat_concurrency.py
"""
import asyncio
import os
import sys
import threading
import time
import traceback
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import LiveCallBlocked, OfflineGuard  # noqa: E402

import app.ai.assistant  # noqa: E402,F401  (ensures the patch target exists)
from app.main import app  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "user@example.com", "role": "viewer"}
CHAT_BODY = {"messages": [{"role": "user", "content": "How many vehicles are moving?"}]}
ASSISTANT_SECONDS = 1.5
HEALTH_BUDGET_SECONDS = 0.5

httpx_attempts = []


def _blocked_httpx_send(self, request, *args, **kwargs):
    httpx_attempts.append(f"{request.method} {request.url}")
    raise LiveCallBlocked(f"Live httpx call blocked in offline test: {request.method} {request.url}")


def _client():
    # ASGITransport does not run the lifespan, so no Wialon login happens at startup.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _chat_while_health_polled():
    loop_thread = threading.get_ident()
    assistant_thread = {}
    assistant_started = threading.Event()

    def slow_assistant(messages):
        assistant_thread["id"] = threading.get_ident()
        assistant_started.set()
        time.sleep(ASSISTANT_SECONDS)  # stands in for blocking Groq + Wialon calls
        return "grounded reply"

    async with _client() as client:
        with patch("app.main.authenticate_access_token", return_value=USER), \
                patch("app.ai.assistant.run_assistant", side_effect=slow_assistant):
            started = time.perf_counter()
            chat_task = asyncio.create_task(client.post("/api/ai/chat", json=CHAT_BODY, headers=AUTH_HEADERS))

            # Wait (without blocking the loop) until the assistant is actually running.
            for _ in range(200):
                if assistant_started.is_set() or chat_task.done():
                    break
                await asyncio.sleep(0.01)

            health_started = time.perf_counter()
            health = await client.get("/api/health")
            health_elapsed = time.perf_counter() - health_started
            chat_still_running = not chat_task.done()

            chat = await chat_task
            chat_elapsed = time.perf_counter() - started

    return {
        "loop_thread": loop_thread,
        "assistant_thread": assistant_thread.get("id"),
        "health": health,
        "health_elapsed": health_elapsed,
        "chat_still_running": chat_still_running,
        "chat": chat,
        "chat_elapsed": chat_elapsed,
    }


def test_blocking_assistant_does_not_block_event_loop():
    r = asyncio.run(_chat_while_health_polled())
    assert r["health"].status_code == 200, r["health"].text
    assert r["health_elapsed"] < HEALTH_BUDGET_SECONDS, (
        f"/api/health took {r['health_elapsed']:.2f}s while the assistant was running; event loop was blocked"
    )
    assert r["chat_still_running"], "chat finished before health returned; health was serialized behind it"
    assert r["assistant_thread"] is not None and r["assistant_thread"] != r["loop_thread"], (
        "run_assistant ran on the event-loop thread"
    )
    assert r["chat"].status_code == 200, r["chat"].text
    assert r["chat"].json() == {"reply": "grounded reply"}, r["chat"].json()
    assert r["chat_elapsed"] >= ASSISTANT_SECONDS * 0.9, r["chat_elapsed"]
    print(
        f"[PASS] /api/health answered in {r['health_elapsed'] * 1000:.0f} ms while a "
        f"{ASSISTANT_SECONDS:.1f}s blocking assistant call was in flight (assistant ran off the loop thread)."
    )


async def _post_chat(body, side_effect=None, return_value=None, headers=AUTH_HEADERS):
    async with _client() as client:
        with patch("app.main.authenticate_access_token", return_value=USER), \
                patch("app.ai.assistant.run_assistant", side_effect=side_effect, return_value=return_value) as mock:
            res = await client.post("/api/ai/chat", json=body, headers=headers)
            return res, mock


def test_response_format_and_errors_unchanged():
    res, mock = asyncio.run(_post_chat(CHAT_BODY, return_value="ok"))
    assert res.status_code == 200 and res.json() == {"reply": "ok"}, (res.status_code, res.text)
    mock.assert_called_once_with([{"role": "user", "content": "How many vehicles are moving?"}])
    print("[PASS] 200 returns {'reply': ...} and passes message dicts to run_assistant unchanged.")

    res, mock = asyncio.run(_post_chat({"messages": []}, return_value="unused"))
    assert res.status_code == 400 and res.json() == {"detail": "No messages provided."}, res.text
    assert not mock.called
    print("[PASS] Empty messages -> 400 without calling the assistant.")

    res, _ = asyncio.run(_post_chat(CHAT_BODY, side_effect=RuntimeError("GROQ_API_KEY is not configured in the environment.")))
    assert res.status_code == 503 and "GROQ" not in res.text, res.text
    print("[PASS] RuntimeError -> 503 with a generic message (M2: exception text stays server-side).")

    # WialonAPIError subclasses RuntimeError; it is handled before the RuntimeError branch (M2).
    res, _ = asyncio.run(_post_chat(CHAT_BODY, side_effect=WialonAPIError("boom")))
    assert res.status_code == 502 and "boom" not in res.text, (res.status_code, res.text)
    print("[PASS] WialonAPIError -> 502 without the upstream error text.")

    res, _ = asyncio.run(_post_chat(CHAT_BODY, side_effect=ValueError("internal")))
    assert res.status_code == 500, res.text
    assert res.json() == {"detail": "Fleet AI is temporarily unavailable. Please try again."}, res.text
    print("[PASS] Other exceptions -> 500 generic message.")


def test_requires_bearer_token():
    async def run():
        async with _client() as client:
            with patch("app.ai.assistant.run_assistant", return_value="unused") as mock:
                res = await client.post("/api/ai/chat", json=CHAT_BODY)
                return res, mock

    res, mock = asyncio.run(run())
    assert res.status_code == 401, res.text
    assert not mock.called
    print("[PASS] No bearer token -> 401 before the assistant runs.")


if __name__ == "__main__":
    guard = OfflineGuard().start()
    httpx_patcher = patch.object(httpx.HTTPTransport, "handle_request", _blocked_httpx_send)
    httpx_patcher.start()
    failures = 0
    try:
        for test in (
            test_blocking_assistant_does_not_block_event_loop,
            test_response_format_and_errors_unchanged,
            test_requires_bearer_token,
        ):
            try:
                test()
            except Exception:  # any error fails this test but lets the remaining tests run
                failures += 1
                print(f"[FAIL] {test.__name__}")
                traceback.print_exc()
        guard.assert_no_live_calls()
        assert not httpx_attempts, f"Offline test attempted live httpx calls: {httpx_attempts}"
        print("[PASS] httpx guard: zero live httpx calls attempted.")
    finally:
        httpx_patcher.stop()
        guard.stop()
    if failures:
        sys.exit(1)
    print("\nAll AI chat concurrency tests passed.")
