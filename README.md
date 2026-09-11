<div align="center">

# RAG Business Chat

**企业级 AI 知识库工作台**

多企业 SaaS 隔离 · 结构化 RAG · 引用溯源 · 会话附件理解 · 360 题评测闭环

[![在线体验](https://img.shields.io/badge/%E5%9C%A8%E7%BA%BF%E4%BD%93%E9%AA%8C-118.31.45.253-2454d6?style=for-the-badge)](http://118.31.45.253/)
[![项目展示站](https://img.shields.io/badge/%E9%A1%B9%E7%9B%AE%E5%B1%95%E7%A4%BA%E7%AB%99-GitHub_Pages-14171f?style=for-the-badge)](https://jizhualiuliubei.github.io/RAG-Business-chat/)

![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square)
![LangChain](https://img.shields.io/badge/LangChain-1.2-1C3C3C?style=flat-square)
![Vue3](https://img.shields.io/badge/Vue3-4FC08D?style=flat-square)
![DeepSeek](https://img.shields.io/badge/DeepSeek-4D6BFE?style=flat-square)
![Zilliz](https://img.shields.io/badge/Zilliz%20%2F%20Milvus-00A1EA?style=flat-square)

</div>

![问答工作台](showcase/assets/screenshots/home-main.webp)

---

## 项目定位

企业内部制度通常散落在员工手册、薪酬绩效、采购付款、合同审批、信息安全、IT 运维等文档中。这类资料有几个共同特点，也是通用 RAG 方案容易失效的地方：

- 提问常带**条款号、角色、金额、时间、流程节点**等硬信息，纯向量语义容易把短编号稀释掉。
- 正确答案往往**跨多个文档**，单个 Top-K 结果覆盖不全证据。
- **表格和扫描 PDF 很常见**，解析质量直接决定检索质量。
- 企业数据、员工会话、上传附件和模型 Key **必须隔离**。
- 回答必须**可追溯**，引用要能对回原文。

项目围绕这些问题实现了一套可运行的端到端方案，完整的技术展开见[项目展示站](https://jizhualiuliubei.github.io/RAG-Business-chat/)。

**目录**：[效果指标](#效果指标) · [RAG 六阶段](#rag-六阶段) · [架构选型](#架构选型agentic-workflow) · [记忆设计](#记忆设计) · [系统架构](#系统架构) · [界面预览](#界面预览) · [核心能力](#核心能力) · [文档导航](#文档导航)

## 效果指标

内置 `enterprise_scale_360_v1` 基准题集，360 题覆盖 A 到 F 六类：单文档事实 96 题、单文档细节与条款 60 题、跨文档关联 60 题、场景应用与规则计算 72 题、边界与易错 48 题、未覆盖与幻觉测试 24 题。full 模式结果：

| 指标 | 结果 | 指标 | 结果 |
| --- | ---: | --- | ---: |
| 总通过率 | **95.83%**（345/360） | 章节级命中率 | 100.0% |
| Top-1 文档命中率 | 91.96%（309/336） | 答案要点覆盖率 | 92.42% |
| Top-3 文档命中率 | 98.21%（330/336） | 必需证据命中率 | 100.0% |
| Top-5 文档命中率 | **100.0%** | 引用正确率 | 100.0% |
| 平均耗时 | 3461 ms | F 类拒答正确率 | 87.5%（21/24） |

评测支持 retrieval 与 full 两种模式，出逐题失败原因与修复建议，并可导出 Markdown 报告。指标分母按题型区分：24 道负例不参与检索命中率，带必需引用的 336 道构成 Top-K 的分母。

## RAG 六阶段

| 阶段 | 核心实现 |
| --- | --- |
| **文档解析** | TXT / CSV 用 `utf-8-sig`；DOCX 读段落与表格；XLSX 首行当表头拼「表头：值」并保留工作表前缀；PDF 优先 pypdf，失败或超时降级 OCR（渲染成位图后二值化，再走 tesseract 中文识别）。 |
| **结构化切片** | 先按章节标题整章成块，再按条款号正则细分；结构头 `来源：{文档} > {章节} > {条款号}` 拼在正文前，让章节名与条款号同时进入向量语义和 BM25 字面匹配。 |
| **查询改写** | 用对话模型把口语问题改写为制度关键词（「采购 30 万谁审批」转为「采购审批 300000元 总经理审批」）。改写只用于检索，展示给用户的仍是原问题。 |
| **混合检索** | Milvus 余弦向量召回与全库 BM25 双通道并行，按 `0.4 × 向量 + 0.6 × BM25` 归一化融合。条款号走正则精确召回，文档名与限定词作为硬信号加权或过滤。 |
| **融合重排** | 一阶段融合排序负责召回，二阶段调用 `BAAI/bge-reranker-v2-m3` 交叉编码器精排，最终分取两者较大值，再按章节去重、截断到 Top-K、过滤低置信结果。 |
| **约束生成** | 分区装配 Prompt：当前时间、用户档案、会话附件、带编号的已知信息、问题。硬规则包括用户身份只以档案为准、制度细节无直接依据必须拒答、文档冲突并列引用双方。 |

## 架构选型：Agentic Workflow

制度问答既要结果可控，又要能处理需要工具判断的问题。团队常见做法是二选一，本项目的答案是分层组合：

| 方案 | 优点 | 取舍 |
| --- | --- | --- |
| 全部交给 Workflow | 确定性最高，每一步可复现 | 流程只能穷举已知情况，遇到需要临时查时间、读档案的请求就无能为力 |
| 全部交给 Agent | 灵活度高，能处理开放目标 | 行为难以复现，同一问题两次回答可能引用不同条款，而制度问答必须可追溯 |
| **Agentic Workflow** | 骨架可控，关键节点灵活 | 需要明确划分哪些节点交给模型 |

本系统的实现分两层：

- **Workflow 层**：`qa_service.stream_answer` 里的六阶段编排，检索、过滤、组装来源、生成、落库的顺序全部写在代码里。
- **Agent 层**：`rag_agent.py` 中用 `create_agent` 挂载「知识库检索」「当前时间」「用户档案」三个工具得到的决策节点，由模型自主选择调用。

两条路径复用同一套混合检索，所以检索质量保持一致。另有 MCP 路径：通过 stdio 启动独立服务进程，把计算器与日期时间能力作为外部工具接入，工具能力与模型推理解耦。

## 记忆设计

对话历史是证据，结构化状态才是结论。系统按信息类型选择存储介质：结构化字段走关系库精确查，非结构化知识走向量库做语义检索。

| 层次 | 实现 |
| --- | --- |
| **短期记忆** | 对话落库在 `message` 表，取用时做**滑动窗口 + 摘要压缩**：历史超过 `HISTORY_SUMMARY_TRIGGER_MESSAGES`（默认 6）条时，把更早的对话交给模型压成一段摘要，只保留最近 `HISTORY_SUMMARY_KEEP_MESSAGES`（默认 2）条原文。追问「那这个流程需要谁审批」这类省略主语的问题时，模型仍然知道上文。 |
| **长期记忆** | 制度文档经解析、切片、向量化后写入 Zilliz / Milvus，按 chunk 粒度存储，每个切片带 `enterprise_id`、`kb_id`、`doc_id`、`source`、`chunk_id` 元数据。检索始终带企业与知识库过滤。 |
| **实体记忆** | 用户主动提供的姓名、称呼、身份、偏好经 `ToolStrategy` 与 Pydantic Schema 抽成结构化字段写入 `user_profile`，每轮注入。写入前用触发词预筛，只对像自我陈述的消息调用模型。 |

**上下文工程**：Prompt 按固定顺序装配。顶部注入当前日期时间，解决模型不知道「现在」的问题；随后是用户档案，无记录时显式写明，压缩编造空间；再接会话附件与已知信息，证据带 `[n]` 编号与出处；最后是用户问题。会话附件与知识库分区注入，冲突时要求并列说明差异。

## 系统架构

![系统整体架构](docs/images/system-architecture.svg)

Vue3 + FastAPI 前后端分离。关系库兼容 SQLite / MySQL；向量库使用 Zilliz / Milvus 统一 collection `knowledge_chunks`（余弦距离 + HNSW 索引），向量主键编码为 `doc_id × 100000 + chunk_index`，重复写入时幂等覆盖。

隔离边界分两层：`enterprise_id` 是企业级边界，`user_id` 是会话、消息与附件的边界。检索与删除始终带企业与知识库过滤，问答前还会从数据库实时生成有效文档白名单，兜住「记录已删但向量残留」的脏数据。

## 界面预览

| 问答与证据链 | 知识库治理 |
| --- | --- |
| ![带引用证据链的回答](showcase/assets/screenshots/home-chat.webp) | ![知识库与文档治理](showcase/assets/screenshots/knowledge-base.webp) |
| 引用编号对应来源区，可查看命中的知识库、文件名、片段与相似度 | 解析状态、切片数量与失败原因直接可见，支持批量删除 |

| 企业级模型配置 | RAG 评测中心 |
| --- | --- |
| ![企业级模型 Key 配置](showcase/assets/screenshots/model-config.webp) | ![RAG 评测中心](showcase/assets/screenshots/evaluation-center.webp) |
| Key 按企业加密保存、遮罩展示，保存与连接测试分离 | 题集管理、逐题诊断与报告导出，任务按企业隔离 |

更多界面见[项目展示站](https://jizhualiuliubei.github.io/RAG-Business-chat/)。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 多企业 SaaS | 系统管理员、企业管理员、企业员工三类角色；企业注册审核、启用禁用、软删除与代管形成完整闭环。 |
| 企业级 Key 路由 | 对话模型与嵌入模型 Key 按企业配置、加密保存、遮罩展示、连接测试，并按企业路由。普通企业缺 Key 时明确提示配置。 |
| 文档治理 | 支持 DOCX、PDF、扫描 PDF（OCR）、TXT、CSV、XLSX；上传后解析、结构化切片、向量入库、状态追踪，失败原因可排查。 |
| 结构化 RAG | 保留文档名、章节、条款号、页码与工作表来源；查询改写、混合检索、重排与引用约束组合使用。 |
| 会话附件 | 提问时上传的附件只属于当前账号当前会话，不进入企业知识库；附件来源与知识库来源分组展示。 |
| Agent 与 MCP | 本地工具调用与 MCP 外部工具接入，工具只返回当前企业授权范围内的数据。 |
| 记忆机制 | 滑动窗口与摘要压缩管理短期上下文，向量库承载长期知识，结构化档案保存用户稳定信息。 |
| 评测中心 | 360 题基准、retrieval 与 full 双模式、逐题诊断、报告导出，任务与报告按企业隔离。 |
| 审计中心 | 企业审核、状态变更、Key 更新、文档与评测删除等关键动作可追踪，日志不保存敏感明文。 |

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [技术架构](docs/技术架构.md) | 前后端、模型、向量库、数据库与核心请求链路。 |
| [RAG 机制说明](docs/RAG机制说明.md) | 解析、切片、查询改写、混合检索、重排与拒答。 |
| [Agent 机制说明](docs/Agent机制说明.md) | 工具调用、MCP 接入、结构化输出与记忆机制。 |
| [数据库设计](docs/数据库设计.md) | 数据表、字段、关系、企业隔离、软删除与审计日志。 |
| [评测体系](docs/评测体系.md) | 题集构成、题型、指标口径与评测模式。 |
| [使用说明](docs/使用说明.md) | 登录、企业管理、模型配置、知识库、问答、附件与评测中心操作。 |
| [部署说明](docs/部署说明.md) | 本地运行、线上部署、Nginx 与 systemd 配置。 |

## 默认体验账号

| 企业代码 | 用户名 | 密码 | 角色 |
| --- | --- | --- | --- |
| `system` | `employee` | `employee123` | 系统员工体验账号 |
| `system` | `admin` | `admin123` | 系统管理员 |

公开演示环境中的账号和数据仅用于功能体验。

<details>
<summary><b>技术栈与目录结构</b></summary>

**后端**：FastAPI · LangChain 1.2 · SQLAlchemy · Pydantic
**模型**：DeepSeek（对话与查询改写）· BGE-M3（向量化）· bge-reranker-v2-m3（重排）
**存储**：Zilliz / Milvus（向量）· SQLite / MySQL（关系）· MCP（外部工具）
**前端**：Vue 3 · Element Plus · Vite

```text
RAG-Business-chat/
├── backend/                 # FastAPI 后端服务
│   ├── app/api/             # 接口层
│   ├── app/core/            # RAG、检索、切分、Agent、记忆
│   ├── app/services/        # 业务编排
│   └── mcp/                 # MCP 服务进程
├── frontend/                # Vue 3 前端应用
├── data/enterprise_scale/   # 合成企业制度文档
├── evaluation/              # 评测题集与基准数据
├── deploy/                  # Nginx、systemd、生产环境模板
├── docs/                    # 架构、RAG、Agent、数据库、评测、部署文档
└── showcase/                # GitHub Pages 展示站
```

</details>
