"""
报销场景 RAG — 规则库
数据源: data/rules/rule_001.txt ~ rule_012.txt（txt 文件，非硬编码）
向量库: Milvus Lite + 千问 text-embedding-v3 (1024-dim)
"""

import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

QWEN_API_KEY = "sk-0a2184255d00431eaf1684b6fae595c4"
EMBED_MODEL  = "text-embedding-v3"
EMBED_DIM    = 1024

RULES_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "rules")
DB_PATH    = os.path.join(os.path.dirname(__file__), "expense_rules.db")
COLLECTION = "expense_rules"

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


def _load_rules_from_txt() -> list[dict]:
    """
    读取 data/rules/*.txt，解析为规则列表。
    格式: 首行 【rule_XXX 分类名称】，其余行为规则正文。
    """
    rules = []
    if not os.path.isdir(RULES_DIR):
        raise FileNotFoundError(f"规则目录不存在: {RULES_DIR}")

    for fname in sorted(os.listdir(RULES_DIR)):
        if not fname.endswith(".txt"):
            continue
        rule_id = fname.replace(".txt", "")          # rule_001
        fpath   = os.path.join(RULES_DIR, fname)
        with open(fpath, encoding="utf-8") as f:
            raw = f.read().strip()

        # 解析首行: 【rule_001 差旅日补贴】
        lines    = [l for l in raw.splitlines() if l.strip()]
        header   = lines[0] if lines else ""
        m        = re.match(r"【[^\s]+\s+(.+?)】", header)
        category = m.group(1) if m else rule_id
        content  = "\n".join(lines[1:]).strip() if len(lines) > 1 else raw

        rules.append({
            "id":       rule_id,
            "category": category,
            "content":  content,
            "source":   fpath,
        })

    return rules


# ─── 公开接口 ──────────────────────────────────────────────────────────────────

def initialize(force: bool = False) -> None:
    """
    从 data/rules/*.txt 读取规定，向量化后写入 Milvus。
    幂等：集合已存在时跳过（force=True 强制重建）。
    """
    client = _get_client()

    if client.has_collection(COLLECTION):
        if not force:
            return
        client.drop_collection(COLLECTION)

    rules = _load_rules_from_txt()
    print(f"  [RAG-规则库] 读取 {len(rules)} 条规定文件，开始向量化...")

    client.create_collection(
        collection_name=COLLECTION,
        dimension=EMBED_DIM,
        metric_type="COSINE",
        enable_dynamic_field=True,
        auto_id=True,
    )

    data = []
    for rule in rules:
        text = f"{rule['category']}：{rule['content']}"
        vec  = _embed(text)
        data.append({
            "vector":   vec,
            "rule_id":  rule["id"],
            "category": rule["category"],
            "content":  rule["content"],
        })

    client.insert(COLLECTION, data)
    print(f"  [RAG-规则库] ✓ {len(data)} 条规定已向量化入库（{COLLECTION}）")


def _ensure_initialized():
    client = _get_client()
    if not client.has_collection(COLLECTION):
        initialize()


def search(query: str, top_k: int = 3) -> str:
    """语义检索规定，返回带规则编号的格式化文本。"""
    _ensure_initialized()
    client    = _get_client()
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
    client    = _get_client()
    query_vec = _embed(query)
    results   = client.search(
        collection_name=COLLECTION,
        data=[query_vec],
        limit=top_k,
        output_fields=["rule_id", "category", "content"],
    )

    if not results or not results[0]:
        return []

    return [
        {
            "rule_id":  hit.get("entity", hit)["rule_id"],
            "category": hit.get("entity", hit)["category"],
            "content":  hit.get("entity", hit)["content"],
            "score":    round(hit.get("distance", 0), 3),
        }
        for hit in results[0]
    ]


def get_rule_by_id(rule_id: str) -> str:
    """精确按 rule_id 读取对应 txt 文件，返回规定全文。"""
    fpath = os.path.join(RULES_DIR, f"{rule_id}.txt")
    if not os.path.isfile(fpath):
        return f"未找到规则文件: {rule_id}.txt"
    with open(fpath, encoding="utf-8") as f:
        raw = f.read().strip()
    lines    = [l for l in raw.splitlines() if l.strip()]
    header   = lines[0] if lines else ""
    m        = re.match(r"【[^\s]+\s+(.+?)】", header)
    category = m.group(1) if m else rule_id
    content  = "\n".join(lines[1:]).strip() if len(lines) > 1 else raw
    return f"【{category}】({rule_id})\n{content}"


def get_all_rules_summary() -> str:
    """返回全部规定一览（供 Agent system prompt 使用）。"""
    rules = _load_rules_from_txt()
    lines = ["=== 公司差旅报销规定一览（来源: data/rules/*.txt）==="]
    for rule in rules:
        brief = rule["content"][:60] + "..." if len(rule["content"]) > 60 else rule["content"]
        lines.append(f"• [{rule['id']}]【{rule['category']}】{brief}")
    return "\n".join(lines)
