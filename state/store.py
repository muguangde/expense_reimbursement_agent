"""
申请状态管理 — 内存存储 + JSON 持久化（PoC）

状态流转:
  PENDING_AUTO_CHECK
    ├── AUTO_APPROVED        (金额<1000 且完全合规)
    └── PENDING_MANAGER      (需走审批流程)
          ├── REJECTED            (经理直接拒绝)
          ├── PENDING_HUMAN_REVIEW (标记人工)
          └── PENDING_FINANCE
                ├── APPROVED
                ├── REJECTED
                └── PENDING_HUMAN_REVIEW
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

STATUS_PENDING_AUTO    = "PENDING_AUTO_CHECK"
STATUS_AUTO_APPROVED   = "AUTO_APPROVED"
STATUS_PENDING_MANAGER = "PENDING_MANAGER"
STATUS_PENDING_FINANCE = "PENDING_FINANCE"
STATUS_PENDING_HUMAN   = "PENDING_HUMAN_REVIEW"
STATUS_APPROVED        = "APPROVED"
STATUS_REJECTED        = "REJECTED"

ALL_STATUSES = [
    STATUS_PENDING_AUTO, STATUS_AUTO_APPROVED, STATUS_PENDING_MANAGER,
    STATUS_PENDING_FINANCE, STATUS_PENDING_HUMAN, STATUS_APPROVED, STATUS_REJECTED,
]

PERSIST_PATH = os.path.join(os.path.dirname(__file__), "applications.json")


class ApplicationStore:
    """
    线程安全的申请状态存储（PoC 单机版）。
    生产环境替换为数据库（PostgreSQL / MySQL）。
    """

    def __init__(self):
        self._store: Dict[str, dict] = {}

    # ─── 加载 ────────────────────────────────────────────────────────────────

    def load_applications(self, applications: List[dict]) -> None:
        """批量导入申请，初始状态设为 PENDING_AUTO_CHECK。"""
        for app in applications:
            record = dict(app)
            record["status"] = STATUS_PENDING_AUTO
            record["history"] = []
            record["manager_decision"] = None
            record["finance_decision"] = None
            self._store[app["app_id"]] = record

    # ─── 查询 ────────────────────────────────────────────────────────────────

    def get(self, app_id: str) -> Optional[dict]:
        return self._store.get(app_id)

    def get_by_status(self, status: str) -> List[dict]:
        return [a for a in self._store.values() if a["status"] == status]

    def all(self) -> List[dict]:
        return list(self._store.values())

    def stats(self) -> Dict[str, int]:
        from collections import Counter
        return dict(Counter(a["status"] for a in self._store.values()))

    # ─── 状态变更 ─────────────────────────────────────────────────────────────

    def update_status(
        self,
        app_id: str,
        new_status: str,
        actor: str = "system",
        reason: str = "",
        decision_detail: dict = None,
    ) -> None:
        app = self._store[app_id]
        old_status = app["status"]
        app["status"] = new_status
        app["history"].append({
            "timestamp": datetime.now().isoformat(),
            "from":      old_status,
            "to":        new_status,
            "actor":     actor,
            "reason":    reason,
        })
        if decision_detail:
            if actor in ("manager", "manager_agent"):
                app["manager_decision"] = decision_detail
            elif actor in ("finance", "finance_agent"):
                app["finance_decision"] = decision_detail

    def approve_auto(self, app_id: str, reason: str = "符合自动审批条件") -> None:
        self.update_status(app_id, STATUS_AUTO_APPROVED, "auto_system", reason)

    def send_to_manager(self, app_id: str) -> None:
        self.update_status(app_id, STATUS_PENDING_MANAGER, "auto_system", "进入上级审批队列")

    def manager_approve(self, app_id: str, reason: str, detail: dict = None) -> None:
        self.update_status(app_id, STATUS_PENDING_FINANCE, "manager_agent", reason, detail)

    def manager_reject(self, app_id: str, reason: str) -> None:
        self.update_status(app_id, STATUS_REJECTED, "manager_agent", reason)

    def manager_flag_human(self, app_id: str, reason: str) -> None:
        self.update_status(app_id, STATUS_PENDING_HUMAN, "manager_agent", reason)

    def finance_approve(self, app_id: str, reason: str, detail: dict = None) -> None:
        self.update_status(app_id, STATUS_APPROVED, "finance_agent", reason, detail)

    def finance_reject(self, app_id: str, reason: str) -> None:
        self.update_status(app_id, STATUS_REJECTED, "finance_agent", reason)

    def finance_flag_human(self, app_id: str, reason: str) -> None:
        self.update_status(app_id, STATUS_PENDING_HUMAN, "finance_agent", reason)

    # ─── 持久化 ───────────────────────────────────────────────────────────────

    def save(self, path: str = PERSIST_PATH) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._store, f, ensure_ascii=False, indent=2)

    def load_from_file(self, path: str = PERSIST_PATH) -> None:
        with open(path, encoding="utf-8") as f:
            self._store = json.load(f)

    # ─── 报表 ────────────────────────────────────────────────────────────────

    def print_summary(self) -> None:
        stats = self.stats()
        total = len(self._store)
        print(f"\n{'─'*60}")
        print(f"  申请状态汇总 (共 {total} 条)")
        print(f"{'─'*60}")
        order = [STATUS_AUTO_APPROVED, STATUS_APPROVED, STATUS_PENDING_MANAGER,
                 STATUS_PENDING_FINANCE, STATUS_PENDING_HUMAN, STATUS_REJECTED,
                 STATUS_PENDING_AUTO]
        for s in order:
            count = stats.get(s, 0)
            if count:
                bar = "█" * count
                print(f"  {s:<25} {count:>4} 条  {bar}")
        print(f"{'─'*60}\n")
