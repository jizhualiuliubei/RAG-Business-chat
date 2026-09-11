# Agent 机制说明

## Agent 在项目中的位置

本项目采用 Agentic Workflow 架构（Workflow 固定骨架 + 关键节点嵌 Agent），对外提供两条入口：

| 链路 | 入口 | 特点 |
| --- | --- | --- |
| Workflow（确定性骨架） | `/api/qa/ask-stream`（前端入口） | 后端固定执行检索、重排、上下文工程和生成，稳定可控，是首页默认主链路。 |
| Agent（自主决策节点） | `/api/qa/agent-ask` | 模型拿到工具后自主决定是否检索、查时间、调用工具，适合作为可扩展 Agent 能力。 |

企业制度问答默认使用 Workflow（确定性骨架），因为它更容易控制证据、引用、拒答和评测。Agent（自主决策节点）作为扩展链路，用于展示工具调用、规划和多能力组合。

## 本地 @tool 工具

`backend/app/core/rag_agent.py` 中使用 LangChain `@tool(parse_docstring=True)` 把普通 Python 函数变成 Agent 可调用工具。

| 工具 | 参数 | 作用 |
| --- | --- | --- |
| `search_knowledge_base` | `query: str` | 检索当前企业当前知识库，返回带编号的证据片段。 |
| `get_current_time` | 无 | 返回当前日期时间，用于回答今天、几号、星期几等问题。 |
| `get_user_profile` | `question: str` | 注册在 Agent 工具清单中的档案工具；档案内容由调用方通过 system prompt 注入提供。 |

工具注册方式：

```python
create_agent(
    model=_agent_model(model_config),
    tools=[search_knowledge_base, get_current_time, get_user_profile],
    system_prompt=_build_system_prompt(profile_text),
)
```

`search_knowledge_base` 内部会从 `config.configurable` 读取：

- `kb_id`：当前知识库。
- `enterprise_id`：当前企业。
- `embed_config`：当前企业 SiliconFlow 配置。
- `allowed_doc_ids`：当前有效文档范围。

因此 Agent 工具调用仍然受企业和知识库隔离约束。

## MCP 工具

`backend/app/core/mcp_agent.py` 通过 MCP 协议加载外部工具。MCP server 位于 `backend/mcp/calc_server.py`，以 stdio 子进程方式启动，再通过 `load_mcp_tools(session)` 转为 LangChain tools。

与本地 `@tool` 的区别：

| 类型 | 运行位置 | 适用场景 |
| --- | --- | --- |
| 本地 `@tool` | 当前 Python 进程 | 项目内部能力，如知识库检索、实体记忆。 |
| MCP 工具 | 独立 MCP server | 可独立部署或扩展的工具服务，如计算、日期、第三方系统。 |

这种设计让后续接入外部系统时不必把所有工具都写死在后端主进程中。

## 规划机制

Agent 的规划机制主要来自模型对系统提示词和工具描述的理解：

1. 用户提出问题。
2. Agent 判断是否需要工具。
3. 如果是知识库问题，调用 `search_knowledge_base`。
4. 如果涉及当前时间，调用 `get_current_time`。
5. 如果涉及用户本人，读取实体记忆。
6. 工具结果进入上下文后，Agent 生成最终回答。

系统提示词要求：需要企业知识时先检索；工具返回无相关内容时不能编造；涉及时间时使用时间工具；涉及用户信息时优先使用实体记忆。

## 记忆体系（短期记忆 / 长期记忆 / 实体记忆）

本项目的记忆由短期记忆、长期记忆和实体记忆共同构成，落地为四层实现。

### 1. 短期记忆：全量历史 + 摘要压缩

会话服务从数据库读取**全量**历史消息，再交给 `rag.build_memory_history()` 做记忆压缩：历史条数超过 `HISTORY_SUMMARY_TRIGGER_MESSAGES=6` 时，把更早的历史用 LLM 压成一段中文摘要（以 `【更早对话摘要】…` 注入上下文），只保留最近 `HISTORY_SUMMARY_KEEP_MESSAGES=2` 条原文。这样模型既能理解同一会话里的追问（例如“那这个流程需要谁审批？”这种省略主语的问题），又不会让无限历史把 prompt 撑爆。

清洗历史时 `_sanitize_history` 会对每条消息做 PII（email / IP）脱敏，摘要只保留身份、偏好、已确认结论等对后续回答有帮助的信息，不回填隐私原文。

### 2. InMemorySaver 会话检查点

`backend/app/core/rag.py` 中使用 LangGraph `InMemorySaver` 作为 checkpointer，并用 `thread_id = conversation_id` 隔离不同会话。

特点：

- 同一个会话可以被 Agent 记住上下文。
- 不同会话的记忆互相隔离。
- 它是内存级存储，进程重启会丢失。
- 重启后系统依赖数据库中的历史消息重新构造上下文。

因此它不是唯一记忆来源，而是运行时会话检查点。

### 3. 摘要压缩

`SummarizationMiddleware` 在带记忆 Agent 链路中启用：

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `trigger` | `messages > 6` | 消息超过 6 条触发摘要。 |
| `keep` | 最近 2 条消息 | 摘要后保留最近 2 条原文。 |
| `summary_prompt` | 自定义中文摘要提示 | 保留历史中的关键信息。 |

摘要前还会经过 PII 中间件，对 email、IP 等信息做脱敏。摘要压缩的目标是减少长对话 token 消耗，同时保留重要上下文。

主流式问答链路（`/api/qa/ask-stream`）的短期记忆由 `rag.build_memory_history()` 实现，在主链路上完成摘要压缩和 PII 脱敏（超过 6 条触发、保留最近 2 条原文，见上文第 1 层）。

### 4. 实体记忆（结构化抽取）

`backend/app/core/memory_agent.py` 使用 LangChain `ToolStrategy(MemoryExtract)` 提取结构化记忆。Schema 包含：

| 字段 | 说明 |
| --- | --- |
| `name` | 用户姓名。 |
| `nickname` | 用户希望被称呼的名字。 |
| `role` | 用户身份或角色。 |
| `preference` | 用户明确表达的偏好。 |

`backend/app/services/user_profile_service.py` 会先用触发词判断一条消息是否值得提取记忆，例如“我叫”“我是”“以后叫我”“回答要简洁”。命中后才调用 LLM，降低成本。

提取出的结构化字段写入 `user_profile.fields`（主键为 `conversation_id`，换一个会话不继承），每次问答时作为实体记忆注入上下文。这样即使更早的原文被摘要压缩，用户姓名、称呼、偏好仍能保留。

## 记忆边界

- 当前长期记忆以会话为单位，不跨账号共享。
- 会话、消息、附件按 `enterprise_id + user_id + conversation_id` 隔离。
- `InMemorySaver` 重启会丢失，数据库历史是持久化兜底。
- 摘要压缩用于长对话，与实体记忆互为补充。
- 企业知识库属于企业共享资产，会话附件属于会话级临时 RAG，二者都不计入长期记忆。

## Temperature 设置

制度问答建议低温度：

| 场景 | 建议 Temperature |
| --- | ---: |
| 企业制度问答、RAG 评测、引用型回答 | 0.1 - 0.3 |
| 一般解释、轻度总结 | 0.3 - 0.5 |
| 创意文案、开放式生成 | 0.6 - 0.9 |

项目默认 `DEEPSEEK_TEMPERATURE=0.1`，是为了减少随机发挥，提升引用回答和评测结果的稳定性。
