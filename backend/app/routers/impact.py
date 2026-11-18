"""影响传播接口：读取血缘记录与字段映射，按操作批次展开多跳下游表并发布一份影响结果。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.impact import impact_service

router = APIRouter(prefix="/api/impact", tags=["影响传播"])

MAX_SIZE = 200


def _check_size(size: int) -> None:
    if size > MAX_SIZE:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")


@router.get("/batches", response_model=PageResult[dict])
def list_batches(
    keyword: str | None = Query(default=None, description="按操作批次检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """列出可选操作批次及其影响概览，用于待复核页的批次选择。"""
    _check_size(size)
    items = impact_service.batches()
    if keyword:
        items = [item for item in items if keyword in item["操作批次"]]
    total = len(items)
    start = max(page - 1, 0) * size
    return PageResult(items=items[start:start + size], total=total, page=page, size=size)


@router.get("/records", response_model=PageResult[dict])
def list_records(
    batch: str | None = Query(default=None, description="按操作批次筛选"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """已发布的影响结果列表；每个操作批次只返回一份。"""
    _check_size(size)
    items, total = impact_service.list_results(batch=batch, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/records/{batch}", response_model=dict)
def get_record(batch: str, preview: bool = Query(default=True, description="未发布时是否预览计算结果")) -> dict:
    """查看某个操作批次的影响结果；未发布时默认返回预览，不落库。"""
    result, message = impact_service.get_result(batch, preview=preview)
    if result is None:
        raise HTTPException(status_code=404, detail=message or f"操作批次 {batch} 的影响结果不存在")
    return result


@router.post("/records/{batch}/publish", response_model=ActionResult)
def publish_record(batch: str) -> ActionResult:
    """发布影响结果；同一操作批次重复发布会被拒绝，始终只保留一份。"""
    result, message, created = impact_service.publish(batch)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=created, message=message, entry=result)


@router.get("/records/{batch}/tables", response_model=PageResult[dict])
def list_tables(
    batch: str,
    keyword: str | None = Query(default=None, description="按表名检索，也会匹配上游表名与字段路径"),
    field: str | None = Query(default=None, description="按字段名定位到具体字段路径"),
    sort: str = Query(default="depth", description="depth 按深度、name 按表名"),
    order: str = Query(default="asc", description="asc 升序、desc 降序"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按操作批次、表名、字段与深度分页定位下游表。"""
    _check_size(size)
    if sort not in {"depth", "name"}:
        raise HTTPException(status_code=400, detail="排序字段只支持 depth 或 name")
    if order not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="排序方向只支持 asc 或 desc")
    items, total, result, message = impact_service.list_tables(
        batch, keyword=keyword, field=field, sort=sort, order=order, page=page, size=size
    )
    if result is None:
        raise HTTPException(status_code=404, detail=message)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/records/{batch}/paths", response_model=PageResult[dict])
def list_paths(
    batch: str,
    table: str | None = Query(default=None, description="按下游表筛选完整字段路径"),
    field: str | None = Query(default=None, description="按字段名筛选完整字段路径"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """待复核页按操作批次查看完整字段级路径，可直接定位到具体字段。"""
    _check_size(size)
    items, total, result, message = impact_service.list_paths(
        batch, table=table, field=field, page=page, size=size
    )
    if result is None:
        raise HTTPException(status_code=404, detail=message)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/records/{batch}/actions", response_model=ActionResult)
def review_record(batch: str, payload: EntryPayload) -> ActionResult:
    """影响复核：确认复核或退回复核，复核结果同步保存在影响结果上。"""
    action = str(payload.values.get("action") or "").strip()
    remark = str(payload.values.get("remark") or payload.remark or "").strip()
    operator = str(payload.values.get("operator") or "").strip()
    result, message = impact_service.review(batch, action, remark=remark, operator=operator)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


@router.post("/records/{batch}/consume", response_model=ActionResult)
def consume_record(batch: str, payload: EntryPayload) -> ActionResult:
    """其它业务模块消费下游表清单后，把版本依据同步到影响复核工作台。"""
    values = payload.values
    result, message = impact_service.consume(
        batch,
        module=str(values.get("module") or ""),
        record_no=str(values.get("record_no") or ""),
        table=str(values.get("table") or ""),
        version=str(values.get("version") or ""),
        note=str(values.get("note") or values.get("remark") or ""),
    )
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


@router.get("/export")
def export_records(batch: str | None = None) -> dict[str, Any]:
    """导出影响结果清单：默认导出全部已发布结果。"""
    items, total = impact_service.list_results(batch=batch, page=1, size=10000)
    return {"module": "impact", "total": total, "items": items}
