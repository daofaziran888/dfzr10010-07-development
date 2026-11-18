"""影响传播业务规则：按操作批次读取血缘记录与字段映射，展开多跳下游表清单。

这里的规则刻意保守：
- 上游表名或下游表名缺一侧，整条血缘不参与传播；
- 字段映射缺失或无法解析时，同样不猜一条边；
- 同一操作批次只允许发布一份影响结果；
- 迁移记录与回滚记录版本冲突时，只沿事件时间更新的一条表链展开，
  另一条表链整体排除，保证待复核页不会出现重复路径。
"""
from __future__ import annotations

import json
import re
from collections import deque
from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable

from app.store import store

MODULE = "impact"
LINEAGE_MODULE = "lineage"
ROLLBACK_MODULE = "rollback"
MIGRATE_MODULE = "migrate"

INACTIVE_STATUSES = {"已过期", "已清理"}
REVIEW_ACTIONS = {"确认复核": "已复核", "退回复核": "退回整改"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_time(value: Any) -> datetime:
    text = _text(value)
    if not text:
        return datetime.min
    text = text.replace("T", " ").replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.min


def _split_mapping_text(text: str) -> list[Any]:
    """解析 `--` 或 `->` 形式的显式字段映射；解析不出来就当作缺失。"""
    items: list[Any] = []
    for part in re.split(r"[,;；，]", text):
        part = part.strip()
        if not part:
            continue
        if "->" in part:
            left, right = part.split("->", 1)
        elif "→" in part:
            left, right = part.split("→", 1)
        elif "=" in part:
            left, right = part.split("=", 1)
        else:
            # 只有一个字段名，无法判断映射到哪一侧，不能猜
            continue
        items.append({"source": left.strip(), "target": right.strip()})
    return items


def parse_field_mappings(raw: Any) -> list[dict[str, str]]:
    """把不同来源的字段映射统一成 [{source, target}]。

    支持 JSON 字符串、对象、二元数组以及显式的 `source->target` 文本。
    只要有一侧缺字段，该条映射就丢弃；全部丢弃时调用方会跳过整条血缘边。
    """
    data: Any = []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            data = _split_mapping_text(text)
    elif isinstance(raw, (list, tuple)):
        data = list(raw)
    elif isinstance(raw, dict):
        # {"order_id": "order_id"} 或 {"source": "...", "target": "..."}
        if "source" in raw or "target" in raw or "源字段" in raw or "目标字段" in raw:
            data = [raw]
        else:
            data = [{"source": key, "target": value} for key, value in raw.items()]
    else:
        return []

    if isinstance(data, dict):
        data = [data]

    mappings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in data:
        source = target = ""
        if isinstance(item, dict):
            source = _text(
                item.get("source")
                or item.get("源字段")
                or item.get("上游字段")
                or item.get("source_field")
            )
            target = _text(
                item.get("target")
                or item.get("目标字段")
                or item.get("下游字段")
                or item.get("target_field")
            )
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            source, target = _text(item[0]), _text(item[1])
        elif isinstance(item, str):
            parsed = _split_mapping_text(item)
            if parsed:
                source = parsed[0]["source"]
                target = parsed[0]["target"]
        if not source or not target:
            # 漏掉任何一侧都不生成字段级路径
            continue
        key = (source, target)
        if key in seen:
            continue
        seen.add(key)
        mappings.append({"source": source, "target": target})
    return mappings


def _normalise_mapping_text(mappings: Iterable[dict[str, str]]) -> str:
    return "、".join(f"{item['source']}→{item['target']}" for item in mappings) or "—"


def _batch_rows(module: str, batch: str) -> list[dict[str, Any]]:
    return [
        row
        for row in store.rows(module)
        if _text(row.get("操作批次")) == batch
    ]


def _build_edges(batch: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """读取一个批次的血缘记录，返回 (有效边, 跳过记录, 原始记录)。"""
    raw_rows = _batch_rows(LINEAGE_MODULE, batch)
    edges: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_edges: set[tuple[Any, ...]] = set()

    for row in raw_rows:
        record_no = _text(row.get("记录编号")) or f"ID-{row.get('id')}"
        source = _text(row.get("上游表名"))
        target = _text(row.get("下游表名"))
        status = _text(row.get("status") or row.get("记录状态"))

        if status in INACTIVE_STATUSES:
            skipped.append({
                "记录编号": record_no,
                "上游表名": source or "—",
                "下游表名": target or "—",
                "跳过原因": f"记录状态为{status}，不参与本次影响传播",
            })
            continue
        if not source or not target:
            missing = []
            if not source:
                missing.append("上游表名")
            if not target:
                missing.append("下游表名")
            skipped.append({
                "记录编号": record_no,
                "上游表名": source or "—",
                "下游表名": target or "—",
                "跳过原因": f"缺少{'、'.join(missing)}，无法确定血缘边",
            })
            continue

        mappings = parse_field_mappings(row.get("字段映射"))
        if not mappings:
            skipped.append({
                "记录编号": record_no,
                "上游表名": source,
                "下游表名": target,
                "跳过原因": "字段映射缺失或不可解析，不猜测血缘边",
            })
            continue

        edge = {
            "记录编号": record_no,
            "source": source,
            "target": target,
            "mappings": mappings,
            "映射文本": _normalise_mapping_text(mappings),
            "upstream_version": _text(row.get("上游版本")),
            "downstream_version": _text(row.get("下游版本")),
            "转换类型": _text(row.get("转换类型")),
            "操作时间": _text(row.get("操作时间")),
        }
        key = (
            source,
            target,
            edge["upstream_version"],
            edge["downstream_version"],
            tuple((item["source"], item["target"]) for item in mappings),
        )
        if key in seen_edges:
            skipped.append({
                "记录编号": record_no,
                "上游表名": source,
                "下游表名": target,
                "跳过原因": "同批次重复血缘边，已合并为一条路径",
            })
            continue
        seen_edges.add(key)
        edges.append(edge)

    return edges, skipped, raw_rows


def _closure(root: str, edges: list[dict[str, Any]]) -> set[str]:
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["source"], []).append(edge["target"])
    seen = {root}
    queue = deque([root])
    while queue:
        node = queue.popleft()
        for nxt in adjacency.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _detect_conflict(batch: str, edges: list[dict[str, Any]]) -> tuple[dict[str, Any], set[str]]:
    """识别迁移/回滚版本冲突，并返回冲突信息和应排除的表链。"""
    migrates = _batch_rows(MIGRATE_MODULE, batch)
    rollbacks = _batch_rows(ROLLBACK_MODULE, batch)
    conflicts: list[dict[str, Any]] = []
    excluded: set[str] = set()

    for rollback in rollbacks:
        base = _text(rollback.get("表名称"))
        rollback_version = _text(
            rollback.get("目标版本") or rollback.get("回滚版本") or rollback.get("版本依据")
        )
        rollback_time = _text(rollback.get("回滚时间"))
        if not base or not rollback_version:
            continue
        for migrate in migrates:
            source_table = _text(migrate.get("源表名"))
            target_table = _text(migrate.get("目标表名"))
            migrate_version = _text(migrate.get("目标版本"))
            if source_table != base or not target_table or not migrate_version:
                continue
            if migrate_version == rollback_version:
                continue

            migrate_time = _text(migrate.get("迁移时间") or migrate.get("操作时间"))
            # 回滚时间不早于迁移时间时，数据最终停留在回滚后的版本；
            # 同一时间也优先采用回滚后的表链，避免把已回退的路径算进影响面。
            rollback_wins = _parse_time(rollback_time) >= _parse_time(migrate_time)
            winner_label = "回滚后表链" if rollback_wins else "迁移后表链"
            loser_label = "迁移后表链" if rollback_wins else "回滚后表链"
            winner_root = base if rollback_wins else target_table
            loser_root = target_table if rollback_wins else base
            winner_tables = _closure(winner_root, edges)
            loser_tables = _closure(loser_root, edges) - winner_tables
            excluded.update(loser_tables)
            conflicts.append({
                "冲突表": base,
                "迁移记录": _text(migrate.get("任务编号")),
                "回滚记录": _text(rollback.get("记录编号")),
                "迁移版本": migrate_version,
                "回滚版本": rollback_version,
                "迁移时间": migrate_time,
                "回滚时间": rollback_time,
                "采用表链": winner_label,
                "采用根表": winner_root,
                "丢弃表链": loser_label,
                "丢弃表": sorted(loser_tables),
                "采用依据": (
                    f"回滚时间 {rollback_time or '未记录'} 不早于迁移时间 "
                    f"{migrate_time or '未记录'}，沿回滚后的表链展开"
                    if rollback_wins
                    else f"迁移时间 {migrate_time or '未记录'} 晚于回滚时间 "
                    f"{rollback_time or '未记录'}，沿迁移后的表链展开"
                ),
            })

    if not conflicts:
        return {"存在冲突": False}, set()

    # 多个冲突时按事件时间先后稳定排序，首个作为主要说明。
    conflicts.sort(key=lambda item: _parse_time(item.get("回滚时间")), reverse=True)
    primary = conflicts[0]
    info = {
        "存在冲突": True,
        **primary,
        "冲突清单": conflicts,
        "排除表": sorted(excluded),
    }
    return info, excluded


def _root_tables(edges: list[dict[str, Any]]) -> list[str]:
    sources = {edge["source"] for edge in edges}
    targets = {edge["target"] for edge in edges}
    return sorted(sources - targets)


def _table_depths(roots: list[str], edges: list[dict[str, Any]]) -> dict[str, int]:
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["source"], []).append(edge["target"])
    depths = {root: 0 for root in roots}
    queue = deque(roots)
    while queue:
        node = queue.popleft()
        for nxt in adjacency.get(node, []):
            if nxt not in depths or depths[node] + 1 < depths[nxt]:
                depths[nxt] = depths[node] + 1
                queue.append(nxt)
    return depths


def _collect_paths(
    roots: list[str],
    edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    adjacency: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        adjacency.setdefault(edge["source"], []).append(edge)
    for bucket in adjacency.values():
        bucket.sort(key=lambda item: (item["target"], item["记录编号"]))

    paths: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    duplicates = 0

    def walk(
        root: str,
        table: str,
        field: str,
        tables: list[str],
        fields: list[str],
        hops: list[dict[str, Any]],
        versions: list[str],
    ) -> None:
        nonlocal duplicates
        for edge in adjacency.get(table, []):
            if edge["target"] in tables:
                # 环状血缘不再展开，避免一条路径无限重复
                continue
            for mapping in edge["mappings"]:
                if mapping["source"] != field:
                    continue
                next_tables = [*tables, edge["target"]]
                next_fields = [*fields, mapping["target"]]
                next_hops = [
                    *hops,
                    {
                        "上游表": table,
                        "上游字段": mapping["source"],
                        "下游表": edge["target"],
                        "下游字段": mapping["target"],
                        "记录编号": edge["记录编号"],
                    },
                ]
                next_versions = [*versions, edge["downstream_version"]]
                path_key = tuple(zip(next_tables, next_fields))
                if path_key not in seen:
                    seen.add(path_key)
                    depth = len(next_hops)
                    paths.append({
                        "路径编号": f"PATH-{len(paths) + 1:04d}",
                        "根表": root,
                        "下游表": edge["target"],
                        "深度": depth,
                        "血缘链": next_tables,
                        "字段链": [f"{table_name}.{field_name}" for table_name, field_name in path_key],
                        "起始字段": f"{root}.{fields[0]}",
                        "结束字段": f"{edge['target']}.{mapping['target']}",
                        "字段路径": " → ".join(
                            f"{table_name}.{field_name}" for table_name, field_name in path_key
                        ),
                        "字段映射": next_hops,
                        "映射文本": "；".join(
                            f"{hop['上游字段']}→{hop['下游字段']}" for hop in next_hops
                        ),
                        "起始版本": next_versions[0],
                        "结束版本": edge["downstream_version"],
                        "版本路径": next_versions,
                        "路径起止版本": f"{next_versions[0] or '—'} → {edge['downstream_version'] or '—'}",
                    })
                else:
                    duplicates += 1
                walk(
                    root,
                    edge["target"],
                    mapping["target"],
                    next_tables,
                    next_fields,
                    next_hops,
                    next_versions,
                )

    for root in roots:
        for edge in adjacency.get(root, []):
            for mapping in edge["mappings"]:
                tables = [root, edge["target"]]
                fields = [mapping["source"], mapping["target"]]
                hops = [{
                    "上游表": root,
                    "上游字段": mapping["source"],
                    "下游表": edge["target"],
                    "下游字段": mapping["target"],
                    "记录编号": edge["记录编号"],
                }]
                versions = [edge["upstream_version"], edge["downstream_version"]]
                key = tuple(zip(tables, fields))
                if key not in seen:
                    seen.add(key)
                    paths.append({
                        "路径编号": f"PATH-{len(paths) + 1:04d}",
                        "根表": root,
                        "下游表": edge["target"],
                        "深度": 1,
                        "血缘链": tables,
                        "字段链": [f"{table_name}.{field_name}" for table_name, field_name in key],
                        "起始字段": f"{root}.{mapping['source']}",
                        "结束字段": f"{edge['target']}.{mapping['target']}",
                        "字段路径": " → ".join(
                            f"{table_name}.{field_name}" for table_name, field_name in key
                        ),
                        "字段映射": hops,
                        "映射文本": f"{mapping['source']}→{mapping['target']}",
                        "起始版本": edge["upstream_version"],
                        "结束版本": edge["downstream_version"],
                        "版本路径": list(versions),
                        "路径起止版本": f"{edge['upstream_version'] or '—'} → {edge['downstream_version'] or '—'}",
                    })
                else:
                    duplicates += 1
                walk(
                    root,
                    edge["target"],
                    mapping["target"],
                    tables,
                    fields,
                    hops,
                    versions,
                )

    return paths, duplicates


def _table_rows(
    roots: list[str],
    edges: list[dict[str, Any]],
    paths: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    depths = _table_depths(roots, edges)
    incoming: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        incoming.setdefault(edge["target"], []).append(edge)

    rows: list[dict[str, Any]] = []
    for table, depth in depths.items():
        if table in roots or depth <= 0:
            continue
        parents = sorted({edge["source"] for edge in incoming.get(table, [])})
        table_paths = [path for path in paths if path["下游表"] == table]
        first_edge = sorted(incoming.get(table, []), key=lambda item: item["source"])[0]
        chains: list[list[str]] = []
        for path in table_paths:
            if path["血缘链"] not in chains:
                chains.append(path["血缘链"])

        # 只展示直接进入该表的字段映射，避免把上游多跳的映射混在一起
        unique_mappings: list[dict[str, str]] = []
        seen_mapping: set[tuple[str, str, str, str]] = set()
        for edge in incoming.get(table, []):
            for mapping in edge["mappings"]:
                key = (edge["source"], mapping["source"], table, mapping["target"])
                if key not in seen_mapping:
                    seen_mapping.add(key)
                    unique_mappings.append({
                        "上游表": edge["source"],
                        "上游字段": mapping["source"],
                        "下游表": table,
                        "下游字段": mapping["target"],
                    })
        unique_mappings.sort(key=lambda item: (item["上游表"], item["上游字段"]))

        rows.append({
            "表名": table,
            "深度": depth,
            "直接上游": parents,
            "上游版本": first_edge["upstream_version"],
            "下游版本": first_edge["downstream_version"],
            "路径起止版本": (
                table_paths[0]["路径起止版本"] if table_paths
                else f"{first_edge['upstream_version'] or '—'} → {first_edge['downstream_version'] or '—'}"
            ),
            "版本路径清单": [list(item) for item in sorted({tuple(path["版本路径"]) for path in table_paths})],
            "字段路径数": len(table_paths),
            "字段映射": unique_mappings,
            "映射文本": "；".join(
                f"{item['上游字段']}→{item['下游字段']}" for item in unique_mappings
            ) or "—",
            "血缘链": chains,
            "完整字段路径": table_paths,
            "下游版本依据": first_edge["downstream_version"],
        })

    rows.sort(key=lambda item: (item["深度"], item["表名"]))
    return rows


def _version_basis(batch: str, conflict: dict[str, Any]) -> list[dict[str, Any]]:
    """把同批次迁移/回滚记录的版本依据带到影响复核工作台。"""
    adopted = _text(conflict.get("采用表链"))
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(item: dict[str, Any]) -> None:
        key = (
            item["来源模块"],
            item["记录编号"],
            item["表名"],
            item["版本"],
        )
        if key in seen:
            return
        seen.add(key)
        items.append(item)

    for migrate in _batch_rows(MIGRATE_MODULE, batch):
        version = _text(migrate.get("目标版本") or migrate.get("版本依据"))
        table = _text(migrate.get("目标表名"))
        if not version and not table:
            continue
        add({
            "来源模块": "数据迁移管理",
            "记录编号": _text(migrate.get("任务编号")),
            "表名": table or "—",
            "版本": version or "—",
            "操作时间": _text(migrate.get("迁移时间") or migrate.get("操作时间")),
            "采用状态": "未采用（冲突后采用回滚后表链）" if adopted == "回滚后表链" and conflict.get("存在冲突") else "采用",
            "同步时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    for rollback in _batch_rows(ROLLBACK_MODULE, batch):
        version = _text(
            rollback.get("目标版本") or rollback.get("回滚版本") or rollback.get("版本依据")
        )
        table = _text(rollback.get("表名称"))
        if not version and not table:
            continue
        add({
            "来源模块": "数据回滚恢复",
            "记录编号": _text(rollback.get("记录编号")),
            "表名": table or "—",
            "版本": version or "—",
            "操作时间": _text(rollback.get("回滚时间")),
            "采用状态": "未采用（冲突后采用迁移后表链）" if adopted == "迁移后表链" and conflict.get("存在冲突") else "采用",
            "同步时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    items.sort(key=lambda item: (item["操作时间"], item["来源模块"]), reverse=True)
    return items


def build_impact(batch: str) -> dict[str, Any]:
    """计算一个操作批次的影响结果。不写仓库，供预览与发布共用。"""
    edges, skipped, raw_rows = _build_edges(batch)
    conflict, excluded = _detect_conflict(batch, edges)

    if excluded:
        edges = [
            edge
            for edge in edges
            if edge["source"] not in excluded and edge["target"] not in excluded
        ]

    roots = _root_tables(edges)
    paths, duplicate_count = _collect_paths(roots, edges)
    tables = _table_rows(roots, edges, paths)

    if conflict.get("存在冲突"):
        conflict["重复路径数"] = duplicate_count
        conflict["采用路径数"] = len(paths)
        conflict["已排除表数量"] = len(excluded)

    return {
        "操作批次": batch,
        "发布状态": "未发布",
        "发布时间": "",
        "根表": roots,
        "下游表总数": len(tables),
        "字段路径总数": len(paths),
        "下游表清单": tables,
        "字段路径清单": paths,
        "跳过血缘": skipped,
        "冲突信息": conflict,
        "版本依据": _version_basis(batch, conflict),
        "消费记录": [],
        "复核状态": "待复核",
        "复核意见": "",
        "复核时间": "",
        "结果数量": 1,
        "唯一结果": True,
        "完整性说明": (
            "影响面只包含一条完整图结果；缺失映射与被排除的冲突表链不会进入下游表清单。"
            if edges
            else "没有可展开的有效血缘边：请检查操作批次、表名两侧是否齐全以及字段映射是否填写。"
        ),
        "血缘记录数": len(raw_rows),
        "有效血缘边数": len(edges),
    }


class ImpactService:
    def _published(self, batch: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if _text(row.get("操作批次")) == batch:
                return row
        return None

    def get_result(self, batch: str, preview: bool = True) -> tuple[dict[str, Any] | None, str]:
        batch = _text(batch)
        if not batch:
            return None, "缺少操作批次"
        published = self._published(batch)
        if published is not None:
            return published, ""
        if not preview:
            return None, f"操作批次 {batch} 尚未发布影响结果"
        has_records = any(_batch_rows(module, batch) for module in (
            LINEAGE_MODULE, ROLLBACK_MODULE, MIGRATE_MODULE
        ))
        if not has_records:
            return None, f"操作批次 {batch} 不存在血缘记录，无法计算影响面"
        result = build_impact(batch)
        return result, ""

    def publish(self, batch: str) -> tuple[dict[str, Any] | None, str, bool]:
        batch = _text(batch)
        if not batch:
            return None, "缺少操作批次", False
        existing = self._published(batch)
        if existing is not None:
            return existing, f"操作批次 {batch} 已发布影响结果，本批次只保留一份", False
        built = build_impact(batch)
        if not built["下游表清单"]:
            missing = "、".join(item["记录编号"] for item in built["跳过血缘"][:5]) or "无"
            return None, (
                f"操作批次 {batch} 没有可展开的下游表：有效血缘边为 0，"
                f"已跳过记录 {missing}；请先补齐上游、下游与字段映射"
            ), False
        built["发布状态"] = "已发布"
        built["发布时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = store.rows(MODULE)
        built["id"] = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        rows.append(built)
        self._bind_batch_records(batch, built)
        return built, f"操作批次 {batch} 的影响结果已发布，共 {built['下游表总数']} 张下游表", True

    def _bind_batch_records(self, batch: str, result: dict[str, Any]) -> None:
        """把同一份下游表清单回写到回滚记录，保证两边属于同一批次。"""
        names = [item["表名"] for item in result["下游表清单"]]
        for row in _batch_rows(ROLLBACK_MODULE, batch):
            row["受影响表"] = "、".join(names)
            row["受影响表清单"] = names
            row["下游表数量"] = len(names)
            row["清单同步状态"] = "已同步"

    def list_results(
        self, *, batch: str | None = None, page: int = 1, size: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(MODULE))
        if batch:
            rows = [row for row in rows if _text(row.get("操作批次")) == batch]
        rows.sort(key=lambda row: _text(row.get("发布时间")), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return deepcopy(rows[start:start + size]), total

    def list_tables(
        self,
        batch: str,
        *,
        keyword: str | None = None,
        field: str | None = None,
        sort: str = "depth",
        order: str = "asc",
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None, str]:
        result, message = self.get_result(batch)
        if result is None:
            return [], 0, None, message
        rows = deepcopy(result.get("下游表清单", []))
        if keyword:
            keyword = keyword.strip()
            rows = [
                row
                for row in rows
                if keyword in row["表名"]
                or any(keyword in parent for parent in row.get("直接上游", []))
                or any(
                    keyword in path.get("字段路径", "")
                    for path in row.get("完整字段路径", [])
                )
            ]
        if field:
            field = field.strip()
            rows = [
                row
                for row in rows
                if any(
                    field in path.get("字段路径", "") or field in path.get("字段链", [])
                    or field == path.get("起始字段", "").split(".")[-1]
                    or field == path.get("结束字段", "").split(".")[-1]
                    for path in row.get("完整字段路径", [])
                )
            ]
        reverse = order == "desc"
        if sort == "name":
            rows.sort(key=lambda row: row["表名"], reverse=reverse)
        else:
            rows.sort(
                key=lambda row: (row["深度"], row["表名"]),
                reverse=reverse,
            )
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = rows[start:start + size]
        # 分页后字段路径只保留当前页表的完整路径，避免响应过大
        return page_rows, total, result, ""

    def list_paths(
        self,
        batch: str,
        *,
        table: str | None = None,
        field: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None, str]:
        result, message = self.get_result(batch)
        if result is None:
            return [], 0, None, message
        rows = deepcopy(result.get("字段路径清单", []))
        if table:
            rows = [row for row in rows if table in row.get("血缘链", [])]
        if field:
            rows = [
                row
                for row in rows
                if field in row.get("字段路径", "")
                or field in row.get("字段链", [])
                or field == row.get("起始字段", "").split(".")[-1]
                or field == row.get("结束字段", "").split(".")[-1]
            ]
        rows.sort(key=lambda row: (row["深度"], row["字段路径"]))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total, result, ""

    def batches(self) -> list[dict[str, Any]]:
        batches: set[str] = set()
        for module in (LINEAGE_MODULE, ROLLBACK_MODULE, MIGRATE_MODULE):
            for row in store.rows(module):
                batch = _text(row.get("操作批次"))
                if batch:
                    batches.add(batch)
        items: list[dict[str, Any]] = []
        for batch in sorted(batches, reverse=True):
            published = self._published(batch)
            preview = published or build_impact(batch)
            items.append({
                "操作批次": batch,
                "血缘记录数": preview.get("血缘记录数", 0),
                "有效血缘边数": preview.get("有效血缘边数", 0),
                "下游表总数": preview.get("下游表总数", 0),
                "字段路径总数": preview.get("字段路径总数", 0),
                "发布状态": "已发布" if published else "未发布",
                "复核状态": preview.get("复核状态", "待复核"),
                "存在冲突": bool(preview.get("冲突信息", {}).get("存在冲突")),
                "最近操作时间": max(
                    (_text(row.get("操作时间") or row.get("回滚时间") or row.get("迁移时间"))
                     for module in (LINEAGE_MODULE, ROLLBACK_MODULE, MIGRATE_MODULE)
                     for row in _batch_rows(module, batch)),
                    default="",
                ),
            })
        return items

    def consume(
        self,
        batch: str,
        *,
        module: str,
        record_no: str = "",
        table: str = "",
        version: str = "",
        note: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        batch = _text(batch)
        if not batch:
            return None, "缺少操作批次"
        result, message = self.get_result(batch, preview=False)
        if result is None:
            return None, message or f"操作批次 {batch} 尚未发布影响结果，无法同步版本依据"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        basis = result.setdefault("版本依据", [])
        key = (_text(module), _text(record_no), _text(table), _text(version))
        exists = any(
            (
                _text(item.get("来源模块")),
                _text(item.get("记录编号")),
                _text(item.get("表名")),
                _text(item.get("版本")),
            ) == key
            for item in basis
        )
        if not exists:
            basis.append({
                "来源模块": _text(module) or "外部模块",
                "记录编号": _text(record_no),
                "表名": _text(table) or "—",
                "版本": _text(version) or "—",
                "操作时间": now,
                "采用状态": "采用",
                "同步时间": now,
                "说明": note,
            })
        consumption = result.setdefault("消费记录", [])
        consume_key = (_text(module), _text(record_no), _text(table), _text(version))
        if not any(
            (
                _text(item.get("模块")),
                _text(item.get("记录编号")),
                _text(item.get("表名")),
                _text(item.get("版本")),
            ) == consume_key
            for item in consumption
        ):
            consumption.append({
                "模块": _text(module) or "外部模块",
                "记录编号": _text(record_no),
                "表名": _text(table),
                "版本": _text(version),
                "同步时间": now,
                "说明": note,
            })
        result["版本依据总数"] = len(basis)
        result["消费模块数"] = len({item.get("模块") for item in consumption})
        return result, f"版本依据已同步到操作批次 {batch} 的影响复核工作台"

    def bind_rollback(
        self, entry_id: int, batch: str | None = None
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        entry = store.find(ROLLBACK_MODULE, entry_id)
        if entry is None:
            return None, None, f"回滚记录 {entry_id} 不存在或已归档"
        batch = _text(batch) or _text(entry.get("操作批次"))
        if not batch:
            return None, None, "回滚记录缺少操作批次，无法关联下游表清单"
        result, message, _created = self.publish(batch)
        if result is None:
            return None, None, message
        names = [item["表名"] for item in result["下游表清单"]]
        version = _text(
            entry.get("目标版本") or entry.get("回滚版本") or entry.get("版本依据")
        )
        entry["操作批次"] = batch
        entry["受影响表"] = "、".join(names)
        entry["受影响表清单"] = names
        entry["下游表数量"] = len(names)
        entry["清单同步状态"] = "已同步"
        entry["版本依据"] = version
        self.consume(
            batch,
            module="数据回滚恢复",
            record_no=_text(entry.get("记录编号")),
            table=_text(entry.get("表名称")),
            version=version,
            note="回滚清单使用同一批下游表清单",
        )
        return result, entry, f"回滚记录已关联操作批次 {batch} 的下游表清单"

    def sync_migrate(
        self, entry_id: int, batch: str | None = None
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        entry = store.find(MIGRATE_MODULE, entry_id)
        if entry is None:
            return None, None, f"迁移任务 {entry_id} 不存在或已归档"
        batch = _text(batch) or _text(entry.get("操作批次"))
        if not batch:
            return None, None, "迁移任务缺少操作批次，无法同步版本依据"
        result, message = self.get_result(batch, preview=False)
        if result is None:
            # 其它模块消费前先确保同批次影响结果已经生成，发布仍只保留一份
            result, message, _created = self.publish(batch)
        if result is None:
            return None, None, message
        version = _text(entry.get("目标版本") or entry.get("版本依据"))
        entry["操作批次"] = batch
        entry["版本依据"] = version
        entry["影响清单同步"] = "已同步"
        result, message = self.consume(
            batch,
            module="数据迁移管理",
            record_no=_text(entry.get("任务编号")),
            table=_text(entry.get("目标表名")),
            version=version,
            note="迁移记录消费下游表清单后回写版本依据",
        )
        return result, entry, message

    def review(
        self, batch: str, action: str, remark: str = "", operator: str = ""
    ) -> tuple[dict[str, Any] | None, str]:
        result, _message = self.get_result(batch, preview=False)
        if result is None:
            return None, f"操作批次 {batch} 尚未发布影响结果，不能复核"
        if action not in REVIEW_ACTIONS:
            return None, f"动作「{action}」不属于影响复核范围"
        result["复核状态"] = REVIEW_ACTIONS[action]
        result["复核意见"] = remark
        result["复核时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result["复核人"] = operator
        return result, f"操作批次 {batch} 的影响结果已{action}"


impact_service = ImpactService()
