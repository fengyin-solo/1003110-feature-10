"""冲击地压微震分级看板领域服务。

口径（业务约定，全系统只此一份判定实现）：
1. 评级单位为「测点 × 自然日」：取当天微震能量最大值与应力最大值，
   能量、应力分别按当前生效的分级门槛定级，取两者中较高的等级。
2. 每日评级落一条快照（day_grades）。已收盘日期的快照冻结：
   - 补数落到已收盘日期会被拒绝；
   - 口径调整只重算尚未收盘日期，收盘日保留收盘时使用的规则版本与判定说明。
3. 补数只能从补数当天起，按日期升序逐日重算，不许跳过或改写更早的等级；
   同一天内补到新数据只允许把当天评级往上抬，不允许用更低结果覆盖。
4. 等级较「最近一个已有评级的日期」抬高时，往值班调度提醒清单推一条；
   同一测点同一自然日只保留一条，重算后若等级继续抬高则更新原提醒。
5. 看板与测点详情读的是同一张日评级表，因此看板上的档位与详情里必然一致。

本模块只依赖标准库，方便不接 Web 层时直接验证判定与重算逻辑。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

LEVELS: list[dict[str, Any]] = [
    {"code": "normal", "name": "无风险", "index": 0, "tone": "gray"},
    {"code": "blue", "name": "蓝色", "index": 1, "tone": "blue"},
    {"code": "yellow", "name": "黄色", "index": 2, "tone": "yellow"},
    {"code": "orange", "name": "橙色", "index": 3, "tone": "orange"},
    {"code": "red", "name": "红色", "index": 4, "tone": "red"},
]
LEVEL_BY_CODE = {item["code"]: item for item in LEVELS}

# 测点的当天明细里，能量与应力都可能缺测；缺测视为该项不参与定级。
NO_READING_BASELINE = -1


def _to_float(value: Any) -> float | None:
    """把前端/种子里的数字宽容地转成 float；空串、None、非数字一律按缺测处理。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _shift_date(day: str, delta: int) -> str:
    """对 YYYY-MM-DD 做整天平移，保持字典序与时间序一致。"""
    current = datetime.strptime(day, "%Y-%m-%d")
    return datetime.fromordinal(current.toordinal() + delta).strftime("%Y-%m-%d")


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def classify_index(value: float | None, thresholds: list[float]) -> int | None:
    """单项指标定级：返回过了几道门槛（0~4）；缺测返回 None。"""
    if value is None:
        return None
    return sum(1 for threshold in thresholds if value >= threshold)


class RockburstBoardService:
    """以内存表维护测点、读数、口径版本、日评级、收盘状态、解危与提醒。"""

    def __init__(self) -> None:
        self.stations: list[dict[str, Any]] = []
        self.readings: list[dict[str, Any]] = []
        self.rules: list[dict[str, Any]] = []
        self.day_grades: list[dict[str, Any]] = []
        self.day_states: list[dict[str, Any]] = []  # 每个自然日一条：是否已收盘
        self.reliefs: list[dict[str, Any]] = []
        self.alerts: list[dict[str, Any]] = []
        self.business_today = ""
        self._counters = {"station": 0, "reading": 0, "rule": 0, "grade": 0, "relief": 0, "alert": 0}
        self._seeded = False

    # ------------------------------------------------------------------ 基础工具

    def _next_id(self, key: str) -> int:
        self._counters[key] += 1
        return self._counters[key]

    def ensure_seed(self) -> None:
        if self._seeded:
            return
        self._seeded = True
        self._build_seed()

    def find_station(self, station_id: int) -> dict[str, Any] | None:
        for station in self.stations:
            if station["id"] == station_id:
                return station
        return None

    def find_alert(self, alert_id: int) -> dict[str, Any] | None:
        for alert in self.alerts:
            if alert["id"] == alert_id:
                return alert
        return None

    def rule_for(self, day: str) -> dict[str, Any]:
        """当天适用口径：生效日期不晚于该日的最新版本。

        同一天启用了多个版本（两种做法取舍、当天拍板）时，以最新采用的一版为准，
        收盘日则早已把当时那版写进冻结快照，不会再被这里影响。
        """
        candidates = [rule for rule in self.rules if rule["effective_date"] <= day]
        return max(candidates, key=lambda rule: (rule["effective_date"], rule["id"]))

    def current_rule(self) -> dict[str, Any]:
        return self.rule_for(self.business_today)

    def closed_dates(self) -> list[str]:
        return [item["date"] for item in self.day_states if item["closed"]]

    def is_closed(self, day: str) -> bool:
        return any(item["date"] == day and item["closed"] for item in self.day_states)

    def closed_through(self) -> str | None:
        dates = self.closed_dates()
        return max(dates) if dates else None

    def _day_state(self, day: str) -> dict[str, Any]:
        for item in self.day_states:
            if item["date"] == day:
                return item
        item = {"date": day, "closed": False, "closed_at": None}
        self.day_states.append(item)
        return item

    def _grade_row(self, station_id: int, day: str) -> dict[str, Any] | None:
        for row in self.day_grades:
            if row["station_id"] == station_id and row["date"] == day:
                return row
        return None

    def _latest_grade_before(self, station_id: int, day: str) -> dict[str, Any] | None:
        rows = [
            row for row in self.day_grades
            if row["station_id"] == station_id and row["date"] < day
        ]
        return max(rows, key=lambda row: row["date"], default=None)

    def _grade_on(self, station_id: int, day: str) -> dict[str, Any] | None:
        """看板取数：优先当天快照；当天缺测时回退到最近一次评级并标记 stale。"""
        row = self._grade_row(station_id, day)
        if row is not None:
            return row
        return self._latest_grade_before(station_id, day)

    def _day_readings(self, station_id: int, day: str) -> list[dict[str, Any]]:
        prefix = f"{day} "
        return [
            item for item in self.readings
            if item["station_id"] == station_id and item["ts"].startswith(prefix)
        ]

    def _aggregate(self, station_id: int, day: str) -> tuple[float | None, float | None]:
        rows = self._day_readings(station_id, day)
        energies = [item["energy_j"] for item in rows if item["energy_j"] is not None]
        stresses = [item["stress_mpa"] for item in rows if item["stress_mpa"] is not None]
        return (max(energies) if energies else None, max(stresses) if stresses else None)

    # ------------------------------------------------------------------ 判定核心

    def _basis_note(
        self,
        energy: float | None,
        stress: float | None,
        energy_index: int | None,
        stress_index: int | None,
        final_index: int,
    ) -> str:
        parts: list[str] = []
        if energy is not None:
            parts.append(f"能量 {energy:g}J→{LEVELS[energy_index or 0]['name']}")
        if stress is not None:
            parts.append(f"应力 {stress:g}MPa→{LEVELS[stress_index or 0]['name']}")
        head = "、".join(parts) if parts else "当天无有效读数"
        return f"{head}；取高：{LEVELS[final_index]['name']}"

    def _evaluate_day(self, station_id: int, day: str, rule: dict[str, Any]) -> dict[str, Any] | None:
        """按给定口径对某测点某天做一次纯计算（不落库）。"""
        energy, stress = self._aggregate(station_id, day)
        if energy is None and stress is None:
            return None
        energy_index = classify_index(energy, rule["energy_thresholds"])
        stress_index = classify_index(stress, rule["stress_thresholds"])
        final_index = max(idx for idx in (energy_index, stress_index) if idx is not None)
        level = LEVELS[final_index]
        return {
            "station_id": station_id,
            "date": day,
            "level_code": level["code"],
            "level_name": level["name"],
            "level_index": final_index,
            "max_energy": energy,
            "max_stress": stress,
            "rule_id": rule["id"],
            "rule_version": rule["version"],
            "basis_note": self._basis_note(energy, stress, energy_index, stress_index, final_index),
        }

    def _save_grade(self, evaluated: dict[str, Any], *, closed: bool) -> dict[str, Any]:
        """落评级快照：同日同测点已存在时，只允许口径/数值更新，等级不许往低覆盖。"""
        existing = self._grade_row(evaluated["station_id"], evaluated["date"])
        if existing is None:
            row = {"id": self._next_id("grade"), "closed": closed, "updated_at": _now_text()}
            row.update(evaluated)
            self.day_grades.append(row)
            return row
        if evaluated["level_index"] >= existing["level_index"]:
            existing.update(evaluated)
        existing["closed"] = closed or existing["closed"]
        existing["updated_at"] = _now_text()
        return existing

    def _upsert_raise_alert(
        self,
        station: dict[str, Any],
        day: str,
        previous: dict[str, Any] | None,
        grade: dict[str, Any],
        reason: str,
    ) -> dict[str, Any] | None:
        """抬高才推提醒；同日已有提醒则更新（签收后再次抬高会重新进清单）。"""
        previous_index = previous["level_index"] if previous else NO_READING_BASELINE
        if grade["level_index"] <= previous_index:
            return None
        alert = next(
            (item for item in self.alerts
             if item["station_id"] == station["id"] and item["date"] == day),
            None,
        )
        from_level = previous["level_name"] if previous else "无评级"
        payload = {
            "from_level": from_level,
            "from_index": previous_index,
            "to_level": grade["level_name"],
            "to_index": grade["level_index"],
            "reason": reason,
            "rule_version": grade["rule_version"],
        }
        if alert is None:
            alert = {
                "id": self._next_id("alert"),
                "station_id": station["id"],
                "station_code": station["code"],
                "station_name": station["name"],
                "area": station["area"],
                "date": day,
                "acked": False,
                "acked_by": None,
                "acked_at": None,
                "created_at": _now_text(),
            }
            alert.update(payload)
            self.alerts.append(alert)
        else:
            # 只在新结果更高时抬升提醒；签收后再次抬高视为新提醒，重新挂回清单。
            if grade["level_index"] > alert["to_index"] or alert["acked"]:
                alert.update(payload)
                alert["acked"] = False
                alert["acked_by"] = None
                alert["acked_at"] = None
        return alert

    def _reconcile_day_alert(
        self,
        station: dict[str, Any],
        day: str,
        grade: dict[str, Any] | None,
        reason: str,
    ) -> None:
        """顺序重算后核对当天提醒：仍抬高则 upsert，不再抬高且未签收的提醒撤下。"""
        previous = self._latest_grade_before(station["id"], day)
        previous_index = previous["level_index"] if previous else NO_READING_BASELINE
        alert = next(
            (item for item in self.alerts
             if item["station_id"] == station["id"] and item["date"] == day),
            None,
        )
        is_raise = grade is not None and grade["level_index"] > previous_index
        if is_raise:
            assert grade is not None
            self._upsert_raise_alert(station, day, previous, grade, reason)
        elif alert is not None and not alert["acked"]:
            # 顺序重算后基线被抬高，当天已不构成新的抬升；尚未签收的提醒撤下。
            self.alerts = [item for item in self.alerts if item["id"] != alert["id"]]

    def _recompute_station_from(
        self,
        station: dict[str, Any],
        start_day: str,
        reason: str,
    ) -> list[dict[str, Any]]:
        """从指定日期起按日升序逐日重算一个测点（收盘日跳过，保持冻结）。"""
        changed: list[dict[str, Any]] = []
        day = start_day
        guard = 0
        while day <= self.business_today and guard < 400:
            guard += 1
            if not self.is_closed(day):
                rule = self.rule_for(day)
                evaluated = self._evaluate_day(station["id"], day, rule)
                if evaluated is not None:
                    grade = self._save_grade(evaluated, closed=False)
                    self._reconcile_day_alert(station, day, grade, reason)
                    changed.append(grade)
                else:
                    self._reconcile_day_alert(station, day, None, reason)
            day = _shift_date(day, 1)
        return changed

    def _recompute_all_open_days(self, start_day: str, reason: str) -> None:
        """口径调整时调用：所有测点从生效日起顺序重算，收盘日不动。"""
        for station in self.stations:
            self._recompute_station_from(station, start_day, reason)

    # ------------------------------------------------------------------ 对外动作

    def backfill_reading(
        self,
        station_id: int,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        """补一条读数，然后从当天起顺序重算；收盘日、未来日一律拒绝。"""
        station = self.find_station(station_id)
        if station is None:
            return None, f"测点 {station_id} 不存在"
        day = str(values.get("date") or "").strip()
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return None, "日期格式应为 YYYY-MM-DD"
        if day > self.business_today:
            return None, f"{day} 晚于当前日期 {self.business_today}，不能提前补数"
        if self.is_closed(day):
            return (
                None,
                f"{day} 已按当日口径收盘，历史评级已冻结；补数只能落在未收盘日期",
            )
        energy = _to_float(values.get("energy_j"))
        stress = _to_float(values.get("stress_mpa"))
        if energy is None and stress is None:
            return None, "微震能量与应力值至少要补一个有效数字"
        if energy is not None and energy < 0:
            return None, "微震能量不能为负"
        if stress is not None and stress < 0:
            return None, "应力值不能为负"
        clock = str(values.get("time") or "当前班次").strip()
        ts = f"{day} {clock}" if ":" in clock else f"{day} 12:00 补录"
        reading = {
            "id": self._next_id("reading"),
            "station_id": station_id,
            "ts": ts,
            "energy_j": energy,
            "stress_mpa": stress,
            "source": str(values.get("source") or "补数"),
        }
        self.readings.append(reading)
        self._recompute_station_from(station, day, "补数重算")
        grade = self._grade_row(station_id, day)
        return {"reading": reading, "grade": grade}, f"补数已入账，{day} 起的评级已按日顺序重算"

    def register_relief(
        self,
        station_id: int,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        """登记一次解危措施；解危不改评级，只更新看板上的最近解危时间。"""
        station = self.find_station(station_id)
        if station is None:
            return None, f"测点 {station_id} 不存在"
        measure = str(values.get("measure") or "").strip()
        if not measure:
            return None, "解危措施不能为空"
        ts = str(values.get("ts") or "").strip()
        if not ts:
            ts = _now_text()
        relief = {
            "id": self._next_id("relief"),
            "station_id": station_id,
            "station_code": station["code"],
            "ts": ts,
            "measure": measure,
            "operator": str(values.get("operator") or "值班调度").strip() or "值班调度",
        }
        self.reliefs.append(relief)
        return relief, f"{station['code']} 解危措施已登记"

    def create_rule(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """新增一版判定口径：收盘日不可追溯；生效日起的未收盘日期按新口径顺序重算。"""
        version = str(values.get("version") or "").strip()
        if not version:
            return None, "口径版本号不能为空"
        if any(rule["version"] == version for rule in self.rules):
            return None, f"口径版本 {version} 已存在"
        effective_date = str(values.get("effective_date") or "").strip()
        try:
            datetime.strptime(effective_date, "%Y-%m-%d")
        except ValueError:
            return None, "生效日期格式应为 YYYY-MM-DD"
        frozen = self.closed_through()
        if frozen and effective_date <= frozen:
            return None, f"{effective_date} 已收盘，新口径不能追溯到已收盘日期"
        if effective_date > self.business_today:
            return None, "生效日期不能晚于当前日期"

        def parse_thresholds(key: str) -> list[float] | None:
            raw = values.get(key)
            if not isinstance(raw, list) or len(raw) != 4:
                return None
            parsed = [_to_float(item) for item in raw]
            if any(item is None or item < 0 for item in parsed):
                return None
            if parsed != sorted(parsed):
                return None
            return [float(item) for item in parsed]  # type: ignore[arg-type]

        energy_thresholds = parse_thresholds("energy_thresholds")
        stress_thresholds = parse_thresholds("stress_thresholds")
        if energy_thresholds is None or stress_thresholds is None:
            return None, "两档门槛都必须是 4 个非负且从小到大排列的数字（蓝/黄/橙/红）"
        rule = {
            "id": self._next_id("rule"),
            "version": version,
            "name": str(values.get("name") or f"微震分级口径 {version}").strip(),
            "effective_date": effective_date,
            "energy_thresholds": energy_thresholds,
            "stress_thresholds": stress_thresholds,
            "basis": str(
                values.get("basis")
                or "按微震能量与应力值双重门槛定级，取两者较高等级"
            ).strip(),
            "created_at": _now_text(),
        }
        self.rules.append(rule)
        self._recompute_all_open_days(effective_date, f"口径调整（{version}）")
        return rule, f"口径 {version} 已生效，{effective_date} 起未收盘日期已按新口径重算"

    def close_day(self, day: str | None) -> tuple[dict[str, Any] | None, str]:
        """收盘：只允许收最早一个未收盘日。收盘后评级与所用口径一并冻结。"""
        target = (day or "").strip()
        closed_through = self.closed_through()
        expected = _shift_date(closed_through, 1) if closed_through else self._earliest_data_day()
        if not target:
            target = expected
        if target != expected:
            return None, f"只能按顺序收盘，下一个待收盘日期是 {expected}"
        if target > self.business_today:
            return None, f"{target} 还没有数据，无法收盘"
        # 收盘前用当天适用口径最后算一遍，确保「以当天收盘的口径为准」。
        rule = self.rule_for(target)
        for station in self.stations:
            evaluated = self._evaluate_day(station["id"], target, rule)
            if evaluated is not None:
                self._save_grade(evaluated, closed=True)
        state = self._day_state(target)
        state["closed"] = True
        state["closed_at"] = _now_text()
        return {"date": target, "closed_at": state["closed_at"], "rule_version": rule["version"]}, (
            f"{target} 已按口径 {rule['version']} 收盘，评级已冻结"
        )

    def _earliest_data_day(self) -> str:
        return min(item["ts"][:10] for item in self.readings)

    def acknowledge_alert(self, alert_id: int, operator: str) -> tuple[dict[str, Any] | None, str]:
        alert = self.find_alert(alert_id)
        if alert is None:
            return None, f"提醒 {alert_id} 不存在"
        if alert["acked"]:
            return alert, "该提醒已签收，无需重复操作"
        alert["acked"] = True
        alert["acked_by"] = operator or "值班调度"
        alert["acked_at"] = _now_text()
        return alert, "提醒已签收"

    # ------------------------------------------------------------------ 看板出参

    def _station_view(self, station: dict[str, Any], as_of: str) -> dict[str, Any]:
        current = self._grade_on(station["id"], as_of)
        yday = self._grade_row(station["id"], _shift_date(as_of, -1))
        dby = self._grade_row(station["id"], _shift_date(as_of, -2))
        baseline = yday or dby
        raised = bool(current and baseline and current["level_index"] > baseline["level_index"])
        relief = max(
            (item for item in self.reliefs if item["station_id"] == station["id"]),
            key=lambda item: item["ts"],
            default=None,
        )
        return {
            "station": station,
            "grade": current,
            "stale": current is not None and current["date"] != as_of,
            "yesterday": yday,
            "day_before": dby,
            "raised": raised,
            "last_relief": relief,
        }

    def board(self) -> dict[str, Any]:
        self.ensure_seed()
        as_of = self.business_today
        area_map: dict[str, list[dict[str, Any]]] = {}
        for station in self.stations:
            view = self._station_view(station, as_of)
            area_map.setdefault(station["area"], []).append(view)
        areas: list[dict[str, Any]] = []
        counts = [{**level, "count": 0} for level in LEVELS]
        for area, views in area_map.items():
            # 同区域：预警等级高的在前，再比当天能量，最后按测点编号兜底。
            views.sort(
                key=lambda view: (
                    -(view["grade"]["level_index"] if view["grade"] else -1),
                    -(float(view["grade"]["max_energy"] or 0) if view["grade"] else 0.0),
                    view["station"]["code"],
                )
            )
            max_index = max((view["grade"]["level_index"] for view in views if view["grade"]), default=-1)
            areas.append({"area": area, "max_level_index": max_index, "stations": views})
            for view in views:
                if view["grade"]:
                    counts[view["grade"]["level_index"]]["count"] += 1
        # 区域之间：当前最高档更危险的排前面，档位相同按区域名稳定排序。
        areas.sort(key=lambda item: (-item["max_level_index"], item["area"]))
        open_alerts = [
            alert for alert in sorted(self.alerts, key=lambda item: (item["date"], -item["to_index"]))
            if not alert["acked"]
        ]
        return {
            "as_of": as_of,
            "closed_through": self.closed_through(),
            "next_close_day": _shift_date(self.closed_through(), 1) if self.closed_through() else None,
            "rule": self._rule_view(self.current_rule()),
            "levels": LEVELS,
            "counts": counts,
            "areas": areas,
            "open_alert_count": len(open_alerts),
            "alerts": open_alerts,
        }

    def _rule_view(self, rule: dict[str, Any]) -> dict[str, Any]:
        return {key: rule[key] for key in rule}

    def list_stations(self) -> list[dict[str, Any]]:
        self.ensure_seed()
        return [dict(station) for station in self.stations]

    def list_rules(self) -> list[dict[str, Any]]:
        self.ensure_seed()
        return [self._rule_view(rule) for rule in sorted(self.rules, key=lambda item: item["effective_date"])]

    def day_calendar(self) -> dict[str, Any]:
        self.ensure_seed()
        start = self._earliest_data_day()
        days: list[dict[str, Any]] = []
        day = start
        while day <= self.business_today:
            state = next((item for item in self.day_states if item["date"] == day), None)
            rule = self.rule_for(day)
            days.append({
                "date": day,
                "closed": bool(state and state["closed"]),
                "closed_at": state["closed_at"] if state else None,
                "rule_version": rule["version"],
            })
            day = _shift_date(day, 1)
        return {
            "as_of": self.business_today,
            "closed_through": self.closed_through(),
            "next_close_day": _shift_date(self.closed_through(), 1) if self.closed_through() else None,
            "days": days,
        }

    def station_detail(self, station_id: int) -> dict[str, Any] | None:
        self.ensure_seed()
        station = self.find_station(station_id)
        if station is None:
            return None
        as_of = self.business_today
        history = sorted(
            (dict(row) for row in self.day_grades if row["station_id"] == station_id),
            key=lambda row: row["date"],
            reverse=True,
        )
        readings = sorted(
            (dict(item) for item in self.readings if item["station_id"] == station_id),
            key=lambda item: item["ts"],
            reverse=True,
        )
        reliefs = sorted(
            (dict(item) for item in self.reliefs if item["station_id"] == station_id),
            key=lambda item: item["ts"],
            reverse=True,
        )
        alerts = sorted(
            (dict(item) for item in self.alerts if item["station_id"] == station_id),
            key=lambda item: item["date"],
            reverse=True,
        )
        return {
            **self._station_view(station, as_of),
            "station": station,
            "as_of": as_of,
            "history": history,
            "readings": readings,
            "reliefs": reliefs,
            "alerts": alerts,
        }

    def list_alerts(self, scope: str = "open") -> list[dict[str, Any]]:
        self.ensure_seed()
        alerts = sorted(self.alerts, key=lambda item: (item["acked"], item["date"], -item["to_index"]))
        if scope == "open":
            alerts = [item for item in alerts if not item["acked"]]
        return [dict(item) for item in alerts]

    # ------------------------------------------------------------------ 种子数据

    def _add_station(self, code: str, name: str, area: str) -> dict[str, Any]:
        station = {"id": self._next_id("station"), "code": code, "name": name, "area": area}
        self.stations.append(station)
        return station

    def _add_reading(self, station_id: int, ts: str, energy: float | None, stress: float | None) -> None:
        self.readings.append({
            "id": self._next_id("reading"),
            "station_id": station_id,
            "ts": ts,
            "energy_j": energy,
            "stress_mpa": stress,
            "source": "微震台网",
        })

    def _add_rule(
        self,
        version: str,
        effective_date: str,
        energy_thresholds: list[float],
        stress_thresholds: list[float],
        basis: str,
    ) -> dict[str, Any]:
        rule = {
            "id": self._next_id("rule"),
            "version": version,
            "name": f"微震分级口径 {version}",
            "effective_date": effective_date,
            "energy_thresholds": energy_thresholds,
            "stress_thresholds": stress_thresholds,
            "basis": basis,
            "created_at": f"{effective_date} 08:00",
        }
        self.rules.append(rule)
        return rule

    def _add_relief(self, station_id: int, ts: str, measure: str, operator: str) -> None:
        station = self.find_station(station_id)
        assert station is not None
        self.reliefs.append({
            "id": self._next_id("relief"),
            "station_id": station_id,
            "station_code": station["code"],
            "ts": ts,
            "measure": measure,
            "operator": operator,
        })

    def _build_seed(self) -> None:
        """构造一段有头有尾的演示序列：10-01~10-03 已收盘，10-04/10-05 未收盘。"""
        s1 = self._add_station("MS-01", "北翼1#掘进面", "北翼采区")
        s2 = self._add_station("MS-02", "北翼2#回采面", "北翼采区")
        s3 = self._add_station("MS-03", "北翼3#备用面", "北翼采区")
        s4 = self._add_station("MS-04", "北翼4#掘进面", "北翼采区")
        s5 = self._add_station("MS-05", "南翼1#回采面", "南翼采区")
        s6 = self._add_station("MS-06", "南翼2#掘进面", "南翼采区")
        s7 = self._add_station("MS-07", "南翼3#巷道", "南翼采区")
        s8 = self._add_station("MS-08", "西翼集中巷", "西翼巷道")

        series: dict[int, list[tuple[str, float | None, float | None]]] = {
            s1["id"]: [
                ("2026-10-01 08:30", 5e3, 9.5),
                ("2026-10-02 09:10", 3e4, 10.8),
                ("2026-10-03 10:05", 6e4, 11.5),
                ("2026-10-04 11:20", 1.5e5, 12.6),
                ("2026-10-05 09:00", 4e5, 13.5),
                ("2026-10-05 16:20", 8e5, 13.8),
            ],
            s2["id"]: [
                ("2026-10-01 08:40", 8e3, 9.0),
                ("2026-10-02 09:00", 7e3, 9.2),
                ("2026-10-03 10:10", 8.5e3, 9.6),
                ("2026-10-04 11:00", 9e4, 11.9),
                ("2026-10-05 15:40", 9e4, 11.9),
            ],
            s3["id"]: [
                ("2026-10-01 08:20", 2e4, 10.4),
                ("2026-10-02 09:40", 4e4, 10.9),
                ("2026-10-03 10:30", 5e4, 11.0),
                ("2026-10-04 14:00", 3e4, 10.6),
                ("2026-10-05 15:10", 4.5e4, 10.7),
            ],
            s4["id"]: [
                ("2026-10-01 08:35", 6e4, 11.2),
                ("2026-10-02 09:25", 7e4, 11.8),
                ("2026-10-03 10:15", 1.2e5, 12.4),
                ("2026-10-04 13:40", 8e4, 11.6),
                ("2026-10-05 16:00", 3.2e5, 13.2),
            ],
            s5["id"]: [
                ("2026-10-01 08:10", 1e3, 8.2),
                ("2026-10-02 09:30", 3e4, 10.2),
                ("2026-10-03 10:40", 2e4, 9.9),
                ("2026-10-04 13:20", 4e4, 10.3),
            ],
            s6["id"]: [
                ("2026-10-01 08:45", 6e5, 14.2),
                ("2026-10-02 09:50", 7e5, 14.5),
                ("2026-10-03 10:50", 5.5e5, 14.0),
                ("2026-10-04 14:30", 6.2e5, 14.3),
                ("2026-10-05 08:50", 4e5, 13.9),
            ],
            s7["id"]: [
                ("2026-10-01 08:15", 1e5, 12.2),
                ("2026-10-02 09:35", 9e4, 12.0),
                ("2026-10-03 10:20", 1.1e5, 12.6),
                ("2026-10-04 12:40", 9.5e4, 12.3),
                ("2026-10-05 15:20", 1e5, 14.3),
            ],
            s8["id"]: [
                ("2026-10-01 08:50", 2e3, 8.8),
                ("2026-10-02 09:20", 5e3, 9.4),
                ("2026-10-03 10:35", 8e3, 9.8),
                ("2026-10-04 13:10", 2e4, 10.5),
                ("2026-10-05 14:50", 9e4, 11.6),
            ],
        }
        for station_id, items in series.items():
            for ts, energy, stress in items:
                self._add_reading(station_id, ts, energy, stress)

        self.business_today = max(item["ts"][:10] for item in self.readings)

        self._add_relief(s3["id"], "2026-10-04 15:20", "大直径钻孔卸压（孔深 25m）", "防冲队-李队")
        self._add_relief(s4["id"], "2026-10-02 14:00", "煤层注水卸压", "防冲队-王队")
        self._add_relief(s6["id"], "2026-10-05 09:30", "煤层注水卸压", "防冲队-李队")

        v1 = self._add_rule(
            "V1.0",
            "2026-01-01",
            [1e4, 1e5, 5e5, 1e6],
            [10.0, 12.0, 14.0, 16.0],
            "初版口径：能量 1e4/1e5/5e5/1e6 J，应力 10/12/14/16 MPa 对应蓝/黄/橙/红",
        )
        # 已收盘日（10-01~10-03）：直接按收盘时口径 V1.0 落冻结快照，不产生面向当前班次的提醒。
        for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
            state = self._day_state(day)
            state["closed"] = True
            state["closed_at"] = f"{day} 20:00"
            for station in self.stations:
                evaluated = self._evaluate_day(station["id"], day, v1)
                if evaluated is not None:
                    self._save_grade(evaluated, closed=True)

        # 未收盘日：先用 V1.0 顺序算一遍（模拟口径调整前的运行态），提醒随之产生。
        for station in self.stations:
            self._recompute_station_from(station, "2026-10-04", "评级抬高")

        # 当天启用 V2.0（收紧能量黄/橙门槛）：只重算未收盘日期，收盘三天保持 V1.0 不动。
        v2 = self._add_rule(
            "V2.0",
            "2026-10-05",
            [1e4, 8e4, 3e5, 1e6],
            [10.0, 12.0, 14.0, 16.0],
            "防冲队新口径：黄色能量门槛由 1e5 收紧到 8e4 J，橙色由 5e5 收紧到 3e5 J；应力门槛不变",
        )
        self._recompute_all_open_days(v2["effective_date"], "口径调整（V2.0）")


board_service = RockburstBoardService()
