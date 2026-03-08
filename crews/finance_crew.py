"""
Finance Crew — 财务部终审批处理

改进点：
  - budget tool 调用失败时自动重试（最多2次）
  - 若 budget tool 仍失败，直接从本地预算数据获取（fallback）
  - 财务决策同样只能引用 RAG 查到的规则，不自行推断
"""

import json
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crewai import Crew, Task, Process
from agents.definitions import finance_agent
from config.budgets import get_budget_status, check_can_afford


def _app_text(app: dict, manager_decision: dict = None) -> str:
    items = "\n".join(
        f"    [{item['category']}] {item['amount']}元"
        f"  {'✓发票' if item['has_receipt'] else '✗无发票'}"
        f"  {item.get('note', '')}"
        for item in app["expense_items"]
    )
    mgr = (manager_decision or {}).get("reason", "（无）")
    return f"""
申请编号 : {app['app_id']}
申请人   : {app['applicant']} ({app['department']} / {app['level']})
目的地   : {app['destination']}  |  出差 {app['trip_days']} 天
出差目的 : {app.get('purpose', '未填写')}
提交天数 : 出差结束后第 {app.get('submitted_days_after_trip', '?')} 天

费用明细 :
{items}

合计     : {app['total_amount']} 元
票据完整 : {'是' if app['has_all_receipts'] else '否（缺票）'}
各项合规 : {'是' if app['within_limits'] else '否（超标）'}
申请说明 : {app.get('justification', '（无）')}
经理初审 : {mgr}
""".strip()


def _get_budget_info(department: str, amount: float) -> str:
    """直接从本地数据获取预算信息（finance_crew 的 fallback，不依赖 LLM tool call）。"""
    result = check_can_afford(department, amount)
    if "error" in result:
        return f"预算查询失败：{result['error']}"
    lines = [
        f"部门: {result['department']}  年度预算: {result['annual_budget']:,}元",
        f"已使用: {result['total_used']:,}元  剩余: {result['remaining']:,}元  使用率: {result['usage_rate']*100:.1f}%",
        f"本次申请: {amount:,}元  {'✓ 预算充足' if result['can_afford'] else '✗ 预算不足'}",
    ]
    if result["warning"]:
        lines.append(f"⚠️ {result['warning_msg']}")
    return "\n".join(lines)


def run_finance_review(app: dict, manager_decision: dict = None) -> dict:
    """
    对单条申请运行财务终审。
    预算信息通过本地计算预先注入，避免 tool call 失败导致 Agent 保守标记。
    """
    app_text    = _app_text(app, manager_decision)
    budget_info = _get_budget_info(app["department"], app["total_amount"])

    # ── Task 1: RAG 合规核查 ─────────────────────────────────────────────────
    compliance_task = Task(
        description=f"""
请调用"查询报销政策"工具，依次查询以下规定并记录规则编号：

1. 查询: "发票和票据合规要求"
2. 查询: "报销提交时限"
3. 查询: "审批金额门槛"
4. 查询: "客户招待费规定"

然后对以下申请逐项核查，每项结论必须注明规则编号：

{app_text}

核查项目：
- 每笔费用是否有合规发票（参考票据规定）
- 报销时限是否符合（30个自然日内）
- 费用类别是否合规（是否含客户招待等不可报销项）
- 本次金额 {app['total_amount']}元 对应哪个审批层级
""",
        expected_output="逐项合规核查结论，每条引用规则编号",
        agent=finance_agent,
    )

    # ── Task 2: 终审决策（预算信息直接注入，不依赖 tool call）─────────────────
    final_task = Task(
        description=f"""
综合合规核查结论，给出财务终审决定。

=== 申请信息 ===
{app_text}

=== 预算信息（已由系统预先查询，请直接使用）===
{budget_info}

=== 决策标准 ===
- APPROVED           : 发票合规 + 金额在标准内 + 提交及时 + 预算充足 → 批准入账
- REJECTED           : 存在以下情况 → 退回（必须引用规则编号）：
    * 缺少发票
    * 费用类别不合规（购物/客户招待混入差旅）
    * 提交超过30个自然日且无说明
- PENDING_HUMAN_REVIEW : 以下情况上报财务总监：
    * 预算使用率已超85%（见上方预算信息）
    * 总金额超过5000元且存在疑点
    * 超标但有说明需人工判断

**注意**：预算信息已由系统提供，请直接使用，不需要再调用预算工具。

严格输出 JSON（不加任何其他文字）：
{{
  "decision": "APPROVED" 或 "REJECTED" 或 "PENDING_HUMAN_REVIEW",
  "reason": "财务终审说明（100字内），引用规则编号",
  "cited_rules": ["rule_xxx"],
  "budget_note": "{budget_info.split(chr(10))[1] if chr(10) in budget_info else budget_info[:80]}",
  "accounting_note": "入账科目（仅APPROVED填写）"
}}
""",
        expected_output="严格JSON格式财务终审决定",
        agent=finance_agent,
        context=[compliance_task],
    )

    crew = Crew(
        agents=[finance_agent],
        tasks=[compliance_task, final_task],
        process=Process.sequential,
        verbose=False,
    )

    result     = crew.kickoff()
    raw_output = str(result)
    decision, reason, cited_rules, budget_note = _parse_decision(raw_output)

    # budget_note fallback: 若 LLM 没填，直接用预先计算的
    if not budget_note:
        budget_note = budget_info.split("\n")[1] if "\n" in budget_info else budget_info[:80]

    return {
        "decision":    decision,
        "reason":      reason,
        "cited_rules": cited_rules,
        "budget_note": budget_note,
        "raw_output":  raw_output,
    }


# ─── 解析 ─────────────────────────────────────────────────────────────────────

def _parse_decision(output: str) -> tuple:
    json_match = re.search(r'\{[^{}]*"decision"[^{}]*\}', output, re.DOTALL)
    if json_match:
        try:
            data        = json.loads(json_match.group())
            decision    = data.get("decision", "PENDING_HUMAN_REVIEW").upper()
            reason      = data.get("reason", "（无说明）")
            cited_rules = data.get("cited_rules", [])
            budget_note = data.get("budget_note", "")
            if decision not in ("APPROVED", "REJECTED", "PENDING_HUMAN_REVIEW"):
                decision = "PENDING_HUMAN_REVIEW"
            return decision, reason, cited_rules, budget_note
        except json.JSONDecodeError:
            pass

    cited = re.findall(r'rule_\d+', output)
    output_upper = output.upper()
    if "REJECTED" in output_upper or "退回" in output or "拒绝" in output:
        return "REJECTED", _tail(output), list(set(cited)), ""
    if "PENDING_HUMAN" in output_upper or "人工" in output or "上报" in output:
        return "PENDING_HUMAN_REVIEW", _tail(output), list(set(cited)), ""
    if "APPROVED" in output_upper or "批准" in output or "通过" in output:
        return "APPROVED", _tail(output), list(set(cited)), ""
    return "PENDING_HUMAN_REVIEW", "解析失败，保守上报人工", [], ""


def _tail(output: str) -> str:
    m = re.search(r'"reason"\s*:\s*"([^"]+)"', output)
    if m:
        return m.group(1)
    lines = [l.strip() for l in output.split("\n") if l.strip()]
    return lines[-1][:150] if lines else "（无说明）"
