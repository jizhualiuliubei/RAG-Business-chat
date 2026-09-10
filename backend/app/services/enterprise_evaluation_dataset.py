"""Enterprise-scale synthetic evaluation dataset.

The source documents are controlled demo materials. Public documents may guide
their structure, but the facts below are synthetic so expected answers can be
verified exactly.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


DATASET_VERSION = "enterprise_scale_v1"
DATASET_NAME = "企业规模评测集 v1"
FULL_DATASET_VERSION = "enterprise_scale_360_v1"
FULL_DATASET_NAME = "企业规模知识库 360 题全量评测集"
ENTERPRISE_KB_NAME = "企业规模评测库"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVALUATION_DIR = PROJECT_ROOT / "evaluation" / "enterprise_scale"
FULL_DATASET_CSV = EVALUATION_DIR / "企业规模知识库评测题集.csv"
FULL_DATASET_MD = EVALUATION_DIR / "企业规模知识库评测题集.md"

CATEGORY_LABELS = {
    "A": "直接事实检索",
    "B": "跨文档综合",
    "C": "规则应用与计算",
    "D": "易错点与矛盾检测",
    "E": "未覆盖问题拒答",
}


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    category: str
    question: str
    expected_doc: str | None
    expected_section: str | None
    expected_clause: str | None
    expected_keywords: tuple[str, ...]
    answer_points: tuple[str, ...]
    difficulty: str = ""
    negative_case: bool = False
    required_citations: tuple[str, ...] = ()


CASES: list[EvaluationCase] = [
    EvaluationCase("EA1", "A", "正式员工年度体检安排在几月完成？", "员工手册", "福利与关怀", "HR-05-004", ("10月", "年度体检"), ("每年10月完成年度体检",)),
    EvaluationCase("EA2", "A", "秘密级数据外发前需要谁审批？", "信息安全管理制度", "数据分级与外发", "SEC-02-006", ("部门负责人", "信息安全负责人"), ("部门负责人审批", "信息安全负责人复核")),
    EvaluationCase("EA3", "A", "P1 生产事故的首次响应时限是多少？", "IT运维与服务SLA", "生产事故分级", "IT-03-001", ("10分钟", "P1"), ("P1事故10分钟内首次响应",)),
    EvaluationCase("EA4", "A", "战略供应商准入至少要完成几项审查？", "采购与供应商管理制度", "供应商准入", "PUR-02-003", ("资质", "财务", "安全", "合规"), ("至少完成资质、财务、安全、合规审查",)),
    EvaluationCase("EA5", "A", "一线城市普通员工住宿上限是多少？", "差旅与费用报销制度", "住宿标准", "TRV-03-002", ("480元", "一线城市"), ("一线城市普通员工住宿上限480元/晚",)),
    EvaluationCase("EA6", "A", "年度绩效申诉应在结果公示后几天内提交？", "薪酬绩效制度", "绩效申诉", "PAY-06-002", ("5个工作日", "申诉"), ("公示后5个工作日内提交申诉",)),
    EvaluationCase("EA7", "A", "外部培训超过多少钱需要签署服务协议？", "培训与晋升制度", "外部培训", "TRN-04-005", ("8000元", "服务协议"), ("外部培训超过8000元需签服务协议",)),
    EvaluationCase("EA8", "A", "生产发布窗口默认安排在什么时间？", "研发规范与发布流程", "发布窗口", "RD-05-001", ("周二", "周四", "20:00-22:00"), ("默认周二、周四20:00-22:00发布",)),
    EvaluationCase("EA9", "A", "访客临时门禁最长有效期是多少？", "行政后勤制度", "访客管理", "ADM-02-004", ("8小时", "门禁"), ("访客临时门禁最长8小时",)),
    EvaluationCase("EA10", "A", "预算外付款超过多少需要 CFO 审批？", "财务付款与预算制度", "预算外付款", "FIN-04-003", ("10万元", "CFO"), ("预算外付款超过10万元需CFO审批",)),
    EvaluationCase("EA11", "A", "标准合同法务初审时限是多少？", "合同审批制度", "合同评审时限", "CON-03-002", ("2个工作日", "法务初审"), ("标准合同法务初审2个工作日",)),
    EvaluationCase("EA12", "A", "S1 客户故障承诺多久内给出处理方案？", "客户支持与售后制度", "故障响应", "CS-02-001", ("30分钟", "处理方案"), ("S1故障30分钟内给出处理方案",)),
    EvaluationCase("EB1", "B", "员工离职时账号、资产和保密分别要按哪些制度处理？", "员工手册", "离职管理", "HR-08-003", ("24小时", "资产归还", "保密"), ("员工手册规定离职交接", "IT制度规定24小时关闭账号", "信息安全制度规定保密义务")),
    EvaluationCase("EB2", "B", "生产数据库变更同时涉及发布和数据安全时，需要哪些审批？", "研发规范与发布流程", "数据库变更", "RD-06-004", ("DBA", "信息安全", "发布经理"), ("DBA审核", "信息安全复核", "发布经理确认窗口")),
    EvaluationCase("EB3", "B", "采购软件订阅并涉及合同付款时，采购、合同、财务三边分别关注什么？", "采购与供应商管理制度", "软件订阅采购", "PUR-06-002", ("供应商", "合同", "付款"), ("采购关注供应商和比价", "合同关注条款审批", "财务关注预算和付款")),
    EvaluationCase("EB4", "B", "客户S1故障需要研发紧急发布时，售后和研发制度如何衔接？", "客户支持与售后制度", "升级处理", "CS-03-004", ("S1", "hotfix", "回滚"), ("售后升级S1", "研发走hotfix", "发布需回滚方案")),
    EvaluationCase("EB5", "B", "员工外出参会产生差旅和外部培训费用时，分别按哪两套标准？", "培训与晋升制度", "外部培训", "TRN-04-006", ("差旅", "培训预算"), ("差旅按差旅制度报销", "培训费按培训制度审批")),
    EvaluationCase("EB6", "B", "供应商远程接入生产系统时，需要采购、IT和安全做哪些控制？", "信息安全管理制度", "第三方访问", "SEC-06-003", ("供应商", "VPN", "最小权限"), ("供应商合规准入", "IT开通临时VPN", "安全要求最小权限和审计")),
    EvaluationCase("EC1", "C", "普通员工去一线城市出差3晚住宿最多报销多少？", "差旅与费用报销制度", "住宿标准", "TRV-03-002", ("480", "3", "1440"), ("480乘以3等于1440元",)),
    EvaluationCase("EC2", "C", "一笔预算内8万元采购需要哪些审批？", "采购与供应商管理制度", "采购审批", "PUR-03-002", ("8万元", "部门负责人", "采购经理"), ("8万元属于5万到20万", "需部门负责人和采购经理审批")),
    EvaluationCase("EC3", "C", "P3员工绩效为A时，绩效系数是多少？", "薪酬绩效制度", "绩效系数", "PAY-05-004", ("P3", "A", "1.15"), ("P3员工A档绩效系数1.15",)),
    EvaluationCase("EC4", "C", "外部培训12000元且员工入职未满一年，需要签几年服务协议？", "培训与晋升制度", "外部培训", "TRN-04-005", ("12000", "2年", "服务协议"), ("超过8000元需签服务协议", "未满一年参加需2年服务期")),
    EvaluationCase("EC5", "C", "预算外付款15万元走什么审批链？", "财务付款与预算制度", "预算外付款", "FIN-04-003", ("15万元", "CFO", "总经理"), ("超过10万元需CFO审批", "超过15万元含15万元需总经理会签")),
    EvaluationCase("EC6", "C", "标准合同金额80万元需要哪几级审批？", "合同审批制度", "合同金额审批", "CON-04-003", ("80万元", "业务负责人", "法务", "财务"), ("80万元需业务负责人、法务、财务审批",)),
    EvaluationCase("ED1", "D", "员工手册和薪酬绩效制度对工资日遇节假日的说法是否一致？", "员工手册", "薪酬发放", "HR-06-002", ("提前", "一致", "薪酬绩效制度"), ("员工手册和薪酬绩效制度均规定提前至最近一个工作日发放", "不存在制度冲突")),
    EvaluationCase("ED2", "D", "客户资料能否通过个人邮箱发给供应商？", "信息安全管理制度", "数据分级与外发", "SEC-02-006", ("禁止", "个人邮箱", "供应商"), ("秘密级以上禁止个人邮箱外发",)),
    EvaluationCase("ED3", "D", "P1事故是否可以无回滚方案直接发布修复？", "研发规范与发布流程", "紧急发布", "RD-05-006", ("不得", "回滚方案"), ("紧急发布也必须有回滚方案",)),
    EvaluationCase("ED4", "D", "采购验收人可以和申请人是同一个人吗？", "采购与供应商管理制度", "验收分离", "PUR-05-003", ("不得", "职责分离"), ("验收人不得与申请人为同一人",)),
    EvaluationCase("ED5", "D", "S1客户故障如果超过4小时未恢复，需要升级到谁？", "客户支持与售后制度", "升级处理", "CS-03-004", ("业务负责人", "CTO"), ("超过4小时需升级业务负责人和CTO",)),
    EvaluationCase("ED6", "D", "合同已经盖章后还能直接修改正文吗？", "合同审批制度", "用印后变更", "CON-07-002", ("不能", "变更审批"), ("盖章后修改需重新走变更审批",)),
    EvaluationCase("EE1", "E", "公司是否提供股票期权激励，行权价是多少？", None, None, None, ("未覆盖",), ("知识库未提供期权激励信息",)),
    EvaluationCase("EE2", "E", "员工子女入学补贴每年多少钱？", None, None, None, ("未覆盖",), ("知识库未提供子女入学补贴",)),
    EvaluationCase("EE3", "E", "公司是否允许永久远程办公？申请入口在哪里？", None, None, None, ("未覆盖",), ("知识库未规定永久远程办公",)),
    EvaluationCase("EE4", "E", "客户合同违约金最高可以写到合同金额的百分之多少？", None, None, None, ("未覆盖",), ("知识库未给违约金上限",)),
    EvaluationCase("EE5", "E", "供应商黑名单是否会同步到集团海外分公司？", None, None, None, ("未覆盖",), ("知识库未说明海外分公司同步机制",)),
    EvaluationCase("EE6", "E", "研发人员是否必须使用指定品牌电脑？", None, None, None, ("未覆盖",), ("知识库未规定电脑品牌",)),
]


def summarize_dataset() -> dict:
    categories = {
        key: {"label": label, "case_count": 0}
        for key, label in CATEGORY_LABELS.items()
    }
    for case in CASES:
        categories[case.category]["case_count"] += 1
    return {
        "version": DATASET_VERSION,
        "name": DATASET_NAME,
        "case_count": len(CASES),
        "categories": categories,
        "recommended_kb_name": ENTERPRISE_KB_NAME,
    }


def _category_key(raw: str) -> str:
    return (raw or "").strip()[:1]


def _split_semicolon(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split("；") if item.strip())


def load_full_cases() -> list[EvaluationCase]:
    with FULL_DATASET_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    cases: list[EvaluationCase] = []
    for row in rows:
        cases.append(
            EvaluationCase(
                case_id=row["case_id"],
                category=_category_key(row["category"]),
                question=row["question"],
                expected_doc=row.get("expected_doc") or None,
                expected_section=row.get("expected_section") or None,
                expected_clause=row.get("expected_clause") or None,
                expected_keywords=_split_semicolon(row.get("expected_keywords")),
                answer_points=_split_semicolon(row.get("answer_points")),
                difficulty=row.get("difficulty", ""),
                negative_case=row.get("negative_case", "").lower() == "true",
                required_citations=_split_semicolon(row.get("required_citations")),
            )
        )
    return cases


def summarize_full_dataset() -> dict:
    categories: dict[str, dict] = {}
    for case in load_full_cases():
        categories.setdefault(case.category, {"label": CATEGORY_LABELS_360.get(case.category, case.category), "case_count": 0})
        categories[case.category]["case_count"] += 1
    return {
        "version": FULL_DATASET_VERSION,
        "name": FULL_DATASET_NAME,
        "case_count": sum(item["case_count"] for item in categories.values()),
        "categories": categories,
        "recommended_kb_name": ENTERPRISE_KB_NAME,
        "download_formats": ["csv", "md"],
    }


def get_cases(dataset_version: str) -> list[EvaluationCase]:
    if dataset_version == FULL_DATASET_VERSION:
        return load_full_cases()
    if dataset_version == DATASET_VERSION:
        return CASES
    raise ValueError(f"未知评测集版本: {dataset_version}")


def summarize_by_version(dataset_version: str) -> dict:
    if dataset_version == FULL_DATASET_VERSION:
        return summarize_full_dataset()
    if dataset_version == DATASET_VERSION:
        return summarize_dataset()
    raise ValueError(f"未知评测集版本: {dataset_version}")


def list_datasets() -> list[dict]:
    return [summarize_dataset(), summarize_full_dataset()]


CATEGORY_LABELS_360 = {
    "A": "单文档事实检索",
    "B": "单文档细节定位与条款解释",
    "C": "跨文档关联推理",
    "D": "场景应用与规则计算",
    "E": "边界条件与易错判断",
    "F": "文档未覆盖与幻觉测试",
}
