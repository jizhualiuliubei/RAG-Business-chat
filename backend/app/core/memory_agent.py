"""
记忆提取 Agent（结构化输出）
================
作用：用 Agent + ToolStrategy 从用户对话中提取结构化记忆（姓名/称呼/身份/偏好）。

对应知识点：Agent 结构化输出（课程 chapter07，course_agent.py）
- create_agent(response_format=ToolStrategy(Schema))：把 Pydantic 模型伪装成工具，
  模型通过"工具调用"输出结构化对象 —— DeepSeek 不支持原生 response_format，
  必须用 ToolStrategy（课程明确说明）。
- result["structured_response"]：拿到校验后的结构化对象，无需手动解析 JSON。

为什么用 Agent 而不是手动解析：
- 手动解析要写 JSON 提取正则、处理模型带 ```json 包裹等脏输出
- Agent + ToolStrategy 由框架负责：模型直接输出 Schema 实例，字段校验、缺失处理全自动
- 工程收益：使用 LangChain 的 ToolStrategy 让模型结构化输出，避免手写 JSON 解析。
"""
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from pydantic import BaseModel, Field

from app.core.llm import get_model

# Agent 是重量级对象，只初始化一次
_agent = None


class MemoryExtract(BaseModel):
    """从用户消息中提取的记忆信息"""
    name: str = Field(default="", description="用户的全名，如「我叫李明」→李明")
    nickname: str = Field(default="", description="用户希望被称呼的名字，如「叫我阿明」→阿明")
    role: str = Field(default="", description="用户的身份/角色，如「我是市场部的」→市场部员工")
    preference: str = Field(default="", description="明确表达的偏好，如「回答要简洁」→简洁")


def _get_memory_agent():
    """懒加载记忆提取 Agent（绑定结构化输出）。"""
    global _agent
    if _agent is None:
        _agent = create_agent(
            model=get_model(),
            response_format=ToolStrategy(MemoryExtract),
            system_prompt=(
                "你是记忆提取器。分析用户的这句话，判断是否包含【值得长期记住的用户信息】"
                "（姓名、昵称、称呼、身份/角色、偏好等）。"
                "只提取用户明确提供的事实，不要猜测，没有的字段保持空字符串。"
            ),
        )
    return _agent


def extract_memory(text: str) -> dict:
    """用 Agent 结构化提取记忆，返回 dict（失败返回空 dict）。"""
    try:
        result = _get_memory_agent().invoke({
            "messages": [{"role": "user", "content": f"用户说：{text}"}],
        })
        data = result["structured_response"]  # MemoryExtract 实例
        if data is None:
            return {}
        return {k: v for k, v in data.model_dump().items() if v}
    except Exception:
        return {}
