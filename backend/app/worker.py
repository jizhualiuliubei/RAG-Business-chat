"""Celery Worker：执行 Agent 图和恢复 Outbox。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import lru_cache
from threading import Event, Thread

from langgraph.checkpoint.redis import RedisSaver
from redis import Redis
from sqlalchemy import or_, select

from app.agent.events import AGENT_WORKER_HEARTBEAT_KEY, publish_event
from app.agent.redis_config import delete_checkpoint_thread, validate_checkpoint_redis_url
from app.agent.runner import process_agent_task
from app.celery_app import celery_app
from app.config import (
    AGENT_CHECKPOINT_REDIS_URL,
    AGENT_LOCK_REDIS_URL,
    AGENT_LOCK_TIMEOUT_SECONDS,
    AGENT_WORKER_HEARTBEAT_TTL_SECONDS,
)
from app.database import SessionLocal
from app.models.agent import AgentEvaluationRun, AgentOutbox, AgentTask
from app.services.agent_evaluation_service import execute_evaluation_run


@lru_cache(maxsize=1)
def _heartbeat_redis_client():
    return Redis.from_url(
        AGENT_LOCK_REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=2,
    )


@celery_app.task(name="agent.worker_heartbeat", ignore_result=True, acks_late=False)
def agent_worker_heartbeat():
    _heartbeat_redis_client().set(
        AGENT_WORKER_HEARTBEAT_KEY,
        datetime.now().isoformat(),
        ex=max(10, AGENT_WORKER_HEARTBEAT_TTL_SECONDS),
    )
    return {"status": "ok"}


def _renew_redis_lock(lock, stop_event: Event, *, lease_seconds: int) -> None:
    """Keep a short Redis lease alive while its Worker process is healthy."""
    interval = max(1, min(30, int(lease_seconds) // 3, AGENT_WORKER_HEARTBEAT_TTL_SECONDS // 3))
    while not stop_event.wait(interval):
        try:
            lock.extend(int(lease_seconds), replace_ttl=True)
            agent_worker_heartbeat.run()
        except Exception:
            # The execution guard rejects subsequent nodes if ownership is lost.
            return


@contextmanager
def _held_redis_lock(lock_client, key: str):
    lock = lock_client.lock(
        key,
        timeout=AGENT_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=0,
        thread_local=False,
    )
    if not lock.acquire(blocking=False):
        yield None
        return

    stop_event = Event()
    renewer = Thread(
        target=_renew_redis_lock,
        args=(lock, stop_event),
        kwargs={"lease_seconds": AGENT_LOCK_TIMEOUT_SECONDS},
        name=f"redis-lock-renewer:{key}",
        daemon=True,
    )
    renewer.start()
    try:
        yield lock
    finally:
        stop_event.set()
        renewer.join(timeout=1)
        try:
            lock.release()
        except Exception:
            pass


def _task_enterprise_id(task_id: int) -> int | None:
    """事件流按企业命名空间，发布前先取一次企业 id；任务不存在时返回 None。"""
    with SessionLocal() as db:
        row = db.execute(
            select(AgentTask.enterprise_id).where(AgentTask.id == int(task_id))
        ).first()
    return int(row[0]) if row and row[0] is not None else None


@celery_app.task(name="agent.run_task", bind=True, max_retries=0)
def run_agent_task(self, task_id: int):
    lock_client = Redis.from_url(AGENT_LOCK_REDIS_URL, decode_responses=True, socket_connect_timeout=2, socket_timeout=5)
    with _held_redis_lock(lock_client, f"agent:lock:task:{int(task_id)}") as lock:
        if lock is None:
            return {"task_id": task_id, "status": "already_running"}
        enterprise_id = _task_enterprise_id(task_id)
        publish_event(task_id, "task_started", {"task_id": task_id}, enterprise_id=enterprise_id)

        def assert_lease_owned():
            if not lock.owned():
                raise RuntimeError("任务执行租约已丢失，停止执行；请重试恢复")

        try:
            checkpoint_url = validate_checkpoint_redis_url(AGENT_CHECKPOINT_REDIS_URL)
            with RedisSaver.from_conn_string(checkpoint_url) as checkpointer:
                checkpointer.setup()
                result = process_agent_task(
                    task_id,
                    session_factory=SessionLocal,
                    checkpointer=checkpointer,
                    event_publisher=lambda event_type, payload: publish_event(
                        task_id, event_type, payload, enterprise_id=enterprise_id,
                    ),
                    execution_guard=assert_lease_owned,
                )
                if result.get("status") in {"completed", "cancelled", "rejected"} and result.get("enterprise_id"):
                    try:
                        delete_checkpoint_thread(
                            checkpointer,
                            checkpoint_url,
                            f"enterprise:{int(result['enterprise_id'])}:task:{int(task_id)}",
                        )
                    except Exception:
                        publish_event(task_id, "checkpoint_cleanup_failed", {"task_id": int(task_id)},
                                      enterprise_id=enterprise_id)
            publish_event(task_id, "task_status", {"status": result.get("status")}, enterprise_id=enterprise_id)
            return {"task_id": int(task_id), "status": result.get("status")}
        except Exception as exc:
            publish_event(task_id, "task_failed", {"message": str(exc)[:500]}, enterprise_id=enterprise_id)
            raise


@celery_app.task(name="agent.dispatch_pending_outbox", acks_late=False)
def dispatch_pending_outbox(limit: int = 100):
    now = datetime.now()
    dispatched = 0
    with SessionLocal() as db:
        items = list(
            db.execute(
                select(AgentOutbox)
                .join(AgentTask, AgentTask.id == AgentOutbox.task_id)
                .where(
                    AgentOutbox.status.in_(["pending", "failed", "queued", "processing"]),
                    or_(AgentOutbox.next_attempt_at.is_(None), AgentOutbox.next_attempt_at <= now),
                    AgentTask.status.in_(
                        ["pending_dispatch", "queued", "running", "cancel_requested", "cancelled", "rejected"]
                    ),
                )
                .order_by(AgentOutbox.id.asc())
                .limit(max(1, min(int(limit), 500)))
            ).scalars().all()
        )
        for item in items:
            run_agent_task.apply_async(args=[item.task_id], queue="agent_tasks")
            item.status = "queued"
            item.attempts += 1
            item.next_attempt_at = now + timedelta(seconds=60)
            dispatched += 1
        db.commit()
    return {"dispatched": dispatched}


@celery_app.task(name="agent.run_evaluation", bind=True, max_retries=0)
def run_agent_evaluation(self, run_id: int):
    lock_client = Redis.from_url(AGENT_LOCK_REDIS_URL, decode_responses=True, socket_connect_timeout=2, socket_timeout=5)
    with _held_redis_lock(lock_client, f"agent:lock:evaluation:{int(run_id)}") as lock:
        if lock is None:
            return {"run_id": run_id, "status": "already_running"}
        result = execute_evaluation_run(run_id, session_factory=SessionLocal)
        return {"run_id": int(run_id), "status": result.get("status")}


@celery_app.task(name="agent.dispatch_pending_evaluations", acks_late=False)
def dispatch_pending_agent_evaluations(limit: int = 20):
    with SessionLocal() as db:
        run_ids = list(
            db.execute(
                select(AgentEvaluationRun.id)
                .where(AgentEvaluationRun.status.in_(["pending", "running"]))
                .order_by(AgentEvaluationRun.id.asc())
                .limit(max(1, min(int(limit), 100)))
            ).scalars().all()
        )
    lock_client = Redis.from_url(AGENT_LOCK_REDIS_URL, socket_connect_timeout=2, socket_timeout=5)
    dispatched = 0
    for run_id in run_ids:
        if lock_client.exists(f"agent:lock:evaluation:{int(run_id)}"):
            continue
        run_agent_evaluation.apply_async(args=[run_id], queue="agent_evaluations")
        dispatched += 1
    return {"dispatched": dispatched}
