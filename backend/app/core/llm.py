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

from app.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_TEMPERATURE

# 模块级单例：模型是重量级对象（内部有 HTTP 连接），只初始化一次
_model = None
_model_cache = {}


def get_model():
    """懒加载 DeepSeek 模型单例。"""
    global _model
    if _model is None:
        _model = init_chat_model(
            model="deepseek:deepseek-v4-flash",
            api_key=DEEPSEEK_API_KEY,
            api_base=DEEPSEEK_BASE_URL,  # 注意：init_chat_model 用 api_base（init_embeddings 才用 base_url）
            temperature=DEEPSEEK_TEMPERATURE,
            extra_body={"thinking": {"type": "disabled"}},  # 关键：必须关闭思考模式
        )
    return _model


def get_model_for_config(api_key: str, base_url: str | None = None, model_name: str | None = None):
    """按企业配置创建/复用 DeepSeek 模型。"""
    key = (api_key, base_url or DEEPSEEK_BASE_URL, model_name or "deepseek-v4-flash", DEEPSEEK_TEMPERATURE)
    if key not in _model_cache:
        _model_cache[key] = init_chat_model(
            model=f"deepseek:{key[2]}",
            api_key=key[0],
            api_base=key[1],
            temperature=key[3],
            extra_body={"thinking": {"type": "disabled"}},
        )
    return _model_cache[key]
