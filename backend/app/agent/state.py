"""Agent 任务状态与合法转换。"""

from enum import StrEnum


class AgentTaskStatus(StrEnum):
    DRAFT = "draft"
    PENDING_DISPATCH = "pending_dispatch"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    # 被审批人驳回。和 CANCELLED（申请人自己撤回）是两件事，档案里必须分得清。
    REJECTED = "rejected"


_ALLOWED_TRANSITIONS: dict[AgentTaskStatus, set[AgentTaskStatus]] = {
    AgentTaskStatus.DRAFT: {AgentTaskStatus.PENDING_DISPATCH, AgentTaskStatus.CANCELLED},
    AgentTaskStatus.PENDING_DISPATCH: {AgentTaskStatus.QUEUED, AgentTaskStatus.RUNNING, AgentTaskStatus.FAILED, AgentTaskStatus.CANCEL_REQUESTED, AgentTaskStatus.CANCELLED},
    AgentTaskStatus.QUEUED: {AgentTaskStatus.RUNNING, AgentTaskStatus.FAILED, AgentTaskStatus.CANCEL_REQUESTED, AgentTaskStatus.CANCELLED},
    AgentTaskStatus.RUNNING: {
        AgentTaskStatus.WAITING_INPUT,
        AgentTaskStatus.WAITING_APPROVAL,
        AgentTaskStatus.EXECUTING,
        AgentTaskStatus.COMPLETED,
        AgentTaskStatus.CANCELLED,
        AgentTaskStatus.FAILED,
        AgentTaskStatus.CANCEL_REQUESTED,
    },
    AgentTaskStatus.WAITING_INPUT: {AgentTaskStatus.PENDING_DISPATCH, AgentTaskStatus.CANCEL_REQUESTED, AgentTaskStatus.CANCELLED},
    # 审批人「退回补充」会回到 waiting_input：申请人补材料 → 重新走计划与审计。
    # 这条转换原先只靠审批服务直接赋 status 实现，状态机映射里根本没有 —— 换成
    # 统一入口后就会抛「非法转换」。补进来，让映射和真实语义一致。
    AgentTaskStatus.WAITING_APPROVAL: {
        AgentTaskStatus.PENDING_DISPATCH,
        AgentTaskStatus.WAITING_INPUT,
        AgentTaskStatus.CANCELLED,
        AgentTaskStatus.REJECTED,
    },
    AgentTaskStatus.EXECUTING: {AgentTaskStatus.COMPLETED, AgentTaskStatus.FAILED, AgentTaskStatus.CANCEL_REQUESTED},
    AgentTaskStatus.FAILED: {AgentTaskStatus.PENDING_DISPATCH, AgentTaskStatus.CANCELLED},
    AgentTaskStatus.CANCEL_REQUESTED: {AgentTaskStatus.CANCELLED, AgentTaskStatus.FAILED},
    AgentTaskStatus.COMPLETED: set(),
    AgentTaskStatus.CANCELLED: set(),
    AgentTaskStatus.REJECTED: set(),
}


def assert_status_transition(current: str | AgentTaskStatus, target: str | AgentTaskStatus) -> None:
    current_status = AgentTaskStatus(current)
    target_status = AgentTaskStatus(target)
    if current_status == target_status:
        return
    if target_status not in _ALLOWED_TRANSITIONS[current_status]:
        raise ValueError(f"非法任务状态转换：{current_status.value} -> {target_status.value}")
