"""
对话模型模块
================
作用：封装 DeepSeek 对话模型，为 create_agent 提供模型实例。

对应知识点：模型初始化（课程 chapter07/10，course_agent.py / course_rag_full.py）
- init_chat_model 是 LangChain 1.2.x 的新版统一入口（替代旧版 ChatOpenAI 等）
- DeepSeek 必须传 extra_body={"thinking": {"type": "disabled"}}，
  否则模型默认开启"深度思考"，工具调用会报错（课程里反复强调的坑）
"""
from langchain.chat_models import init_chat_model

from app.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_TEMPERATURE,
    LLM_MAX_RETRIES,
    LLM_REQUEST_TIMEOUT,
)

# 模型是重量级对象（内部带 HTTP 连接），按需创建后缓存复用。
# 分两个缓存：默认模型按温度（改写用 0、生成用 DEEPSEEK_TEMPERATURE），
# 企业自有模型按 (key, base_url, model, temperature)。
_default_models: dict = {}
_model_cache: dict = {}


def get_model(temperature: float | None = None):
    """懒加载 DeepSeek 模型（按温度缓存）。

    temperature 不传就用 DEEPSEEK_TEMPERATURE。只有查询改写 / 一问多求拆分
    会显式传 0（见 config.QUERY_REWRITE_TEMPERATURE）：那类调用要的是稳定，
    不要发挥 —— 否则同一问题每次检索到的证据都不一样。答案生成不传，仍用默认温度。
    """
    temp = DEEPSEEK_TEMPERATURE if temperature is None else float(temperature)
    if temp not in _default_models:
        _default_models[temp] = init_chat_model(
            model="deepseek:deepseek-v4-flash",
            api_key=DEEPSEEK_API_KEY,
            api_base=DEEPSEEK_BASE_URL,  # 注意：init_chat_model 用 api_base（init_embeddings 才用 base_url）
            temperature=temp,
            extra_body={"thinking": {"type": "disabled"}},  # 关键：必须关闭思考模式
            timeout=LLM_REQUEST_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )
    return _default_models[temp]


def get_model_for_config(
    api_key: str,
    base_url: str | None = None,
    model_name: str | None = None,
    temperature: float | None = None,
):
    """按企业配置创建/复用 DeepSeek 模型（温度也是缓存键的一部分）。"""
    temp = DEEPSEEK_TEMPERATURE if temperature is None else float(temperature)
    key = (api_key, base_url or DEEPSEEK_BASE_URL, model_name or "deepseek-v4-flash", temp)
    if key not in _model_cache:
        _model_cache[key] = init_chat_model(
            model=f"deepseek:{key[2]}",
            api_key=key[0],
            api_base=key[1],
            temperature=key[3],
            extra_body={"thinking": {"type": "disabled"}},
            timeout=LLM_REQUEST_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )
    return _model_cache[key]
