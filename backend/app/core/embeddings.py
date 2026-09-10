"""
嵌入模型模块
================
作用：封装 BGE-M3 嵌入模型，提供"批量文档向量化"和"单句问题向量化"两个方法。

对应知识点：嵌入模型（课程 chapter10-03，course_rag_embed.py）
- init_embeddings 是 LangChain 1.2.x 的新版统一入口（和 init_chat_model 同级）
- 硅基流动走 OpenAI 兼容协议，所以模型名写 "openai:BAAI/bge-m3"
- 免费版不带 Pro/ 前缀；Pro 版为付费增强（EMBED_MODEL_NAME 在 .env 里可配）
- 维度固定 1024，建 Milvus collection 时 dimension 必须一致
"""
from langchain.embeddings import init_embeddings

from app.config import (
    EMBED_MODEL_NAME,
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
            # 这是课程代码里的细节差异，写错会连不上硅基流动
            base_url=SILICONFLOW_BASE_URL,
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
        )
    return _embed_model_cache[key]


def embed_documents(texts: list, model_config: dict | None = None) -> list:
    """批量向量化：建库时对一批 chunk 文本生成向量列表。

    参数：texts —— List[str]，如 [chunk1, chunk2, ...]
    返回：List[List[float]]，和 texts 一一对应
    """
    if model_config and not model_config.get("platform"):
        return get_embed_model_for_config(
            model_config["api_key"],
            model_config.get("base_url"),
            model_config.get("model"),
        ).embed_documents(texts)
    return get_embed_model().embed_documents(texts)


def embed_query(query: str, model_config: dict | None = None) -> list:
    """单句向量化：用户提问时对一个 query 生成向量。

    返回：List[float]，长度 1024
    """
    if model_config and not model_config.get("platform"):
        return get_embed_model_for_config(
            model_config["api_key"],
            model_config.get("base_url"),
            model_config.get("model"),
        ).embed_query(query)
    return get_embed_model().embed_query(query)
