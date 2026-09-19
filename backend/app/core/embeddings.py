"""
嵌入模型模块
================
作用：封装 BGE-M3 嵌入模型，提供"批量文档向量化"和"单句问题向量化"两个方法。

技术栈：嵌入模型
- init_embeddings 是 LangChain 1.2.x 的新版统一入口（和 init_chat_model 同级）
- 硅基流动走 OpenAI 兼容协议，所以模型名写 "openai:BAAI/bge-m3"
- 免费版不带 Pro/ 前缀；Pro 版为付费增强（EMBED_MODEL_NAME 在 .env 里可配）
- 维度固定 1024，建 Milvus collection 时 dimension 必须一致
"""
from langchain.embeddings import init_embeddings

from app.config import (
    EMBED_MODEL_NAME,
    LLM_MAX_RETRIES,
    LLM_REQUEST_TIMEOUT,
    SILICONFLOW_API_KEY,
    SILICONFLOW_BASE_URL,
)

# 模块级单例：嵌入模型是线程安全的、重量级对象，只初始化一次
# （每次调用都会重新建 HTTP 连接池，浪费资源）
_embed_model = None
_embed_model_cache = {}


def get_embed_model():
    """懒加载嵌入模型单例：第一次调用时才真正初始化。"""
    global _embed_model
    if _embed_model is None:
        _embed_model = init_embeddings(
            model=f"openai:{EMBED_MODEL_NAME}",
            api_key=SILICONFLOW_API_KEY,
            # 注意：init_embeddings 用 base_url 参数（init_chat_model 才是 api_base）
            # 两个函数的参数名不同，写错会连不上硅基流动
            base_url=SILICONFLOW_BASE_URL,
            timeout=LLM_REQUEST_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )
    return _embed_model


def get_embed_model_for_config(api_key: str, base_url: str | None = None, model_name: str | None = None):
    """按企业配置创建/复用嵌入模型。"""
    key = (api_key, base_url or SILICONFLOW_BASE_URL, model_name or EMBED_MODEL_NAME)
    if key not in _embed_model_cache:
        _embed_model_cache[key] = init_embeddings(
            model=f"openai:{key[2]}",
            api_key=key[0],
            base_url=key[1],
            timeout=LLM_REQUEST_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )
    return _embed_model_cache[key]


def _pick_model(model_config: dict | None = None):
    """按 model_config 选平台模型还是企业自己的模型。"""
    if model_config and not model_config.get("platform"):
        return get_embed_model_for_config(
            model_config["api_key"],
            model_config.get("base_url"),
            model_config.get("model"),
        )
    return get_embed_model()


def embed_documents(texts: list, model_config: dict | None = None) -> list:
    """批量向量化：建库时对一批 chunk 文本生成向量列表。

    参数：texts —— List[str]，如 [chunk1, chunk2, ...]
    返回：List[List[float]]，和 texts 一一对应
    """
    return _pick_model(model_config).embed_documents(texts)


def embed_query(query: str, model_config: dict | None = None) -> list:
    """单句向量化：用户提问时对一个 query 生成向量。

    返回：List[float]，长度 1024
    """
    return _pick_model(model_config).embed_query(query)


def embed_queries(queries: list, model_config: dict | None = None) -> list:
    """把多条检索 Query 一次性向量化：固定 1 次 HTTP 往返。

    为什么需要：多路改写 + 一问多求会产生 1~5 条 Query，逐条调用 embed_query 就是
    1~5 次网络往返；批量后固定 1 次，省掉的是这几次往返的等待。

    等价性：
    - 单条时 embed_documents([q]) 与 embed_query(q) 向量**完全相同**（分量差 0）
    - 多条批量与逐条相比只有极小的数值差异，余弦相似度接近 1，不影响排序

    返回：List[List[float]]，与入参顺序一一对应。
    """
    items = [str(q) for q in (queries or [])]
    if not items:
        return []
    try:
        return [list(vec) for vec in _pick_model(model_config).embed_documents(items)]
    except Exception:
        # 批量失败就退回逐条：宁可慢一点，也不要因为一次批量失败就把证据全丢掉
        return [embed_query(item, model_config=model_config) for item in items]
