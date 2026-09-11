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

RAG Business Chat 把这些问题做成了一条可运行的端到端链路：企业资料上传 → 文档解析 → 结构化切片 → 向量入库 → 查询改写 → 混合检索 → 重排与多证据召回 → 引用溯源 → 无依据拒答 → 评测诊断。

## 效果指标

内置 `enterprise_scale_360_v1` 基准题集，覆盖 A–F 六类题型（单文档事实、细节定位、跨文档推理、场景计算、边界判断、幻觉测试）。full 模式评测结果：

| 指标 | 结果 | 指标 | 结果 |
| --- | ---: | --- | ---: |
| 总通过率 | **95.83%** | 章节级命中率 | 100.0% |
| Top-1 文档命中率 | 91.96% | 答案要点覆盖率 | 92.42% |
| Top-3 文档命中率 | 98.21% | 必需证据命中率 | 100.0% |
| Top-5 文档命中率 | **100.0%** | 引用正确率 | 100.0% |
| 平均耗时 | 3461 ms | F 类拒答正确率 | 87.5% |

## 四个关键差异

| 差异点 | 具体做法 |
| --- | --- |
| **结构化切片** | 切片时写入文档名、章节、条款号、页码 / Sheet。`HR-01-001`、`IT-03-001` 这类短编号走精确召回通道，不被向量语义稀释；表格展开为含 Sheet、表头与行号的可检索文本。 |
| **会话附件临时 RAG** | 提问时上传的附件只属于当前账号当前会话，不进入企业正式知识库。知识库来源与附件来源分区注入 Prompt，冲突时并列说明差异。 |
| **企业级 Key 路由** | DeepSeek 与 SiliconFlow Key 按企业加密保存、遮罩展示、支持连接测试。检索与删除始终带 `enterprise_id` + `kb_id` 过滤；普通企业缺 Key 时明确报错，不回退平台 Key。 |
| **评测闭环** | 内置 360 题基准，支持 retrieval / full 两种模式、逐题失败诊断与 Markdown 报告下载，任务与报告按企业隔离。 |

## 系统架构

![系统整体架构](docs/images/system-architecture.svg)

系统采用 Vue3 + FastAPI 前后端分离架构。关系库使用 SQLite / MySQL 兼容设计，向量库使用 Zilliz / Milvus 统一 collection，通过元数据过滤实现企业、知识库与文档三级隔离。`enterprise_id` 是企业级隔离边界，`user_id` 是会话、消息与附件的隔离边界。

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

> 两个容易踩的坑：本项目后端依赖装在 conda 环境 `langchain1.2` 中，可直接用该环境的 python；
> 若 `npm install` 后提示找不到 vite，是 `NODE_ENV=production` 跳过了开发依赖，改用 `npm install --include=dev`。

完整步骤、环境变量重点与线上部署见 [部署说明](docs/部署说明.md)。

## 默认体验账号

| 企业代码 | 用户名 | 密码 | 角色 |
| --- | --- | --- | --- |
| `system` | `employee` | `employee123` | 系统员工体验账号 |
| `system` | `admin` | `admin123` | 系统管理员 |

> 公开演示环境中的账号和数据仅用于功能体验。模型 Key、数据库密码、Zilliz Token 等敏感配置不要提交到仓库。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [技术架构](docs/技术架构.md) | 前后端、模型、向量库、数据库与核心请求链路。 |
| [RAG 机制说明](docs/RAG机制说明.md) | 解析、切片、查询改写、混合检索、Rerank、多证据召回与拒答。 |
| [Agent 机制说明](docs/Agent机制说明.md) | `@tool`、MCP、`ToolStrategy`、记忆机制与 Agent RAG 边界。 |
| [数据库设计](docs/数据库设计.md) | 数据表、字段、关系、企业隔离、软删除与审计日志。 |
| [评测体系](docs/评测体系.md) | 360 题基准、题型、指标口径与评测模式。 |
| [部署说明](docs/部署说明.md) | 本地运行、线上部署、Nginx / systemd 与环境变量。 |
| [使用说明](docs/使用说明.md) | 登录、企业管理、模型配置、知识库、问答、附件与评测中心操作。 |

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
