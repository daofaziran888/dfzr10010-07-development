"""数据回滚恢复业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "rollback"
REQUIRED_FIELDS = ["记录编号", "表名称", "回滚版本"]
OPTIONAL_FIELDS = ["操作批次", "起始版本", "目标版本", "回滚时间"]
STATUS_ORDER = ["待回滚", "回滚中", "已回滚", "已恢复"]
ACTION_RULES = {"执行回滚": "待回滚", "确认恢复": "回滚中", "归档记录": "已回滚"}
NEGATIVE_ACTIONS = []


class RollbackService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        batch: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("记录编号", ""))
                or keyword in str(row.get("表名称", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if batch:
            rows = [row for row in rows if str(row.get("操作批次", "")) == batch]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_FIELDS:
            entry[field] = values.get(field) or ""
        entry["受影响表"] = ""
        entry["受影响表清单"] = []
        entry["下游表数量"] = 0
        entry["版本依据"] = entry.get("目标版本") or entry.get("回滚版本") or ""
        entry["清单同步状态"] = "待生成"
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def bind_impact(
        self, entry_id: int, batch: str | None = None
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        """回滚清单使用同一批下游表清单，并保存受影响表与操作批次。"""
        from app.services.impact import impact_service

        return impact_service.bind_rollback(entry_id, batch)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"回滚记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据回滚恢复可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"回滚记录已{action}"
