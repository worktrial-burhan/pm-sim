from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/pm_sim"
    tick_wall_seconds: float = 1.0
    tick_sim_seconds: int = 60
    worker_poll_seconds: float = 1.0
    ui_refresh_seconds: int = 2
    enable_temporal: bool = False
    temporal_address: str = "localhost:7233"
    temporal_task_queue: str = "pm-sim"
    max_actor_invocations_per_tick: int = 10
    actor_cooldown_seconds: int = 60
    max_outstanding_self_wakes: int = 3

    # Claude Agent SDK settings
    claude_model: str = "claude-sonnet-4-20250514"
    claude_max_turns: int = 2
    anthropic_api_key: str | None = None

    # Working directory for SDK sessions (session files stored in ~/.claude/projects/)
    sdk_session_dir: Path = Path.home() / "pm-sim-sessions"

    project_root: Path = Path(__file__).resolve().parents[2]
    scenario_root: Path = Path(__file__).resolve().parents[1] / "scenarios"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("PM_SIM_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/pm_sim"),
        tick_wall_seconds=float(os.getenv("PM_SIM_TICK_WALL_SECONDS", "1.0")),
        tick_sim_seconds=int(os.getenv("PM_SIM_TICK_SIM_SECONDS", "60")),
        worker_poll_seconds=float(os.getenv("PM_SIM_WORKER_POLL_SECONDS", "1.0")),
        ui_refresh_seconds=int(os.getenv("PM_SIM_UI_REFRESH_SECONDS", "2")),
        enable_temporal=_env_bool("PM_SIM_ENABLE_TEMPORAL", False),
        temporal_address=os.getenv("PM_SIM_TEMPORAL_ADDRESS", "localhost:7233"),
        temporal_task_queue=os.getenv("PM_SIM_TEMPORAL_TASK_QUEUE", "pm-sim"),
        max_actor_invocations_per_tick=int(
            os.getenv("PM_SIM_MAX_ACTOR_INVOCATIONS_PER_TICK", "10")
        ),
        actor_cooldown_seconds=int(os.getenv("PM_SIM_ACTOR_COOLDOWN_SECONDS", "60")),
        max_outstanding_self_wakes=int(os.getenv("PM_SIM_MAX_OUTSTANDING_SELF_WAKES", "3")),
        claude_model=os.getenv("PM_SIM_CLAUDE_MODEL", "claude-sonnet-4-20250514"),
        claude_max_turns=int(os.getenv("PM_SIM_CLAUDE_MAX_TURNS", "2")),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        sdk_session_dir=Path(os.getenv("PM_SIM_SDK_SESSION_DIR", str(Path.home() / "pm-sim-sessions"))),
    )
