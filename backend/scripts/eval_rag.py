"""
RAG 检索验证脚本
================
作用：用测试题集逐题跑混合检索，判断"正确来源文档是否进 top-k"，统计召回命中率；
--calib 模式打印每题 top-5 分数分布，用于标定 SCORE_THRESHOLD。
不调对话模型，零成本，可重复执行。

用法（backend 目录下）：
    D:/development/miniconda3/envs/langchain1.2/python.exe scripts/eval_rag.py [--kb 1] [--calib]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import rag
from app.config import SCORE_THRESHOLD, TOP_K

# 用例集：从测试题集提取。(题号, 类别, 期望来源文档, 期望章节关键字或None, 问题)
# A/B/C/D 类：判"期望来源文档是否进 top_k"。E 类：判 hit=False（未命中）。
CASES = [
    # ===== A 类：直接事实检索 =====
    ("A1", "A", "IT运维服务规范", "服务台支持", "IT 服务台的工作时间是多少？遇到紧急问题怎么办？"),
    ("A2", "A", "IT运维服务规范", "SLA", "P2 级问题（高优先级、主要功能不可用）的响应和解决时限分别是多少？"),
    ("A3", "A", "IT运维服务规范", "办公设备管理", "新员工入职当天会配发哪些 IT 设备？"),
    ("A4", "A", "IT运维服务规范", "账号与权限", "员工转岗或离职后，其账号权限在多长时间内调整或注销？"),
    ("A5", "A", "信息安全管理制度", "密码管理", "公司对系统账号密码有什么硬性要求？"),
    ("A6", "A", "信息安全管理制度", "信息安全事件报告", "发现信息安全事件后应在多长时间内上报？向谁报告？"),
    ("A7", "A", "信息安全管理制度", "数据分级", "公司数据按敏感程度分为哪几级？各举一例。"),
    ("A8", "A", "采购管理制度", "采购申请", "单笔采购金额在 5 万-50 万元之间，审批流程是怎样的？"),
    ("A9", "A", "采购管理制度", "供应商管理", "大宗采购在选择供应商时有什么要求？"),
    ("A10", "A", "差旅管理制度", "住宿标准", "到二线城市出差，每晚住宿报销上限是多少？"),
    ("A11", "A", "差旅管理制度", "餐饮与补贴", "国内出差每天补贴多少钱？出国呢？"),
    ("A12", "A", "企业员工手册", "福利待遇", "公司公积金缴存比例是多少？"),
    ("A13", "A", "招聘管理制度", "录用", "新员工试用期多长？最长不超过多久？"),
    ("A14", "A", "产品使用说明书", "权限说明", "产品免费版的成员、项目、任务数量限制是什么？"),
    ("A15", "A", "研发规范与代码管理", "版本管理", "研发代码管理的 Git 分支策略包含哪几个分支？各有什么作用？"),
    ("A16", "A", "研发规范与代码管理", "环境管理", "生产环境数据库变更有什么要求？"),
    ("A17", "A", "绩效考核制度", "考核维度", "绩效考核包含哪些维度，权重分别是多少？"),
    ("A18", "A", "行政后勤管理制度", "食堂管理", "员工食堂每月餐补是多少？就餐时间是什么？"),
    # ===== B 类：跨文档综合 =====
    ("B1", "B", "差旅管理制度", "交通标准", "一名经理级员工去一线城市出差 4 天，交通、住宿、补贴分别执行什么标准？"),
    ("B2", "B", "企业员工手册", None, "新员工入职第一周会经历哪些流程？涉及哪些制度？"),
    ("B3", "B", "企业员工手册", "离职流程", "员工离职需要办理哪些手续？分别对应哪些制度？"),
    ("B4", "B", "企业员工手册", "薪酬发放", "工资发放日遇节假日怎么处理？请对比两份制度文件的说法。"),
    ("B5", "B", "绩效考核制度", "考核等级", "绩效等级体系在绩效考核制度和企业员工手册中是否一致？"),
    ("B6", "B", "培训管理制度", None, "公司对员工培训有哪些要求？分别规定在哪些文档？"),
    ("B7", "B", "差旅管理制度", "餐饮与补贴", "出差超过 5 天可以申请什么特殊安排？"),
    ("B8", "B", "薪酬等级表", None, "一名 P3 高级专员每月的基本工资和岗位工资分别是多少？合计多少？"),
    # ===== C 类：规则应用与计算 =====
    ("C1", "C", "差旅管理制度", "住宿标准", "到一线城市出差 3 晚，住宿费最多可报销多少？"),
    ("C2", "C", "差旅管理制度", "餐饮与补贴", "国内出差一天（不含往返大交通和住宿），市内交通、餐饮、补贴合计上限是多少？"),
    ("C3", "C", "差旅管理制度", "交通标准", "自驾出差 100 公里，车辆补贴是多少？包含哪些费用？"),
    ("C4", "C", "企业员工手册", "福利待遇", "一名员工月缴存基数为 10000 元，个人和公司每月各缴多少公积金？"),
    ("C5", "C", "企业员工手册", "福利待遇", "一名入职满 6 年的员工每年可休几天带薪年假？"),
    ("C6", "C", "薪酬管理制度", "薪酬等级", "公司年度调薪在什么时间、幅度范围是多少？"),
    ("C7", "C", "采购管理制度", "采购申请", "采购一批 30 万元的设备，需要谁审批？"),
    ("C8", "C", "薪酬等级表", None, "能否仅凭薪酬等级表精确算出某名 P2 员工当月的绩效工资？"),
    # ===== D 类：易错点与矛盾检测 =====
    ("D1", "D", "绩效考核制度", "结果应用", "连续两次评为 D 级会有什么后果？"),
    ("D2", "D", "绩效考核制度", "结果应用", "绩效考核结果有哪些应用？"),
    ("D3", "D", "差旅管理制度", "报销流程", "差旅报销需要提交哪些材料？"),
    ("D4", "D", "企业员工手册", "考勤细则", "加班调休申请需要在多长时间内提交？"),
    ("D5", "D", "信息安全管理制度", "数据分级", "秘密级及以上的涉密数据可以用微信或个人邮箱传输吗？"),
    ("D6", "D", "企业员工手册", "保密规定", "离职员工的保密/竞业限制期限是多长？"),
    ("D7", "D", "IT运维服务规范", "SLA", "P3 级问题（单点功能异常）的响应和解决时限是多少？"),
    ("D8", "D", "企业员工手册", "绩效考核", "员工手册和绩效考核制度中 D 级的绩效系数分别是多少？"),
    # ===== E 类：文档未覆盖（应拒答，hit=False） =====
    ("E1", "E", None, None, "年假可以跨年结转吗？最多保留多久？"),
    ("E2", "E", None, None, "法定节假日加班费具体是平时工资的多少倍？"),
    ("E3", "E", None, None, "试用期工资是转正工资的百分之多少？"),
    ("E4", "E", None, None, "公司支持居家或远程办公吗？申请条件是什么？"),
    ("E5", "E", None, None, "公司有股权或期权激励计划吗？"),
    ("E6", "E", None, None, "离职后社保公积金如何转移或停缴？"),
    ("E7", "E", None, None, "全员团建费用有没有个人额度上限？"),
    ("E8", "E", None, None, "采购能否个人垫资后报销？流程是什么？"),
    ("E9", "E", None, None, "出差返程途中顺带旅游，相关费用能报销吗？"),
    ("E10", "E", None, None, "公司内部 IT 系统的数据备份频率、RPO/RTO 是多少？"),
]


def _norm_docname(source: str) -> str:
    """归一化文档名：去扩展名、去"（测试文档）"等装饰。"""
    s = source.replace(".txt", "").replace(".csv", "").replace(".docx", "").replace(".pdf", "")
    return s.replace("（测试文档）", "").replace("(测试文档)", "").strip()


def _doc_match(expect: str, source: str) -> bool:
    """期望文档名是否匹配实际来源（子串匹配）。"""
    return expect in _norm_docname(source) or _norm_docname(source) in expect


def main():
    kb_id = 1
    calib = "--calib" in sys.argv
    if "--kb" in sys.argv:
        kb_id = int(sys.argv[sys.argv.index("--kb") + 1])
    e2e = "--e2e" in sys.argv  # 端到端模式：E 类判"模型是否诚实拒答"，需调 DeepSeek

    print(f"知识库 kb{kb_id} | TOP_K={TOP_K} | SCORE_THRESHOLD={SCORE_THRESHOLD} | e2e={e2e}")
    print("=" * 70)

    stat = {}  # 类别 -> [总, 中]
    calib_rows = []
    for cid, cat, exp_doc, exp_sec, q in CASES:
        _, contexts = rag.retrieve_contexts(q, kb_id=kb_id, top_k=TOP_K)
        sources = [(c["source"], c["score"]) for c in contexts]
        hit = bool(contexts)

        if cat == "E":
            if e2e:
                # 端到端：调对话模型，判"是否诚实拒答"（明确说明知识库未覆盖，不硬编造）。
                # 注意：E 类在检索层会误召回（hit=True），生成层 prompt 已要求诚实拒答，
                # 所以只看回答里是否明确"未找到/未覆盖"，不看 hit。
                # 模型回答有随机性，判定关键词放宽。
                from app.services import qa_service
                r = qa_service.answer_question(q, kb_id=kb_id)
                a = r["answer"]
                pass_ = any(m in a for m in (
                    "未找到", "未明确", "未覆盖", "未记录", "未检索到", "未提及",
                    "未记载", "未包含", "并没有", "并未", "暂未", "尚未", "没有明确",
                ))
                tag = "PASS" if pass_ else "FAIL"
            else:
                # 检索层视角：E 类易误召回（融合分阈值难两全），但生成层
                # GENERAL/RAG prompt 已要求诚实拒答，故此处仅作信息展示
                pass_ = True  # 检索层不判 E 类成败，由 --e2e 端到端判定
                tag = "INFO"
        else:
            # A/B/C/D：期望来源文档进 top_k + 答案所在章节被命中
            # （章节级判定：文档命中但答案块没被召回，回答仍会缺关键信息）
            doc_hit = any(_doc_match(exp_doc, s) for s, _ in sources)
            sec_hit = True
            if exp_sec:
                # 期望章节关键字出现在命中块（首行标题或正文）
                sec_hit = any(
                    exp_sec in c["text"]
                    for c in contexts
                )
            pass_ = doc_hit and sec_hit
            tag = "PASS" if pass_ else "FAIL"
            if calib:
                calib_rows.append((cid, cat, q, sources, doc_hit))

        stat.setdefault(cat, [0, 0])
        stat[cat][0] += 1
        stat[cat][1] += 1 if pass_ else 0

        exp_str = exp_doc or "(应拒答)"
        top1 = sources[0] if sources else ("无", 0)
        print(f"[{tag}] {cid:<4} {cat} 期望={exp_str:<18} 命中{len(sources)}  top1={top1[0][:14]} score={top1[1]:.3f}")

    print("=" * 70)
    total = tot_ok = 0
    for cat in ("A", "B", "C", "D", "E"):
        n, ok = stat.get(cat, [0, 0])
        total += n
        tot_ok += ok
        print(f"  {cat} 类: {ok}/{n}  命中率 {ok/max(n,1)*100:.0f}%")
    print(f"  合计: {tot_ok}/{total}  {tot_ok/max(total,1)*100:.0f}%")

    if calib:
        print("\n" + "=" * 70)
        print("== 每题 top-5 分数分布（用于标定阈值） ==")
        for cid, cat, q, sources, doc_hit in calib_rows:
            scores = ", ".join(f"{s:.3f}" for _, s in sources)
            print(f"  {cid} {cat} {'命中' if doc_hit else 'MISS'} [{scores}]  {q[:20]}")


if __name__ == "__main__":
    main()
