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

# load_dotenv(override=True)：对应课程写法
# - 把 .env 文件里的 "键=值" 读成环境变量
# - override=True 表示：如果内存里已有同名环境变量，用 .env 的值覆盖它
load_dotenv(BASE_DIR / ".env", override=True)


# ============ 一、对话模型 DeepSeek ============
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
# 企业制度/RAG 问答以稳定、可复现为主，默认低温；需要创意回答时可在 .env 调高。
DEEPSEEK_TEMPERATURE = float(os.getenv("DEEPSEEK_TEMPERATURE", "0.1"))


# ============ 二、嵌入模型 硅基流动 BGE-M3 ============
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
# 注意：嵌入模型的 base_url 要以 /v1 结尾（课程里的一个细节坑）
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
# - 350 折中：章节完整、开头块干扰可控（实测最优）
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "350"))       # 每个片段约多少字符
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "80"))  # 相邻片段重叠多少字符


# ============ 五、检索参数 ============
TOP_K = int(os.getenv("TOP_K", "5"))                          # 每次检索返回几个片段
# 相似度阈值：混合检索的融合分（0~1，真相关>0.6，无关<0.3）
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.45"))  # 低于此值视为无相关内容
ENABLE_RERANK = os.getenv("ENABLE_RERANK", "true").lower() == "true"
RERANK_MODEL_NAME = os.getenv("RERANK_MODEL_NAME", "BAAI/bge-reranker-v2-m3")
RERANK_TIMEOUT_SECONDS = float(os.getenv("RERANK_TIMEOUT_SECONDS", "8"))

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
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(20 * 1024 * 1024)))

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
