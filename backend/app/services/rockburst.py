"""冲击地压业务规则：平铺表接口适配分级看板领域。

等级判定只存在于 rockburst_domain 一处，本模块只做旧平铺页需要的字段转换，
保证平铺表、分级看板、测点详情读到的是同一份等级结果。
"""
from __future__ import annotations

from typing import Any

from app.services.rockburst_domain import NO_DATA, domain

MODULE = "rockburst"
REQUIRED_FIELDS = ["监测编号", "所在区域", "微震能量"]
STATUS_ORDER = ["正常监测", "应力集中", "预警处置", "已解危"]


class RockburstService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = domain.flat_rows()
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("监测编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        detail = domain.point_detail(entry_id)
        if detail is None:
            return None
        row = next((row for row in domain.flat_rows() if int(row["id"]) == entry_id), None)
        return row or {"id": entry_id, "监测编号": detail["point"]["code"], "预警等级": NO_DATA}

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        point, missing = domain.create_point(values)
        if missing:
            return None, missing
        # 登记时带了能量/应力就顺手作为今天读数进入判定；不带则先建测点
        energy, stress = values.get("微震能量"), values.get("应力值")
        if energy not in (None, "") or stress not in (None, ""):
            domain.ingest_reading({
                "point_id": point["id"],
                "date": domain.meta["today"],
                "energy": energy or 0,
                "stress": stress or 0,
                "count": values.get("微震频次"),
            })
        return point, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        return domain.run_flow_action(entry_id, action)
