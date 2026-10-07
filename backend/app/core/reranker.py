"""Rerank 重排：对混合检索候选做二阶段排序。"""
import json
from urllib import request

from app.config import (
    ENABLE_RERANK,
    RERANK_MODEL_NAME,
    RERANK_TIMEOUT_SECONDS,
    SILICONFLOW_API_KEY,
    SILICONFLOW_BASE_URL,
)


def rerank_hits(query: str, hits: list, top_k: int, model_config: dict | None = None) -> list:
    """使用 SiliconFlow rerank 模型重排候选；失败时回退原排序。"""
    if not ENABLE_RERANK or not hits:
        return hits[:top_k]

    api_key = _api_key(model_config)
    if not api_key:
        return hits[:top_k]

    documents = [hit.get("entity", {}).get("text", "") for hit in hits]
    try:
        scores = _call_siliconflow_rerank(query, documents, api_key, _base_url(model_config), top_k)
    except Exception:
        return hits[:top_k]
    if not scores:
        return hits[:top_k]

    by_index = {item["index"]: item["score"] for item in scores}
    ranked = []
    for index, hit in enumerate(hits):
        if index not in by_index:
            continue
        score = float(by_index[index])
        entity = {**hit.get("entity", {}), "rerank_score": score}
        ranked.append({
            **hit,
            "entity": entity,
            "distance": max(float(hit.get("distance", 0.0)), score),
            "_rerank_score": score,
        })
    if not ranked:
        return hits[:top_k]

    ranked.sort(key=lambda item: (item["_rerank_score"], float(item.get("distance", 0.0))), reverse=True)
    return [{k: v for k, v in item.items() if k != "_rerank_score"} for item in ranked[:top_k]]


def _api_key(model_config: dict | None) -> str:
    if model_config and not model_config.get("platform"):
        return model_config.get("api_key", "")
    return SILICONFLOW_API_KEY


def _base_url(model_config: dict | None) -> str:
    if model_config and not model_config.get("platform"):
        return model_config.get("base_url") or SILICONFLOW_BASE_URL
    return SILICONFLOW_BASE_URL


def _call_siliconflow_rerank(query: str, documents: list[str], api_key: str, base_url: str, top_k: int) -> list[dict]:
    payload = json.dumps({
        "model": RERANK_MODEL_NAME,
        "query": query,
        "documents": documents,
        "top_n": min(top_k, len(documents)),
        "return_documents": False,
    }).encode("utf-8")
    url = base_url.rstrip("/") + "/rerank"
    req = request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=RERANK_TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [
        {"index": int(item["index"]), "score": float(item.get("relevance_score", 0.0))}
        for item in data.get("results", [])
        if "index" in item
    ]
