from __future__ import annotations

import os

import pytest

from scripts.live_stack_smoke import run_live_smoke


pytestmark = pytest.mark.skipif(
    os.getenv("PM_SIM_RUN_LIVE_E2E") != "1",
    reason="set PM_SIM_RUN_LIVE_E2E=1 to run the live Docker/Claude smoke test",
)


def test_live_stack_smoke() -> None:
    summary = run_live_smoke(
        boot_stack=True,
        reset_stack=True,
        all_claude=os.getenv("PM_SIM_LIVE_E2E_ALL_CLAUDE") == "1",
        timeout_seconds=float(os.getenv("PM_SIM_LIVE_E2E_TIMEOUT_SECONDS", "240")),
    )
    assert summary.live_minio_object_count > 0
    assert summary.live_event_count > 0
