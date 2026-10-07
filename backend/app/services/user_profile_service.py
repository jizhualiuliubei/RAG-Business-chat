"""
用户档案业务逻辑（重构：LLM 结构化提取记忆）
================
作用：实现"长期记忆"——用大模型从用户对话中提取结构化记忆（姓名/称呼/偏好等），
存到 user_profile 表，并在每次问答时读出来注入 prompt。

为什么用 LLM 提取（对齐 mem0/LightMem 主流 Agent 记忆架构）：
- 用户表达姓名的方式千变万化："我叫李明你记"、"以后叫我阿明"、"你可以喊我曦姐"、
  "我是市场部的"……正则永远枚举不完
- 大模型天然理解这些表达，让模型自己判断"用户说了什么值得记住"，输出结构化 JSON
- 相比正则，LLM 提取更能利用模型自身的理解能力

记忆存储：user_profile.fields 是 JSON 字典，可扩展任意字段：
  {"name": "李明", "nickname": "阿明", "preference": "喜欢简洁回答"}
"""
import json
import re

from sqlalchemy.orm import Session

from app.models.user_profile import UserProfile


# ============ LLM 结构化提取记忆 ============

def extract_memory_with_llm(text: str) -> dict:
    """用 Agent + ToolStrategy 从用户消息提取结构化记忆，返回 dict（失败返回空 dict）。

    只对"可能包含记忆"的消息调用（触发词预筛），控制成本：
    - 含"我叫/叫我/记住/我是/我的名字/记得/称呼/喊我/叫我/喜欢/偏好"等
    - 或长度较短的主动陈述（用户通常简短告知）

    实现：委托 core/memory_agent.py（Agent 结构化输出），
    由框架校验字段，避免手写 JSON 解析。
    """
    from app.core.memory_agent import extract_memory

    return extract_memory(text)


# ============ 触发词预筛（避免每条消息都调 LLM） ============

_TRIGGER_PATTERN = re.compile(
    r"(我叫|我是|叫我|记住|记得|我的名字|我的姓名|称呼|喊我|叫我|喜欢|偏好|"
    r"以后叫|你可以叫我|请叫我|我是.{0,6}部的|我在.{0,8}工作)"
)


def _should_extract(text: str) -> bool:
    """判断这条消息是否值得调 LLM 提取记忆（含触发词或像自我介绍）。"""
    if _TRIGGER_PATTERN.search(text):
        return True
    # 主动自我介绍（短句 + 含"我"），如"我是李明""我叫阿明"
    if len(text) <= 20 and "我" in text and re.search(r"(叫|是|记得|记住)", text):
        return True
    return False


def extract_and_save(db: Session, conversation_id: int, text: str) -> bool:
    """从用户消息用 LLM 提取记忆并保存。返回是否识别到新信息。"""
    # 预筛：不是自我陈述的消息不提取（省 LLM 调用）
    if not _should_extract(text):
        return False

    memory = extract_memory_with_llm(text)
    if not memory:
        return False

    profile = get_profile(db, conversation_id)
    if profile is None:
        profile = UserProfile(conversation_id=conversation_id)
        db.add(profile)

    fields = profile.get_fields()
    changed = False
    for key, val in memory.items():
        # 新信息才更新（避免反复写库）
        if val and fields.get(key) != val:
            fields[key] = val
            changed = True
    if changed:
        profile.set_fields(fields)
        db.commit()
    return changed


# ============ 读取注入 ============

def get_profile(db: Session, conversation_id: int) -> UserProfile | None:
    """读会话的用户档案，没有则返回 None。"""
    return (
        db.query(UserProfile)
        .filter(UserProfile.conversation_id == conversation_id)
        .first()
    )


def get_profile_text(db: Session, conversation_id: int) -> str:
    """读档案并格式化成 prompt 注入文本。

    返回如："用户姓名：李明；称呼：阿明；偏好：简洁回答"；没档案返回空串。
    """
    profile = get_profile(db, conversation_id)
    if not profile:
        return ""
    fields = profile.get_fields()
    parts = []
    # 字段显示名映射（中文更自然）
    label_map = {
        "name": "姓名",
        "nickname": "称呼",
        "role": "身份",
        "preference": "偏好",
    }
    for key in ("name", "nickname", "role", "preference"):
        val = fields.get(key)
        if val:
            label = label_map.get(key, key)
            parts.append(f"用户{label}：{val}")
    return "；".join(parts)


def clear_profile(db: Session, conversation_id: int) -> None:
    """清空会话的用户档案（重置记忆）。"""
    profile = get_profile(db, conversation_id)
    if profile:
        db.delete(profile)
        db.commit()
