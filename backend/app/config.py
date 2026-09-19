"""
全局配置模块
================
作用：从项目根目录的 .env 文件读取所有配置，集中导出成 Python 变量，
     供整个后端项目引用。

为什么要集中到一个文件？
- 如果配置散落在各处，改一个参数（比如切分大小、相似度阈值）要到处找；
- 集中后，所有"可调参数"都在一个地方，既方便维护，
  也体现"配置与代码分离"的设计思想。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录：本文件位于 backend/app/config.py
# parents[0]=backend/app  parents[1]=backend  parents[2]=项目根目录
BASE_DIR = Path(__file__).resolve().parents[2]

# load_dotenv(override=True) 的作用：
# - 把 .env 文件里的 "键=值" 读成环境变量
# - override=True 表示：如果内存里已有同名环境变量，用 .env 的值覆盖它
load_dotenv(BASE_DIR / ".env", override=True)


# ============ 一、对话模型 DeepSeek ============
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
# 企业制度/RAG 问答以稳定、可复现为主，默认低温；需要创意回答时可在 .env 调高。
DEEPSEEK_TEMPERATURE = float(os.getenv("DEEPSEEK_TEMPERATURE", "0.1"))

# ============ 一之二、模型调用的超时与重试 ============
# 长跑评测要发几千次外部请求，网络抖动躲不掉，显式配置比用 SDK 默认值可控。
LLM_REQUEST_TIMEOUT = float(os.getenv("LLM_REQUEST_TIMEOUT", "120"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
# 评测链路对「工程失败」（超时、连接中断、限流、5xx）的最大尝试次数（含首次）。
# 默认 3 表示首次 + 2 次重试；值本身是总次数，不是额外重试次数。
# 工程失败不是 RAG 能力问题，不该因为一次抖动就把整道题记成失败。
EVALUATION_API_RETRY_ATTEMPTS = int(os.getenv("EVALUATION_API_RETRY_ATTEMPTS", "3"))
EVALUATION_API_RETRY_BASE_DELAY = float(os.getenv("EVALUATION_API_RETRY_BASE_DELAY", "2.0"))


# ============ 二、嵌入模型 硅基流动 BGE-M3 ============
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
# 注意：嵌入模型的 base_url 要以 /v1 结尾，少了会连不上
SILICONFLOW_BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
# 默认用免费版 BAAI/bge-m3；要更强效果可改成 "Pro/BAAI/bge-m3"（付费）
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-m3")
# 向量维度：BGE-M3 固定 1024，建 Milvus 集合（collection）时必须一致
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))


# ============ 三、Milvus 向量数据库 ============
MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", "")
MILVUS_DB_NAME = os.getenv("MILVUS_DB_NAME", "kb_qa")
MILVUS_COLLECTION = os.getenv("MILVUS_COLLECTION", "knowledge_chunks")


# ============ 四、文本切分参数 ============
# 说明：
# - 220 太小：章节标题+正文被切碎，关键内容落不进同一块
# - 500 太大：文档开头"标题+所有章节标题+第一章"拼成万能候选块，
#   对任何问题得分都偏高，导致无关问题误命中
# - 350 折中：章节完整、开头块干扰可控
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "350"))       # 每个片段约多少字符
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "80"))  # 相邻片段重叠多少字符


# ============ 五、检索参数 ============
TOP_K = int(os.getenv("TOP_K", "5"))                          # 每次检索返回几个片段
# 相似度阈值：混合检索的融合分（0~1）
# 注意：融合分是「两通道 min-max 归一化后的加权和」，所以阈值并不是绝对相似度，
# 而是"相对候选集的位置 + 两通道的加权"。
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.40"))  # 低于此值视为无相关内容
ENABLE_RERANK = os.getenv("ENABLE_RERANK", "true").lower() == "true"
RERANK_MODEL_NAME = os.getenv("RERANK_MODEL_NAME", "BAAI/bge-reranker-v2-m3")
RERANK_TIMEOUT_SECONDS = float(os.getenv("RERANK_TIMEOUT_SECONDS", "8"))
# rerank 保底：最终 Top-K 里为「融合排序（向量 + BM25）的前 N 名」保留位置。
#
# 为什么需要：rerank 只会给片段加分，但排名是相对的 —— 它把别的片段抬高，
# 同样能把正确条款挤出 Top-K。融合排序是向量和 BM25 两个召回通道的共同判断，
# 不该被一次 rerank 调用整个抹掉 ——
# 这和代码里对「条款号」做的保底是同一个思路（见 retrieve_contexts）。
#
# 代价：会稀释 rerank 的判断。如果换成 rerank 明显强于融合排序的语料，
# 这个值应该调小甚至设 0。设 0 = 完全听 rerank（本次改动前的行为）。
#
# 默认取 1：只给融合排序的第 1 名留一个位置，在"保底"和"少稀释"之间取折中。
RERANK_KEEP_FUSION_TOP_N = int(os.getenv("RERANK_KEEP_FUSION_TOP_N", "1"))
# 多 Query 改写（口语 → 制度语体）。**这是"口语提问能不能召回"的关键一步**：
# 员工不会用制度里的原词提问，口语说法与制度正文几乎没有共同字串，两种检索
# 通道都够不着；改写成制度语体后，目标条款才能被召回。
# 代价：每题多一次 LLM 调用（在检索之前，会加到首字节延迟上）。要省成本可以关掉。
QUERY_REWRITE_ENABLED = os.getenv("QUERY_REWRITE_ENABLED", "true").lower() == "true"
QUERY_REWRITE_MAX_QUERIES = int(os.getenv("QUERY_REWRITE_MAX_QUERIES", "3"))
# 改写与拆子问题这类"抽取式"调用的温度，固定 0。
# 它们要的是稳定而不是发挥：同一个问题应该得到同一组检索角度。温度不为 0 时，
# 每次检索 Query 都略有不同 → 候选池浮动 → 同一个问题两次答案不一样
# （同一个问题可能一次拒答、一次编造）。答案生成走 DEEPSEEK_TEMPERATURE，
# 不受这里影响。
QUERY_REWRITE_TEMPERATURE = float(os.getenv("QUERY_REWRITE_TEMPERATURE", "0"))

# 混合检索融合权重（两个之和应为 1）。
# 为什么默认偏向向量：BM25 走字符 bigram，员工口语提问（"我早上几点到"）
# 与制度正文（"弹性到岗范围为8:30-10:00"）几乎没有共同 bigram，此时 BM25 排序
# 基本是噪声；而噪声块一旦拿到高分就会把真正相关的块挤出 Top-K。
HYBRID_VECTOR_WEIGHT = float(os.getenv("HYBRID_VECTOR_WEIGHT", "0.6"))
HYBRID_BM25_WEIGHT = float(os.getenv("HYBRID_BM25_WEIGHT", "0.4"))
# ⚠️ 耦合约束：SCORE_THRESHOLD 不能高于上面较小的那个权重。
# 只被单个通道召回的块，另一个通道分为 0，融合分就等于它的通道权重；
# 阈值一旦超过它，该通道的召回会被阈值整体丢掉（表现为"明明库里有却答不出"）。
# 当前 0.40 <= min(0.6, 0.4)，两个通道的独有召回都能过阈值。

# 一问多求（一句话里问了两件以上事）是否先拆成子问题，各自检索后合并。
# 不拆的话，向量 embedding 会变成多个主题的平均、BM25 词被稀释，每个子问题都排不上。
QUERY_DECOMPOSE_ENABLED = os.getenv("QUERY_DECOMPOSE_ENABLED", "true").lower() == "true"
# 单次检索最多发几条 Query（原问题 + LLM 改写 + 子问题），用于控制 embedding 调用次数。
RETRIEVAL_MAX_QUERIES = int(os.getenv("RETRIEVAL_MAX_QUERIES", "5"))

# RAGAs 评测阈值：独立评测/组合评测使用，负例仍走本项目拒答规则。
RAGAS_FAITHFULNESS_THRESHOLD = float(os.getenv("RAGAS_FAITHFULNESS_THRESHOLD", "0.8"))
RAGAS_CONTEXT_RECALL_THRESHOLD = float(os.getenv("RAGAS_CONTEXT_RECALL_THRESHOLD", "0.7"))
RAGAS_FACTUAL_CORRECTNESS_THRESHOLD = float(os.getenv("RAGAS_FACTUAL_CORRECTNESS_THRESHOLD", "0.7"))
RAGAS_ANSWER_RELEVANCY_THRESHOLD = float(os.getenv("RAGAS_ANSWER_RELEVANCY_THRESHOLD", "0.75"))

# ============ 记忆窗口 ============
# 对话上下文记忆：只带最近 N 轮（每轮 = 1 问 1 答），控制 prompt 长度/成本
HISTORY_ROUNDS = int(os.getenv("HISTORY_ROUNDS", "10"))
# 主流式问答链路的历史摘要压缩：超过 N 条历史消息时，把更早历史压缩成摘要，
# 再保留最近 M 条原文，避免线上 /qa/ask-stream 绕过 Agent 中间件后只剩滑动窗口。
HISTORY_SUMMARY_TRIGGER_MESSAGES = int(os.getenv("HISTORY_SUMMARY_TRIGGER_MESSAGES", "6"))
HISTORY_SUMMARY_KEEP_MESSAGES = int(os.getenv("HISTORY_SUMMARY_KEEP_MESSAGES", "2"))


# ============ 六、中间件开关 ============
# P1 进阶功能：给问答 Agent 挂中间件（对话摘要 + 隐私保护）
# 默认开；改 false 可对比"有中间件 vs 无中间件"的效果。
ENABLE_MIDDLEWARE = os.getenv("ENABLE_MIDDLEWARE", "true").lower() == "true"


# ============ 六、本地文件存储路径 ============
# 上传的原始文件先落盘到这里，再交给解析器读取
UPLOAD_DIR = BASE_DIR / "backend" / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)  # 确保目录存在

# 会话附件目录（对话中上传，按会话隔离）
ATTACHMENT_DIR = BASE_DIR / "backend" / "data" / "attachments"
ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)  # 确保目录存在

# 上传文件大小上限（字节）。超大文件（几十 MB）会导致后台解析/向量化
# 长时间卡在 processing，且企业制度文档普遍仅几 KB，限制避免此类问题。
# 默认 20MB，可通过 .env 的 MAX_FILE_SIZE 覆盖。
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(20 * 1024 * 1024)))  # 默认 20MB

# ============ 体验账号限额 ============
# 体验企业 = 登录页免审核注册的那种。它面向公网开放，必须封顶，
# 否则脚本批量注册 + 上传会把这台 2 核 4G 的展示机磁盘占满。
# 注意：模型调用不在这里限制 —— 体验企业必须自己配 API Key 才能问答，
# 用的是访客自己的额度，不消耗平台的。
TRIAL_MAX_KNOWLEDGE_BASES = int(os.getenv("TRIAL_MAX_KNOWLEDGE_BASES", "3"))
TRIAL_MAX_DOCUMENTS = int(os.getenv("TRIAL_MAX_DOCUMENTS", "20"))

# SQLite 数据库文件路径
DATABASE_PATH = BASE_DIR / "backend" / "data" / "app.db"
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)  # 确保 data 目录存在

# 数据库连接串（支持 MySQL / SQLite 切换）
# MySQL 示例：mysql+pymysql://root:密码@localhost:3306/kb_qa?charset=utf8mb4
# SQLite 示例：sqlite:///绝对路径
# 默认用 SQLite（演示零配置）；想用 MySQL 时在 .env 里配 DB_* 即可
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "kb_qa")
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "60"))

# 文档解析/切分/向量化/入库的后台并发数。
# 2核4G 线上展示机建议保持 1，避免批量上传同时打满 MySQL、嵌入接口和 Zilliz。
DOCUMENT_PROCESS_CONCURRENCY = int(os.getenv("DOCUMENT_PROCESS_CONCURRENCY", "1"))

# 判断是否启用 MySQL：配了 DB_PASSWORD 或用 DB_DRIVER=mysql 才走 MySQL
DB_DRIVER = os.getenv("DB_DRIVER", "sqlite").lower()
if DB_DRIVER == "mysql":
    DATABASE_URL = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
    )
else:
    DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"

# ============ 七、认证配置 ============
# 开发默认值用于本地演示；上线时建议在 .env 中配置强随机 AUTH_SECRET_KEY。
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "dev-local-auth-secret-change-before-deploy")
AUTH_TOKEN_EXPIRE_DAYS = int(os.getenv("AUTH_TOKEN_EXPIRE_DAYS", "7"))
