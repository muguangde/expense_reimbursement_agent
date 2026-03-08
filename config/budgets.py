"""
各部门预算 Mock 数据（年度差旅预算，单位：元）
"""

DEPARTMENT_BUDGETS = {
    "技术部": {
        "annual_budget":   200000,
        "q1_used":          38000,
        "q2_used":          42000,
        "q3_used":          35000,
        "q4_used":          12000,
        "total_used":      127000,
    },
    "销售部": {
        "annual_budget":   350000,
        "q1_used":          85000,
        "q2_used":          92000,
        "q3_used":          88000,
        "q4_used":          31000,
        "total_used":      296000,
    },
    "市场部": {
        "annual_budget":   180000,
        "q1_used":          41000,
        "q2_used":          38000,
        "q3_used":          44000,
        "q4_used":          21000,
        "total_used":      144000,
    },
    "人事部": {
        "annual_budget":    80000,
        "q1_used":          18000,
        "q2_used":          15000,
        "q3_used":          19000,
        "q4_used":           8000,
        "total_used":       60000,
    },
    "财务部": {
        "annual_budget":    60000,
        "q1_used":          12000,
        "q2_used":          11000,
        "q3_used":          10000,
        "q4_used":           5000,
        "total_used":       38000,
    },
    "运营部": {
        "annual_budget":   120000,
        "q1_used":          24000,
        "q2_used":          28000,
        "q3_used":          32000,
        "q4_used":          18000,
        "total_used":      102000,  # 85%，接近预警线
    },
    "法务部": {
        "annual_budget":    50000,
        "q1_used":           8000,
        "q2_used":           9000,
        "q3_used":           7000,
        "q4_used":           3000,
        "total_used":       27000,
    },
}


def get_budget_status(department: str) -> dict:
    b = DEPARTMENT_BUDGETS.get(department)
    if not b:
        return {"error": f"未找到部门 {department} 的预算信息"}
    remaining = b["annual_budget"] - b["total_used"]
    usage_rate = b["total_used"] / b["annual_budget"]
    warning = usage_rate >= 0.85
    return {
        "department": department,
        "annual_budget": b["annual_budget"],
        "total_used": b["total_used"],
        "remaining": remaining,
        "usage_rate": round(usage_rate, 4),
        "warning": warning,
        "warning_msg": "⚠️ 预算使用超85%，需总监审批" if warning else "",
    }


def check_can_afford(department: str, amount: float) -> dict:
    status = get_budget_status(department)
    if "error" in status:
        return status
    can_afford = status["remaining"] >= amount
    return {
        **status,
        "requested_amount": amount,
        "can_afford": can_afford,
        "message": (
            f"预算充足，剩余 {status['remaining']} 元" if can_afford
            else f"预算不足，剩余 {status['remaining']} 元，无法覆盖 {amount} 元"
        ),
    }
