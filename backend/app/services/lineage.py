"""湖仓血缘追踪业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.impact import parse_field_mappings
from app.store import store

MODULE = "lineage"
REQUIRED_FIELDS = ["记录编号", "上游表名", "下游表名"]
OPTIONAL_FIELDS = ["转换类型", "上游版本", "下游版本", "操作时间", "操作批次"]
STATUS_ORDER = ["已记录", "已关联", "已过期", "已清理"]
ACTION_RULES = {"关联血缘": "已记录", "标记过期": "已关联", "清理记录": "已过期"}
NEGATIVE_ACTIONS = []


class LineageService:
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
                or keyword in str(row.get("上游表名", ""))
                or keyword in str(row.get("下游表名", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if batch:
            rows = [row for row in rows if str(row.get("操作批次", "")) == batch]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = []
        for row in rows[start:start + size]:
            mappings = parse_field_mappings(row.get("字段映射"))
            page_rows.append({
                **row,
                # 列表里直接展示可读映射，原始 JSON 仍保留在字段映射里
                "映射文本": "、".join(
                    f"{item['source']}→{item['target']}" for item in mappings
                ) or "映射缺失",
            })
        return page_rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return {**entry, "字段映射明细": parse_field_mappings(entry.get("字段映射"))}

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        mapping_text = values.get("字段映射")
        if mapping_text and not parse_field_mappings(mapping_text):
            # 有映射文本但解析不出任何 source→target，说明字段缺失，登记时直接说明原因
            return None, ["字段映射（需包含上下游字段，如 order_id->order_id）"]
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["字段映射"] = values.get("字段映射") or ""
        for field in OPTIONAL_FIELDS:
            entry[field] = values.get(field) or ""
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"血缘记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于湖仓血缘追踪可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"血缘记录已{action}"
