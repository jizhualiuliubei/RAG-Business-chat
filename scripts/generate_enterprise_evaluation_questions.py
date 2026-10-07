"""Generate 360 enterprise-scale RAG evaluation questions.

The output files are intended for manual review and later import into the
evaluation system. Questions are generated from the stable clause IDs in
``generate_enterprise_scale_docs.py`` so expected clauses stay traceable to the
synthetic enterprise corpus.
"""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

try:
    from generate_enterprise_scale_docs import DOCS, EnterpriseDoc, Section
except ModuleNotFoundError:  # pragma: no cover - used when imported as scripts.*
    from scripts.generate_enterprise_scale_docs import DOCS, EnterpriseDoc, Section

ROOT = Path(__file__).resolve().parents[1]
EVALUATION_DIR = ROOT / "evaluation" / "enterprise_scale"
CSV_OUT = EVALUATION_DIR / "企业规模知识库评测题集.csv"
MD_OUT = EVALUATION_DIR / "企业规模知识库评测题集.md"

FIELDS = [
    "case_id",
    "category",
    "difficulty",
    "question",
    "expected_doc",
    "expected_section",
    "expected_clause",
    "expected_keywords",
    "standard_answer",
    "answer_points",
    "required_citations",
    "evaluation_focus",
    "negative_case",
]

CATEGORY_LABELS = {
    "A": "A 单文档事实检索",
    "B": "B 单文档细节定位与条款解释",
    "C": "C 跨文档关联推理",
    "D": "D 场景应用与规则计算",
    "E": "E 边界条件与易错判断",
    "F": "F 文档未覆盖与幻觉测试",
}

CATEGORY_TARGETS = {
    "A": 96,
    "B": 60,
    "C": 60,
    "D": 72,
    "E": 48,
    "F": 24,
}

PER_DOC_CATEGORY_COUNTS = {
    "A": 8,
    "B": 5,
    "C": 5,
    "D": 6,
    "E": 4,
    "F": 2,
}

DIFFICULTY_BY_CATEGORY = {
    "A": "基础",
    "B": "中等",
    "C": "困难",
    "D": "困难",
    "E": "中等",
    "F": "中等",
}


@dataclass(frozen=True)
class ClauseRef:
    doc: EnterpriseDoc
    section: Section
    clause_id: str
    clause_text: str

    @property
    def doc_file(self) -> str:
        return f"{self.doc.name}{self.doc.suffix}"


def _clause_refs(doc: EnterpriseDoc) -> list[ClauseRef]:
    refs: list[ClauseRef] = []
    for section in doc.sections:
        for clause in section.clauses:
            clause_id, clause_text = clause.split(" ", 1)
            refs.append(ClauseRef(doc, section, clause_id, clause_text))
    return refs


def _all_refs() -> list[ClauseRef]:
    refs: list[ClauseRef] = []
    for doc in DOCS:
        refs.extend(_clause_refs(doc))
    return refs


def _refs_by_clause() -> dict[str, ClauseRef]:
    return {ref.clause_id: ref for ref in _all_refs()}


def _keywords(ref: ClauseRef, *extra: str) -> str:
    tokens = [ref.doc.name, ref.section.title, ref.clause_id]
    for text in (ref.clause_text, *extra):
        for marker in ("天", "小时", "万元", "分钟", "审批", "复核", "保留", "禁止", "必须", "不得", "上限", "负责人"):
            if marker in text:
                tokens.append(marker)
        for part in text.replace("，", " ").replace("。", " ").replace("、", " ").split():
            if 2 <= len(part) <= 12 and len(tokens) < 10:
                tokens.append(part)
    return "；".join(dict.fromkeys(tokens))


def _answer_points(*points: str) -> str:
    clean = [point.strip("；; ") for point in points if point and point.strip()]
    if len(clean) < 2:
        raise ValueError("answer_points must contain at least 2 points")
    return "；".join(dict.fromkeys(clean))


def _required_citations(*refs: ClauseRef) -> str:
    return "；".join(f"{ref.doc_file}#{ref.section.title}#{ref.clause_id}" for ref in refs)


def _case_id(doc_index: int, category: str, seq: int) -> str:
    return f"ES-{doc_index:02d}-{category}-{seq:03d}"


def _row(
    *,
    doc_index: int,
    category: str,
    seq: int,
    question: str,
    expected_ref: ClauseRef | None,
    standard_answer: str,
    answer_points: str,
    required_citations: str,
    evaluation_focus: str,
    negative_case: bool = False,
    expected_doc_override: str = "",
) -> dict[str, str]:
    return {
        "case_id": _case_id(doc_index, category, seq),
        "category": CATEGORY_LABELS[category],
        "difficulty": DIFFICULTY_BY_CATEGORY[category],
        "question": question,
        "expected_doc": expected_ref.doc_file if expected_ref else expected_doc_override,
        "expected_section": expected_ref.section.title if expected_ref else "",
        "expected_clause": expected_ref.clause_id if expected_ref else "",
        "expected_keywords": _keywords(expected_ref) if expected_ref else "未覆盖；拒答；无依据；不要编造",
        "standard_answer": standard_answer,
        "answer_points": answer_points,
        "required_citations": required_citations,
        "evaluation_focus": evaluation_focus,
        "negative_case": "true" if negative_case else "false",
    }


def _make_a(doc_index: int, refs: list[ClauseRef], start_seq: int) -> list[dict[str, str]]:
    templates = [
        "根据《{doc}》，{section}中的{clause_id}具体规定是什么？",
        "《{doc}》里关于{section}的{clause_id}条款要求如何执行？",
        "请直接检索《{doc}》中{clause_id}的原始制度要求。",
        "在{doc}的{section}章节，{clause_id}说明了哪项管理要求？",
    ]
    rows = []
    for idx in range(PER_DOC_CATEGORY_COUNTS["A"]):
        ref = refs[idx % len(refs)]
        question = templates[idx % len(templates)].format(
            doc=ref.doc.name,
            section=ref.section.title,
            clause_id=ref.clause_id,
        )
        rows.append(
            _row(
                doc_index=doc_index,
                category="A",
                seq=start_seq + idx,
                question=question,
                expected_ref=ref,
                standard_answer=f"{ref.clause_id}规定：{ref.clause_text}",
                answer_points=_answer_points(ref.clause_id, ref.clause_text),
                required_citations=_required_citations(ref),
                evaluation_focus="检验Top-K能否命中目标文档、目标章节和精确条款编号。",
            )
        )
    return rows


def _make_b(doc_index: int, refs: list[ClauseRef], start_seq: int) -> list[dict[str, str]]:
    templates = [
        "请解释《{doc}》{section}中{clause_id}的执行含义，并说明需要关注的证据。",
        "如果员工只记得{section}相关规则，系统应如何定位并解释{clause_id}？",
        "请把《{doc}》{clause_id}转成可执行检查点，哪些信息必须被回答出来？",
        "{clause_id}容易被误读，请说明它的适用对象、处理动作和留痕要求。",
        "围绕《{doc}》{section}，请说明{clause_id}与普通口头说明相比为什么需要引用原文。",
    ]
    rows = []
    offset = 2
    for idx in range(PER_DOC_CATEGORY_COUNTS["B"]):
        ref = refs[(idx + offset) % len(refs)]
        rows.append(
            _row(
                doc_index=doc_index,
                category="B",
                seq=start_seq + idx,
                question=templates[idx].format(
                    doc=ref.doc.name,
                    section=ref.section.title,
                    clause_id=ref.clause_id,
                ),
                expected_ref=ref,
                standard_answer=(
                    f"{ref.clause_id}属于《{ref.doc.name}》{ref.section.title}章节，核心要求是："
                    f"{ref.clause_text}回答时应说明适用场景，并引用该条款作为依据。"
                ),
                answer_points=_answer_points(ref.clause_id, ref.section.title, ref.clause_text),
                required_citations=_required_citations(ref),
                evaluation_focus="检验章节定位、条款解释、关键词覆盖和引用支撑是否一致。",
            )
        )
    return rows


CROSS_LINKS = {
    "员工手册": [
        ("HR-06-002", "PAY-07-001", "工资发放日遇节假日时，员工手册和薪酬绩效制度应如何共同引用？"),
        ("HR-08-003", "IT-04-002", "员工离职时，交接、资产归还和账号关闭应如何形成闭环？"),
        ("HR-01-001", "SEC-02-001", "新员工入职涉及客户资料接触前，身份核验和数据分级规则应如何组合？"),
        ("HR-08-006", "ADM-06-002", "离职交接清单和固定资产调拨登记应如何共同完成？"),
        ("HR-06-005", "PAY-06-002", "员工提出薪酬或绩效异议时，直属负责人确认与绩效申诉流程如何衔接？"),
    ],
    "信息安全管理制度": [
        ("SEC-06-003", "PUR-02-006", "涉及客户数据的供应商远程访问生产系统时，需要同时满足哪些安全和采购准入要求？"),
        ("SEC-07-002", "CS-03-008", "客户数据泄露且存在合同赔付风险时，应如何同步安全、法务和客户经理？"),
        ("SEC-04-009", "RD-06-004", "研发终端访问生产堡垒机并执行数据库变更时，需要哪些复核？"),
        ("SEC-02-006", "CON-01-006", "合同涉及个人信息处理且需要外发秘密级数据时，应如何组合审批？"),
        ("SEC-03-002", "IT-06-002", "管理员账号权限和服务器资产负责人登记应如何共同控制生产权限？"),
    ],
    "IT运维与服务SLA": [
        ("IT-03-001", "CS-02-001", "客户侧 S1 故障升级为 P1 生产事故时，服务响应和事故响应如何对齐？"),
        ("IT-04-001", "RD-05-001", "生产变更和正式发布窗口都涉及周二周四晚间时，应如何安排窗口？"),
        ("IT-05-003", "RD-06-009", "核心数据库备份和数据库回滚脚本演练如何共同支撑发布安全？"),
        ("IT-04-002", "HR-08-003", "员工离职后账号关闭与工作交接、资产归还如何联动？"),
        ("IT-06-008", "FIN-06-003", "释放闲置云资源后，与付款电子档案长期保留之间如何区分处理？"),
    ],
    "采购与供应商管理制度": [
        ("PUR-02-006", "SEC-06-003", "供应商涉及客户数据并需要远程访问生产系统时，采购和安全要求如何同时满足？"),
        ("PUR-03-004", "FIN-04-003", "超过20万元采购且预算外付款时，需要哪些审批角色？"),
        ("PUR-05-007", "FIN-05-002", "采购验收记录和付款材料应如何互相支撑付款申请？"),
        ("PUR-06-002", "CON-01-006", "软件订阅采购涉及数据安全条款和个人信息处理合同时，应如何审查？"),
        ("PUR-08-006", "CON-06-002", "重大违约供应商再次申请用印合同时，应先核对哪些审批和供应商限制？"),
    ],
    "差旅与费用报销制度": [
        ("TRV-03-006", "FIN-03-006", "超标准住宿且预算余额不足时，能否直接提交报销？应如何处理？"),
        ("TRV-06-003", "CS-02-001", "客户紧急现场支持出差和 S1 故障响应应如何并行推进？"),
        ("TRV-05-002", "FIN-05-002", "差旅报销材料和付款材料在证据完整性上有哪些共同要求？"),
        ("TRV-05-006", "FIN-06-007", "电子发票查重与审计抽样缺失材料补齐如何衔接？"),
        ("TRV-06-009", "SEC-02-006", "境外差旅可能携带秘密级客户资料外发时，需要哪些额外控制？"),
    ],
    "薪酬绩效制度": [
        ("PAY-07-001", "HR-06-002", "工资日遇节假日时，薪酬制度和员工手册的口径是否一致？"),
        ("PAY-03-006", "HR-06-005", "绩效面谈确认记录与薪酬异议事实确认应如何关联？"),
        ("PAY-06-002", "TRN-05-002", "年度绩效申诉和晋升材料中的近两期绩效应如何互相影响？"),
        ("PAY-05-004", "FIN-01-002", "P3员工绩效系数影响奖金预算时，应如何关联年度预算维度？"),
        ("PAY-07-004", "FIN-05-002", "专项奖金单独列示后，付款材料还需要哪些凭证支撑？"),
    ],
    "培训与晋升制度": [
        ("TRN-02-003", "SEC-03-002", "管理员未完成信息安全必修课时，权限申请是否应被放行？"),
        ("TRN-04-006", "TRV-05-002", "外出参会产生差旅费用时，培训预算审批和差旅报销材料如何分工？"),
        ("TRN-05-002", "PAY-03-002", "晋升材料中的绩效信息与年度绩效用途如何对应？"),
        ("TRN-04-005", "FIN-03-001", "外部培训费用付款前，需要如何校验预算、合同、验收和发票？"),
        ("TRN-06-007", "PAY-06-006", "导师评价作为晋升输入时，为什么不能直接替代绩效复核？"),
    ],
    "研发规范与发布流程": [
        ("RD-05-001", "IT-04-001", "正式发布和生产变更窗口都在周二周四晚间时，应如何统一排期？"),
        ("RD-05-006", "CS-03-004", "S1客户故障触发 hotfix 时，研发和客户支持应各自满足什么要求？"),
        ("RD-06-004", "SEC-04-009", "生产数据库变更前，DBA审核和研发终端基线检查如何同时满足？"),
        ("RD-01-002", "CON-07-002", "需求回滚影响和合同盖章后变更都涉及变更风险时，应如何留痕？"),
        ("RD-04-006", "IT-03-001", "S1/S2缺陷未关闭且存在P1事故时，是否可以进入正式发布？"),
    ],
    "行政后勤制度": [
        ("ADM-02-007", "SEC-04-009", "访客进入研发区且接触生产堡垒机终端时，应如何控制陪同和基线检查？"),
        ("ADM-03-006", "SEC-02-006", "办公区拍摄涉及秘密级客户资料时，部门同意和信息安全复核如何同时满足？"),
        ("ADM-06-002", "HR-08-003", "员工离职资产归还后，固定资产调拨登记应如何更新？"),
        ("ADM-04-005", "PUR-08-002", "食堂食品安全投诉后，供应商年度评分应如何处理？"),
        ("ADM-05-008", "CON-06-002", "董事会会议涉及合同用印材料时，会议室排期和用印前确认如何衔接？"),
    ],
    "财务付款与预算制度": [
        ("FIN-03-001", "PUR-05-007", "预算内付款前，合同、验收、发票一致性和采购验收记录如何互相校验？"),
        ("FIN-04-003", "CON-04-008", "预算外付款超过或等于15万元且合同超过200万元时，需要哪些审批？"),
        ("FIN-05-002", "CON-06-002", "付款材料和合同用印材料都要求附件齐备时，如何避免缺件？"),
        ("FIN-06-007", "TRV-05-006", "审计抽样发现电子发票材料异常时，应如何补齐和查重？"),
        ("FIN-01-006", "PAY-01-006", "年度预算调整和调薪窗口都影响人力成本时，应如何解释依据？"),
    ],
    "合同审批制度": [
        ("CON-01-006", "SEC-02-006", "合同涉及个人信息处理且需外发秘密级数据时，应如何满足法务和安全要求？"),
        ("CON-04-003", "PUR-03-004", "标准合同80万元同时对应采购金额超过20万元时，应走哪些审批？"),
        ("CON-06-002", "FIN-05-002", "用印前附件齐备和付款材料完整性如何互相支撑？"),
        ("CON-07-006", "CS-03-008", "已寄出合同作废且存在客户赔付风险时，应同步哪些角色？"),
        ("CON-03-006", "RD-01-007", "非标合同评审和跨部门需求决策都涉及重大项目时，应如何指定责任人？"),
    ],
    "客户支持与售后制度": [
        ("CS-02-001", "IT-03-001", "客户 S1 故障达到 P1 生产事故标准时，首次响应和故障群要求如何对齐？"),
        ("CS-03-004", "RD-05-006", "S1客户故障超过4小时并需要 hotfix 时，升级和发布要求如何组合？"),
        ("CS-04-002", "SEC-07-006", "重大故障关闭后，客户回访和安全事件复盘应分别覆盖什么？"),
        ("CS-05-001", "RD-01-002", "高频问题沉淀知识库文章时，为什么要关联产品版本和验收边界？"),
        ("CS-06-003", "FIN-06-003", "月度服务报告和付款电子档案在长期留存上应如何区分？"),
    ],
}


def _make_c(doc_index: int, refs: list[ClauseRef], start_seq: int) -> list[dict[str, str]]:
    rows = []
    refs_by_clause = _refs_by_clause()
    links = CROSS_LINKS[refs[0].doc.name]
    for idx, (primary_clause, secondary_clause, question) in enumerate(links):
        ref = refs_by_clause[primary_clause]
        other = refs_by_clause[secondary_clause]
        rows.append(
            _row(
                doc_index=doc_index,
                category="C",
                seq=start_seq + idx,
                question=question,
                expected_ref=ref,
                standard_answer=(
                    f"主证据应引用《{ref.doc.name}》{ref.section.title}{ref.clause_id}：{ref.clause_text}"
                    f"辅助证据应引用《{other.doc.name}》{other.section.title}{other.clause_id}：{other.clause_text}"
                    "回答需要先说明主制度要求，再补充跨部门或跨流程约束。"
                ),
                answer_points=_answer_points(
                    f"主证据{ref.clause_id}",
                    f"辅助证据{other.clause_id}",
                    "需要同时说明处理顺序和引用依据",
                ),
                required_citations=_required_citations(ref, other),
                evaluation_focus="检验跨文档召回、证据组合、主辅来源区分和Agent多步检索能力。",
            )
        )
    return rows


def _make_d(doc_index: int, refs: list[ClauseRef], start_seq: int) -> list[dict[str, str]]:
    rows = []
    templates = [
        "场景：业务方已经触发{clause_id}描述的条件，但希望先执行后补材料。按制度应如何处理？",
        "场景：审批人只同意口头放行，事项涉及《{doc}》{section}的{clause_id}。系统应怎么回答？",
        "场景：申请人提交的信息不完整，但声称事项紧急。请依据{clause_id}给出处理建议。",
        "场景：审计要求复盘{section}事项。根据{clause_id}，应检查哪些关键要素？",
        "场景：用户问能否跳过{section}中的制度动作。请依据{clause_id}判断并说明原因。",
        "场景：同一事项被多次退回，负责人要求说明制度依据。请用{clause_id}给出可执行结论。",
    ]
    for idx in range(PER_DOC_CATEGORY_COUNTS["D"]):
        ref = refs[(idx + 1) % len(refs)]
        rows.append(
            _row(
                doc_index=doc_index,
                category="D",
                seq=start_seq + idx,
                question=templates[idx].format(
                    doc=ref.doc.name,
                    section=ref.section.title,
                    clause_id=ref.clause_id,
                ),
                expected_ref=ref,
                standard_answer=(
                    f"应按{ref.clause_id}执行：{ref.clause_text}"
                    "不能只给泛化建议；若存在例外，也必须说明审批、复核或留痕要求。"
                ),
                answer_points=_answer_points(
                    ref.clause_id,
                    ref.clause_text,
                    "给出明确可执行结论",
                    "说明审批或留痕要求",
                ),
                required_citations=_required_citations(ref),
                evaluation_focus="检验场景理解、规则应用、边界处理和答案可执行性。",
            )
        )
    return rows


def _make_e(doc_index: int, refs: list[ClauseRef], start_seq: int) -> list[dict[str, str]]:
    rows = []
    templates = [
        "{clause_id}有没有可以随意豁免的空间？请说明边界条件。",
        "如果用户把《{doc}》{section}的要求理解成“只要口头同意即可”，是否正确？",
        "当{section}事项同时存在紧急性和合规要求时，{clause_id}的底线是什么？",
        "请判断：只要最终结果正确，就可以不保留{clause_id}相关证据。这个说法对吗？",
    ]
    for idx in range(PER_DOC_CATEGORY_COUNTS["E"]):
        ref = refs[(idx + 6) % len(refs)]
        rows.append(
            _row(
                doc_index=doc_index,
                category="E",
                seq=start_seq + idx,
                question=templates[idx].format(
                    doc=ref.doc.name,
                    section=ref.section.title,
                    clause_id=ref.clause_id,
                ),
                expected_ref=ref,
                standard_answer=(
                    f"不能扩大解释或随意豁免。{ref.clause_id}的依据是：{ref.clause_text}"
                    "回答应明确边界条件，避免把审批、复核、禁止或时限要求弱化成建议。"
                ),
                answer_points=_answer_points(
                    ref.clause_id,
                    "不得随意豁免或弱化制度",
                    ref.clause_text,
                ),
                required_citations=_required_citations(ref),
                evaluation_focus="检验易错判断、边界识别、反向问题回答和引用是否支撑结论。",
            )
        )
    return rows


UNCOVERED_TOPICS = [
    ("员工股票期权行权价格是否固定为每股1元？", "知识库没有提供股票期权行权价格或股权激励细则。"),
    ("公司是否承诺为所有员工提供永久远程办公资格？", "知识库没有提供永久远程办公承诺或对应资格规则。"),
    ("是否规定供应商必须购买指定品牌的办公设备？", "知识库没有规定供应商必须采购指定品牌设备。"),
    ("客户投诉是否一定可以获得固定金额赔付？", "知识库没有给出固定赔付金额或自动赔付承诺。"),
    ("公司是否提供员工子女入学名额或学费报销？", "知识库没有提供员工子女入学或学费报销政策。"),
    ("是否允许部门自行发行虚拟积分替代工资奖金？", "知识库没有提供虚拟积分替代工资奖金的政策依据。"),
    ("公司是否规定所有合同都必须使用英文版本？", "知识库没有要求所有合同必须使用英文版本。"),
    ("是否存在海外办公室租赁补贴的统一金额标准？", "知识库没有提供海外办公室租赁补贴金额标准。"),
]


def _make_f(doc_index: int, doc: EnterpriseDoc, start_seq: int) -> list[dict[str, str]]:
    rows = []
    for idx in range(PER_DOC_CATEGORY_COUNTS["F"]):
        topic, answer = UNCOVERED_TOPICS[(doc_index * 2 + idx) % len(UNCOVERED_TOPICS)]
        rows.append(
            _row(
                doc_index=doc_index,
                category="F",
                seq=start_seq + idx,
                question=f"在《{doc.name}》相关知识库中，{topic}",
                expected_ref=None,
                expected_doc_override=f"{doc.name}{doc.suffix}",
                standard_answer=f"现有知识库未提供依据，不能编造结论。{answer}",
                answer_points=_answer_points("明确拒答或说明无依据", answer),
                required_citations="",
                evaluation_focus="检验未覆盖问题拒答、无依据数字识别和幻觉控制。",
                negative_case=True,
            )
        )
    return rows


def build_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for doc_index, doc in enumerate(DOCS, start=1):
        refs = _clause_refs(doc)
        rows.extend(_make_a(doc_index, refs, 1))
        rows.extend(_make_b(doc_index, refs, 1))
        rows.extend(_make_c(doc_index, refs, 1))
        rows.extend(_make_d(doc_index, refs, 1))
        rows.extend(_make_e(doc_index, refs, 1))
        rows.extend(_make_f(doc_index, doc, 1))
    validate_rows(rows)
    return rows


def validate_rows(rows: list[dict[str, str]]) -> None:
    if len(rows) != 360:
        raise AssertionError(f"expected 360 rows, got {len(rows)}")
    ids = [row["case_id"] for row in rows]
    questions = [row["question"] for row in rows]
    if len(ids) != len(set(ids)):
        raise AssertionError("duplicate case_id detected")
    if len(questions) != len(set(questions)):
        raise AssertionError("duplicate question detected")
    by_category = Counter(row["category"].split(" ", 1)[0] for row in rows)
    if dict(by_category) != CATEGORY_TARGETS:
        raise AssertionError(f"category counts mismatch: {by_category}")
    by_doc = Counter(row["expected_doc"] for row in rows)
    expected_docs = {f"{doc.name}{doc.suffix}" for doc in DOCS}
    if set(by_doc) != expected_docs:
        raise AssertionError(f"document coverage mismatch: {set(by_doc) ^ expected_docs}")
    if any(count != 30 for count in by_doc.values()):
        raise AssertionError(f"each document must have 30 cases: {by_doc}")
    for row in rows:
        points = [p for p in row["answer_points"].split("；") if p.strip()]
        if len(points) < 2:
            raise AssertionError(f"{row['case_id']} has fewer than 2 answer points")
        category = row["category"].split(" ", 1)[0]
        if category in {"C", "D", "E"} and len(points) < 3:
            raise AssertionError(f"{row['case_id']} complex case has fewer than 3 answer points")
        if category == "F":
            if row["expected_section"] or row["expected_clause"] or row["required_citations"]:
                raise AssertionError(f"{row['case_id']} negative case should not fake expected clause")
            if row["negative_case"] != "true":
                raise AssertionError(f"{row['case_id']} should be negative")
        elif not row["expected_doc"] or not row["expected_section"] or not row["expected_clause"]:
            raise AssertionError(f"{row['case_id']} missing expected source")


def write_csv(rows: list[dict[str, str]]) -> None:
    EVALUATION_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def write_markdown(rows: list[dict[str, str]]) -> None:
    EVALUATION_DIR.mkdir(parents=True, exist_ok=True)
    by_doc: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_doc.setdefault(row["expected_doc"], []).append(row)

    lines = [
        "# 企业规模知识库评测题集",
        "",
        "本题集用于人工审题、项目展示和后续导入评测系统。第一版只定义测试问题和标准答案，不包含真实运行结果。",
        "",
        "## 评测口径",
        "",
        "- 检索侧：Top-K 文档命中、章节/条款命中、Context Recall、Context Precision。",
        "- 生成侧：Faithfulness、Answer Relevancy、答案要点覆盖、引用支撑。",
        "- 幻觉侧：未覆盖问题拒答、无依据数字或政策识别。",
        "- Agent 侧：多步骤问题拆解、跨文件证据组合、是否调用知识库检索。",
        "- 参考框架：RAGAS、DeepEval、NIST AI RMF、Stanford HELM。本题集只映射公开评测思想，不伪造结果。",
        "",
        "## 题型分布",
        "",
        "| 题型 | 数量 | 评测重点 |",
        "| --- | ---: | --- |",
    ]
    for key, target in CATEGORY_TARGETS.items():
        lines.append(f"| {CATEGORY_LABELS[key]} | {target} | {_category_focus(key)} |")
    lines.extend(["", "## 题目明细", ""])

    doc_order = [f"{doc.name}{doc.suffix}" for doc in DOCS]
    for doc_file in doc_order:
        doc_rows = by_doc[doc_file]
        lines.extend(
            [
                f"### {doc_file}",
                "",
                "| case_id | category | difficulty | question | expected_source | standard_answer | answer_points | evaluation_focus | negative_case |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in doc_rows:
            source = (
                f"{row['expected_doc']} / {row['expected_section']} / {row['expected_clause']}"
                if row["expected_clause"]
                else f"{row['expected_doc']} / 未覆盖 / 无期望条款"
            )
            lines.append(
                "| "
                + " | ".join(
                    _escape_md(value)
                    for value in [
                        row["case_id"],
                        row["category"],
                        row["difficulty"],
                        row["question"],
                        source,
                        row["standard_answer"],
                        row["answer_points"],
                        row["evaluation_focus"],
                        row["negative_case"],
                    ]
                )
                + " |"
            )
        lines.append("")
    MD_OUT.write_text("\n".join(lines), encoding="utf-8")


def _category_focus(category: str) -> str:
    return {
        "A": "直接事实定位，检查文档和条款命中。",
        "B": "细节解释，检查标准答案是否完整引用原文。",
        "C": "跨文档组合，检查主证据和辅助证据是否齐全。",
        "D": "真实业务场景，检查规则应用、审批和留痕。",
        "E": "边界和易错判断，检查是否扩大解释。",
        "F": "未覆盖拒答，检查模型是否编造政策。",
    }[category]


def main() -> None:
    rows = build_rows()
    write_csv(rows)
    write_markdown(rows)
    print(f"Generated {len(rows)} questions")
    print(f"- {MD_OUT}")
    print(f"- {CSV_OUT}")


if __name__ == "__main__":
    main()
