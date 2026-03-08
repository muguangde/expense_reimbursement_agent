"""
Chat Crew — 报销发起助手（Agent 1 对话模式）

功能：
  - 对话式引导员工填写差旅报销申请
  - 回答报销政策问题（RAG）
  - 实时提取申请字段，构建申请草稿
  - 草稿完成后可直接提交审批流水线

实现方案：
  直接调用 OpenAI 兼容接口（qwen-plus）+ function calling
  - 比 CrewAI 更适合多轮对话场景
  - 保持完整对话历史 context
  - 支持工具调用（RAG 检索）
"""

import json
import os
import sys
import re
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI

QWEN_API_KEY = "sk-0a2184255d00431eaf1684b6fae595c4"
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_client = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)
    return _client


# ─── 工具定义（function calling）────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_policy",
            "description": "查询公司差旅报销规定，返回最相关的规则条文。用于回答员工关于报销政策的问题，或核查某项费用是否合规。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索关键词，例如：'上海住宿费标准'、'餐饮费发票要求'、'报销时限'"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_similar_cases",
            "description": "从历史申请库中检索相似案例，帮助员工了解类似情况的处理结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "场景描述，例如：'客户指定酒店超标'、'餐饮无发票'、'出差天数4天北京'"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_budget",
            "description": "查询某部门当前预算余额，帮助员工了解申请是否在预算范围内。",
            "parameters": {
                "type": "object",
                "properties": {
                    "department": {
                        "type": "string",
                        "description": "部门名称，例如：'销售部'、'市场部'、'技术部'"
                    }
                },
                "required": ["department"]
            }
        }
    }
]


# ─── 工具执行 ───────────────────────────────────────────────────────────────

def _execute_tool(name: str, args: dict) -> str:
    if name == "search_policy":
        from rag.expense_rag import search
        return search(args["query"], top_k=3)

    elif name == "search_similar_cases":
        try:
            from rag.application_rag import search_similar
            return search_similar(args["query"], top_k=3)
        except Exception as e:
            return f"历史案例检索暂不可用: {e}"

    elif name == "check_budget":
        from tools.budget_tool import DEPT_BUDGETS
        dept = args["department"]
        for key, val in DEPT_BUDGETS.items():
            if dept in key or key in dept:
                used = val.get("used", 0)
                total = val.get("total", 0)
                remaining = total - used
                pct = used / total * 100 if total else 0
                return (
                    f"【{dept}】预算状况\n"
                    f"  年度预算: {total:,.0f} 元\n"
                    f"  已使用:   {used:,.0f} 元 ({pct:.1f}%)\n"
                    f"  剩余:     {remaining:,.0f} 元"
                )
        return f"未找到部门 [{dept}] 的预算数据。"

    return f"未知工具: {name}"


# ─── 系统提示词 ─────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    today = date.today().isoformat()
    return f"""你是"报销发起助手"，负责帮助员工发起差旅报销申请，并回答报销政策问题。

今天日期: {today}

## 你的两种工作模式

### 模式 A：政策问答
员工询问报销政策时，你需要：
1. 调用 search_policy 工具检索相关规定
2. 用通俗语言解释规则，给出明确数字
3. 如有相似案例，可调用 search_similar_cases 参考

### 模式 B：引导填写申请
员工表示要提交报销申请时，你需要逐步收集以下信息：

**必填字段**（不足时主动追问）：
- 申请人姓名、部门、级别（P级）
- 目的地城市
- 出差开始/结束日期
- 出差目的
- 费用明细：类别、金额、是否有发票

**自动核查**（收集到信息后主动做）：
- 调用 search_policy 查询目的地住宿/餐饮标准
- 对照标准提示超标项
- 提醒缺少发票的后果

**申请草稿格式**（信息收集完整后输出，必须包含此JSON块）：
```json
{{
  "draft": {{
    "applicant": "姓名",
    "department": "部门",
    "level": "P级别",
    "destination": "城市",
    "trip_start": "YYYY-MM-DD",
    "trip_end": "YYYY-MM-DD",
    "trip_days": 天数,
    "purpose": "出差目的",
    "expense_items": [
      {{"category": "住宿费", "amount": 金额, "has_receipt": true/false, "note": "备注"}},
      {{"category": "餐饮费", "amount": 金额, "has_receipt": true/false, "note": "备注"}},
      {{"category": "交通费", "amount": 金额, "has_receipt": true/false, "note": "备注"}}
    ],
    "total_amount": 合计金额,
    "justification": "超标说明（如无超标则为空字符串）",
    "ready_to_submit": true/false,
    "issues": ["问题1", "问题2"]
  }}
}}
```

## 重要原则
- 住宿标准：一线城市(北京/上海/广州/深圳) 600元/晚，二线城市(杭州/成都/武汉等) 400元/晚，其他 300元/晚
- 餐饮标准：统一 150元/天，不分城市
- 报销时限：出差结束后30个自然日内
- 超标需提供说明，由上级审批
- 回答简洁清晰，避免冗长，必要时分点列出
- 在中文对话环境中，保持中文回复
"""


# ─── 主对话函数 ─────────────────────────────────────────────────────────────

def chat(history: list[dict], user_message: str) -> tuple[str, dict | None]:
    """
    单轮对话。

    Args:
        history: 对话历史，格式 [{"role": "user"/"assistant", "content": "..."}]
        user_message: 用户本轮输入

    Returns:
        (assistant_reply, draft_or_None)
        draft 为 None 表示申请草稿尚未完成，否则返回草稿 dict
    """
    client = _get_client()
    messages = [{"role": "system", "content": _build_system_prompt()}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # 最多循环 3 次（处理工具调用链）
    for _ in range(3):
        response = client.chat.completions.create(
            model="qwen-plus",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=2000,
        )

        msg = response.choices[0].message

        # 有工具调用
        if msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                }
                for tc in msg.tool_calls
            ]})
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                tool_result = _execute_tool(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })
            continue

        # 普通文本回复
        reply = msg.content or ""
        draft = _extract_draft(reply)
        return reply, draft

    return "抱歉，处理超时，请重试。", None


def _extract_draft(text: str) -> dict | None:
    """从 LLM 回复中提取申请草稿 JSON。"""
    # 匹配 ```json ... ``` 代码块
    pattern = r'```json\s*(\{[\s\S]*?\})\s*```'
    matches = re.findall(pattern, text)
    for m in matches:
        try:
            data = json.loads(m)
            if "draft" in data:
                return data["draft"]
        except json.JSONDecodeError:
            continue

    # 也尝试裸 JSON
    try:
        idx = text.find('"draft"')
        if idx > 0:
            start = text.rfind('{', 0, idx)
            if start >= 0:
                data = json.loads(text[start:])
                if "draft" in data:
                    return data["draft"]
    except Exception:
        pass

    return None


def build_app_from_draft(draft: dict, app_id: str) -> dict:
    """将草稿 dict 转为 ApplicationStore 兼容的申请格式。"""
    items = draft.get("expense_items", [])
    total = draft.get("total_amount") or sum(i.get("amount", 0) for i in items)
    has_all_receipts = all(i.get("has_receipt", False) for i in items)

    # 计算 trip_days
    try:
        d1 = datetime.strptime(draft["trip_start"], "%Y-%m-%d").date()
        d2 = datetime.strptime(draft["trip_end"],   "%Y-%m-%d").date()
        trip_days = max(1, (d2 - d1).days + 1)
    except Exception:
        trip_days = draft.get("trip_days", 1)

    # 计算 submitted_days
    try:
        d2 = datetime.strptime(draft["trip_end"], "%Y-%m-%d").date()
        submitted_days = (date.today() - d2).days
    except Exception:
        submitted_days = 0

    return {
        "app_id":                    app_id,
        "applicant":                 draft.get("applicant", ""),
        "department":                draft.get("department", ""),
        "level":                     draft.get("level", "P4"),
        "destination":               draft.get("destination", ""),
        "trip_start":                draft.get("trip_start", ""),
        "trip_end":                  draft.get("trip_end", ""),
        "trip_days":                 trip_days,
        "purpose":                   draft.get("purpose", ""),
        "expense_items":             items,
        "total_amount":              total,
        "has_all_receipts":          has_all_receipts,
        "all_items_compliant":       len(draft.get("issues", [])) == 0,
        "within_limits":             len(draft.get("issues", [])) == 0,
        "justification":             draft.get("justification", ""),
        "submitted_days_after_trip": max(0, submitted_days),
    }
