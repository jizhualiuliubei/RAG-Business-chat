"""Redis Stream 任务事件，供工作台断线续传。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterator

from redis import Redis

from app.config import (
    AGENT_CHECKPOINT_REDIS_URL,
    AGENT_EVENT_REDIS_URL,
    AGENT_EVENT_STREAM_MAXLEN,
    AGENT_EVENT_STREAM_TTL_SECONDS,
    AGENT_LOCK_REDIS_URL,
    CELERY_BROKER_URL,
)


AGENT_WORKER_HEARTBEAT_KEY = "agent:health:worker"


def _client() -> Redis:
    return Redis.from_url(
        AGENT_EVENT_REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=6,
    )


def stream_key(task_id: int, enterprise_id: int) -> str:
    """事件流 key 带企业命名空间。

    这和 checkpoint 用的 `enterprise:{id}:task:{id}` 是同一套规则：裸任务号
    不出现在 Redis key 里，Redis 内也不存在「只凭任务号就能定位」的键。
    真正的访问控制仍由 API 层做（读事件前先校验任务归属），这里是不让命名
    空间本身成为可枚举的入口。
    """
    return f"agent:events:{int(enterprise_id)}:{int(task_id)}"


def publish_event(
    task_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    enterprise_id: int,
) -> str | None:
    try:
        client = _client()
        key = stream_key(task_id, enterprise_id)
        event_id = client.xadd(
            key,
            {
                "type": event_type,
                "payload": json.dumps(payload or {}, ensure_ascii=False, default=str),
                "created_at": datetime.now().isoformat(),
            },
            maxlen=AGENT_EVENT_STREAM_MAXLEN,
            approximate=True,
        )
        # 每条事件都刷新 TTL。原先只在终态事件上设过期，卡在 waiting_input 或
        # 中断态的流会永久留在 Redis 里 —— 条数有 MAXLEN 兜，生命周期得靠 TTL。
        try:
            client.expire(key, max(60, AGENT_EVENT_STREAM_TTL_SECONDS))
        except Exception:
            pass
        return event_id
    except Exception:
        # Redis 故障只影响实时推送，不能连累 MySQL 里的正式状态；
        # 前端轮询任务详情接口仍能补齐状态，丢的只是事件流本身。
        return None


def read_events(
    task_id: int,
    last_event_id: str = "0-0",
    block_ms: int = 5000,
    *,
    enterprise_id: int,
) -> Iterator[dict]:
    response = _client().xread(
        {stream_key(task_id, enterprise_id): last_event_id or "0-0"},
        count=100,
        block=block_ms,
    )
    # xread 在 block 超时、流已过期或不存在时返回 None，直接迭代会 TypeError。
    for _, messages in (response or []):
        for event_id, fields in messages:
            try:
                payload = json.loads(fields.get("payload") or "{}")
            except (TypeError, ValueError):
                payload = {}
            yield {
                "id": event_id,
                "type": fields.get("type") or "message",
                "payload": payload,
                "created_at": fields.get("created_at") or "",
            }


def runtime_health() -> dict:
    """同时检查 Redis 各用途端点和由 Beat 驱动的 Worker 心跳。"""
    endpoints = {
        "broker": CELERY_BROKER_URL,
        "checkpoint": AGENT_CHECKPOINT_REDIS_URL,
        "events": AGENT_EVENT_REDIS_URL,
        "locks": AGENT_LOCK_REDIS_URL,
    }
    checks: dict[str, bool] = {}
    lock_client = None
    errors: list[str] = []
    for name, url in endpoints.items():
        try:
            client = Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
            checks[name] = bool(client.ping())
            if name == "locks":
                lock_client = client
        except Exception as exc:
            checks[name] = False
            errors.append(f"{name}: {str(exc)[:120]}")

    redis_available = all(checks.values())
    worker_available = False
    if redis_available and lock_client is not None:
        try:
            worker_available = bool(lock_client.get(AGENT_WORKER_HEARTBEAT_KEY))
        except Exception as exc:
            errors.append(f"worker: {str(exc)[:120]}")

    available = redis_available and worker_available
    if available:
        message = "Redis、Celery Worker 与 Beat 均可用"
    elif not redis_available:
        message = "Agent Redis 基础设施不可用"
    else:
        message = "Celery Worker 或 Beat 心跳缺失"
    return {
        "available": available,
        "redis_available": redis_available,
        "worker_available": worker_available,
        "components": checks,
        "message": message,
        "errors": errors,
    }
