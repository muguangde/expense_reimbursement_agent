"""
报销申请历史 RAG — 申请库
数据源: data/applications/EXP*.txt（100条 mock 申请 txt 文件）
向量库: Milvus Lite，集合 expense_applications
用途:
  - 将每条申请向量化入库，支持语义搜索历史相似申请
  - 审批完成后可将决定写回 Milvus，建立历史案例库
"""

import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

QWEN_API_KEY   = "sk-0a2184255d00431eaf1684b6fae595c4"
EMBED_MODEL    = "text-embedding-v3"
EMBED_DIM      = 1024

APPS_DIR    = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "applications")
DB_PATH     = os.path.join(os.path.dirname(__file__), "expense_rules.db")   # 复用同一 DB 文件
COLLECTION  = "expense_applications"

_milvus_client = None


# ─── 内部工具 ──────────────────────────────────────────────────────────────────

def _get_client():
    from pymilvus import MilvusClient
    global _milvus_client
    if _milvus_client is None:
        _milvus_client = MilvusClient(DB_PATH)
    return _milvus_client


def _embed(text: str) -> list:
    import dashscope
    dashscope.api_key = QWEN_API_KEY
    from dashscope import TextEmbedding
    resp = TextEmbedding.call(model=EMBED_MODEL, input=[text])
    return resp.output["embeddings"][0]["embedding"]


def _parse_app_txt(text: str) -> dict:
    """
    将 txt 文件内容解析回结构化字段（用于 Milvus 存储元数据）。
    """
    def _field(key: str) -> str:
        m = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    return {
        "app_id":     _field("申请编号"),
        "applicant":  _field("申请人"),
        "department": _field("部门"),
        "destination":_field("目的地"),
        "total":      _field("合计").replace("元", ""),
        "outcome":    _field("预期结果"),
    }


def load_applications_from_txt() -> list[dict]:
    """
    读取 data/applications/EXP*.txt，返回 (content_str, meta) 列表。
    content_str 用于 embedding，meta 存入 Milvus 动态字段。
    """
    if not os.path.isdir(APPS_DIR):
        raise FileNotFoundError(f"申请目录不存在: {APPS_DIR}")

    apps = []
    for fname in sorted(os.listdir(APPS_DIR)):
        if not fname.endswith(".txt"):
            continue
        fpath = os.path.join(APPS_DIR, fname)
        with open(fpath, encoding="utf-8") as f:
            content = f.read().strip()
        meta = _parse_app_txt(content)
        apps.append({"content": content, "meta": meta, "source": fpath})
    return apps


# ─── 公开接口 ──────────────────────────────────────────────────────────────────

def initialize(force: bool = False) -> None:
    """
    从 data/applications/*.txt 读取申请，向量化后写入 Milvus。
    幂等：集合已存在时跳过（force=True 强制重建）。
    """
    client = _get_client()

    if client.has_collection(COLLECTION):
        if not force:
            print(f"  [RAG-申请库] 集合已存在，跳过初始化（传 force=True 重建）")
            return
        client.drop_collection(COLLECTION)

    apps = load_applications_from_txt()
    print(f"  [RAG-申请库] 读取 {len(apps)} 条申请 txt，开始向量化...")

    client.create_collection(
        collection_name=COLLECTION,
        dimension=EMBED_DIM,
        metric_type="COSINE",
        enable_dynamic_field=True,
        auto_id=True,
    )

    data     = []
    batch_sz = 10    # 每批10条，避免 API 限速
    for i, app in enumerate(apps):
        vec = _embed(app["content"])
        row = {
            "vector":     vec,
            "app_id":     app["meta"].get("app_id", ""),
            "applicant":  app["meta"].get("applicant", ""),
            "department": app["meta"].get("department", ""),
            "destination":app["meta"].get("destination", ""),
            "total":      app["meta"].get("total", ""),
            "outcome":    app["meta"].get("outcome", ""),
            "content":    app["content"][:500],   # 截断防超长
        }
        data.append(row)
        if (i + 1) % batch_sz == 0:
            client.insert(COLLECTION, data)
            print(f"  [RAG-申请库] 已插入 {i+1}/{len(apps)} 条...")
            data = []

    if data:
        client.insert(COLLECTION, data)

    print(f"  [RAG-申请库] ✓ {len(apps)} 条申请已向量化入库（{COLLECTION}）")


def search_similar(query: str, top_k: int = 5) -> str:
    """
    语义搜索历史相似申请，返回格式化文本。
    用法: search_similar("住宿费超标但有客户指定说明")
    """
    client    = _get_client()
    if not client.has_collection(COLLECTION):
        return "申请库尚未初始化，请先调用 initialize()。"

    query_vec = _embed(query)
    results   = client.search(
        collection_name=COLLECTION,
        data=[query_vec],
        limit=top_k,
        output_fields=["app_id", "applicant", "department", "total", "outcome", "content"],
    )

    if not results or not results[0]:
        return "未找到相似历史申请。"

    parts = []
    for hit in results[0]:
        entity = hit.get("entity", hit)
        score  = round(hit.get("distance", 0), 3)
        parts.append(
            f"[{entity.get('app_id','')}] {entity.get('applicant','')} "
            f"({entity.get('department','')}) {entity.get('total','')}元  "
            f"预期结果: {entity.get('outcome','')}  相似度: {score}\n"
            f"{entity.get('content','')[:200]}..."
        )
    return "\n\n---\n".join(parts)


def upsert_decision(app_id: str, decision: str, reason: str) -> None:
    """
    审批完成后，将最终决定写入 Milvus 对应申请记录（通过 content 更新）。
    实现简化：追加决定文本后重新 embed 并插入新记录。
    """
    client = _get_client()
    if not client.has_collection(COLLECTION):
        return

    # 读取对应 txt 文件内容
    fpath = os.path.join(APPS_DIR, f"{app_id}.txt")
    if not os.path.isfile(fpath):
        return

    with open(fpath, encoding="utf-8") as f:
        content = f.read().strip()

    updated_content = content + f"\n\n审批决定: {decision}\n审批理由: {reason}"
    vec = _embed(updated_content)
    meta = _parse_app_txt(content)

    client.insert(COLLECTION, [{
        "vector":     vec,
        "app_id":     app_id + "_decided",
        "applicant":  meta.get("applicant", ""),
        "department": meta.get("department", ""),
        "destination":meta.get("destination", ""),
        "total":      meta.get("total", ""),
        "outcome":    decision,
        "content":    updated_content[:500],
    }])
