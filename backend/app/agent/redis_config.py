"""Redis configuration constraints for the Agent runtime."""

import re
from urllib.parse import urlparse

from redis import Redis


_CHECKPOINT_THREAD_RE = re.compile(r"^enterprise:\d+:task:\d+$")
_CHECKPOINT_KEY_PREFIXES = (
    "checkpoint",
    "checkpoint_write",
    "checkpoint_latest",
    "write_keys_zset",
)


def validate_checkpoint_redis_url(url: str) -> str:
    """Ensure LangGraph RedisSaver uses Redis DB 0.

    RedisSaver creates RediSearch indexes, and Redis Search only supports the
    default logical database. Other Agent concerns can still use separate
    logical databases because they do not create search indexes.
    """

    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise ValueError("Agent Checkpoint Redis URL 必须是有效的 redis:// 或 rediss:// 地址")
    path = parsed.path.strip("/")
    try:
        database = int(path or "0")
    except ValueError as exc:
        raise ValueError("Agent Checkpoint Redis URL 的数据库编号必须是整数") from exc
    if database != 0:
        raise ValueError(
            "LangGraph Redis Checkpoint 依赖 RediSearch，必须使用 Redis DB 0；"
            "请把 AGENT_CHECKPOINT_REDIS_URL 的结尾改为 /0"
        )
    return value


def delete_checkpoint_thread(checkpointer, redis_url: str, thread_id: str) -> int:
    """Delete a terminal task's checkpoints, including unindexed orphan keys."""

    if not _CHECKPOINT_THREAD_RE.fullmatch(str(thread_id or "")):
        raise ValueError("Checkpoint thread_id 必须符合 enterprise:<id>:task:<id>")

    url = validate_checkpoint_redis_url(redis_url)
    checkpointer.delete_thread(thread_id)
    client = Redis.from_url(url, decode_responses=True)
    deleted = 0
    for prefix in _CHECKPOINT_KEY_PREFIXES:
        batch: list[str] = []
        for key in client.scan_iter(match=f"{prefix}:{thread_id}:*", count=500):
            batch.append(key)
            if len(batch) >= 500:
                deleted += int(client.unlink(*batch) or 0)
                batch.clear()
        if batch:
            deleted += int(client.unlink(*batch) or 0)
    return deleted
