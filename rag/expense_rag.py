"""
报销场景 RAG — Milvus Lite（本地文件）+ 千问 text-embedding-v3

首次调用 initialize() 将所有公司规定向量化并写入 Milvus。
生产环境：将 DB_PATH 改为 Milvus 服务地址，接口不变。
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.company_rules import RULES_CORPUS

# ─── 千问配置（与 crewai_qwen/main.py 保持一致）──────────────────────────────
QWEN_API_KEY  = "sk-0a2184255d00431eaf1684b6fae595c4"
QWEN_BASE_URL = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
EMBED_MODEL   = "text-embedding-v3"
EMBED_DIM     = 1024

# Milvus Lite 本地文件（无需启动服务器）
DB_PATH    = os.path.join(os.path.dirname(__file__), "expense_rules.db")
COLLECTION = "expense_rules"

_milvus_client = None


def _get_client():
    from pymilvus import MilvusClient
    global _milvus_client
    if _milvus_client is None:
        _milvus_client = MilvusClient(DB_PATH)
    return _milvus_client


def _embed(text: str) -> list:
    """调用千问 text-embedding-v3 将文本转为1024维向量（DashScope SDK）。"""
    import dashscope
    dashscope.api_key = QWEN_API_KEY
    from dashscope import TextEmbedding
    resp = TextEmbedding.call(model=EMBED_MODEL, input=[text])
    return resp.output["embeddings"][0]["embedding"]


def initialize(force: bool = False) -> None:
    """
    初始化 Milvus 向量库：将所有公司报销规定向量化并插入集合。
    幂等操作——集合已存在时跳过（force=True 时强制重建）。
    """
    client = _get_client()

    if client.has_collection(COLLECTION):
        if not force:
            return
        client.drop_collection(COLLECTION)

    print(f"  [RAG] 正在初始化 Milvus，向量化 {len(RULES_CORPUS)} 条公司规定...")

    client.create_collection(
        collection_name=COLLECTION,
        dimension=EMBED_DIM,
        metric_type="COSINE",
        enable_dynamic_field=True,
        auto_id=True,
    )

    data = []
    for rule in RULES_CORPUS:
        text = f"{rule['category']}：{rule['content']}"
        vec  = _embed(text)
        data.append({
            "vector":   vec,
            "rule_id":  rule["id"],
            "category": rule["category"],
            "content":  rule["content"],
        })

    client.insert(COLLECTION, data)
    print(f"  [RAG] ✓ 已向量化并插入 {len(data)} 条规定")


def _ensure_initialized():
    client = _get_client()
    if not client.has_collection(COLLECTION):
        initialize()


def search(query: str, top_k: int = 3) -> str:
    """
    语义检索公司报销规定，返回带规则编号的格式化文本供 Agent 引用。

    Args:
        query:  自然语言查询，如 "住宿费标准" / "自动审批条件"
        top_k:  返回最相关条数

    Returns:
        格式化规定文本，包含 rule_id 方便 Agent 引用
    """
    _ensure_initialized()
    client = _get_client()

    query_vec = _embed(query)
    results   = client.search(
        collection_name=COLLECTION,
        data=[query_vec],
        limit=top_k,
        output_fields=["rule_id", "category", "content"],
    )

    if not results or not results[0]:
        return "未在规定库中找到相关规定，建议人工核查。"

    parts = []
    for hit in results[0]:
        entity = hit.get("entity", hit)
        score  = round(hit.get("distance", 0), 3)
        parts.append(
            f"【{entity['category']}】(规则编号: {entity['rule_id']}，相关度: {score})\n"
            f"{entity['content']}"
        )
    return "\n\n".join(parts)


def search_with_ids(query: str, top_k: int = 3) -> list[dict]:
    """返回结构化列表，供需要规则编号的代码直接使用。"""
    _ensure_initialized()
    client = _get_client()

    query_vec = _embed(query)
    results   = client.search(
        collection_name=COLLECTION,
        data=[query_vec],
        limit=top_k,
        output_fields=["rule_id", "category", "content"],
    )

    if not results or not results[0]:
        return []

    hits = []
    for hit in results[0]:
        entity = hit.get("entity", hit)
        hits.append({
            "rule_id":  entity["rule_id"],
            "category": entity["category"],
            "content":  entity["content"],
            "score":    round(hit.get("distance", 0), 3),
        })
    return hits


def get_rule_by_id(rule_id: str) -> str:
    """精确按 rule_id 获取规定文本。"""
    for rule in RULES_CORPUS:
        if rule["id"] == rule_id:
            return f"【{rule['category']}】({rule['id']})\n{rule['content']}"
    return f"未找到规则 {rule_id}"


def get_all_rules_summary() -> str:
    """返回全部规定一览（供 Agent system prompt 使用）。"""
    lines = ["=== 公司差旅报销规定一览 ==="]
    for rule in RULES_CORPUS:
        brief = rule["content"][:60] + "..." if len(rule["content"]) > 60 else rule["content"]
        lines.append(f"• [{rule['id']}]【{rule['category']}】{brief}")
    return "\n".join(lines)
