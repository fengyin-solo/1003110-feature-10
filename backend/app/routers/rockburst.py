"""冲击地压接口：分级看板、测点明细、口径版本、补录收盘与调度提醒。

旧平铺页的登记/状态流转接口保留；所有静态子路径必须声明在 /{entry_id} 之前，
否则会被整数路径参数先匹配走。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BasisPayload,
    EntryPayload,
    PageResult,
    ReadingPayload,
    ReliefPayload,
)
from app.services.rockburst import RockburstService
from app.services.rockburst_domain import LEVEL_NAMES, NO_DATA, domain

router = APIRouter(prefix="/api/rockburst", tags=["冲击地压"])

service = RockburstService()

LIST_FIELDS = ["监测编号", "所在区域", "微震能量", "微震频次", "应力值", "预警等级", "处置措施", "监测状态"]
STATUSES = ["正常监测", "应力集中", "预警处置", "已解危"]


# ---------------------------------------------------------------- 分级看板

@router.get("/board")
def board(
    date: str | None = Query(default=None, description="看板日期 YYYY-MM-DD，默认今天"),
    level: str | None = Query(default=None, description="按预警等级过滤清单"),
) -> dict[str, Any]:
    """分级看板：按区域排列，区域内等级高者、能量大者在前，附近两日趋势与最近解危时间。"""
    if level and level not in LEVEL_NAMES and level != NO_DATA:
        raise HTTPException(status_code=400, detail=f"预警等级只能是：{'、'.join(LEVEL_NAMES)}、{NO_DATA}")
    return domain.board(day=date, level=level)


@router.get("/points/{point_id}")
def point_detail(point_id: int) -> dict[str, Any]:
    """测点详情：看板与详情共用等级结果表，历史等级按当时判定与口径逐行展示。"""
    detail = domain.point_detail(point_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"测点 {point_id} 不存在")
    return detail


@router.get("/basis")
def list_basis() -> dict[str, Any]:
    """判定口径版本清单（只保留一份当前口径，历史版本供收盘行追溯）。"""
    return {"items": domain.basis_list()}


@router.post("/basis", response_model=ActionResult)
def publish_basis(payload: BasisPayload) -> ActionResult:
    """调整判定口径：新口径只重算未收盘日期，已经收盘的日子不动。"""
    basis, message = domain.publish_basis(payload.values)
    if basis is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=basis)


@router.post("/readings", response_model=ActionResult)
def ingest_reading(payload: ReadingPayload) -> ActionResult:
    """补录/刷新读数：只能写未收盘日期，且只顺着当天重新判定，不覆盖更早的等级。"""
    grade, message = domain.ingest_reading(payload.values)
    if grade is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=grade)


@router.post("/close-day", response_model=ActionResult)
def close_day() -> ActionResult:
    """按顺序收盘一天：冻结当天等级行与口径快照，交接当天未确认提醒。"""
    day, message = domain.close_day()
    if day is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry={"date": day})


@router.get("/reminders")
def reminders(
    status: str | None = Query(default=None, description="待处置、已确认、已消除"),
) -> dict[str, Any]:
    """值班调度提醒清单：等级抬高推一条进来，默认看到的是全部，可按状态过滤。"""
    return {"items": domain.reminders(status=status)}


@router.post("/reminders/{reminder_id}/ack", response_model=ActionResult)
def ack_reminder(reminder_id: int, payload: EntryPayload | None = None) -> ActionResult:
    operator = ""
    if payload is not None:
        operator = str(payload.values.get("operator") or "").strip()
    reminder, message = domain.ack_reminder(reminder_id, operator or "值班调度")
    if reminder is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=reminder)


@router.post("/reliefs", response_model=ActionResult)
def register_relief(payload: ReliefPayload) -> ActionResult:
    """登记解危措施与时间，看板上的「最近解危时间」随之更新。"""
    relief, message = domain.register_relief(payload.values)
    if relief is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=relief)


# ---------------------------------------------------------------- 旧平铺页

@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按监测编号检索"),
    status: str | None = Query(default=None, description="正常监测、应力集中、预警处置、已解危"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按监测编号与状态过滤冲击地压列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出冲击地压清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "rockburst", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条微震监测明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"微震监测 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条微震监测，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="微震监测已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条微震监测执行应力预警、解危处置、解危确认；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
