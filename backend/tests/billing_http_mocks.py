"""Reusable fake aiohttp session/response objects for billing API client tests.

These avoid real network calls by emulating the ``async with session.<verb>(...) as resp``
pattern used throughout the billing API client modules.
"""


class FakeResponse:
    """Emulates an aiohttp response usable as an async context manager."""

    def __init__(self, status=200, json_data=None, headers=None, text_data=""):
        self.status = status
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self._text = text_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._json

    async def text(self):
        return self._text


class FakeSession:
    """Emulates aiohttp.ClientSession, returning queued responses per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple] = []

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self._responses:
            raise AssertionError("FakeSession ran out of queued responses")
        return self._responses.pop(0)

    def post(self, url, **kwargs):
        return self._next("post", url, **kwargs)

    def get(self, url, **kwargs):
        return self._next("get", url, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def session_factory(*responses):
    """Returns a no-arg callable that yields a FakeSession with the given responses."""
    session = FakeSession(responses)

    def _factory(*args, **kwargs):
        return session

    _factory.session = session
    return _factory
