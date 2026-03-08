"""
主处理管道

流程：
  1. run_auto_approve_batch()  — 规则引擎，无 LLM，80 条自动审批
  2. process_manager_review()  — 调用 ManagerCrew，逐条处理
  3. process_finance_review()  — 调用 FinanceCrew，逐条处理

由 demo.py 直接调用；生产环境由 scheduler/cron_jobs.py 定时触发步骤 2/3。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.company_rules import (
    HOTEL_LIMIT, MEAL_LIMIT_PER_DAY, APPROVAL_THRESHOLDS,
    RECEIPT_RULES, get_city_tier,
)
from state.store import (
    ApplicationStore,
    STATUS_PENDING_AUTO, STATUS_AUTO_APPROVED, STATUS_PENDING_MANAGER,
)


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 1：规则引擎自动审批（纯 Python，无 LLM）
# ─────────────────────────────────────────────────────────────────────────────

def run_auto_approve_batch(store: ApplicationStore) -> dict:
    """
    检查所有 PENDING_AUTO_CHECK 状态的申请。
    满足自动审批条件的直接批准，其余转入 PENDING_MANAGER 队列。
    """
    pending = store.get_by_status(STATUS_PENDING_AUTO)
    auto_approved = 0
    to_manager    = 0

    for app in pending:
        reason, ok = _check_auto_approve(app)
        if ok:
            store.approve_auto(app["app_id"], reason)
            auto_approved += 1
        else:
            store.send_to_manager(app["app_id"])
            to_manager += 1

    return {"auto_approved": auto_approved, "to_manager": to_manager}


def _check_auto_approve(app: dict) -> tuple[str, bool]:
    """
    返回 (reason, is_auto_approvable)。
    同时满足：
      (1) 总金额 < 1000 元
      (2) 所有费用项均有发票
      (3) 各项费用在城市标准内
      (4) 提交不超过 30 天
    """
    amount = app["total_amount"]
    limit  = APPROVAL_THRESHOLDS["auto_approve_limit"]

    # 条件1: 金额
    if amount >= limit:
        return f"金额 {amount} 元 ≥ {limit} 元，需人工审批", False

    # 条件2: 票据
    if not app.get("has_all_receipts", False):
        return "存在缺票项，需经理审核", False

    # 条件3: 逐项金额合规
    tier = get_city_tier(app.get("destination", ""))
    hotel_limit = HOTEL_LIMIT.get(tier, HOTEL_LIMIT["other"])
    for item in app["expense_items"]:
        cat = item["category"]
        amt = item["amount"]
        days = max(1, app.get("trip_days", 1))
        if cat == "住宿费" and amt > hotel_limit * days:
            return f"住宿费 {amt} 元超过 {tier} 标准 {hotel_limit}元/晚×{days}晚", False
        if cat == "餐饮费" and amt > MEAL_LIMIT_PER_DAY * days:
            return f"餐饮费 {amt} 元超过标准 {MEAL_LIMIT_PER_DAY}元/天×{days}天", False

    # 条件4: 时限
    submitted_days = app.get("submitted_days_after_trip", 0)
    if submitted_days > 30:
        return f"提交超过30天（{submitted_days}天），需经理审核", False

    return "金额<1000元，票据完整，各项合规，提交及时", True


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 2：经理初审（LLM）
# ─────────────────────────────────────────────────────────────────────────────

def process_manager_review(app: dict, store: ApplicationStore) -> str:
    """
    对单条 PENDING_MANAGER 申请运行经理初审。
    返回最终状态字符串。
    """
    from crews.manager_crew import run_manager_review

    result = run_manager_review(app)
    decision = result["decision"]
    reason   = result["reason"]

    if decision == "APPROVED":
        store.manager_approve(
            app["app_id"], reason,
            detail={"decision": decision, "reason": reason},
        )
        return "APPROVED"
    elif decision == "REJECTED":
        store.manager_reject(app["app_id"], reason)
        return "REJECTED"
    else:
        store.manager_flag_human(app["app_id"], reason)
        return "PENDING_HUMAN_REVIEW"


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 3：财务终审（LLM）
# ─────────────────────────────────────────────────────────────────────────────

def process_finance_review(
    app: dict,
    store: ApplicationStore,
    manager_decision: dict = None,
) -> str:
    """
    对单条 PENDING_FINANCE 申请运行财务终审。
    返回最终状态字符串。
    """
    from crews.finance_crew import run_finance_review

    result   = run_finance_review(app, manager_decision)
    decision = result["decision"]
    reason   = result["reason"]

    if decision == "APPROVED":
        store.finance_approve(
            app["app_id"], reason,
            detail={"decision": decision, "reason": reason},
        )
        return "APPROVED"
    elif decision == "REJECTED":
        store.finance_reject(app["app_id"], reason)
        return "REJECTED"
    else:
        store.finance_flag_human(app["app_id"], reason)
        return "PENDING_HUMAN_REVIEW"
