from __future__ import annotations

from pathlib import Path

import yaml

from app.scenarios.schemas import ActorsFile, RubricFile, ScenarioMetadata, TriggersFile, WorldFile
from app.services.assignment_service import public_assignment
from app.services.config import get_settings


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def list_scenarios() -> list[dict]:
    root = get_settings().scenario_root
    scenarios = []
    for scenario_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        scenario_file = scenario_dir / "scenario.yaml"
        if not scenario_file.exists():
            continue
        metadata = ScenarioMetadata.model_validate(_read_yaml(scenario_file))
        payload = metadata.model_dump()
        if metadata.mission is not None:
            payload["mission"] = public_assignment(metadata.mission.model_dump())
        scenarios.append(payload)
    return scenarios


def load_scenario(scenario_id: str) -> dict:
    root = get_settings().scenario_root / scenario_id
    if not root.exists():
        raise FileNotFoundError(f"scenario not found: {scenario_id}")

    metadata = ScenarioMetadata.model_validate(_read_yaml(root / "scenario.yaml"))
    actors = ActorsFile.model_validate(_read_yaml(root / "actors.yaml"))
    world = WorldFile.model_validate(_read_yaml(root / "world.yaml"))
    triggers = TriggersFile.model_validate(_read_yaml(root / "triggers.yaml"))
    rubric = RubricFile.model_validate(_read_yaml(root / "rubric.yaml"))

    return {
        "metadata": metadata,
        "actors": actors,
        "world": world,
        "triggers": triggers,
        "rubric": rubric,
    }
