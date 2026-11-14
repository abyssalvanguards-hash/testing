import asyncio
import json
from unittest.mock import patch

from core.ai import AIUnavailable, GeminiService


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def text(self):
        return json.dumps(self.payload)


class FakeRequest:
    def __init__(self, outcome):
        self.outcome = outcome

    async def __aenter__(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return await self.outcome.__aenter__()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def post(self, url, **kwargs):
        self.urls.append(url)
        return FakeRequest(self.outcomes.pop(0))


def success_response(text="fallback worked"):
    return FakeResponse(200, {
        "candidates": [{
            "content": {"parts": [{"text": text}]}
        }]
    })


def test_primary_timeout_uses_fallback_model():
    service = GeminiService(["test-api-key"])
    session = FakeSession([asyncio.TimeoutError(), success_response()])

    with patch("core.ai.aiohttp.ClientSession", return_value=session):
        result = asyncio.run(service.ask("test prompt"))

    assert result == "fallback worked"
    assert len(session.urls) == 2
    assert "/models/gemini-3.8-flash:" in session.urls[0]
    assert "/models/gemini-3.5-flash-lite:" in session.urls[1]


def test_primary_503_retries_then_uses_fallback():
    service = GeminiService(["test-api-key"])
    overloaded = FakeResponse(503, {"error": {"message": "busy"}})
    session = FakeSession([overloaded, overloaded, success_response("fallback after 503")])

    async def no_wait(_seconds):
        return None

    with (
        patch("core.ai.aiohttp.ClientSession", return_value=session),
        patch("core.ai.asyncio.sleep", new=no_wait),
    ):
        result = asyncio.run(service.ask("test prompt"))

    assert result == "fallback after 503"
    assert len(session.urls) == 3
    assert "/models/gemini-3.5-flash-lite:" in session.urls[-1]


def test_timeout_without_fallback_returns_actionable_error():
    service = GeminiService(["test-api-key"], fallback_model=None)
    session = FakeSession([asyncio.TimeoutError()])

    with patch("core.ai.aiohttp.ClientSession", return_value=session):
        try:
            asyncio.run(service.ask("test prompt"))
        except AIUnavailable as exc:
            assert "timed out for model gemini-3.8-flash" in str(exc)
        else:
            raise AssertionError("Expected AIUnavailable")
