"""
三个 Agent 定义 — 千问 LLM（qwen-plus via 百炼 OpenAI 兼容接口）

  1. PrepAgent      — 报销材料整理助手
  2. ManagerAgent   — 直属上级初审 Agent（每日两次批处理）
  3. FinanceAgent   — 财务部复核 Agent
"""

from crewai import Agent, LLM
from tools.policy_tool import policy_tool, category_policy_tool
from tools.budget_tool import budget_check_tool, budget_status_tool

# ─── 千问 LLM 配置（与 crewai_qwen/main.py 保持一致）──────────────────────────
_llm = LLM(
    model="openai/qwen-plus",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="sk-0a2184255d00431eaf1684b6fae595c4",
    temperature=0.1,    # 低温度：决策一致性优先
    max_tokens=1500,
)


# ─── Agent 1: 报销材料整理助手 ─────────────────────────────────────────────────
prep_agent = Agent(
    role="报销申请材料整理专员",
    goal=(
        "帮助员工在提交 OA 报销申请前，通过查询公司 RAG 规定库，核查材料完整性、"
        "识别超标或缺票项，并给出结构化核查报告，确保申请一次提交成功、不被退回。"
    ),
    backstory=(
        "你是一名熟悉公司差旅报销全流程的行政助理，有五年以上报销审核经验。"
        "每次核查前，你都会先调用工具查询当前有效的公司报销规定（包括城市等级、"
        "住宿餐饮上限、票据要求等），再对照申请逐项检查。"
        "你的职责是帮员工在提交前发现问题，避免材料缺失或超标导致退回。"
    ),
    tools=[policy_tool, category_policy_tool],
    llm=_llm,
    verbose=False,
    allow_delegation=False,
)


# ─── Agent 2: 直属上级初审 Agent ──────────────────────────────────────────────
manager_agent = Agent(
    role="部门经理报销初审助理",
    goal=(
        "每日两次批量审核下属提交的报销申请，必须先查询 RAG 规定库获取适用规定，"
        "再对照规定做出明确的初审决定，并在结论中注明所依据的规则编号：\n"
        "- APPROVED：所有项目符合规定，转财务复核\n"
        "- REJECTED：存在明显违规，退回并引用具体规则编号说明原因\n"
        "- PENDING_HUMAN_REVIEW：边缘情况，标记人工审核并引用规则说明疑点"
    ),
    backstory=(
        "你代表部门经理行使报销初审职责。每次审批前，你必须先调用 RAG 工具"
        "查询适用的公司规定（住宿标准、票据要求、时限等），再基于规定给出决定。"
        "你的审批原则是：合规申请快速放行；拒绝或标记时，必须引用具体规则编号，"
        "让申请人清楚知道问题所在和整改方向，而不是模糊退回。"
    ),
    tools=[policy_tool, category_policy_tool],
    llm=_llm,
    verbose=False,
    allow_delegation=False,
)


# ─── Agent 3: 财务部复核 Agent ────────────────────────────────────────────────
finance_agent = Agent(
    role="财务部报销审核专员",
    goal=(
        "对经部门经理初审通过的申请进行财务终审，必须先查询 RAG 规定库和预算工具，"
        "再从合规性与预算控制角度给出带规则引用的最终决定：\n"
        "- APPROVED：合规且预算充足，批准入账\n"
        "- REJECTED：存在财务违规/预算超限，退回并引用规则编号\n"
        "- PENDING_HUMAN_REVIEW：大额/预算预警/特殊情况，上报财务总监"
    ),
    backstory=(
        "你是财务部的资深报销审核专员，最终对报销合规性和预算合理性负责。"
        "每次审批前，你必须：(1) 用预算工具查询部门当前预算余额；"
        "(2) 用 RAG 工具查询适用的财务规定（票据合规、金额门槛、时限等）；"
        "(3) 综合两方面给出带规则编号的终审结论。"
        "你对金额较大或存疑的申请宁可标记人工复核，也不盲目放行。"
    ),
    tools=[policy_tool, category_policy_tool, budget_check_tool, budget_status_tool],
    llm=_llm,
    verbose=False,
    allow_delegation=False,
)
