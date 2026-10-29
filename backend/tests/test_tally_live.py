"""Optional live TallyPrime smoke test.

This is skipped unless ``TALLY_LIVE_TEST=1`` is set. It is intentionally
separate from the mocked suite so normal CI never depends on a local
TallyPrime installation.
"""

from __future__ import annotations

import os

import pytest

from app.integrations.tally import get_tally_service

pytestmark = pytest.mark.skipif(
    os.getenv("TALLY_LIVE_TEST") != "1",
    reason="Set TALLY_LIVE_TEST=1 and start TallyPrime on the configured port.",
)


def test_live_tally_connectivity_and_company_read():
    service = get_tally_service()
    try:
        assert service.check_connection() is True
        companies = service.get_companies()
        assert isinstance(companies, list)
    finally:
        service.close()
