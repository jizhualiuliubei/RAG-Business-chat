"""
Milvus 向量库封装
================
作用：把 MilvusClient 的"建库/建集合/写入/检索/删除"封装成项目自己的函数，
     业务层只需要调用 add_chunks() / search() / delete_by_doc_id()，不用关心底层 API。

对应知识点：Milvus 向量数据库（课程 chapter10-04，course_rag_milvus.py）
- pymilvus 的 MilvusClient（HTTP 客户端，连接 http://localhost:19530）
- 集合（collection）相当于关系数据库的表
- 维度必须和嵌入模型一致（BGE-M3 = 1024）
- COSINE 余弦相似度：distance 越大越相似

设计要点：
- kb_id 字段：从第一天预留多知识库（P0 只有一个默认库）
- doc_id 字段：删除文档时按它过滤，实现"删文档同步删向量"
"""
from pymilvus import MilvusClient

from app.config import (
    EMBED_DIM,
    MILVUS_COLLECTION,
    MILVUS_DB_NAME,
    MILVUS_TOKEN,
    MILVUS_URI,
)

_client = None  # 模块级单例
_initialized = False  # 是否已确保数据库/集合存在
_collection_fields: set[str] | None = None


def get_client() -> MilvusClient:
    """懒加载 Milvus 客户端单例（连接是重量级对象，只建一次）"""
    global _client
    if _client is None:
        if MILVUS_TOKEN:
            _client = MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN, db_name=MILVUS_DB_NAME)
        else:
            _client = MilvusClient(MILVUS_URI)
    return _client


def _ensure_ready() -> None:
    """确保客户端处于可用状态（自动初始化 + 切到正确数据库 + 确保集合存在）。

    为什么每次操作前都检查：
    1. 集合创建不能只依赖应用启动时的 lifespan，否则 lifespan 未触发
       （测试、脚本直调等场景）就会报"集合不存在"。
    2. pymilvus 的 use_database 状态不稳定——连接可能悄悄回到 default 库，
       所以"切库"要放在每次操作的入口统一处理，保证操作落在正确的库上。
    用一个标志位保证 ensure_collection 只执行一次，避免重复建库查询。
    """
    global _initialized, _collection_fields
    client = get_client()
    if not MILVUS_TOKEN:
        # 本地 Milvus 连接状态不可靠，每次操作前显式切库更稳。
        existed = client.list_databases()
        if MILVUS_DB_NAME not in existed:
            client.create_database(db_name=MILVUS_DB_NAME)
        client.use_database(db_name=MILVUS_DB_NAME)

    if not _initialized:
        if not client.has_collection(collection_name=MILVUS_COLLECTION):
            # 显式定义 schema：主键 + vector + 业务字段
            # 之前只用 create_collection(dimension=...) 建了 id+vector，
            # 导致 text/source/doc_id/kb_id/chunk_id 没进 schema，
            # 写入时被丢弃 → 检索拿不到元数据、kb_id 过滤失效。
            from pymilvus import CollectionSchema, DataType, FieldSchema

            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=EMBED_DIM),
                FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=255),
                FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="enterprise_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="chunk_id", dtype=DataType.INT64),
            ]
            schema = CollectionSchema(fields=fields)
            client.create_collection(
                collection_name=MILVUS_COLLECTION,
                schema=schema,
                metric_type="COSINE",
            )
            # 建向量索引（HNSW），否则无法 load/检索
            from pymilvus.milvus_client.index import IndexParams

            ip = IndexParams()
            ip.add_index(
                field_name="vector", index_type="HNSW",
                metric_type="COSINE",
                params={"M": 16, "efConstruction": 128},
            )
            client.create_index(
                collection_name=MILVUS_COLLECTION,
                index_params=ip,
            )
            # 重建后必须加载到内存才能检索（Milvus 3.x）
            client.load_collection(collection_name=MILVUS_COLLECTION)
        _collection_fields = _describe_collection_fields(client)
        _initialized = True


def _describe_collection_fields(client: MilvusClient | None = None) -> set[str]:
    """读取当前 collection 已声明字段。

    线上可能已经存在旧版 collection，旧 schema 没有 enterprise_id。
    Milvus/Zilliz 未开启 dynamic field 时，插入 schema 外字段会直接报
    DataNotMatchException。这里用字段探测做兼容，避免上传链路被旧集合阻断。
    """
    client = client or get_client()
    try:
        desc = client.describe_collection(collection_name=MILVUS_COLLECTION)
    except Exception:
        return {"id", "vector", "text", "source", "doc_id", "kb_id", "chunk_id"}

    fields = desc.get("fields") if isinstance(desc, dict) else None
    names: set[str] = set()
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict) and field.get("name"):
                names.add(str(field["name"]))
            elif hasattr(field, "name"):
                names.add(str(field.name))
    return names or {"id", "vector", "text", "source", "doc_id", "kb_id", "chunk_id"}


def _field_exists(name: str) -> bool:
    global _collection_fields
    if _collection_fields is None:
        _collection_fields = _describe_collection_fields()
    return name in _collection_fields


def _output_fields(*names: str) -> list[str]:
    return [name for name in names if _field_exists(name)]


def ensure_collection() -> None:
    """确保目标数据库和集合存在（幂等：启动时调用，重复执行无副作用）。

    启动时调用一次，让问题尽早暴露（Milvus 未启动会在这里抛异常）；
    真正的"每次操作前自愈"由 _ensure_ready() 完成。
    """
    _ensure_ready()


def add_chunks(chunks: list, vectors: list, doc_id: int, kb_id: int, source: str, enterprise_id: int | None = None) -> None:
    """把一批切分后的文档块写入向量库。

    参数：
        chunks  ：切分后的 Document 列表（有 page_content）
        vectors ：和 chunks 一一对应的 1024 维向量列表
        doc_id  ：文档主键（删除文档时用它过滤）
        kb_id   ：知识库主键（预留多库）
        source  ：来源文件名（引用溯源展示用）

    对应课程 data 结构 + upsert + flush（course_rag_milvus.py 第 3 步）
    """
    _ensure_ready()
    # 主键：Milvus 默认主键是 int64，不能用字符串。
    # 用 doc_id * 100000 + i 生成全局唯一整数，保证重复 upsert 时幂等覆盖。
    BASE = 100000
    data = []
    for i in range(len(chunks)):
        item = {
            "id": doc_id * BASE + i,       # 整数主键：doc1 的第0块 = 100000
            "vector": vectors[i],
            "text": chunks[i].page_content,
            "source": source,
            "doc_id": str(doc_id),            # 转字符串：Milvus 过滤条件字符串更稳
            "kb_id": str(kb_id),
            "chunk_id": i,
        }
        if enterprise_id is not None and _field_exists("enterprise_id"):
            item["enterprise_id"] = str(enterprise_id)
        data.append(item)
    client = get_client()
    client.upsert(collection_name=MILVUS_COLLECTION, data=data)
    # flush：把内存里的数据真正写入磁盘，检索前必须先 flush 才能搜到
    client.flush(collection_name=MILVUS_COLLECTION)


def _scope_filter(kb_id=None, doc_ids: list[int] | None = None, enterprise_id: int | None = None) -> str:
    parts = []
    if enterprise_id is not None and _field_exists("enterprise_id"):
        parts.append(f'enterprise_id == "{enterprise_id}"')
    if kb_id is not None:
        if isinstance(kb_id, (int, str)):
            parts.append(f'kb_id == "{kb_id}"')
        else:
            ids_str = ", ".join(f'"{i}"' for i in kb_id)
            parts.append(f"kb_id in [{ids_str}]")
    if doc_ids is not None:
        ids_str = ", ".join(f'"{doc_id}"' for doc_id in doc_ids)
        parts.append(f"doc_id in [{ids_str}]")
    return " and ".join(parts)


def search(
    query_vector: list,
    top_k: int,
    kb_id,
    candidate_k: int | None = None,
    enterprise_id: int | None = None,
    doc_ids: list[int] | None = None,
) -> list:
    """语义检索：返回最相似的 top_k 个片段。

    参数：
        query_vector：embed_query 产出的查询向量
        top_k        ：返回几条最相似的
        kb_id        ：知识库 id（支持单个 int 或多个 int 列表，多库联合检索）
        candidate_k  ：可选，召回候选数（用于混合检索：先扩召回再重排）。
                       默认 None 表示直接用 top_k（纯向量检索）。
        doc_ids      ：可选，当前数据库仍存在且解析完成的文档 id 白名单。
    返回：Milvus 原始 hits 列表，每个 hit 有
        hit["distance"]            相似度（COSINE 越大越相似）
        hit["entity"]["text"]      片段文本
        hit["entity"]["source"]    来源文件名
        hit["entity"]["chunk_id"]  片段序号
        hit["entity"]["doc_id"]    文档 id

    对应课程 course_rag_milvus.py 第 4 步 + filter 用法
    """
    if doc_ids is not None and not doc_ids:
        return []
    _ensure_ready()
    # 混合检索需要更大的候选池（真实答案块可能不在纯向量 top_k 里）
    limit = candidate_k if candidate_k is not None else top_k
    scope_expr = _scope_filter(kb_id=kb_id, enterprise_id=enterprise_id, doc_ids=doc_ids)
    results = get_client().search(
        collection_name=MILVUS_COLLECTION,
        data=[query_vector],
        limit=limit,
        output_fields=_output_fields("text", "source", "doc_id", "chunk_id", "kb_id", "enterprise_id"),
        filter=scope_expr,   # 只在指定企业和知识库内检索（可多个）
    )
    return results[0]  # data=[query_vector] 只有一条查询，所以取 results[0]


def delete_by_doc_id(doc_id: int, enterprise_id: int | None = None) -> None:
    """删除某文档的全部向量（删除文档时联动调用）。"""
    delete_by_doc_ids([doc_id], enterprise_id=enterprise_id)


def delete_by_doc_ids(doc_ids: list[int], enterprise_id: int | None = None) -> None:
    """批量删除多个文档的全部向量，减少 Zilliz 网络往返和 flush 次数。"""
    if not doc_ids:
        return
    _ensure_ready()
    client = get_client()
    client.delete(
        collection_name=MILVUS_COLLECTION,
        filter=_scope_filter(doc_ids=doc_ids, enterprise_id=enterprise_id),
    )
    client.flush(collection_name=MILVUS_COLLECTION)


def delete_by_kb_id(kb_id: int, enterprise_id: int | None = None) -> None:
    """删除某个知识库下的全部向量，用于删库时清理可能的历史残留。"""
    _ensure_ready()
    client = get_client()
    client.delete(
        collection_name=MILVUS_COLLECTION,
        filter=_scope_filter(kb_id=kb_id, enterprise_id=enterprise_id),
    )
    client.flush(collection_name=MILVUS_COLLECTION)


def count_by_doc_ids(doc_ids: list[int], enterprise_id: int | None = None) -> int:
    """统计一批文档在向量库中仍残留的 chunk 数，用于强一致删除校验。"""
    if not doc_ids:
        return 0
    _ensure_ready()
    results = get_client().query(
        collection_name=MILVUS_COLLECTION,
        filter=_scope_filter(doc_ids=doc_ids, enterprise_id=enterprise_id),
        output_fields=_output_fields("doc_id"),
        limit=10000,
    )
    return len(results)


def count_by_kb_id(kb_id: int, enterprise_id: int | None = None) -> int:
    """统计某知识库在向量库中仍残留的 chunk 数，用于删库后的强一致校验。"""
    _ensure_ready()
    results = get_client().query(
        collection_name=MILVUS_COLLECTION,
        filter=_scope_filter(kb_id=kb_id, enterprise_id=enterprise_id),
        output_fields=_output_fields("kb_id"),
        limit=10000,
    )
    return len(results)


def list_all_texts(kb_id, enterprise_id: int | None = None, doc_ids: list[int] | None = None) -> list:
    """拉取一个或多个知识库的全部 chunk 文本（用于 BM25 全库召回通道）。

    参数：kb_id 为单个 int 或 int 列表（多库联合检索）。
    返回：[{"text": ..., "doc_id": str, "source": ..., "kb_id": str}, ...]
    本项目文档极少（全库 ~73 块），全库扫描成本可忽略；
    是"双通道召回"（向量 top-K ∪ BM25 全库 top-M）的基础。
    """
    if doc_ids is not None and not doc_ids:
        return []
    _ensure_ready()
    scope_expr = _scope_filter(kb_id=kb_id, enterprise_id=enterprise_id, doc_ids=doc_ids)
    results = get_client().query(
        collection_name=MILVUS_COLLECTION,
        filter=scope_expr,
        output_fields=_output_fields("text", "doc_id", "source", "kb_id", "enterprise_id", "chunk_id"),
        limit=10000,
    )
    return [
        {
            "text": r.get("text", ""),
            "doc_id": str(r.get("doc_id", "")),
            "source": r.get("source", ""),
            "kb_id": str(r.get("kb_id", "")),
            "enterprise_id": str(r.get("enterprise_id", "")),
            "chunk_id": r.get("chunk_id", 0),
        }
        for r in results
    ]


def list_chunks_by_doc(doc_id: int, enterprise_id: int | None = None) -> list:
    """按文档 id 查询其全部 chunk（概览页文档下钻用）。

    返回：[{"text": ..., "chunk_id": int}, ...]，按 chunk_id 升序。
    """
    _ensure_ready()
    results = get_client().query(
        collection_name=MILVUS_COLLECTION,
        filter=_scope_filter(doc_ids=[doc_id], enterprise_id=enterprise_id),
        output_fields=["text", "chunk_id"],
        limit=10000,
    )
    chunks = [
        {"text": r.get("text", ""), "chunk_id": r.get("chunk_id", 0)}
        for r in results
    ]
    chunks.sort(key=lambda c: c["chunk_id"])
    return chunks
