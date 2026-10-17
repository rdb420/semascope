from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    """Rate-limit and sign-in counters live in the cache: each test starts with none."""

    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()
