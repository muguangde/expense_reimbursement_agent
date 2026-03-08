"""
Prep Crew — 报销材料整理助手

供员工在提交 OA 申请前调用，验证材料完整性并生成结构化申请摘要。
"""

import json
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crewai import Crew, Task, Process
from agents.definitions import prep_agent


def run_prep_check(app: dict) -> dict:
    """
    对员工提交的材料进行预检，生成结构化申请摘要。

    Returns:
        dict: {
            "ready_to_submit": bool,
            "issues": list[str],
            "summary": str,
            "raw_output": str,
        }
    """
    items_text = "\n".join(
        f"  - {item['category']}: {item['amount']}元"
        f"  {'✓发票' if item['has_receipt'] else '✗无发票'}"
        f"  {item.get('note', '')}"
        for item in app["expense_items"]
    )

    # Task 1: 材料核查
    check_task = Task(
        description=f"""
员工准备提交以下报销申请，请帮助核查材料完整性：

申请人: {app['applicant']} ({app['department']})
出差目的地: {app['destination']}
出差时间: {app['trip_start']} ~ {app['trip_end']} ({app['trip_days']}天)
出差目的: {app.get('purpose', '未填写')}

费用清单:
{items_text}

合计: {app['total_amount']} 元
提交距出差结束: {app.get('submitted_days_after_trip', '?')} 天

请查询以下内容的公司规定后给出核查结论：
1. {app['destination']} 的住宿费标准
2. 餐饮费标准
3. 各费用项的票据要求
4. 报销时限
5. 本次总金额对应的审批路径
""",
        expected_output="材料核查报告：列出合规项、问题项、缺失项",
        agent=prep_agent,
    )

    # Task 2: 生成提交摘要
    summary_task = Task(
        description=f"""
根据核查结果，生成一份供员工在 OA 系统中使用的报销申请摘要。

格式要求（JSON）：
{{
  "ready_to_submit": true 或 false,
  "issues": ["问题1", "问题2"],  // 发现的问题列表
  "warnings": ["提示1"],         // 需要注意但不阻止提交的提示
  "checklist": {{
    "票据完整": true/false,
    "金额合规": true/false,
    "提交及时": true/false
  }},
  "suggested_note": "建议员工在申请备注中填写的说明文字"
}}
""",
        expected_output="JSON格式的申请摘要",
        agent=prep_agent,
        context=[check_task],
    )

    crew = Crew(
        agents=[prep_agent],
        tasks=[check_task, summary_task],
        process=Process.sequential,
        verbose=False,
    )

    result = crew.kickoff()
    raw_output = str(result)

    return _parse_prep_result(raw_output)


def _parse_prep_result(output: str) -> dict:
    json_match = re.search(r'\{[^{}]*"ready_to_submit"[^{}]*\}', output, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            return {
                "ready_to_submit": data.get("ready_to_submit", False),
                "issues":          data.get("issues", []),
                "warnings":        data.get("warnings", []),
                "suggested_note":  data.get("suggested_note", ""),
                "raw_output":      output,
            }
        except json.JSONDecodeError:
            pass

    # fallback
    ready = "准备就绪" in output or "可以提交" in output or "ready" in output.lower()
    return {
        "ready_to_submit": ready,
        "issues":          [],
        "warnings":        ["输出解析失败，请人工确认"],
        "suggested_note":  "",
        "raw_output":      output,
    }
