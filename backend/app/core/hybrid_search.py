"""
混合检索模块（BM25 关键词 + 向量融合）
================
作用：解决"纯向量检索"在中文短查询上的召回偏差。

为什么需要混合检索：
- bge-m3 是语义匹配，对"报销标准"这类短查询，容易和"标准工时制"等撞词，
  把无关文本的相似度抬得很高
- BM25 是字面匹配（关键词），能精确抓住"报销/标准/保留"等字面词，
  把真正含这些词的文本排前面
- 两者加权融合（权重见 config 的 HYBRID_VECTOR_WEIGHT / HYBRID_BM25_WEIGHT），
  兼顾"语义相关"和"字面命中"，显著提升中文 RAG 召回
- 两个通道各自先按「语义单元」去重再取名额：同一段内容在真实库里常有多份副本
  （多页重复收录、只差页码/轮次的模板行），不合并就会把通道的固定名额吃光

实现说明：
- 纯 Python 标准库，不依赖 jieba / rank_bm25（环境里没有，也不能装新包）
- 用字符 bigram（相邻两个字符）做"词"，避免中文分词依赖
"""
import math
import re
from collections import Counter

from app.config import HYBRID_BM25_WEIGHT, HYBRID_VECTOR_WEIGHT

# 条款编号（如 HR-02-006）。前后用 lookaround 而不是 \b，因为条款号紧贴中文时
# （「…HR-01-004条款…」）\b 不成立。这是全项目唯一来源，rag.py 也从这里导入。
_CLAUSE_ID_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,}-\d{2}-\d{3}(?![A-Za-z0-9])")

# 语义单元归一化用到的正则（见 text_unit_key）：
# 结构头是 splitter 写的首行「来源：…」；数字在重复副本里最易变（页码、轮次）；
# \W 在 str 上是 unicode 感知的，中文汉字属于 \w，不会被当标点去掉。
_STRUCTURED_HEAD_RE = re.compile(r"^来源：.*\n?")
_DIGITS_RE = re.compile(r"\d+")
_PUNCT_SPACE_RE = re.compile(r"[\W_]+")


def _bigrams(text: str) -> list[str]:
    """把文本切成字符 bigram 列表（相邻两个字符）。"""
    text = text.replace(" ", "").replace("\n", "")
    return [text[i : i + 2] for i in range(len(text) - 1)]


class SimpleBM25:
    """字符 bigram 版 BM25（纯 Python，不依赖分词库）。

    保留标准 BM25 的文档长度归一化：长块天然命中更多 query bigram，
    不归一化的话"什么都提一句"的长模板块会系统性地排在前面
    （制度文档里典型的长样本块是把整页表格拼在一起的元数据块）。
    """

    def __init__(self, docs: list[str], k1: float = 1.2, b: float = 0.75):
        doc_count = len(docs)
        # 统计每个词在多少篇文档中出现（文档频率 df）
        df = Counter()
        lengths = []
        for doc in docs:
            grams = _bigrams(doc)
            lengths.append(len(grams))
            for gram in set(grams):
                df[gram] += 1

        # 计算 idf = ln((N - df + 0.5) / (df + 0.5) + 1)
        # 词越罕见、出现文档越少，idf 越高，越能区分文档
        self.idf = {
            gram: math.log((doc_count - freq + 0.5) / (freq + 0.5) + 1)
            for gram, freq in df.items()
        }
        self.k1 = k1
        self.b = b
        self.avg_len = (sum(lengths) / doc_count) if doc_count else 1.0

    def score(self, query: str, doc: str) -> float:
        """query 对单篇 doc 的 BM25 得分。"""
        doc_grams = _bigrams(doc)
        if not doc_grams:
            return 0.0
        tf = Counter(doc_grams)
        avg_len = self.avg_len or 1.0
        # 查询词去重后再算：同一个 bigram 在问题里出现两次不构成两倍权重
        total = 0.0
        for gram in set(_bigrams(query)):
            freq = tf.get(gram)
            if not freq:
                continue
            norm = self.k1 * (1 - self.b + self.b * len(doc_grams) / avg_len)
            total += self.idf.get(gram, 0.0) * (freq * (self.k1 + 1)) / (freq + norm)
        return total


def bm25_ranked(query: str, all_texts: list) -> list:
    """BM25 全库排序（不截断），返回 hit 列表（distance = BM25 原始得分，降序）。

    返回结构和向量通道一致（distance + entity），这样去重、保底、取名额
    这几处都能用同一个 hit_dedup_key 判断"是不是同一份证据"，不会出现
    两个通道对同一个块给出不同判定。

    参数：
        query    ：检索 Query（多路改写拼接后的字符串）
        all_texts：知识库全部 chunk（milvus_store.list_all_texts 的结果）

    返回完整排序而不是 top_n：取名额的那一步要做「按语义单元去重 + 单文档上限」，
    需要能看到足够远的排名才能凑满名额。全库扫描本来就是 O(块数)，不额外花钱。

    为什么需要独立 BM25 通道：
    bge-m3 对部分问题（如"P2 响应时限"这种专有名词组合）向量召回很弱，
    真实答案块的向量分可能排在很后面，根本进不了向量 top-K 候选池，
    混合检索的 BM25 也就"无米下锅"。全库 BM25 扫描让字面相关的块直接进入候选。
    """
    docs = [x.get("text", "") for x in all_texts]
    bm = SimpleBM25(docs)
    scored = [(i, bm.score(query, d)) for i, d in enumerate(docs)]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [
        {
            "distance": score,
            "entity": {
                "text": docs[i],
                "source": all_texts[i].get("source", ""),
                "doc_id": str(all_texts[i].get("doc_id", "")),
                "kb_id": str(all_texts[i].get("kb_id", "")),
                "enterprise_id": str(all_texts[i].get("enterprise_id", "")),
                "chunk_id": all_texts[i].get("chunk_id", 0),
            },
        }
        for i, score in scored
    ]


def text_unit_key(text: str) -> str:
    """把一段 chunk 正文归一化成「语义单元」骨架。

    同一段内容在真实的库里往往有多份副本，而副本之间只差几个易变字符：
    页码（第01页 / 第06页）、轮次（第1轮 / 第2轮业务复盘）、结构头写法。
    归一化后它们得到同一个骨架，去重时只占一个候选名额。

    这一步不绑定任何具体语料：数字在任何语料里都是最易变的重复部分，
    结构头和标点同理。条款编号是更强的信号，由 hit_dedup_key 单独处理。
    """
    t = _STRUCTURED_HEAD_RE.sub("", str(text or ""), count=1)
    t = _DIGITS_RE.sub("#", t)
    return _PUNCT_SPACE_RE.sub("", t)


def hit_dedup_key(hit: dict) -> tuple:
    """同一条证据的去重键。

    _dedup_hits、通道内取名额、rag 里的「rerank 保底」共用这个函数，
    避免多处对"这个块是不是已经在结果里"判断不一致。

    - 正文里有条款编号（XX-01-001）：按 (doc_id, 条款编号集合) 去重。
      **不能按标题去重**：这批制度文档同一个章节会在多页重复出现
      （第02页 / 第07页 / 第12页…），标题里带页码，按标题去重完全失效。
      有编号时也不用正文骨架：编号是精确标识，能避免"数字相近的不同条款"
      被骨架归一化误合并（例如"P1 级 10 分钟响应"与"P2 级 30 分钟响应"）。
    - 正文里没有条款编号（模板行、元数据行、叙述性段落）：按
      (doc_id, 归一化正文骨架) 去重。早期版本退回「首行标题」，但标题里带页码
      （第01页 / 第07页…），同一种模板行会被当成互不相同的块 —— 一份文档里
      十几个同构模板块就足够占满 BM25 通道的全部名额，其他章节连进候选池的
      机会都没有。
    """
    entity = hit.get("entity", {})
    text = entity.get("text", "")
    clause_ids = frozenset(_CLAUSE_ID_RE.findall(text))
    if clause_ids:
        return (entity.get("doc_id", ""), clause_ids)
    return (entity.get("doc_id", ""), text_unit_key(text))


def pick_top_units(rows: list, top_n: int, key_of, doc_of=None, max_per_doc: int | None = None) -> list:
    """按给定顺序取 top_n 个「不同的证据单元」，可选限制单个文档占几个名额。

    参数：
        rows        ：已按相关性排好序的候选（hit 结构）
        top_n       ：取几个
        key_of      ：从一行里取出「证据单元」标识的函数（传 hit_dedup_key）
        doc_of      ：从一行里取出 doc_id 的函数（用于单文档名额限制）
        max_per_doc ：单个文档最多占几个名额；None 表示不限

    key_of 必须和 _dedup_hits 用同一个键：两处判定不一致的话，同一条款的副本
    会在通道内占掉多个名额，融合后再去重已经晚了 —— 名额已经花掉了。

    为什么要按「单元」而不是按「行」取名额：
    一个通道的候选名额是固定的。库里存在大量同构副本时，"先到先得"会让少数
    单元吃光全部名额，其他章节连进候选池的机会都没有 —— 这时候单纯提高候选数
    也没用，多出来的名额还是被同一批副本占掉。

    为什么要限制单文档名额：
    一个通道完全被单份文档垄断时，返回条数固定的情况下，跨文档题必然拿不到
    另一个来源。这个限制只作用于候选池，最终排序仍由融合分和 rerank 决定。
    """
    seen_units = set()
    per_doc: dict = {}
    out = []
    for row in rows:
        key = key_of(row)
        if key in seen_units:
            continue
        doc = doc_of(row) if doc_of else ""
        if max_per_doc is not None and per_doc.get(doc, 0) >= max_per_doc:
            continue
        seen_units.add(key)
        per_doc[doc] = per_doc.get(doc, 0) + 1
        out.append(row)
        if len(out) >= top_n:
            break
    return out


def _dedup_hits(hits: list) -> list:
    """去重：同一份证据只保留分数最高的那一块。

    为什么需要：整章切分后同一个章节可能被切成多块（或多页重复收录同一章节），
    会同时占据 top_k 的多个位置，把"跨文档的其他相关块"（跨文档题需要多来源）挤出。
    """
    seen = set()
    out = []
    for h in sorted(hits, key=lambda x: x["distance"], reverse=True):
        key = hit_dedup_key(h)
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


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
    向量召回很弱，真实答案块的向量分可能排在很后面，根本进不了向量 top-K
    候选池，单通道混合检索的 BM25 也就"无米下锅"。全库 BM25 扫描
    让字面相关的块直接进入候选。

    关于 query 的形态（不要凭直觉改）：
    传进来的是多路改写 Query 的**拼接串**。曾试过改成"传列表、对每条 Query
    分别打分取最大值"，试下来单主题用例排名不变、多主题用例反而更差。
    拼接不会引入噪声 —— 噪声来自**词表补词**那种泛化词（已从查询改写里删掉），
    来自用户问题的内容词不会互相污染。

    参数：
        query      ：检索 Query（多路改写拼接后的字符串）
        vector_hits：Milvus 向量检索返回的候选（含 distance=向量cosine, entity）
        all_texts  ：知识库全部 chunk（milvus_store.list_all_texts）
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

    # 2. BM25 全库通道：先按证据单元去重、再取名额。
    #    名额比 top_k 大得多，因为去重后每个名额都是一个不同的证据单元；
    #    单文档名额上限防止一份风格高度同质的文档（整页模板表之类）垄断通道。
    top_m = max(top_k * 6, 30)
    picked = pick_top_units(
        bm25_ranked(query, all_texts),
        top_m,
        key_of=hit_dedup_key,
        doc_of=lambda h: h["entity"]["doc_id"],
        max_per_doc=max(3, top_m // 3),
    )
    # 优先复用向量通道的原始 hit（保留 chunk_id 等元数据）
    bm_hits = [vec_by_text.get(h["entity"]["text"], h) for h in picked]

    # 3. 合并候选：以 BM25 名额内的单元为主集（已含向量通道重合部分），
    #    再补充向量通道里没进 BM25 名额的块（语义兜底）
    seen_texts = set(h.get("entity", {}).get("text", "") for h in bm_hits)
    for h in vector_hits:
        t = h.get("entity", {}).get("text", "")
        if t and t not in seen_texts:
            bm_hits.append(h)
            seen_texts.add(t)

    # 4. 统一到同一把尺子上：两个通道量纲不同（向量 cosine 0~1、BM25 0~几十），
    #    各自归一化后再按权重相加
    docs = [h.get("entity", {}).get("text", "") for h in bm_hits]
    bm_model = SimpleBM25(docs)
    bm_norm = _norm01([bm_model.score(query, d) for d in docs])
    # BM25 通道召回、但向量没召回的块，向量分记 0 → 融合分主要靠 BM25
    vec_norm = _norm01([
        h.get("distance", 0.0) if h.get("entity", {}).get("text", "") in vec_by_text else 0.0
        for h in bm_hits
    ])
    fused = []
    for i, h in enumerate(bm_hits):
        fused.append({
            **h,
            "distance": HYBRID_VECTOR_WEIGHT * vec_norm[i] + HYBRID_BM25_WEIGHT * bm_norm[i],
        })
    fused.sort(key=lambda x: x["distance"], reverse=True)
    return fused
