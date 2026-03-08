"""
企业报销审批智能体 — 人机交互 POC UI
运行方式: streamlit run ui.py
"""

import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

from data.mock_applications import generate_mock_applications
from state.store import (
    ApplicationStore,
    STATUS_PENDING_AUTO, STATUS_AUTO_APPROVED, STATUS_PENDING_MANAGER,
    STATUS_PENDING_FINANCE, STATUS_PENDING_HUMAN, STATUS_APPROVED, STATUS_REJECTED,
)
from pipeline import run_auto_approve_batch, process_manager_review, process_finance_review

# ─── 页面配置 ─────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="报销审批智能体 POC",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

STATUS_LABELS = {
    STATUS_AUTO_APPROVED:   "✅ 自动通过",
    STATUS_APPROVED:        "✅ 审批通过",
    STATUS_PENDING_MANAGER: "⏳ 经理初审",
    STATUS_PENDING_FINANCE: "⏳ 财务终审",
    STATUS_PENDING_HUMAN:   "🔍 待人工审核",
    STATUS_REJECTED:        "❌ 已拒绝",
    STATUS_PENDING_AUTO:    "⏳ 待自动审核",
}

STATUS_COLORS = {
    STATUS_AUTO_APPROVED:   "#00C853",
    STATUS_APPROVED:        "#1976D2",
    STATUS_PENDING_MANAGER: "#FF9800",
    STATUS_PENDING_FINANCE: "#FF9800",
    STATUS_PENDING_HUMAN:   "#9C27B0",
    STATUS_REJECTED:        "#F44336",
    STATUS_PENDING_AUTO:    "#9E9E9E",
}

# ─── Session 状态初始化 ────────────────────────────────────────────────────────

def _init_store():
    """初始化 ApplicationStore 并运行自动审批，结果存入 session_state。"""
    apps = generate_mock_applications()
    store = ApplicationStore()
    store.load_applications(apps)
    run_auto_approve_batch(store)
    st.session_state["store"] = store
    st.session_state["apps_map"] = {a["app_id"]: a for a in apps}

if "store" not in st.session_state:
    _init_store()

store: ApplicationStore = st.session_state["store"]
apps_map: dict = st.session_state["apps_map"]

# ─── 工具函数 ──────────────────────────────────────────────────────────────────

def get_stats():
    return store.stats()

def all_records():
    return store.all()

def status_badge(status: str) -> str:
    return STATUS_LABELS.get(status, status)

def amount_color(amount: float) -> str:
    if amount < 1000:
        return "green"
    elif amount < 3000:
        return "orange"
    return "red"

# ─── 侧边栏 ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🏦 报销审批智能体")
    st.markdown("**CrewAI + 千问 + Milvus RAG**")
    st.divider()

    page = st.radio(
        "导航",
        ["📊 仪表板", "📋 申请列表", "🔍 人工审核", "🤖 触发 Agent", "🔎 RAG 搜索"],
        label_visibility="collapsed",
    )

    st.divider()
    stats = get_stats()
    total = sum(stats.values())
    approved = stats.get(STATUS_AUTO_APPROVED, 0) + stats.get(STATUS_APPROVED, 0)
    human = stats.get(STATUS_PENDING_HUMAN, 0)
    rejected = stats.get(STATUS_REJECTED, 0)

    st.metric("总申请数", total)
    st.metric("已通过", approved, delta=f"{approved/total*100:.0f}%")
    st.metric("待人工审核", human)
    st.metric("已拒绝", rejected)

    st.divider()
    if st.button("🔄 重置演示数据", use_container_width=True):
        _init_store()
        st.rerun()

# ─── 仪表板 ───────────────────────────────────────────────────────────────────

if page == "📊 仪表板":
    st.title("📊 企业报销审批 — 实时仪表板")
    st.caption("CrewAI + 千问 qwen-plus + Milvus RAG  |  POC 演示系统")

    # KPI 行
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("总申请", total, help="本批次全部申请数")
    col2.metric("✅ 自动通过", stats.get(STATUS_AUTO_APPROVED, 0),
                help="规则引擎自动批准，无 LLM")
    col3.metric("✅ Agent 通过", stats.get(STATUS_APPROVED, 0),
                help="经理 + 财务 Agent 审批通过")
    col4.metric("🔍 待人工", stats.get(STATUS_PENDING_HUMAN, 0),
                help="Agent 标记，需人工复核")
    col5.metric("❌ 拒绝", stats.get(STATUS_REJECTED, 0),
                help="违规退回")

    st.divider()

    col_left, col_right = st.columns([1, 1])

    # 状态分布饼图
    with col_left:
        st.subheader("申请状态分布")
        labels = [STATUS_LABELS.get(s, s) for s in stats.keys()]
        values = list(stats.values())
        colors = [STATUS_COLORS.get(s, "#9E9E9E") for s in stats.keys()]
        fig = go.Figure(data=[go.Pie(
            labels=labels, values=values,
            marker_colors=colors,
            hole=0.4,
            textinfo="label+percent+value",
        )])
        fig.update_layout(height=350, showlegend=False, margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    # 审批漏斗图
    with col_right:
        st.subheader("三级审批漏斗")
        funnel_stages = [
            ("100 条申请提交", total),
            ("规则引擎预筛", total),
            ("自动通过 (EXP0001~0080)", stats.get(STATUS_AUTO_APPROVED, 0)),
            ("进入 Agent 流程", total - stats.get(STATUS_AUTO_APPROVED, 0)),
            ("Agent 审批通过", stats.get(STATUS_APPROVED, 0)),
            ("人工复核", stats.get(STATUS_PENDING_HUMAN, 0)),
        ]
        fig2 = go.Figure(go.Funnel(
            y=[s[0] for s in funnel_stages],
            x=[s[1] for s in funnel_stages],
            textinfo="value+percent initial",
            marker_color=["#1565C0", "#1976D2", "#00C853", "#FF9800", "#2E7D32", "#9C27B0"],
        ))
        fig2.update_layout(height=350, margin=dict(t=10, b=10))
        st.plotly_chart(fig2, use_container_width=True)

    st.divider()

    # 金额分布直方图
    st.subheader("申请金额分布")
    amounts = [r.get("total_amount", 0) for r in all_records()]
    statuses_list = [r.get("status", "") for r in all_records()]
    df_hist = pd.DataFrame({"金额(元)": amounts, "状态": [STATUS_LABELS.get(s, s) for s in statuses_list]})
    color_map = {STATUS_LABELS.get(s, s): STATUS_COLORS.get(s, "#9E9E9E") for s in STATUS_COLORS}
    fig3 = px.histogram(df_hist, x="金额(元)", color="状态", nbins=30,
                        color_discrete_map=color_map, height=280)
    fig3.add_vline(x=1000, line_dash="dash", line_color="red",
                   annotation_text="自动审批上限 1000元")
    fig3.update_layout(margin=dict(t=10, b=10))
    st.plotly_chart(fig3, use_container_width=True)

    # Agent 流水线说明
    st.divider()
    st.subheader("三级智能体审批流水线")
    pipe_cols = st.columns(4)
    pipe_data = [
        ("🔧 规则引擎预审", "自动合规核查", "纯 Python，毫秒级", "#E3F2FD"),
        ("🤖 报销材料预审助手", "Agent 1 — 材料完整性审查", "CrewAI + 千问", "#E8F5E9"),
        ("👔 部门经理初审智能体", "Agent 2 — 依规初审 + 规则引用", "CrewAI + Milvus RAG", "#FFF8E1"),
        ("💼 财务审批智能体", "Agent 3 — 终审 + 预算核查", "CrewAI + RAG + 预算库", "#FCE4EC"),
    ]
    for col, (name, role, tech, bg) in zip(pipe_cols, pipe_data):
        col.markdown(
            f"""<div style="background:{bg};padding:12px;border-radius:8px;text-align:center">
            <b>{name}</b><br/><small>{role}</small><br/>
            <code style="font-size:10px">{tech}</code></div>""",
            unsafe_allow_html=True,
        )

# ─── 申请列表 ──────────────────────────────────────────────────────────────────

elif page == "📋 申请列表":
    st.title("📋 全部申请列表")

    # 筛选
    col_f1, col_f2, col_f3 = st.columns([2, 2, 2])
    with col_f1:
        filter_status = st.multiselect(
            "按状态筛选",
            options=list(STATUS_LABELS.keys()),
            format_func=lambda s: STATUS_LABELS[s],
            default=[],
        )
    with col_f2:
        filter_dept = st.multiselect(
            "按部门筛选",
            options=sorted(set(r.get("department", "") for r in all_records())),
            default=[],
        )
    with col_f3:
        filter_amount = st.slider("金额范围 (元)", 0, 10000, (0, 10000), step=100)

    records = all_records()
    if filter_status:
        records = [r for r in records if r["status"] in filter_status]
    if filter_dept:
        records = [r for r in records if r.get("department", "") in filter_dept]
    records = [r for r in records
               if filter_amount[0] <= r.get("total_amount", 0) <= filter_amount[1]]

    st.caption(f"显示 {len(records)} 条")

    # 表格
    rows = []
    for r in sorted(records, key=lambda x: x["app_id"]):
        rows.append({
            "申请编号": r["app_id"],
            "申请人": r.get("applicant", ""),
            "部门": r.get("department", ""),
            "目的地": r.get("destination", ""),
            "金额(元)": r.get("total_amount", 0),
            "状态": STATUS_LABELS.get(r["status"], r["status"]),
            "票据完整": "✓" if r.get("has_all_receipts") else "✗",
            "各项合规": "✓" if r.get("all_items_compliant") else "✗",
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        height=520,
        column_config={
            "金额(元)": st.column_config.NumberColumn(format="¥%.0f"),
        },
        hide_index=True,
    )

    # 详情查看
    st.divider()
    st.subheader("📄 申请详情")
    app_ids = [r["app_id"] for r in sorted(records, key=lambda x: x["app_id"])]
    selected_id = st.selectbox("选择申请编号", app_ids)

    if selected_id:
        record = store.get(selected_id)
        orig = apps_map.get(selected_id, {})

        col_a, col_b = st.columns([1, 1])
        with col_a:
            st.markdown(f"**申请编号**: `{record['app_id']}`")
            st.markdown(f"**申请人**: {record.get('applicant', '')} ({record.get('department', '')})")
            st.markdown(f"**目的地**: {record.get('destination', '')}  |  **出差天数**: {record.get('trip_days', '')} 天")
            st.markdown(f"**出差目的**: {record.get('purpose', '')}")
            st.markdown(f"**提交天数**: {record.get('submitted_days_after_trip', '')} 天")

        with col_b:
            status = record["status"]
            st.markdown(f"**当前状态**: {STATUS_LABELS.get(status, status)}")
            st.markdown(f"**合计金额**: ¥{record.get('total_amount', 0):.0f}")
            st.markdown(f"**票据完整**: {'✅' if record.get('has_all_receipts') else '❌'}")
            st.markdown(f"**各项合规**: {'✅' if record.get('all_items_compliant') else '❌'}")

        # 费用明细
        st.markdown("**费用明细**")
        items = orig.get("expense_items", record.get("expense_items", []))
        if items:
            df_items = pd.DataFrame(items)
            df_items["has_receipt"] = df_items["has_receipt"].map({True: "✓", False: "✗"})
            df_items.columns = ["费用类别", "金额(元)", "有发票", "备注"] if len(df_items.columns) == 4 else df_items.columns
            st.dataframe(df_items, use_container_width=True, hide_index=True)

        # 审批历史
        history = record.get("history", [])
        if history:
            st.markdown("**审批历史**")
            df_hist = pd.DataFrame(history)[["timestamp", "actor", "from", "to", "reason"]]
            df_hist.columns = ["时间", "操作者", "原状态", "新状态", "原因"]
            df_hist["原状态"] = df_hist["原状态"].map(lambda s: STATUS_LABELS.get(s, s))
            df_hist["新状态"] = df_hist["新状态"].map(lambda s: STATUS_LABELS.get(s, s))
            st.dataframe(df_hist, use_container_width=True, hide_index=True)

        # Agent 决定
        manager_dec = record.get("manager_decision")
        finance_dec = record.get("finance_decision")
        if manager_dec or finance_dec:
            st.markdown("**Agent 决定**")
            if manager_dec:
                with st.expander("👔 部门经理初审决定"):
                    st.markdown(f"**决定**: `{manager_dec.get('decision', '')}`")
                    st.markdown(f"**理由**: {manager_dec.get('reason', '')}")
            if finance_dec:
                with st.expander("💼 财务审批智能体决定"):
                    st.markdown(f"**决定**: `{finance_dec.get('decision', '')}`")
                    st.markdown(f"**理由**: {finance_dec.get('reason', '')}")

# ─── 人工审核 ──────────────────────────────────────────────────────────────────

elif page == "🔍 人工审核":
    st.title("🔍 人工审核队列")
    st.info("以下申请已由 Agent 标记为需要人工复核，请审阅后做出最终决定。")

    pending_human = store.get_by_status(STATUS_PENDING_HUMAN)

    if not pending_human:
        st.success("✅ 当前无待人工审核的申请。")
    else:
        st.markdown(f"**待审核数量**: {len(pending_human)} 条")
        st.divider()

        for record in pending_human:
            app_id = record["app_id"]
            orig = apps_map.get(app_id, {})

            with st.expander(
                f"📄 {app_id} — {record.get('applicant', '')} ({record.get('department', '')}) "
                f"— ¥{record.get('total_amount', 0):.0f}",
                expanded=True,
            ):
                col1, col2, col3 = st.columns([2, 2, 2])
                with col1:
                    st.markdown(f"**目的地**: {record.get('destination', '')}")
                    st.markdown(f"**出差天数**: {record.get('trip_days', '')} 天")
                    st.markdown(f"**出差目的**: {record.get('purpose', '')}")
                with col2:
                    st.markdown(f"**票据完整**: {'✅' if record.get('has_all_receipts') else '❌'}")
                    st.markdown(f"**各项合规**: {'✅' if record.get('all_items_compliant') else '❌'}")
                    st.markdown(f"**提交天数**: {record.get('submitted_days_after_trip', '')} 天")
                with col3:
                    # Agent 标记理由
                    history = record.get("history", [])
                    flag_reason = ""
                    for h in reversed(history):
                        if h.get("to") == STATUS_PENDING_HUMAN:
                            flag_reason = h.get("reason", "")
                            break
                    st.markdown(f"**Agent 标记理由**:")
                    st.warning(flag_reason or "（未记录）")

                # 费用明细
                items = orig.get("expense_items", record.get("expense_items", []))
                if items:
                    df_items = pd.DataFrame(items)
                    st.dataframe(df_items, use_container_width=True, hide_index=True, height=130)

                # Agent 决定
                manager_dec = record.get("manager_decision")
                finance_dec = record.get("finance_decision")
                if manager_dec:
                    st.markdown(f"👔 **经理 Agent**: {manager_dec.get('decision', '')} — {manager_dec.get('reason', '')[:120]}")
                if finance_dec:
                    st.markdown(f"💼 **财务 Agent**: {finance_dec.get('decision', '')} — {finance_dec.get('reason', '')[:120]}")

                st.divider()
                # 人工操作
                hr_col1, hr_col2, hr_col3 = st.columns([3, 1, 1])
                with hr_col1:
                    reason_input = st.text_input(
                        "审核意见（必填）",
                        key=f"reason_{app_id}",
                        placeholder="请填写批准或拒绝原因...",
                    )
                with hr_col2:
                    if st.button("✅ 批准", key=f"approve_{app_id}", use_container_width=True, type="primary"):
                        if reason_input.strip():
                            store.update_status(
                                app_id, STATUS_APPROVED,
                                actor="human_reviewer",
                                reason=reason_input.strip(),
                            )
                            st.success(f"{app_id} 已批准！")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error("请填写审核意见")
                with hr_col3:
                    if st.button("❌ 拒绝", key=f"reject_{app_id}", use_container_width=True):
                        if reason_input.strip():
                            store.update_status(
                                app_id, STATUS_REJECTED,
                                actor="human_reviewer",
                                reason=reason_input.strip(),
                            )
                            st.warning(f"{app_id} 已拒绝。")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error("请填写审核意见")

# ─── 触发 Agent ────────────────────────────────────────────────────────────────

elif page == "🤖 触发 Agent":
    st.title("🤖 手动触发 Agent 审批")
    st.info("选择申请，手动触发经理/财务 Agent 进行审批（调用真实 LLM）。")

    tab1, tab2, tab3 = st.tabs(["经理初审 Agent", "财务终审 Agent", "批量运行"])

    with tab1:
        st.subheader("👔 部门经理初审智能体")
        pending_mgr = store.get_by_status(STATUS_PENDING_MANAGER)
        if not pending_mgr:
            st.success("无待经理审批的申请。")
        else:
            mgr_ids = [r["app_id"] for r in pending_mgr]
            sel_mgr = st.selectbox("选择申请", mgr_ids, key="sel_mgr")
            record = store.get(sel_mgr)
            orig = apps_map.get(sel_mgr, {})
            if record:
                col_m1, col_m2 = st.columns(2)
                col_m1.markdown(f"**申请人**: {record.get('applicant', '')} ({record.get('department', '')})")
                col_m1.markdown(f"**目的地**: {record.get('destination', '')}，{record.get('trip_days', '')} 天")
                col_m2.markdown(f"**合计**: ¥{record.get('total_amount', 0):.0f}")
                col_m2.markdown(f"**票据**: {'✅' if record.get('has_all_receipts') else '❌'}  合规: {'✅' if record.get('all_items_compliant') else '❌'}")

            if st.button("🚀 启动经理 Agent", type="primary", use_container_width=True):
                app_data = dict(orig)
                app_data.update({k: v for k, v in record.items() if k not in app_data})
                with st.spinner("经理 Agent 审批中（调用千问 LLM）..."):
                    result = process_manager_review(app_data, store)
                st.success(f"审批完成: **{result}**")
                new_rec = store.get(sel_mgr)
                if new_rec:
                    dec = new_rec.get("manager_decision", {})
                    if dec:
                        st.markdown(f"**Agent 理由**: {dec.get('reason', '')}")
                st.rerun()

    with tab2:
        st.subheader("💼 财务审批智能体")
        pending_fin = store.get_by_status(STATUS_PENDING_FINANCE)
        if not pending_fin:
            st.success("无待财务审批的申请。")
        else:
            fin_ids = [r["app_id"] for r in pending_fin]
            sel_fin = st.selectbox("选择申请", fin_ids, key="sel_fin")
            record = store.get(sel_fin)
            orig = apps_map.get(sel_fin, {})
            if record:
                col_f1, col_f2 = st.columns(2)
                col_f1.markdown(f"**申请人**: {record.get('applicant', '')} ({record.get('department', '')})")
                col_f1.markdown(f"**目的地**: {record.get('destination', '')}，{record.get('trip_days', '')} 天")
                col_f2.markdown(f"**合计**: ¥{record.get('total_amount', 0):.0f}")
                manager_dec = record.get("manager_decision")
                if manager_dec:
                    col_f2.markdown(f"**经理决定**: {manager_dec.get('decision', '')}")

            if st.button("🚀 启动财务 Agent", type="primary", use_container_width=True):
                app_data = dict(orig)
                app_data.update({k: v for k, v in record.items() if k not in app_data})
                manager_dec = record.get("manager_decision")
                with st.spinner("财务 Agent 审批中（调用千问 LLM + Milvus RAG）..."):
                    result = process_finance_review(app_data, store, manager_dec)
                st.success(f"审批完成: **{result}**")
                new_rec = store.get(sel_fin)
                if new_rec:
                    dec = new_rec.get("finance_decision", {})
                    if dec:
                        st.markdown(f"**Agent 理由**: {dec.get('reason', '')}")
                st.rerun()

    with tab3:
        st.subheader("批量运行 Agent")
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            n_mgr = len(store.get_by_status(STATUS_PENDING_MANAGER))
            st.metric("待经理审批", n_mgr)
            max_mgr = st.number_input("最多处理条数", 1, max(n_mgr, 1), min(3, max(n_mgr, 1)), key="max_mgr")
            if st.button("批量运行经理 Agent", disabled=n_mgr == 0, use_container_width=True):
                progress = st.progress(0, text="经理 Agent 批量审批中...")
                pending = store.get_by_status(STATUS_PENDING_MANAGER)[:int(max_mgr)]
                for i, rec in enumerate(pending):
                    orig = apps_map.get(rec["app_id"], {})
                    app_data = dict(orig)
                    app_data.update({k: v for k, v in rec.items() if k not in app_data})
                    process_manager_review(app_data, store)
                    progress.progress((i + 1) / len(pending), text=f"已处理 {i+1}/{len(pending)} 条")
                st.success("批量经理审批完成")
                st.rerun()

        with col_b2:
            n_fin = len(store.get_by_status(STATUS_PENDING_FINANCE))
            st.metric("待财务审批", n_fin)
            max_fin = st.number_input("最多处理条数", 1, max(n_fin, 1), min(3, max(n_fin, 1)), key="max_fin")
            if st.button("批量运行财务 Agent", disabled=n_fin == 0, use_container_width=True):
                progress = st.progress(0, text="财务 Agent 批量审批中...")
                pending = store.get_by_status(STATUS_PENDING_FINANCE)[:int(max_fin)]
                for i, rec in enumerate(pending):
                    orig = apps_map.get(rec["app_id"], {})
                    app_data = dict(orig)
                    app_data.update({k: v for k, v in rec.items() if k not in app_data})
                    manager_dec = rec.get("manager_decision")
                    process_finance_review(app_data, store, manager_dec)
                    progress.progress((i + 1) / len(pending), text=f"已处理 {i+1}/{len(pending)} 条")
                st.success("批量财务审批完成")
                st.rerun()

# ─── RAG 搜索 ──────────────────────────────────────────────────────────────────

elif page == "🔎 RAG 搜索":
    st.title("🔎 语义搜索")

    tab_rules, tab_apps = st.tabs(["📖 规则库检索", "🗂 历史申请检索"])

    with tab_rules:
        st.subheader("规则库语义搜索")
        st.caption("从 12 条公司报销规定中语义检索最相关条目")
        query_rule = st.text_input(
            "搜索关键词",
            placeholder="例如：住宿费超标、餐饮限额、票据要求...",
            key="query_rule",
        )
        top_k_rule = st.slider("返回条数", 1, 6, 3, key="topk_rule")

        if st.button("🔍 搜索规则", type="primary") and query_rule.strip():
            with st.spinner("向量检索中..."):
                from rag.expense_rag import search as rag_search
                result = rag_search(query_rule.strip(), top_k=top_k_rule)
            st.markdown("**检索结果**")
            st.markdown(result)

        st.divider()
        if st.button("📋 查看全部规则"):
            from rag.expense_rag import get_all_rules_summary
            st.text(get_all_rules_summary())

    with tab_apps:
        st.subheader("历史申请语义搜索")
        st.caption("从 100 条历史申请中语义检索最相似案例（expense_applications 集合）")
        query_app = st.text_input(
            "搜索场景",
            placeholder="例如：客户指定酒店超标住宿、餐饮费无发票、跨月报销...",
            key="query_app",
        )
        top_k_app = st.slider("返回条数", 1, 10, 5, key="topk_app")

        if st.button("🔍 搜索历史申请", type="primary") and query_app.strip():
            with st.spinner("向量检索中（Milvus expense_applications）..."):
                from rag.application_rag import search_similar
                result = search_similar(query_app.strip(), top_k=top_k_app)
            st.markdown("**最相似历史申请**")
            for chunk in result.split("\n\n---\n"):
                if chunk.strip():
                    st.info(chunk.strip())
