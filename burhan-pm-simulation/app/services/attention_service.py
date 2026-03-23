from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.db import ActorRecord

_DEFAULT_DAYS = ["mon", "tue", "wed", "thu", "fri"]
_WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def local_time_for_actor(actor: ActorRecord, sim_time: datetime) -> datetime:
    if sim_time.tzinfo is None:
        sim_time = sim_time.replace(tzinfo=UTC)
    try:
        return sim_time.astimezone(ZoneInfo(actor.timezone or "UTC"))
    except Exception:
        return sim_time.astimezone(UTC)


def normalize_working_hours(actor: ActorRecord) -> dict[str, list[dict[str, str]]]:
    raw = actor.working_hours_json or {}
    if "days" in raw and isinstance(raw["days"], dict):
        normalized: dict[str, list[dict[str, str]]] = {}
        for day, windows in raw["days"].items():
            normalized[str(day).lower()] = [
                {"start": str(window["start"]), "end": str(window["end"])}
                for window in (windows or [])
                if isinstance(window, dict) and window.get("start") and window.get("end")
            ]
        return normalized

    start = str(raw.get("start", "09:00"))
    end = str(raw.get("end", "17:00"))
    days = [str(day).lower() for day in raw.get("days", _DEFAULT_DAYS)]
    return {day: [{"start": start, "end": end}] for day in days}


def is_within_working_hours(actor: ActorRecord, sim_time: datetime) -> bool:
    local_dt = local_time_for_actor(actor, sim_time)
    windows = normalize_working_hours(actor).get(_WEEKDAY_KEYS[local_dt.weekday()], [])
    current_hm = local_dt.strftime("%H:%M")
    for window in windows:
        if window["start"] <= current_hm < window["end"]:
            return True
    return False


def next_working_time(actor: ActorRecord, sim_time: datetime) -> datetime:
    local_dt = local_time_for_actor(actor, sim_time)
    schedule = normalize_working_hours(actor)

    for offset in range(0, 14):
        candidate_date = (local_dt + timedelta(days=offset)).date()
        weekday = _WEEKDAY_KEYS[(local_dt.weekday() + offset) % 7]
        windows = schedule.get(weekday, [])
        if not windows:
            continue
        for window in windows:
            start_hour, start_minute = [int(part) for part in window["start"].split(":", 1)]
            start_local = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                start_hour,
                start_minute,
                tzinfo=local_dt.tzinfo,
            )
            if offset == 0 and start_local <= local_dt:
                current_hm = local_dt.strftime("%H:%M")
                if window["start"] <= current_hm < window["end"]:
                    return local_dt.astimezone(UTC)
                continue
            return start_local.astimezone(UTC)
    return sim_time


def describe_work_window(actor: ActorRecord, sim_time: datetime) -> dict[str, str | bool]:
    local_dt = local_time_for_actor(actor, sim_time)
    schedule = normalize_working_hours(actor)
    weekday = _WEEKDAY_KEYS[local_dt.weekday()]
    windows = schedule.get(weekday, [])
    return {
        "timezone": actor.timezone,
        "local_time": local_dt.isoformat(),
        "within_working_hours": is_within_working_hours(actor, sim_time),
        "today_windows": [f"{window['start']}-{window['end']}" for window in windows],
    }


def _current_load_label(actor: ActorRecord, *, workload: dict | None = None) -> str:
    workload = workload or {}
    current_load = str((workload.get("current_load") or "").lower()).strip()
    if current_load:
        return current_load
    profile = actor.profile_json or {}
    return str((profile.get("current_load") or "").lower()).strip()


def response_delay_minutes(actor: ActorRecord, *, surface: str, workload: dict | None = None) -> int:
    profile = actor.profile_json or {}
    response_behavior = profile.get("response_behavior") or {}
    explicit = response_behavior.get(surface) or response_behavior.get("default")
    if explicit is not None:
        try:
            return max(int(explicit), 1)
        except (TypeError, ValueError):
            pass

    defaults = {
        "chat": 4,
        "email": 18,
        "calendar": 1,
    }
    base = defaults.get(surface, 6)
    current_load = _current_load_label(actor, workload=workload)
    load_penalty = {
        "low": 0,
        "medium": 3,
        "high": 8,
        "very_high": 15,
    }.get(current_load, 0)
    return max(base + load_penalty, 1)


def next_response_due_time(
    actor: ActorRecord,
    *,
    delivered_at_sim: datetime,
    surface: str,
    workload: dict | None = None,
) -> datetime:
    candidate = delivered_at_sim + timedelta(
        minutes=response_delay_minutes(actor, surface=surface, workload=workload)
    )
    if is_within_working_hours(actor, candidate):
        return candidate
    return next_working_time(actor, candidate)
