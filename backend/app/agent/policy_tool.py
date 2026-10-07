"""把现有 RAG 安全封装为 Agent 的只读制度检索工具。"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.core import rag
from app.services import enterprise_service, qa_service


# splitter 写在每个片段首行的结构头：`来源：文档 > 章节 > 条款号`。
# 章节或条款号可能缺失（整章成块时没有条款号），所以三段都可选。
_CLAUSE_RE = re.compile(r"^[A-Z]{2,8}-\d{2,3}-\d{3}$")


def _parse_structure_head(text: str) -> tuple[str, str]:
    """从片段结构头解析 (章节, 条款号)；解析不出就返回空串。

    结构头有三种合法形态，都要认：
        来源：员工手册.docx > 入职管理 > HR-01-001   → 章节 + 条款号
        来源：员工手册.docx > HR-01-001              → 只有条款号
        来源：员工手册.docx > 入职管理                → 只有章节
    判据是**最后一段是不是条款号形态**，而不是段数 —— 按段数硬套会把
    「文档 > 条款号」里的条款号当成章节，条款号则丢成空。
    """
    first_line = str(text or "").split("\n", 1)[0].strip()
    if not first_line.startswith("来源："):
        return "", ""
    parts = [part.strip() for part in first_line[len("来源："):].split(">")]
    rest = parts[1:]
    clause_id = ""
    if rest and _CLAUSE_RE.match(rest[-1]):
        clause_id = rest[-1]
        rest = rest[:-1]
    return (rest[-1] if rest else ""), clause_id


def _with_structure(contexts: list[dict]) -> list[dict]:
    """给 RAG 返回的上下文补上章节与条款号。

    rag.build_context 只返回 text/source/chunk_id/score/kb_name，而下游
    （执行档案、制度核对、执行契约）要按「文档 / 章节 / 条款号」定位依据。
    这里在 Agent 侧补全，不改动 RAG 核心。
    """
    for item in contexts or []:
        if "section" in item and "clause_id" in item:
            continue
        section, clause_id = _parse_structure_head(item.get("text", ""))
        item.setdefault("section", section)
        item.setdefault("clause_id", clause_id)
    return contexts


def search_policy(
    db: Session,
    *,
    enterprise_id: int,
    kb_ids: list[int],
    query: str,
) -> list[dict]:
    if not kb_ids:
        return []
    model_config = enterprise_service.require_enterprise_deepseek_key(db, enterprise_id)
    embed_config = enterprise_service.require_enterprise_siliconflow_key(db, enterprise_id)
    allowed_doc_ids = qa_service.active_document_ids_for_scope(db, kb_ids, enterprise_id)
    _, contexts = rag.retrieve_contexts(
        query,
        kb_id=kb_ids,
        enterprise_id=enterprise_id,
        model_config=model_config,
        embed_config=embed_config,
        allowed_doc_ids=allowed_doc_ids,
    )
    return _with_structure(list(contexts or []))


def _notify(on_search, *, query, hits, status, duration_ms, error="") -> None:
    """把一次工具调用报给观测钩子。钩子自己出错不影响检索 —— 它只是旁路。"""
    if on_search is None:
        return
    try:
        on_search({"query": query, "hits": hits, "status": status,
                   "duration_ms": duration_ms, "error": error})
    except Exception:
        pass


def make_policy_search_tool(
    *,
    session_factory,
    enterprise_id: int,
    kb_ids: list[int],
    collected: list[dict],
    max_calls: int = 4,
    on_search=None,
):
    """把只读制度检索包装成模型可调用的工具。

    两条边界都在这里守住：
    - **多租户上下文由服务端注入**（捕获在闭包里），不作为工具参数。模型没有
      任何途径表达 enterprise_id / kb_ids，隔离边界不靠模型纪律。
    - **检索次数有上限**，防止模型无休止检索。达到上限后工具返回提示文本，
      而不是抛异常 —— 让模型自己收敛到「基于已有证据作答」。

    每次检索到的片段同时追加进 `collected`，这样执行档案能记录模型**实际**
    用过哪些制度，而不是只记录代码预设的那一次检索。

    `on_search(query, hits)` 是给调用方的观测钩子：模型每次自主检索都会回调，
    用来把「模型决定查什么」记进事件流，让运行轨迹里看得见模型的检索动作。
    """
    from langchain_core.tools import tool
    import time

    calls = {"count": 0}

    @tool
    def policy_search(query: str) -> str:
        """在企业制度知识库里检索与当前申请相关的条款。

        Args:
            query: 检索用的关键词或完整问题，例如「采购金额分级 审批权限」
        """
        if calls["count"] >= max_calls:
            return "本轮检索次数已达上限。请基于已经拿到的证据给出结论；证据不足就明确说明缺什么。"
        calls["count"] += 1
        started = time.perf_counter()
        try:
            with session_factory() as db:
                items = search_policy(
                    db,
                    enterprise_id=enterprise_id,
                    kb_ids=list(kb_ids or []),
                    query=query,
                )
        except Exception as exc:
            # 失败也要留痕：模型这次检索没拿到东西，轨迹里必须看得见。
            _notify(on_search, query=query, hits=0, status="failed",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error=type(exc).__name__)
            raise
        collected.extend(items)
        _notify(on_search, query=query, hits=len(items), status="completed",
                duration_ms=int((time.perf_counter() - started) * 1000))
        if not items:
            return "没有检索到相关制度条款。可以换个说法再试；如果仍然没有，请明确说明制度依据不足。"
        blocks = []
        for index, item in enumerate(items, start=1):
            location = " / ".join(
                str(part)
                for part in (item.get("source"), item.get("section"), item.get("clause_id"))
                if part
            )
            blocks.append(f"[{index}] {location or '未命名来源'}\n{item.get('text', '')}")
        return "\n\n".join(blocks)

    return policy_search



