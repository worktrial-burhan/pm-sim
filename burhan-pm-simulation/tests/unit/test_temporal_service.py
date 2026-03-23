from __future__ import annotations


def test_run_workflow_id_is_run_scoped():
    from app.services.temporal_service import run_workflow_id

    assert run_workflow_id("run_abc") == "pm-sim-run-run_abc"
