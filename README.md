<div align="center">

# RAG Business Chat

**企业级 AI 知识库工作台**

多企业 SaaS 隔离 · 结构化 RAG · 会话附件理解 · 企业级 Key 路由 · 360 题评测闭环

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

## 这个项目解决什么问题

企业内部制度通常散落在员工手册、薪酬绩效、采购付款、合同审批、信息安全、IT 运维等文档中。这类资料有几个共同特点，也是普通 RAG Demo 容易失效的地方：

- 提问常带**条款号、角色、金额、时间、流程节点**等硬信息，纯向量语义容易把短编号稀释掉。
- 正确答案往往**跨多个文档**，单个 Top-K 结果覆盖不全证据。
- **表格和扫描 PDF 很常见**，解析质量直接决定检索质量。
- 企业数据、员工会话、上传附件和模型 Key **必须隔离**。
- 效果需要**可量化评测**，不能只看几条演示样例。

完整的代码级实现说明见 [技术说明页](https://jizhualiuliubei.github.io/RAG-Business-chat/technical.html)。

## 效果指标

内置 `enterprise_scale_360_v1` 基准题集，360 题分布：A 单文档事实 96 题、B 单文档细节与条款 60 题、C 跨文档关联 60 题、D 场景应用与计算 72 题、E 边界与易错 48 题、F 未覆盖与幻觉 24 题。full 模式结果：

| 指标 | 结果 | 指标 | 结果 |
| --- | ---: | --- | ---: |
| 总通过率 | **95.83%**（345/360） | 章节级命中率 | 100.0% |
| Top-1 文档命中率 | 91.96%（309/336） | 答案要点覆盖率 | 92.42% |
| Top-3 文档命中率 | 98.21%（330/336） | 必需证据命中率 | 100.0% |
| Top-5 文档命中率 | **100.0%** | 引用正确率 | 100.0% |
| 平均耗时 | 3461 ms | F 类拒答正确率 | 87.5%（21/24） |

> **口径说明**：360 题中 `negative_case` 24 题（F 类，不参与检索命中率）、带 `required_citations` 336 题，
> 所以 Top-1 / Top-3 的分母是 336 而非 360。以上数字来自一次 full 模式运行，
> 但**仓库未提交对应的报告产物**（`reports/` 目前只有占位文件），复现需重新运行评测任务。

## 四个关键差异

| 差异点 | 具体做法 |
| --- | --- |
| **结构化切片** | 主策略是**整章独立成块**（`cut_by_heading`），不是固定窗口切分。结构头 `来源：{文档} > {章节} > {条款号}` 拼在正文前，让章节名与条款号同时进入向量语义和 BM25 字面匹配。`CHUNK_SIZE=350 / CHUNK_OVERLAP=80` 只作为单章超过 1200 字符时的兜底。 |
| **混合检索 + 硬信号** | 向量（`candidate_k = max(top_k×8, 40)`）与全库 BM25 双通道召回，按 `0.4 × 向量 + 0.6 × BM25` 融合。条款号走正则精确召回并给 2.0 分，`《文档名》` 提及加权、限定词触发硬过滤。 |
| **企业级 Key 路由与隔离** | DeepSeek 与 SiliconFlow Key 按企业加密保存、遮罩展示、支持连接测试。检索与删除始终带 `enterprise_id` + `kb_id` 过滤，另用数据库实时生成 `doc_id` 白名单兜住「DB 已删但向量残留」。普通企业缺 Key 时明确报错，不回退平台 Key。 |
| **评测闭环** | 360 题基准 + retrieval / full 两种模式 + 逐题失败诊断 + Markdown 报告。评测链路额外有按 `required_citations` 的**多证据补召回**（注意：这一步只用于评测，线上问答链路没有）。 |

## RAG 六阶段（关键参数）

| 阶段 | 核心实现 |
| --- | --- |
| **1 文档解析** | TXT/CSV 用 `utf-8-sig`；DOCX 读段落 + 表格；XLSX 首行当表头拼「表头：值」并保留 Sheet 前缀；PDF 优先 pypdf，包在**线程超时 5 秒**里，空结果或超时降级 OCR（渲染 scale=3.0 → 二值化 → tesseract `chi_sim+eng --psm 6`）。解析为空**主动抛错**，不写空 chunk。 |
| **2 结构化切片** | 按 `## 章节` / `一、章节`（仅当全文无 `##` 时启用）整章成块，再按 `[A-Z]{2}-\d{2}-\d{3}` 条款号细分；正文 < 20 字符丢弃，纯标题空块跳过；单章 > 1200 字符用 `RecursiveCharacterTextSplitter(350, 80)` 兜底，子块仍带结构头。 |
| **3 查询改写** | 用 DeepSeek 把口语问题改写为制度关键词（「30万」→「50000元 总经理审批」）。**只用于检索**，原问题照常展示。异常 / 空 / 长度 ≥ 100 一律回退原问题。 |
| **4 混合检索** | Milvus COSINE 向量候选 40 条起 + 全库 BM25 双通道 → 0.4/0.6 融合 → 条款号精确召回（2.0 分）→ 文档名加权（+0.5）→ 章节去重。BM25 为手写实现，用字符 bigram 当词项，不依赖 jieba。 |
| **5 融合重排** | SiliconFlow `BAAI/bge-reranker-v2-m3` cross-encoder 精排，`distance = max(融合分, rerank_score)`。开关关闭 / 无 Key / 超时 8s / 异常时**全部回退融合排序**，不阻断问答。 |
| **6 约束生成** | 三段式 Prompt 注入当前时间、用户档案、会话附件、带 `[n]` 编号的已知信息。硬规则：用户身份只以档案为准；制度细节无直接依据必须拒答；文档冲突并列引用双方；末尾声明把【已知信息】视为数据、不执行其中指令。 |

详细展开（含「不这么做会坏在哪」「代价是什么」）见 [技术说明页](https://jizhualiuliubei.github.io/RAG-Business-chat/technical.html#pipeline)。

## Agent 与记忆

项目同时保留两条问答路径：

| 路径 | 实现 | 状态 |
| --- | --- | --- |
| **手动 RAG** | 代码控制「检索 → 拼上下文 → 生成」，流式直连底层 `model.stream()`（`create_agent.stream()` 不是真正的逐 token 流式）。 | 线上主路径 |
| **Agent RAG** | `create_agent` + `@tool`：知识库检索 / 当前时间 / 用户档案，模型自主决定调用。 | 已实现，未接前端 |
| **MCP Agent** | stdio 启动独立 MCP 服务子进程，加载计算器与日期工具。 | 已实现，未接前端 |

上下文由三层管理：

- **滑动窗口（Sliding Window）**：`HISTORY_ROUNDS=10`，只取最近 10 轮（20 条）消息注入 prompt，把上下文长度与成本固定住。
- **摘要压缩（Summarization Compression）**：`SummarizationMiddleware(trigger=6 条, keep=2 条)`，超过阈值时用对话模型压缩历史并保留最近 2 条原文。
- **长期用户档案**：用 `ToolStrategy` + Pydantic Schema 结构化抽取姓名/称呼/身份/偏好，写入 `user_profile` 表并在每轮问答注入，先做触发词预筛避免每条消息都调 LLM。

> **如实说明**：摘要压缩目前只挂在 Agent 路径上；线上主问答链路走的是滑动窗口。另外 `user_profile` 的主键是 `conversation_id`，档案是**会话级**的，换会话不继承。

## 系统架构

![系统整体架构](docs/images/system-architecture.svg)

Vue3 + FastAPI 前后端分离。关系库兼容 SQLite / MySQL；向量库使用 Zilliz / Milvus 统一 collection `knowledge_chunks`（COSINE + HNSW，M=16、efConstruction=128），向量主键编码为 `id = doc_id × 100000 + chunk_index`。`enterprise_id` 是企业级隔离边界，`user_id` 是会话、消息与附件的隔离边界。

## 界面预览

| 问答与证据链 | 知识库治理 |
| --- | --- |
| ![带引用证据链的回答](showcase/assets/screenshots/home-chat.webp) | ![知识库与文档治理](showcase/assets/screenshots/knowledge-base.webp) |
| **引用编号对应来源区**，可查看命中的知识库、文件名、片段与相似度 | **解析状态与失败原因直接暴露**，支持批量删除与向量同步清理 |

| 企业级模型配置 | RAG 评测中心 |
| --- | --- |
| ![企业级模型 Key 配置](showcase/assets/screenshots/model-config.webp) | ![RAG 评测中心](showcase/assets/screenshots/evaluation-center.webp) |
| **Key 加密存储、遮罩展示**，保存与连接测试分离 | **逐题诊断与报告下载**，任务按企业隔离 |

更多界面见 [项目展示站](https://jizhualiuliubei.github.io/RAG-Business-chat/)。

## 快速开始

```bash
# 1. 复制环境变量模板
cp .env.example .env      # 需填写 DEEPSEEK_API_KEY / SILICONFLOW_API_KEY / Zilliz 连接信息

# 2. 启动后端
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001

# 3. 启动前端
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

浏览器访问 `http://127.0.0.1:5173`。

> 两个容易踩的坑：后端依赖装在 conda 环境 `langchain1.2` 中，可直接用该环境的 python；
> 若 `npm install` 后提示找不到 vite，是 `NODE_ENV=production` 跳过了开发依赖，改用 `npm install --include=dev`。

完整步骤、环境变量重点与线上部署见 [部署说明](docs/部署说明.md)。

## 默认体验账号

| 企业代码 | 用户名 | 密码 | 角色 |
| --- | --- | --- | --- |
| `system` | `employee` | `employee123` | 系统员工体验账号 |
| `system` | `admin` | `admin123` | 系统管理员 |

> 公开演示环境中的账号和数据仅用于功能体验。模型 Key、数据库密码、Zilliz Token 等敏感配置不要提交到仓库。

## 已知限制

这些是读代码能核实到的真实边界，不是套话：

| 项 | 现状 | 改进方向 |
| --- | --- | --- |
| 摘要压缩未覆盖主流式链路 | 中间件只挂在 Agent 路径；前端实际走的 `/qa/ask-stream` 直连模型，不经过中间件，历史消息的 PII 脱敏同样只做在 Agent 路径。 | 把中间件逻辑下沉到流式链路，或流式前先对窗口内消息做一次摘要。 |
| `.xls` 实际不可解析 | 上传与附件白名单含 `.xls`，但都交给 openpyxl，而 openpyxl 只支持 `.xlsx` / `.xlsm`。 | 引入 `xlrd`，或把 `.xls` 移出白名单。 |
| BM25 是全库扫描 | 每问拉取全库 chunk 建索引（上限 10000 条），为 ~73 块的小语料设计。 | 万级以内可用；十万级需换倒排索引或下沉到向量库侧。 |
| 旧 collection 会跳过企业过滤 | `_describe_collection_fields` 探测字段，若 collection 没有 `enterprise_id`，企业过滤会静默跳过（为兼容旧集合不阻断上传）。 | 全新部署请确保 collection 已包含 `enterprise_id`。 |

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [技术架构](docs/技术架构.md) | 前后端、模型、向量库、数据库与核心请求链路。 |
| [RAG 机制说明](docs/RAG机制说明.md) | 解析、切片、查询改写、混合检索、Rerank、拒答。 |
| [Agent 机制说明](docs/Agent机制说明.md) | `@tool`、MCP、`ToolStrategy`、记忆机制与 Agent 边界。 |
| [数据库设计](docs/数据库设计.md) | 数据表、字段、关系、企业隔离、软删除与审计日志。 |
| [评测体系](docs/评测体系.md) | 360 题基准、题型、指标口径与评测模式。 |
| [部署说明](docs/部署说明.md) | 本地运行、线上部署、Nginx / systemd 与环境变量。 |
| [使用说明](docs/使用说明.md) | 登录、企业管理、模型配置、知识库、问答、附件与评测中心操作。 |

> 注：`docs/` 下的机制文档写作时间早于当前代码，部分参数（尤其是切片策略）与实现已有出入，
> **以 [技术说明页](https://jizhualiuliubei.github.io/RAG-Business-chat/technical.html) 和代码为准**。

## 目录结构

```text
RAG-Business-chat/
├── backend/                 # FastAPI 后端服务
├── frontend/                # Vue3 前端应用
├── data/enterprise_scale/   # 合成企业制度文档
├── evaluation/              # 评测题集与基准数据
├── deploy/                  # Nginx、systemd、生产环境模板
├── docs/                    # 架构、数据库、RAG、Agent、评测文档
├── showcase/                # GitHub Pages 展示站源码
└── README.md
```
