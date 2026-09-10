"""
混合检索模块（BM25 关键词 + 向量融合）
================
作用：解决"纯向量检索"在中文短查询上的召回偏差。

为什么需要混合检索：
- bge-m3 是语义匹配，对"报销标准"这类短查询，容易和"标准工时制"等撞词，
  把无关文本的相似度抬得很高（实测：考勤制度 0.629 > 报销制度 0.337）
- BM25 是字面匹配（关键词），能精确抓住"报销/标准/保留"等字面词，
  把真正含这些词的文本排前面
- 两者加权融合（各 0.5），兼顾"语义相关"和"字面命中"，显著提升中文 RAG 召回

实现说明：
- 纯 Python 标准库，不依赖 jieba / rank_bm25（环境里没有，也不能装新包）
- 用字符 bigram（相邻两个字符）做"词"，避免中文分词依赖
- 这是简化版 BM25（不按文档长度归一），对我们的场景足够有效
"""
import math
from collections import Counter


def _bigrams(text: str) -> list[str]:
    """把文本切成字符 bigram 列表（相邻两个字符）。"""
    text = text.replace(" ", "").replace("\n", "")
    return [text[i : i + 2] for i in range(len(text) - 1)]


class SimpleBM25:
    """简化版 BM25：统计每个"词"（bigram）在文档集合中的逆文档频率。"""

    def __init__(self, docs: list[str]):
        # 统计每个词在多少篇文档中出现（文档频率 df）
        doc_count = len(docs)
        df = Counter()
        for doc in docs:
            for gram in set(_bigrams(doc)):
                df[gram] += 1

        # 计算 idf = ln((N - df + 0.5) / (df + 0.5) + 1)
        # 词越罕见、出现文档越少，idf 越高，越能区分文档
        self.idf = {
            gram: math.log((doc_count - freq + 0.5) / (freq + 0.5) + 1)
            for gram, freq in df.items()
        }

    def score(self, query: str, doc: str) -> float:
        """计算 query 对单篇 doc 的 BM25 得分 = 所有命中词 idf 之和。"""
        q_grams = _bigrams(query)
        doc_grams = set(_bigrams(doc))
        return sum(self.idf.get(g, 0) for g in q_grams if g in doc_grams)


def _dedup_hits(hits: list) -> list:
    """按 (doc_id, 章节标题) 去重，同一章节的多块只保留最高分。

    为什么需要：整章切分后，同一个章节若因超长被兜底切成多块，
    会同时占据 top_k 的多个位置，把"跨文档的其他相关块"（B/C 类题
    需要多来源）挤出。去重保证 top_k 尽量来自不同章节/文档。

    章节标题取 entity["text"] 的首行（"文档名 章节名"，整章切分写入）。
    """
    seen = set()
    out = []
    for h in sorted(hits, key=lambda x: x["distance"], reverse=True):
        entity = h.get("entity", {})
        text = entity.get("text", "")
        # 首行是"文档名 章节名"；若 text 无首行（异常），用空串，退化为按 doc_id 去重
        heading = text.split("\n", 1)[0] if text else ""
        key = (entity.get("doc_id", ""), heading)
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def bm25_top_hits(query: str, all_docs: list, top_n: int) -> list:
    """BM25 全库排序，返回得分最高的 top_n 个候选（不归一化）。

    参数：
        query   ：用户问题
        all_docs：知识库全部 chunk 文本列表（与向量候选同序）
        top_n   ：返回几个
    返回：
        [{"text": ..., "bm25": 原始得分}, ...]，按得分降序。
        与向量候选合并后供 hybrid_search 做最终融合。

    为什么需要独立 BM25 通道：
    bge-m3 对部分问题（如"P2 响应时限"这种专有名词组合）向量召回很弱，
    真实答案块的向量分可能排到第 29 位，根本进不了向量 top-K 候选池，
    混合检索的 BM25 也就"无米下锅"。全库 BM25 扫描（本项目仅 ~73 块）
    能让字面相关的块直接进入候选，弥补向量召回的盲区。
    """
    bm = SimpleBM25(all_docs)
    scored = [(i, bm.score(query, d)) for i, d in enumerate(all_docs)]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [{"index": i, "text": all_docs[i], "bm25": s} for i, s in scored[:top_n]]


def _norm01(values: list) -> list:
    """把一组数值线性归一化到 [0,1]（min-max）；全为 0 时返回全 0。"""
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    span = hi - lo
    if span <= 1e-9:
        return [0.0] * len(values)
    return [(v - lo) / span for v in values]


def fuse_dual(query: str, vector_hits: list, all_texts: list, top_k: int) -> list:
    """双通道召回 + 融合重排（向量 top-K ∪ BM25 全库 top-M → 统一打分）。

    为什么需要 BM25 全库通道：
    bge-m3 对部分问题（如"P2 响应时限""公积金缴存比例"这类专有名词组合）
    向量召回很弱，真实答案块的向量分可能排到第 29 位，根本进不了向量 top-K
    候选池，单通道混合检索的 BM25 也就"无米下锅"。全库 BM25 扫描
    （本项目仅 ~73 块，成本可忽略）让字面相关的块直接进入候选。

    参数：
        query      ：用户问题
        vector_hits：Milvus 向量检索返回的候选（含 distance=向量cosine, entity）
        all_texts  ：知识库全部 chunk 文本列表（milvus_store.list_all_texts）
        top_k      ：最终返回几个
    返回：
        融合重排后的 hits 列表（含 distance=融合分，entity.text/source/doc_id）。
        注意：返回结果里可能混入 BM25 通道召回、向量通道没召回的块，
        它们的 entity 来自 all_texts，没有 vector 原始 hits 的 chunk_id 字段。
    """
    if not vector_hits and not all_texts:
        return []

    # 1. 建一个 "text -> 向量候选hit" 的映射（向量通道带原始元数据）
    vec_by_text = {}
    for h in vector_hits:
        t = h.get("entity", {}).get("text", "")
        if t:
            vec_by_text[t] = h

    # 2. BM25 全库通道：取 topM（= top_k 的 3 倍，保证候选足够）。
    #    all_texts 是 [{"text","source","doc_id","kb_id"}]，抽 text 列表给 BM25
    texts = [x.get("text", "") for x in all_texts]
    top_m = max(top_k * 3, 15)
    bm_top = bm25_top_hits(query, texts, top_m)
    bm_hits = []
    for item in bm_top:
        t = item["text"]
        # 优先复用向量通道的原始 hit（保留 chunk_id 等元数据）
        if t in vec_by_text:
            bm_hits.append(vec_by_text[t])
        else:
            src = all_texts[item["index"]] if item["index"] < len(all_texts) else {}
            bm_hits.append({
                "entity": {
                    "text": t,
                    "source": src.get("source", ""),
                    "doc_id": src.get("doc_id", ""),
                    "kb_id": src.get("kb_id", ""),
                    "enterprise_id": src.get("enterprise_id", ""),
                    "chunk_id": src.get("chunk_id", 0),
                },
            })

    # 3. 合并候选：以 BM25 top-M 为主集（已含向量通道重合部分），
    #    再补充向量通道里 BM25 没进 top-M 的块（语义兜底）
    seen_texts = set(h.get("entity", {}).get("text", "") for h in bm_hits)
    for h in vector_hits:
        t = h.get("entity", {}).get("text", "")
        if t and t not in seen_texts:
            bm_hits.append(h)
            seen_texts.add(t)

    # 4. 统一打分：向量分 + BM25 分各自归一化后融合
    docs = [h.get("entity", {}).get("text", "") for h in bm_hits]
    bm_model = SimpleBM25(docs)
    bm_scores = [bm_model.score(query, d) for d in docs]
    vec_scores = [
        h.get("distance", 0.0) if h.get("entity", {}).get("text", "") in vec_by_text else 0.0
        for h in bm_hits
    ]
    # BM25 通道召回、但向量没召回的块，向量分补 0 → 融合分主要靠 BM25
    for i, h in enumerate(bm_hits):
        t = h.get("entity", {}).get("text", "")
        if t not in vec_by_text:
            vec_scores[i] = 0.0
    bm_norm = _norm01(bm_scores)
    vec_norm = _norm01(vec_scores)
    fused = []
    for i, h in enumerate(bm_hits):
        fused.append({
            **h,
            "distance": 0.4 * vec_norm[i] + 0.6 * bm_norm[i],
        })
    fused.sort(key=lambda x: x["distance"], reverse=True)
    return fused


def hybrid_search(query: str, vector_hits: list) -> list:
    """把 Milvus 向量候选，用"向量得分 + BM25"融合重排。

    参数：
        query       ：用户问题
        vector_hits ：Milvus 向量检索返回的候选（每个含 distance 和 entity.text）
    返回：
        融合重排后的 hits 列表（含 distance 字段，语义和向量检索一致），
        distance 越大越相关。

    融合逻辑：
        1. 用候选文本建 BM25 索引
        2. 对每个候选算 BM25 得分，并归一化到 [0,1]
        3. 向量距离也归一化到 [0,1]
        4. 融合分 = 0.5 * 向量归一化分 + 0.5 * BM25 归一化分
    为什么归一化：向量距离（COSINE 0~1）和 BM25（0~几十）量纲完全不同，
    必须先各自归一化才能相加。
    """
    if not vector_hits:
        return []

    # 1. 提取候选文本，建 BM25 索引
    docs = [h["entity"].get("text", "") for h in vector_hits]
    bm25 = SimpleBM25(docs)

    # 2. 算每个候选的 BM25 得分，并归一化
    bm25_scores = [bm25.score(query, d) for d in docs]
    max_bm25 = max(bm25_scores) if max(bm25_scores) > 0 else 1.0
    bm25_norm = [s / max_bm25 for s in bm25_scores]

    # 3. 向量距离归一化（COSINE 距离已在 [0,1]，取原值即可）
    vec_norm = [h["distance"] for h in vector_hits]

    # 4. 融合分 = 0.4 * 向量 + 0.6 * BM25，并重排
    # 权重说明：制度类短问题（"报销标准""培训预算"）字面命中比语义更可靠，
    # 所以 BM25 权重略高于向量（0.6）。向量负责"语义兜底"（不同词但同义）。
    fused = []
    for i, h in enumerate(vector_hits):
        fused.append({
            **h,  # 保留原始字段（entity 等）
            "distance": 0.4 * vec_norm[i] + 0.6 * bm25_norm[i],  # 融合分
        })
    fused.sort(key=lambda x: x["distance"], reverse=True)
    return fused
