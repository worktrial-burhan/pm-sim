from __future__ import annotations

from app.services import run_service
from app.services.db import session_scope
from app.services.temporal_service import ensure_run_workflow_sync, run_workflow_id


def transition_run_with_temporal_sync(*, run_id: str, transition: str):
    with session_scope() as session:
        run_service.begin_orchestration_attach(
            session,
            run_id=run_id,
            transition=transition,
            workflow_id=run_workflow_id(run_id),
        )

    try:
        ensure_run_workflow_sync(run_id)
    except Exception as exc:
        with session_scope() as recovery_session:
            run_service.fail_orchestration_attach(
                recovery_session,
                run_id=run_id,
                error=str(exc),
            )
        raise

    with session_scope() as finalize_session:
        return run_service.finalize_orchestration_attach(
            finalize_session,
            run_id=run_id,
            transition=transition,
        )
