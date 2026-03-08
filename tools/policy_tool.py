"""
CrewAI Tool: 查询公司报销政策（调用 RAG 检索）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import BaseModel, Field
from crewai.tools import BaseTool
from rag.expense_rag import search, get_rule_by_id, get_all_rules_summary


class PolicyQueryInput(BaseModel):
    query: str = Field(description="关于公司报销政策的自然语言查询，例如：住宿费上限、机票报销规则、自动审批条件")


class PolicyTool(BaseTool):
    name: str = "查询报销政策"
    description: str = (
        "根据关键词查询公司差旅报销政策和规定。"
        "输入自然语言查询即可，例如：'住宿费标准'、'餐饮费上限'、'审批权限'、'自动审批条件'。"
        "返回相关规定文本供决策参考。"
    )
    args_schema: type[BaseModel] = PolicyQueryInput

    def _run(self, query: str) -> str:
        return search(query, top_k=3)


class RuleIdInput(BaseModel):
    rule_id: str = Field(description="规则编号，例如：rule_001、rule_007、rule_012")


class CategoryPolicyTool(BaseTool):
    name: str = "按规则编号查询规定"
    description: str = (
        "按规则编号精确查询某条报销规定全文。"
        "支持编号：rule_001(日补贴) rule_002(住宿费) rule_003(餐饮) rule_004(机票) "
        "rule_005(火车) rule_006(出租车) rule_007(票据) rule_008(审批权限) "
        "rule_009(预算) rule_010(客户招待) rule_011(时限) rule_012(自动审批条件)。"
    )
    args_schema: type[BaseModel] = RuleIdInput

    def _run(self, rule_id: str) -> str:
        return get_rule_by_id(rule_id)


# 实例（供 Agent 直接使用）
policy_tool          = PolicyTool()
category_policy_tool = CategoryPolicyTool()
