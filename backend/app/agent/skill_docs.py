"""按需加载 skills/<skill_id>/SKILL.md，把它注入模型上下文。

为什么需要这个模块：SKILL.md 如果只是给人看的文档，它就不驱动任何行为 ——
改了正文里的步骤，模型收到的指令不会变，那「用 SKILL.md 组织能力」就名不副实。
这里做的是真正的**按需加载**：路由选中哪个 Skill，就只把那个 Skill 的正文
拼进提示词（其余 Skill 只暴露 id 与描述），避免把所有 Skill 正文一次性塞进上下文。

正文按 (skill_id, 文件修改时间) 缓存：改文件即失效，不用重启。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

SKILLS_DIR = BASE_DIR / "skills"

# key: (skill_id, mtime_ns) —— 文件改了自然换 key，等于失效
_cache: dict[tuple[str, int], str] = {}


def skill_document_path(skill_id: str) -> Path:
    """目录名用规范形式（小写 + 连字符）：内部 id `access_request` 对应目录
    `access-request`。内部 id 保持不变，免得动数据库里已有任务的 skill_id。"""
    return SKILLS_DIR / skill_id.replace("_", "-") / "SKILL.md"


def load_skill_body(skill_id: str) -> str:
    """读取 Skill 正文（剥掉 YAML frontmatter）。读不到就返回空串。

    读不到不能让任务失败：Skill 文件缺失是配置问题，应该降级成"没有额外规范"
    继续跑，并在提示词里如实反映，而不是把整条链路打挂。
    """
    path = skill_document_path(skill_id)
    try:
        stat = path.stat()
        key = (skill_id, stat.st_mtime_ns)
        if key not in _cache:
            _cache.clear()  # 只保留当前版本的少量文件
            _cache[key] = _strip_frontmatter(path.read_text(encoding="utf-8"))
        return _cache[key]
    except Exception as exc:
        # 不能静默：加载不到 Skill 正文等于这次任务少了一整套操作规范，
        # 必须留下可查的痕迹（调用方还会在运行轨迹里落一条记录）。
        logger.warning(
            "未加载到 Skill 文档，本次没有该 Skill 的额外规范：%s（%s）",
            skill_document_path(skill_id), type(exc).__name__,
        )
        return ""


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text.strip()
    parts = text.split("---\n", 2)
    return parts[2].strip() if len(parts) >= 3 else text.strip()


def skill_prompt_block(skill_id: str) -> str:
    """给提示词用的 Skill 规范段落；没有正文时返回空串。"""
    body = load_skill_body(skill_id)
    if not body:
        return ""
    return f"\n\n【{skill_id} 的操作规范（来自 skills/{skill_id}/SKILL.md，以此为准）】\n{body}\n"
