"""
端到端 PoC Demo — 企业报销审批 Agent（CrewAI + 千问 + Milvus RAG）

运行方式：
    python demo.py              # 完整运行（含 LLM，处理全部非自动申请）
    python demo.py --no-llm     # 仅演示自动审批逻辑（无 LLM，秒级完成）
    python demo.py --max 5      # LLM 批处理限制条数（默认全部）
    python demo.py --single EXP0091   # 单条完整三阶段流程演示
    python demo.py --no-rag-init      # 跳过RAG初始化（已初始化时使用）

批次分布：
    EXP0001~0080  AUTO_APPROVED          80条  规则引擎，无LLM
    EXP0081~0090  APPROVED via pipeline  10条  经理+财务审批
    EXP0091~0095  PENDING_HUMAN_REVIEW    5条  标记人工
    EXP0096~0100  REJECTED                5条  违规退回
"""

import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.mock_applications import generate_mock_applications
from state.store import ApplicationStore
from pipeline import run_auto_approve_batch, process_manager_review, process_finance_review

W = 72

# ─── Agent 名称定义 ────────────────────────────────────────────────────────────
AGENTS = {
    0: {
        "name":  "规则引擎预审模块",
        "role":  "自动合规核查（无 LLM）",
        "tech":  "纯 Python 规则引擎，毫秒级",
    },
    1: {
        "name":  "报销材料预审助手",
        "role":  "Agent 1 — 帮助员工在提交前检查材料完整性、票据合规、超标项",
        "tech":  "CrewAI + 千问 qwen-plus + Milvus RAG",
    },
    2: {
        "name":  "部门经理初审智能体",
        "role":  "Agent 2 — 依据 RAG 规定对申请做批量初审，输出带规则引用的决定",
        "tech":  "CrewAI + 千问 qwen-plus + Milvus RAG",
    },
    3: {
        "name":  "财务审批智能体",
        "role":  "Agent 3 — 从合规性与预算角度做终审，必须引用规则编号",
        "tech":  "CrewAI + 千问 qwen-plus + Milvus RAG + 预算数据库",
    },
}


# ─── 输出工具 ─────────────────────────────────────────────────────────────────

def _icon(result: str) -> str:
    return {"APPROVED": "✅", "AUTO_APPROVED": "✅",
            "REJECTED": "❌", "PENDING_HUMAN_REVIEW": "🔍"}.get(result, "⬜")


def _phase_start(agent_id: int, queue: int):
    """每个 Agent 阶段开始时的醒目横幅。"""
    ag = AGENTS[agent_id]
    print(f"\n\n{'═'*W}")
    print(f"  {ag['name']}")
    print(f"  {ag['role']}")
    print(f"  技术栈: {ag['tech']}")
    print(f"  队列: {queue} 条  →  开始处理")
    print(f"{'═'*W}\n")


def _phase_end(agent_id: int, total: int, approved: int,
               rejected: int, flagged: int, elapsed: float,
               issues: list[dict], next_queue: str = ""):
    """每个 Agent 阶段结束的摘要框（统一格式，所有 Agent 必印）。"""
    ag = AGENTS[agent_id]
    print(f"\n{'─'*W}")
    print(f"  【{ag['name']} — 阶段结果】")
    print(f"{'─'*W}")
    print(f"  处理总计  {total:>4} 条")
    print(f"  ✅ 通过    {approved:>4} 条")
    print(f"  ❌ 拒绝    {rejected:>4} 条")
    print(f"  🔍 转人工  {flagged:>4} 条")
    print(f"  ⏱  耗时    {elapsed:.1f}s")
    if issues:
        print(f"{'─'*W}")
        print(f"  非通过明细（共 {len(issues)} 条）：")
        for item in issues:
            icon   = _icon(item["result"])
            reason = item.get("reason", "")[:48]
            rules  = ", ".join(item.get("cited_rules", []))
            tag    = f"  [{rules}]" if rules else ""
            print(f"    {icon} {item['app_id']}  {reason}{tag}")
    else:
        print(f"  （所有申请均通过，无异常）")
    print(f"{'─'*W}")
    if next_queue:
        print(f"\n  ➡️  {next_queue}")


# ─── 阶段函数 ─────────────────────────────────────────────────────────────────

def demo_init_rag():
    print(f"\n{'─'*W}")
    print(f"  RAG 初始化  |  Milvus Lite + 千问 text-embedding-v3 (1024-dim)")
    print(f"{'─'*W}")

    # 规则库：从 data/rules/*.txt 读取
    from rag.expense_rag import initialize as init_rules
    init_rules()
    print("  ✓ 规则库就绪：data/rules/*.txt → Milvus expense_rules（12条 rule_001~rule_012）")

    # 申请库：从 data/applications/*.txt 读取
    from rag.application_rag import initialize as init_apps
    init_apps()
    print("  ✓ 申请库就绪：data/applications/*.txt → Milvus expense_applications（100条）")


def demo_auto_approve(store: ApplicationStore) -> dict:
    """阶段 0 — 规则引擎（无 LLM）预筛选。"""
    _phase_start(0, len(store.all()))

    t0      = time.time()
    result  = run_auto_approve_batch(store)
    elapsed = time.time() - t0

    auto = result["auto_approved"]
    to_m = result["to_manager"]

    samples = store.get_by_status("AUTO_APPROVED")[:5]
    for s in samples:
        hist   = s["history"][-1] if s["history"] else {}
        reason = hist.get("reason", "")
        print(f"  ✅ {s['app_id']} | {s['applicant']:<4} | {s['destination']:<4} | "
              f"{s['total_amount']:>6.0f}元 | {reason[:36]}")
    if auto > 5:
        print(f"  ··· 共 {auto} 条自动通过，仅展示前 5")

    _phase_end(0, total=auto + to_m, approved=auto, rejected=0, flagged=0,
               elapsed=elapsed, issues=[],
               next_queue=f"{to_m} 条进入「报销材料预审助手（Agent 1）」队列")
    return result


def demo_prep_batch(store: ApplicationStore, max_process: int = 999) -> list[dict]:
    """阶段 1 — 报销材料预审助手：对进入经理队列的申请做材料核查。"""
    from crews.prep_crew import run_prep_check

    pending = store.get_by_status("PENDING_MANAGER")
    to_proc = pending[:max_process]
    _phase_start(1, len(to_proc))

    passed  = flagged = 0
    issues  = []
    t0      = time.time()

    for app in to_proc:
        print(f"  ⏳ {app['app_id']} | {app['applicant']:<4} | {app['department']:<4} | "
              f"{app['total_amount']:>6.0f}元 ...", end=" ", flush=True)

        prep = run_prep_check(app)
        ready = prep.get("ready_to_submit", True)
        print("✅ 材料齐全" if ready else "⚠️  材料有问题")

        if not ready:
            flagged += 1
            top_issue = (prep.get("issues") or ["（见详情）"])[0]
            print(f"       问题: {top_issue[:65]}")
            issues.append({"app_id": app["app_id"], "result": "WARN",
                           "reason": top_issue, "cited_rules": []})
        else:
            passed += 1

    _phase_end(1, total=len(to_proc), approved=passed, rejected=0,
               flagged=flagged, elapsed=time.time() - t0, issues=issues,
               next_queue=f"{len(to_proc)} 条进入「部门经理初审智能体（Agent 2）」队列")
    return issues


def demo_manager_batch(store: ApplicationStore, max_process: int = 999) -> list[dict]:
    """阶段 2 — 部门经理初审智能体：批量初审。"""
    pending = store.get_by_status("PENDING_MANAGER")
    to_proc = pending[:max_process]
    _phase_start(2, len(to_proc))

    approved = rejected = flagged = 0
    issues   = []
    t0       = time.time()

    for app in to_proc:
        print(f"  ⏳ {app['app_id']} | {app['applicant']:<4} | {app['department']:<4} | "
              f"{app['total_amount']:>6.0f}元 ...", end=" ", flush=True)

        result_status = process_manager_review(app, store)
        print(f"{_icon(result_status)} {result_status}")

        updated     = store.get(app["app_id"])
        hist_entry  = updated["history"][-1] if updated["history"] else {}
        reason      = hist_entry.get("reason", "")
        cited_rules = (updated.get("manager_decision") or {}).get("cited_rules", [])

        if result_status != "APPROVED":
            if reason:
                print(f"       原因: {reason[:65]}")
            if cited_rules:
                print(f"       规则: {', '.join(cited_rules)}")

        if result_status == "APPROVED":
            approved += 1
        elif result_status == "REJECTED":
            rejected += 1
            issues.append({"app_id": app["app_id"], "result": result_status,
                           "reason": reason, "cited_rules": cited_rules})
        else:
            flagged += 1
            issues.append({"app_id": app["app_id"], "result": result_status,
                           "reason": reason, "cited_rules": cited_rules})

    fin_count = len(store.get_by_status("PENDING_FINANCE"))
    _phase_end(2, len(to_proc), approved, rejected, flagged,
               time.time() - t0, issues,
               next_queue=f"{fin_count} 条进入「财务审批智能体（Agent 3）」队列" if fin_count else "")
    return issues


def demo_finance_batch(store: ApplicationStore) -> list[dict]:
    """阶段 3 — 财务审批智能体：终审 + 预算核查。"""
    pending = store.get_by_status("PENDING_FINANCE")
    _phase_start(3, len(pending))

    approved = rejected = flagged = 0
    issues   = []
    t0       = time.time()

    for app in pending:
        mgr_decision = app.get("manager_decision") or {}
        print(f"  ⏳ {app['app_id']} | {app['applicant']:<4} | {app['department']:<4} | "
              f"{app['total_amount']:>6.0f}元 ...", end=" ", flush=True)

        result_status = process_finance_review(app, store, mgr_decision)
        print(f"{_icon(result_status)} {result_status}")

        updated     = store.get(app["app_id"])
        hist_entry  = updated["history"][-1] if updated["history"] else {}
        reason      = hist_entry.get("reason", "")
        fin_detail  = updated.get("finance_decision") or {}
        cited_rules = fin_detail.get("cited_rules", [])
        budget_note = fin_detail.get("budget_note", "")

        if result_status == "APPROVED":
            approved += 1
            if budget_note:
                print(f"       预算: {budget_note[:62]}")
        else:
            if reason:
                print(f"       原因: {reason[:65]}")
            if cited_rules:
                print(f"       规则: {', '.join(cited_rules)}")
            if budget_note:
                print(f"       预算: {budget_note[:55]}")

        if result_status == "REJECTED":
            rejected += 1
            issues.append({"app_id": app["app_id"], "result": result_status,
                           "reason": reason, "cited_rules": cited_rules})
        elif result_status == "PENDING_HUMAN_REVIEW":
            flagged += 1
            issues.append({"app_id": app["app_id"], "result": result_status,
                           "reason": reason, "cited_rules": cited_rules})

    _phase_end(3, len(pending), approved, rejected, flagged,
               time.time() - t0, issues)
    return issues


def demo_single_app(app_id: str, store: ApplicationStore):
    """单条申请完整三阶段 Agent 流程演示。"""
    from crews.prep_crew import run_prep_check

    app = store.get(app_id)
    if not app:
        print(f"未找到申请 {app_id}")
        return

    print(f"\n{'═'*W}")
    print(f"  单条流程演示  |  {app_id} — {app['applicant']} ({app['department']})")
    print(f"  目的地: {app['destination']}  |  金额: {app['total_amount']}元  "
          f"|  票据: {'完整' if app['has_all_receipts'] else '缺票'}  "
          f"|  合规: {'是' if app['within_limits'] else '超标'}")
    print(f"{'═'*W}")

    def _section(agent_id: int):
        ag = AGENTS[agent_id]
        print(f"\n  {'─'*W}")
        print(f"  {ag['name']}  ({ag['tech']})")
        print(f"  {'─'*W}")

    # ── Agent 1: 报销材料预审助手 ────────────────────────────────────────────
    _section(1)
    t0   = time.time()
    prep = run_prep_check(app)
    print(f"  就绪状态  {'✅ 材料完整，可提交' if prep['ready_to_submit'] else '❌ 材料有问题，请整改后再提交'}")
    for issue in prep.get("issues", [])[:4]:
        print(f"  ⚠️   {issue[:70]}")
    for warn in prep.get("warnings", [])[:2]:
        print(f"  💡  {warn[:70]}")
    if prep.get("suggested_note"):
        print(f"  📝  建议备注: {prep['suggested_note'][:62]}")
    print(f"  ⏱   耗时 {time.time()-t0:.1f}s")

    # ── Agent 2: 部门经理初审智能体 ──────────────────────────────────────────
    _section(2)
    t0         = time.time()
    mgr_status = process_manager_review(app, store)
    updated    = store.get(app_id)
    hist       = updated["history"][-1] if updated["history"] else {}
    mgr_detail = updated.get("manager_decision") or {}
    print(f"  决定  {_icon(mgr_status)} {mgr_status}")
    print(f"  理由  {hist.get('reason','')[:70]}")
    if mgr_detail.get("cited_rules"):
        print(f"  规则  {', '.join(mgr_detail['cited_rules'])}")
    print(f"  ⏱   耗时 {time.time()-t0:.1f}s")

    # ── Agent 3: 财务审批智能体 ──────────────────────────────────────────────
    if mgr_status == "APPROVED":
        _section(3)
        t0         = time.time()
        fin_status = process_finance_review(app, store, mgr_detail)
        updated    = store.get(app_id)
        hist       = updated["history"][-1] if updated["history"] else {}
        fin_detail = updated.get("finance_decision") or {}
        print(f"  决定  {_icon(fin_status)} {fin_status}")
        print(f"  理由  {hist.get('reason','')[:70]}")
        if fin_detail.get("cited_rules"):
            print(f"  规则  {', '.join(fin_detail['cited_rules'])}")
        if fin_detail.get("budget_note"):
            print(f"  预算  {fin_detail['budget_note'][:62]}")
        print(f"  ⏱   耗时 {time.time()-t0:.1f}s")
    else:
        print(f"\n  [跳过财务审批智能体 — 经理初审未通过，流程终止]")

    print(f"\n{'─'*W}")


def print_final_report(store: ApplicationStore, t_total: float = 0):
    stats  = store.stats()
    total  = len(store.all())
    auto   = stats.get("AUTO_APPROVED", 0)
    fin    = stats.get("APPROVED", 0)
    human  = stats.get("PENDING_HUMAN_REVIEW", 0)
    rej    = stats.get("REJECTED", 0)
    pend   = stats.get("PENDING_MANAGER", 0) + stats.get("PENDING_FINANCE", 0)
    processed      = total - pend
    approved_total = auto + fin
    approval_rate  = approved_total / processed * 100 if processed else 0

    # ── 大标题 ───────────────────────────────────────────────────────────────
    print(f"\n\n{'█'*W}")
    print(f"{'█'*W}")
    title = "企业报销审批 PoC  —  三级 Agent 运行结果摘要"
    print(f"{'█'*3}  {title:<{W-7}}{'█'*3}")
    print(f"{'█'*W}")
    print(f"{'█'*W}")

    # ── Agent 说明 ───────────────────────────────────────────────────────────
    print(f"\n  三级智能体流水线：")
    print(f"    Agent 1  {AGENTS[1]['name']:<18}  材料预审，票据核查")
    print(f"    Agent 2  {AGENTS[2]['name']:<18}  依规初审，引用规则编号")
    print(f"    Agent 3  {AGENTS[3]['name']:<18}  终审 + 预算控制")

    # ── 核心数据 ─────────────────────────────────────────────────────────────
    print(f"\n  {'─'*54}")
    print(f"  总申请数      {total:>4} 条")
    if pend:
        print(f"  本批已处理    {processed:>4} 条  （余 {pend} 条队列未处理）")
    else:
        print(f"  本批已处理    {processed:>4} 条")
    print(f"  总通过率      {approval_rate:>5.1f}%   ({approved_total} 通过 / {processed} 已处理)")
    print(f"  {'─'*54}")
    print(f"  ✅  规则引擎自动通过    {auto:>4} 条   无 LLM，毫秒级")
    print(f"  ✅  三级 Agent 审批通过  {fin:>4} 条   Agent1+2+3 全流程")
    print(f"  🔍  转人工复核          {human:>4} 条   边缘案例 / 预算预警")
    print(f"  ❌  拒绝退回            {rej:>4} 条   违规，引用规则编号退回")
    if pend:
        print(f"  ⬜  队列待处理          {pend:>4} 条   （--max 限制）")
    if t_total:
        print(f"  ⏱  总运行耗时        {t_total:>6.1f}s")
    print(f"  {'─'*54}")

    # ── 人工复核明细 ─────────────────────────────────────────────────────────
    human_list = store.get_by_status("PENDING_HUMAN_REVIEW")
    if human_list:
        print(f"\n  【{AGENTS[3]['name']} — 转人工复核 {len(human_list)} 条】")
        for app in human_list:
            hist   = app["history"][-1] if app["history"] else {}
            actor  = hist.get("actor", "")
            reason = hist.get("reason", "")
            detail = app.get("finance_decision") or app.get("manager_decision") or {}
            rules  = ", ".join(detail.get("cited_rules", []))
            tag    = f"  ({rules})" if rules else ""
            print(f"    🔍 {app['app_id']} | {app['applicant']:<4} | "
                  f"{app['total_amount']:>6.0f}元  [{actor}] {reason[:44]}{tag}")

    # ── 拒绝明细 ────────────────────────────────────────────────────────────
    rej_list = store.get_by_status("REJECTED")
    if rej_list:
        print(f"\n  【拒绝退回 {len(rej_list)} 条 — 引用规则编号】")
        for app in rej_list:
            hist   = app["history"][-1] if app["history"] else {}
            actor  = hist.get("actor", "")
            reason = hist.get("reason", "")
            detail = app.get("finance_decision") or app.get("manager_decision") or {}
            rules  = ", ".join(detail.get("cited_rules", []))
            tag    = f"  ({rules})" if rules else ""
            print(f"    ❌ {app['app_id']} | {app['applicant']:<4} | "
                  f"{app['total_amount']:>6.0f}元  [{actor}] {reason[:44]}{tag}")

    # ── 底部 ─────────────────────────────────────────────────────────────────
    print(f"\n{'█'*W}")
    print(f"{'█'*3}  {'PoC Demo 完成 — CrewAI + 千问 qwen-plus + Milvus RAG':<{W-7}}{'█'*3}")
    print(f"{'█'*W}\n")


# ─── 入口 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="报销审批 Agent PoC Demo")
    parser.add_argument("--no-llm",      action="store_true", help="跳过LLM阶段，仅测试自动审批")
    parser.add_argument("--single",      type=str,            help="单条完整流程演示，如 EXP0091")
    parser.add_argument("--max",         type=int, default=999, help="LLM批处理最大条数")
    parser.add_argument("--no-rag-init", action="store_true", help="跳过RAG初始化（已初始化时使用）")
    args = parser.parse_args()

    print(f"\n{'═'*W}")
    print(f"  企业报销审批智能体 PoC  —  CrewAI + 千问 + Milvus RAG")
    print(f"  LLM    : qwen-plus (DashScope 百炼)")
    print(f"  Embed  : text-embedding-v3, 1024-dim (Milvus Lite)")
    print(f"  Agents : {AGENTS[1]['name']} → {AGENTS[2]['name']} → {AGENTS[3]['name']}")
    print(f"{'═'*W}")

    t_start = time.time()

    # RAG 初始化
    if not args.no_llm and not args.no_rag_init:
        demo_init_rag()

    # 加载申请
    store = ApplicationStore()
    apps  = generate_mock_applications()
    store.load_applications(apps)
    print(f"\n  ✓ 已加载 {len(apps)} 条测试申请 (EXP0001 ~ EXP{len(apps):04d})")

    # 单条演示
    if args.single:
        if not args.no_rag_init:
            demo_init_rag()
        demo_single_app(args.single, store)
        print_final_report(store)
        return

    # 阶段 0: 规则引擎预筛
    demo_auto_approve(store)

    if args.no_llm:
        print(f"\n  [--no-llm 模式] 跳过三个 Agent LLM 阶段")
        print_final_report(store, time.time() - t_start)
        return

    # 阶段 1: 报销材料预审助手
    demo_prep_batch(store, max_process=args.max)

    # 阶段 2: 部门经理初审智能体
    demo_manager_batch(store, max_process=args.max)

    # 阶段 3: 财务审批智能体
    demo_finance_batch(store)

    # 汇总
    print_final_report(store, time.time() - t_start)

    store.save()
    print(f"  结果已保存  →  state/applications.json\n")


if __name__ == "__main__":
    main()
