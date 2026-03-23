from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4


class RunStatus(str, Enum):
    PAUSED = "paused"
    RUNNING = "running"
    STOPPED = "stopped"
    COMPLETED = "completed"


class OrchestrationStatus(str, Enum):
    UNATTACHED = "unattached"
    ATTACHING = "attaching"
    ATTACHED = "attached"
    ERROR = "error"


class ActorStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ControllerType(str, Enum):
    SCRIPTED = "scripted"
    CLAUDE = "claude"
    NOOP = "noop"


class ObjectKind(str, Enum):
    PROJECT = "project"
    TASK = "task"
    MILESTONE = "milestone"
    BLOCKER = "blocker"
    THREAD = "thread"
    MEETING = "meeting"
    DOCUMENT = "document"
    OBLIGATION = "obligation"
    DECISION = "decision"
    REMINDER = "reminder"


class DeliveryStatus(str, Enum):
    UNREAD = "unread"
    READ = "read"


class TriggerType(str, Enum):
    ACTOR_ROUTINE_WAKE = "actor_routine_wake"
    RESPONSE_DELAY = "response_delay"
    OBLIGATION_DUE = "obligation_due"
    MEETING_START = "meeting_start"
    MEETING_END = "meeting_end"
    EVALUATION_CHECKPOINT = "evaluation_checkpoint"


class TriggerStatus(str, Enum):
    PENDING = "pending"
    FIRED = "fired"
    CANCELLED = "cancelled"


class TraceType(str, Enum):
    AWARENESS_READ = "awareness_read"
    ACTION_ATTEMPT = "action_attempt"
    COMMAND_REJECTED = "command_rejected"
    CONTROLLER_REQUEST = "controller_request"
    CONTROLLER_RESPONSE = "controller_response"
    INTROSPECTION_WRITE = "introspection_write"
    SYSTEM_DEBUG = "system_debug"


class ActorTurnStatus(str, Enum):
    PREPARED = "prepared"
    DECIDING = "deciding"
    DECIDED = "decided"
    APPLIED = "applied"
    FAILED = "failed"
    CANCELLED = "cancelled"


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"
