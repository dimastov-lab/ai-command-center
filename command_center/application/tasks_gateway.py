"""AICC-owned public boundary for task services.

This module deliberately has no AIOS dependency.  Product code talks in these
DTOs and typed errors; the one AIOS SDK adapter translates at the edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


class TasksGatewayError(RuntimeError):
    """Base class for safe, product-owned task gateway failures."""


class TasksGatewayConfigurationError(TasksGatewayError):
    pass


class TasksGatewayRemoteError(TasksGatewayError):
    def __init__(
        self,
        code: str,
        *,
        request_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(f"task gateway request failed: {code}")
        self.code = code
        self.request_id = request_id
        self.retryable = retryable


class TasksGatewayContractError(TasksGatewayError):
    pass


class CorruptTaskMapError(TasksGatewayError):
    pass


class TaskMapWriteError(TasksGatewayError):
    pass


class UnsupportedTaskStateError(TasksGatewayContractError):
    pass


class UnsupportedTaskTransitionError(TasksGatewayContractError):
    pass


@dataclass(frozen=True)
class CreateTaskDTO:
    subject_ref: str
    type: str
    title: str
    priority: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class TaskDTO:
    id: str
    subject_ref: str
    type: str
    title: str
    state: str
    priority: int
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class GatewayEvidence:
    event: str
    request_id: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {"event": self.event, "request_id": self.request_id}


@dataclass(frozen=True)
class TaskResult:
    task: TaskDTO
    evidence: tuple[GatewayEvidence, ...] = ()


@dataclass(frozen=True)
class TaskListResult:
    tasks: tuple[TaskDTO, ...]
    evidence: tuple[GatewayEvidence, ...] = ()


@runtime_checkable
class TasksGateway(Protocol):
    def list_tasks(self) -> TaskListResult: ...

    def create_task(self, request: CreateTaskDTO, *, idempotency_key: str) -> TaskResult: ...

    def get_task(self, task_id: str) -> TaskResult: ...

    def assign_task(self, task_id: str, assignee: str) -> TaskResult: ...

    def start_task(self, task_id: str) -> TaskResult: ...

    def complete_task(self, task_id: str) -> TaskResult: ...

    def cancel_task(self, task_id: str) -> TaskResult: ...
