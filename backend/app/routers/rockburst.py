"""冲击地压接口：平铺台账 + 微震能量/应力分级看板。

固定路径（/board、/alerts 等）必须排在 /{entry_id} 之前声明：
FastAPI 按声明顺序匹配，int 路径参数遇到非数字会直接 422 而不是继续尝试下一条。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    AlertAckPayload,
    CloseDayPayload,
    EntryPayload,
    PageResult,
    ReadingBackfillPayload,
    ReliefPayload,
    RulePayload,
)
from app.services.rockburst import RockburstService
from app.services.rockburst_board import board_service

router = APIRouter(prefix="/api/rockburst", tags=["冲击地压"])

service = RockburstService()

LIST_FIELDS = ["监测编号", "所在区域", "微震能量", "微震频次", "应力值", "预警等级", "处置措施", "监测状态"]
STATUSES = ["正常", "应力集中", "预警", "已解危"]


# ------------------------------------------------------------------ 分级看板

@router.get("/board")
def board() -> dict[str, Any]:
    """分级看板：按区域聚合测点的当天档位、近三日变化、最近解危时间与待签收提醒。"""
    return board_service.board()


@router.get("/alerts")
def list_alerts(scope: str = Query(default="open", pattern="^(open|all)$")) -> list[dict[str, Any]]:
    """值班调度提醒清单：默认只看未签收的等级抬高提醒。"""
    return board_service.list_alerts(scope=scope)


@router.post("/alerts/{alert_id}/ack", response_model=ActionResult)
def ack_alert(alert_id: int, payload: AlertAckPayload) -> ActionResult:
    """签收一条等级抬高提醒；签收后同日再次抬高会重新进清单。"""
    alert, message = board_service.acknowledge_alert(alert_id, payload.operator or "值班调度")
    if alert is None:
        raise HTTPException(status_code=404, detail=message)
    return ActionResult(ok=True, message=message, entry=alert)


@router.get("/stations")
def list_stations() -> list[dict[str, Any]]:
    """测点目录，供补数/解危表单下拉使用。"""
    return board_service.list_stations()


@router.get("/stations/{station_id}")
def station_detail(station_id: int) -> dict[str, Any]:
    """测点详情：看板与详情共用日评级表，读到的档位与看板完全一致。"""
    detail = board_service.station_detail(station_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"测点 {station_id} 不存在")
    return detail


@router.post("/stations/{station_id}/readings", response_model=ActionResult)
def backfill_reading(station_id: int, payload: ReadingBackfillPayload) -> ActionResult:
    """补录读数：收盘日/未来日拒绝；接受后从当日起按日顺序重算，不跳级覆盖。"""
    result, message = board_service.backfill_reading(station_id, payload.model_dump())
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


@router.post("/stations/{station_id}/reliefs", response_model=ActionResult)
def register_relief(station_id: int, payload: ReliefPayload) -> ActionResult:
    """登记解危措施：只更新最近解危时间，不改变能量/应力判定出的等级。"""
    relief, message = board_service.register_relief(station_id, payload.model_dump())
    if relief is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=relief)


@router.get("/rules")
def list_rules() -> list[dict[str, Any]]:
    """判定口径版本历史（只此一份判定口径，按生效日期排列）。"""
    return board_service.list_rules()


@router.post("/rules", response_model=ActionResult)
def create_rule(payload: RulePayload) -> ActionResult:
    """新增口径：不追溯收盘日；生效日起的未收盘日期按新口径顺序重算当天等级。"""
    rule, message = board_service.create_rule(payload.model_dump())
    if rule is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=rule)


@router.get("/days")
def day_calendar() -> dict[str, Any]:
    """日期日历：哪些天已收盘、各天收盘时所用的口径版本。"""
    return board_service.day_calendar()


@router.post("/days/close", response_model=ActionResult)
def close_day(payload: CloseDayPayload) -> ActionResult:
    """按顺序收盘最早一个未收盘日；收盘后评级与口径一并冻结。"""
    result, message = board_service.close_day(payload.date)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


# ------------------------------------------------------------------ 平铺台账（原功能）

@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按监测编号检索"),
    status: str | None = Query(default=None, description="正常、应力集中、预警、已解危"),
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
