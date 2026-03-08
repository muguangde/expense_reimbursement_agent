"""
CrewAI Tool: 查询和更新部门预算
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import BaseModel, Field
from crewai.tools import BaseTool
from config.budgets import get_budget_status, check_can_afford, DEPARTMENT_BUDGETS


class BudgetCheckInput(BaseModel):
    department: str = Field(description="部门名称，例如：技术部、销售部、市场部、人事部、财务部、运营部、法务部")
    amount: float = Field(description="本次报销申请金额（元）")


class BudgetStatusInput(BaseModel):
    department: str = Field(description="部门名称")


class BudgetCheckTool(BaseTool):
    name: str = "检查部门预算"
    description: str = (
        "检查指定部门是否有足够预算覆盖本次报销申请。"
        "输入部门名称和申请金额，返回预算余额、使用率和是否可以报销的判断。"
        "使用率超过85%时会触发预警，需要总监审批。"
    )
    args_schema: type[BaseModel] = BudgetCheckInput

    def _run(self, department: str, amount: float) -> str:
        result = check_can_afford(department, amount)
        if "error" in result:
            return result["error"]

        lines = [
            f"部门: {result['department']}",
            f"年度预算: {result['annual_budget']:,} 元",
            f"已使用: {result['total_used']:,} 元",
            f"剩余: {result['remaining']:,} 元",
            f"使用率: {result['usage_rate']*100:.1f}%",
            f"本次申请: {result['requested_amount']:,} 元",
            f"结论: {result['message']}",
        ]
        if result["warning"]:
            lines.append(f"⚠️ {result['warning_msg']}")
        return "\n".join(lines)


class BudgetStatusTool(BaseTool):
    name: str = "查询部门预算状态"
    description: str = "查询指定部门当前预算使用情况，包括年度预算、已用金额、剩余额度和使用率。"
    args_schema: type[BaseModel] = BudgetStatusInput

    def _run(self, department: str) -> str:
        result = get_budget_status(department)
        if "error" in result:
            return result["error"]
        lines = [
            f"部门: {result['department']}",
            f"年度预算: {result['annual_budget']:,} 元  |  已使用: {result['total_used']:,} 元  |  剩余: {result['remaining']:,} 元",
            f"使用率: {result['usage_rate']*100:.1f}%",
        ]
        if result["warning"]:
            lines.append(f"⚠️ {result['warning_msg']}")
        return "\n".join(lines)


# 实例（供 Agent 直接使用）
budget_check_tool  = BudgetCheckTool()
budget_status_tool = BudgetStatusTool()
