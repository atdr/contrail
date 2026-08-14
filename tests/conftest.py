from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_feed_path() -> Path:
    return FIXTURES / "sample_feed.ics"


@pytest.fixture
def sample_feed_bytes(sample_feed_path) -> bytes:
    return sample_feed_path.read_bytes()
