"""
Manager Crew — 直属上级初审批处理

关键设计：
  - Task 1: 查询 RAG，返回适用规定 + 具体数字（上限/时限等）
  - Task 2: 严格基于 Task 1 结果做决策，禁止使用未经查询的数字
  - Task context 将 Task 1 输出传递给 Task 2
"""

import json
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crewai import Crew, Task, Process
from agents.definitions import manager_agent
from config.company_rules import get_city_tier, HOTEL_LIMIT, MEAL_LIMIT_PER_DAY, APPROVAL_THRESHOLDS


def _app_text(app: dict) -> str:
    items = "\n".join(
        f"    [{item['category']}] {item['amount']}元"
        f"  {'✓发票' if item['has_receipt'] else '✗无发票'}"
        f"  {item.get('note', '')}"
        for item in app["expense_items"]
    )
    return f"""
申请编号 : {app['app_id']}
申请人   : {app['applicant']} ({app['department']} / {app['level']})
目的地   : {app['destination']}
出差时间 : {app['trip_start']} ~ {app['trip_end']} ({app['trip_days']}天)
出差目的 : {app.get('purpose', '未填写')}
提交天数 : 出差结束后第 {app.get('submitted_days_after_trip', '?')} 天提交

费用明细 :
{items}

合计     : {app['total_amount']} 元
票据完整 : {'是' if app['has_all_receipts'] else '否（存在缺票项）'}
各项合规 : {'是' if app['within_limits'] else '否（存在超标项）'}
申请说明 : {app.get('justification', '（无）')}
""".strip()


def _compute_limits(app: dict) -> dict:
    """预先计算本次申请适用的城市标准，返回结构化数据供 Task 2 直接引用。"""
    tier        = get_city_tier(app.get("destination", ""))
    hotel_night = HOTEL_LIMIT.get(tier, HOTEL_LIMIT["other"])
    meal_day    = MEAL_LIMIT_PER_DAY
    days        = app.get("trip_days", 1)
    return {
        "tier":              tier,
        "hotel_night":       hotel_night,
        "hotel_total":       hotel_night * days,
        "meal_day":          meal_day,
        "meal_total":        meal_day * days,
        "days":              days,
        "auto_limit":        APPROVAL_THRESHOLDS["auto_approve_limit"],
        "submit_limit_days": 30,
    }


def _limits_text(L: dict) -> str:
    """将限额数据格式化为任务描述中的文字块。"""
    return (
        f"  城市等级          : {L['tier']} "
        f"（北京/上海/广州/深圳为tier1，杭州/成都/武汉等为tier2，其余为other）\n"
        f"  住宿费上限/晚     : {L['hotel_night']}元  ← 来自rule_002，无职级区分\n"
        f"  住宿费总上限      : {L['hotel_total']}元  ({L['hotel_night']}元×{L['days']}晚)\n"
        f"  餐饮费上限/天     : {L['meal_day']}元  ← 来自rule_003，无城市区分\n"
        f"  餐饮费总上限      : {L['meal_total']}元  ({L['meal_day']}元×{L['days']}天)\n"
        f"  报销提交时限      : 出差结束后{L['submit_limit_days']}个自然日内\n"
        f"  自动审批门槛      : {L['auto_limit']}元（本次已超，走人工流程）\n"
        f"\n"
        f"  !! 重要说明 !!\n"
        f"  - rule_002 住宿上限 只按城市分级，不按员工职级区分，不存在'经理级450元'等说法\n"
        f"  - rule_003 餐饮上限 统一150元/天，不区分一线/二线城市\n"
        f"  - 以上数字为系统权威来源，禁止使用任何其他数字"
    )


def run_manager_review(app: dict) -> dict:
    """
    对单条申请运行经理初审，返回带规则引用的决策。

    Returns:
        {decision, reason, cited_rules, raw_output}
    """
    app_text = _app_text(app)
    L        = _compute_limits(app)
    lim_text = _limits_text(L)

    # ── Task 1: RAG 查询，返回规定原文 ───────────────────────────────────────
    policy_task = Task(
        description=f"""
请依次调用"查询报销政策"工具，查询以下5项公司规定并返回原文：

1. 查询: "住宿费报销标准上限"
2. 查询: "餐饮费每日上限"
3. 查询: "机票和交通费报销规则"
4. 查询: "发票和票据要求"
5. 查询: "报销提交时限"

每次查询后记录规则编号和原文。不需要做任何判断，只需返回查到的规定原文。
""",
        expected_output="5项规定的原文和对应规则编号（rule_XXX）",
        agent=manager_agent,
    )

    # ── Task 2: 基于规定做决策 ────────────────────────────────────────────────
    review_task = Task(
        description=f"""
你是部门经理的审批助理。请严格基于下方"系统权威限额"对申请做出初审决定。

=== 申请详情 ===
{app_text}

=== 系统权威限额（唯一数字来源，禁止使用其他任何数字）===
{lim_text}

=== 逐项核查流程 ===
1. 住宿费实际总额 vs 住宿费总上限 {L['hotel_total']}元 → 超则REJECTED（除非有说明→PENDING）
2. 餐饮费实际总额 vs 餐饮费总上限 {L['meal_total']}元 → 超则REJECTED（除非有说明→PENDING）
3. 是否有无发票项 → 有则REJECTED（无说明）或PENDING（有说明）
4. 是否含商务舱且无审批 → 有则REJECTED
5. 是否含客户招待/购物等非差旅类别 → 有则REJECTED
6. 提交天数是否 > {L['submit_limit_days']}天 → 超且无说明则REJECTED，有说明则PENDING

=== 决策规则 ===
- APPROVED            : 全部6项检查通过
- REJECTED            : 存在上述任一明确违规且无合理说明
- PENDING_HUMAN_REVIEW: 存在违规但申请人有说明，需人工判断是否豁免

**硬性约束（违反则输出无效）**：
- 住宿上限只能用 {L['hotel_total']}元，禁止使用 450元/晚 或任何其他数字
- 餐饮上限只能用 {L['meal_total']}元，禁止使用 100元/天 或 120元/天
- 报销时限只能用 {L['submit_limit_days']}天，禁止使用 5天/7天/60天
- 引用规则编号必须来自上一步RAG查询结果

严格输出 JSON（不加任何其他文字）：
{{
  "decision": "APPROVED" 或 "REJECTED" 或 "PENDING_HUMAN_REVIEW",
  "reason": "中文审批理由（100字内），引用规则编号",
  "cited_rules": ["rule_xxx"],
  "key_issues": ["具体问题（含规则编号）"]
}}
""",
        expected_output="严格JSON格式审批决定",
        agent=manager_agent,
        context=[policy_task],
    )

    crew = Crew(
        agents=[manager_agent],
        tasks=[policy_task, review_task],
        process=Process.sequential,
        verbose=False,
    )

    result     = crew.kickoff()
    raw_output = str(result)
    decision, reason, cited_rules = _parse_decision(raw_output)

    return {
        "decision":    decision,
        "reason":      reason,
        "cited_rules": cited_rules,
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
            if decision not in ("APPROVED", "REJECTED", "PENDING_HUMAN_REVIEW"):
                decision = "PENDING_HUMAN_REVIEW"
            return decision, reason, cited_rules
        except json.JSONDecodeError:
            pass

    cited = re.findall(r'rule_\d+', output)
    output_upper = output.upper()
    if "REJECTED" in output_upper or "拒绝" in output or "退回" in output:
        return "REJECTED", _tail(output), list(set(cited))
    if "PENDING_HUMAN" in output_upper or "人工" in output:
        return "PENDING_HUMAN_REVIEW", _tail(output), list(set(cited))
    if "APPROVED" in output_upper or "通过" in output or "批准" in output:
        return "APPROVED", _tail(output), list(set(cited))
    return "PENDING_HUMAN_REVIEW", "解析失败，保守标记人工", []


def _tail(output: str) -> str:
    m = re.search(r'"reason"\s*:\s*"([^"]+)"', output)
    if m:
        return m.group(1)
    lines = [l.strip() for l in output.split("\n") if l.strip()]
    return lines[-1][:150] if lines else "（无说明）"
