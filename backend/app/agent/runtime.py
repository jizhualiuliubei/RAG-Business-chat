"""Agent 任务派发入口；队列不可用时保留 MySQL Outbox 等待恢复。"""

from __future__ import annotations

import os

from redis import Redis

from app.config import CELERY_BROKER_URL


def _broker_ready() -> bool:
    """探活 Celery broker；连接用完就关，避免每次派发泄漏一个 Redis 连接。"""
    client = Redis.from_url(
        CELERY_BROKER_URL,
        socket_connect_timeout=0.25,
        socket_timeout=0.25,
    )
    try:
        return bool(client.ping())
    except Exception:
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass


def dispatch_pending_task(task_id: int) -> bool:
    """尽力派发任务，不让 Redis 故障影响 FastAPI 与原有 RAG。"""
    if os.getenv("AGENT_DISABLE_DISPATCH", "").lower() in {"1", "true", "yes"}:
        return False
    try:
        if not _broker_ready():
            return False
        from app.worker import run_agent_task

        run_agent_task.apply_async(args=[int(task_id)], queue="agent_tasks")
        return True
    except Exception:
        return False


def dispatch_agent_evaluation(run_id: int) -> bool:
    """尽力派发 Agent 评测；失败时由 Celery Beat 扫描 pending 记录恢复。"""
    if os.getenv("AGENT_DISABLE_DISPATCH", "").lower() in {"1", "true", "yes"}:
        return False
    try:
        if not _broker_ready():
            return False
        from app.worker import run_agent_evaluation

        run_agent_evaluation.apply_async(args=[int(run_id)], queue="agent_evaluations")
        return True
    except Exception:
        return False
