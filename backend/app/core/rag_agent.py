"""
检索工具化 Agent（多工具 Agent RAG）
================
作用：把多个能力封装成工具，让 LLM **自己决定**何时调用哪个：
- search_knowledge_base：检索企业知识库
- get_current_time    ：返回当前日期时间（解决时效性问题）
- get_user_profile    ：读取当前会话的用户记忆（姓名/称呼/偏好）

与 P0"手动检索"（core/rag.py）的对比：
- 手动检索：代码控制"检索→拼上下文→生成"，确定性高、阈值可靠
- 工具化 Agent：Agent 拿到多个工具后自主规划，模型能力强时更灵活，
  能自己决定"这个问题需要检索吗 / 需要时间吗 / 需要用户信息吗"

对应知识点：Agent 绑定工具（课程 chapter07，course_agent.py）
- @tool(parse_docstring=True) 定义工具，docstring 写清参数
- create_agent(model, tools=[...]) 把多个工具挂给 Agent
- Agent 在对话中自主调用工具，最终给出回答
"""
from datetime import datetime

from langchain.agents import create_agent
from langchain_core.tools import tool

from app.core import milvus_store
from app.core.embeddings import embed_query
from app.core.hybrid_search import _dedup_hits, fuse_dual
from app.core.llm import get_model, get_model_for_config
from app.config import TOP_K, SCORE_THRESHOLD

# Agent 是重量级对象，只初始化一次
_agent = None


@tool(parse_docstring=True)
def search_knowledge_base(query: str) -> str:
    """
    在企业知识库中检索与问题相关的文档片段。

    Args:
        query: 用户的搜索关键词或完整问题
    """
    from langchain_core.runnables.config import RunnableConfig
    from langgraph.config import get_config

    # 从运行 config 读取当前知识库（Agent 调用时通过 config 注入）
    kb_id = 1
    enterprise_id = None
    embed_config = None
    allowed_doc_ids = None
    try:
        cfg: RunnableConfig = get_config()
        kb_id = cfg.get("configurable", {}).get("kb_id", 1)
        enterprise_id = cfg.get("configurable", {}).get("enterprise_id")
        embed_config = cfg.get("configurable", {}).get("embed_config")
        allowed_doc_ids = cfg.get("configurable", {}).get("allowed_doc_ids")
    except Exception:
        kb_id = 1

    if allowed_doc_ids is not None and not allowed_doc_ids:
        return "知识库中没有检索到相关内容。"

    # 复用手动检索的混合检索逻辑（双通道召回：向量 ∪ BM25 全库 + 章节去重）
    query_vector = embed_query(str(query), model_config=embed_config)
    candidates = milvus_store.search(
        query_vector,
        top_k=TOP_K,
        kb_id=kb_id,
        candidate_k=max(TOP_K * 8, 40),
        enterprise_id=enterprise_id,
        doc_ids=allowed_doc_ids,
    )
    all_texts = milvus_store.list_all_texts(kb_id, enterprise_id=enterprise_id, doc_ids=allowed_doc_ids)
    fused = fuse_dual(query, candidates, all_texts, TOP_K)
    fused = _dedup_hits(fused)[:TOP_K]

    # 阈值过滤：低于阈值说明知识库里没有相关内容，返回明确提示
    hits = [h for h in fused if h["distance"] >= SCORE_THRESHOLD]
    if not hits:
        return "知识库中没有检索到相关内容。"

    # 按引用编号排版返回（和手动检索的 prompt 格式一致，方便 Agent 标注 [n]）
    blocks = []
    for i, h in enumerate(hits, 1):
        entity = h["entity"]
        blocks.append(
            f"[{i}] 出处：{entity.get('source', '未知')}\n{entity.get('text', '')}"
        )
    return "\n\n".join(blocks)


@tool(parse_docstring=True)
def get_current_time() -> str:
    """
    获取当前日期和时间，用于回答涉及"今天、几号、星期几、几点"等时效性问题。

    Returns:
        当前日期时间字符串，如 "2026年08月27日 14:30"
    """
    return datetime.now().strftime("%Y年%m月%d日 %H:%M")


@tool(parse_docstring=True)
def get_user_profile(question: str) -> str:
    """
    获取当前会话中已记录的用户信息（姓名/称呼/身份/偏好）。

    Args:
        question: 用户的提问（用于在档案中检索相关记忆）
    """
    # 通过全局注入的 profile_text（见 ask_agent_rag）返回；此处默认返回空
    # 实际值由调用方通过 system_prompt 注入，工具本身作为"显式能力"存在
    return "用户档案：暂无额外信息（由系统在提示词中注入）。"


def _build_system_prompt(profile_text: str = "") -> str:
    """构造带用户记忆的 system prompt（记忆通过提示词注入，工具负责检索/时间）。"""
    prompt = (
        "你是一个智能的企业知识库问答助手，同时具备 DeepSeek 大语言模型的通用能力。"
        "回答规则："
        "1. 回答需要企业知识时，先调用 search_knowledge_base 工具检索，严格依据工具返回内容回答，并在句末标注编号[1][2]，不要编造。"
        "2. 涉及日期、时间、星期等时效性信息时，调用 get_current_time 工具获取当前真实时间。"
        "3. 涉及用户本人（姓名/称呼/身份）时，优先使用用户档案信息。"
        "4. 如果工具返回'没有检索到相关内容'，先明确说明：该企业知识库中没有相关内容，然后用你自己的通用能力自然回答用户的问题。"
        "回答要简洁、准确、有条理，使用中文。"
    )
    if profile_text:
        prompt = f"【用户档案】{profile_text}\n\n{prompt}"
    return prompt


def _agent_model(model_config: dict | None = None):
    if model_config and not model_config.get("platform"):
        return get_model_for_config(
            model_config["api_key"],
            model_config.get("base_url"),
            model_config.get("model"),
        )
    return get_model()


def get_agent_rag(profile_text: str = "", kb_id: int = 1, model_config: dict | None = None):
    """创建工具化 Agent（绑定多个工具）。

    profile_text：当前会话的用户档案，注入 system prompt（长期记忆）。
    kb_id：当前选中的知识库，通过 config 注入给检索工具（多知识库隔离）。
    注意：因为 system prompt / 知识库依赖会话上下文，每次问答重建
    （工具 Agent 初始化成本可接受，换来正确的记忆 + 知识库注入）。
    """
    return create_agent(
        model=_agent_model(model_config),
        tools=[search_knowledge_base, get_current_time, get_user_profile],
        system_prompt=_build_system_prompt(profile_text),
    )


def ask_agent_rag(
    question: str,
    profile_text: str = "",
    kb_id: int = 1,
    enterprise_id: int | None = None,
    model_config: dict | None = None,
    embed_config: dict | None = None,
    allowed_doc_ids: list[int] | None = None,
) -> str:
    """用多工具 Agent 回答一个问题（Agent 自主决定调用哪些工具）。

    通过 config.configurable.kb_id 把当前知识库传给检索工具，
    让 Agent 只检索当前选中的知识库（多知识库隔离）。
    """
    agent = get_agent_rag(profile_text, kb_id, model_config=model_config)
    config = {
        "configurable": {
            "kb_id": kb_id,
            "enterprise_id": enterprise_id,
            "embed_config": embed_config,
            "allowed_doc_ids": allowed_doc_ids,
            "thread_id": "agent_rag",
        }
    }
    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config=config,
    )
    return result["messages"][-1].content
