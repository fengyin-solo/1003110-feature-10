"""冲击地压分级看板领域服务。

这里只保留一份判定口径，所有页面读到的等级都来自同一张等级结果表：

- 微震能量、应力值各自分四档阈值，取两条轴线上的高者作为测点当日等级；
- 口径做成带生效日期的版本表，等级结果行落库时快照所用口径版本与阈值；
- 日期只能按顺序收盘（last_closed_date + 1），收盘后的日期拒绝补录、拒绝重算；
- 补数只能 upsert 当天读数，按当天生效口径重算当天这一行，不触碰更早的行；
- 发布新口径只重算未收盘日期，已收盘日期永远按当时口径保留；
- 当日等级高于此前最近一日等级时，向值班调度提醒清单推一条「待处置」提醒。
"""
from __future__ import annotations

from datetime import date as date_cls
from datetime import timedelta
from typing import Any

from app.store import store

# ------------------------------------------------------------------ 常量与口径

TODAY = "2026-10-04"

# 等级从低到高：看板分组排序、提醒比较都以 level_idx 为准，名称只用于展示
LEVELS: list[tuple[str, int]] = [
    ("无预警", 0),
    ("蓝色", 1),
    ("黄色", 2),
    ("橙色", 3),
    ("红色", 4),
]
LEVEL_NAMES = [name for name, _ in LEVELS]
NO_DATA = "无数据"

# 阈值轴：蓝/黄/橙/红 的「达到该档」的下限，等级取轴上满足阈值的最高档
ENERGY_KEYS = ["energy_blue", "energy_yellow", "energy_orange", "energy_red"]
STRESS_KEYS = ["stress_blue", "stress_yellow", "stress_orange", "stress_red"]

DEFAULT_BASIS: dict[str, Any] = {
    "id": 1,
    "name": "v1",
    "note": "初始口径：能量与应力双轴取高",
    "effective_date": "2026-09-01",
    # 微震能量单位：焦耳（J）
    "energy_blue": 1_000.0,
    "energy_yellow": 10_000.0,
    "energy_orange": 100_000.0,
    "energy_red": 1_000_000.0,
    # 应力值单位：兆帕（MPa）
    "stress_blue": 10.0,
    "stress_yellow": 12.0,
    "stress_orange": 16.0,
    "stress_red": 20.0,
    "created_at": f"{TODAY} 08:00",
}

# 旧平铺页的监测状态动作，沿用原有状态序列
FLOW_ORDER = ["正常监测", "应力集中", "预警处置", "已解危"]
FLOW_ACTION_TARGET = {"应力预警": "应力集中", "解危处置": "预警处置", "解危确认": "已解危"}


def _shift_day(day: str, delta: int) -> str:
    return str(date_cls.fromisoformat(day) + timedelta(days=delta))


def _axis_level(value: float, basis: dict[str, Any], keys: list[str]) -> int:
    """单轴分级：达到哪档阈值就给哪档，都不满足为无预警。"""
    level = 0
    for idx, key in enumerate(keys, start=1):
        if value >= float(basis[key]):
            level = idx
    return level


def classify(energy: float, stress: float, basis: dict[str, Any]) -> tuple[str, int]:
    """全平台唯一的等级判定入口：能量、应力双轴取高。"""
    level_idx = max(
        _axis_level(energy, basis, ENERGY_KEYS),
        _axis_level(stress, basis, STRESS_KEYS),
    )
    return LEVEL_NAMES[level_idx], level_idx


def _to_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class RockburstDomain:
    """分级看板、历史等级、口径版本与调度提醒共用的领域对象（内存实现）。"""

    def __init__(self) -> None:
        self._tables = {
            "rb_points": store.rows("rb_points"),
            "rb_readings": store.rows("rb_readings"),
            "rb_grades": store.rows("rb_grades"),
            "rb_reliefs": store.rows("rb_reliefs"),
            "rb_basis": store.rows("rb_basis"),
            "rb_reminders": store.rows("rb_reminders"),
            "rb_meta": store.rows("rb_meta"),
        }
        if not self._tables["rb_meta"]:
            self._seed()

    # ------------------------------------------------------------ 基础读取

    @property
    def meta(self) -> dict[str, Any]:
        return self._tables["rb_meta"][0]

    def _points(self) -> list[dict[str, Any]]:
        return self._tables["rb_points"]

    def _find_point(self, point_id: int) -> dict[str, Any] | None:
        return next((row for row in self._points() if int(row["id"]) == point_id), None)

    def _basis_on(self, day: str) -> dict[str, Any]:
        """当天收盘口径：取生效日期不晚于当天的最新版本。"""
        candidates = [b for b in self._tables["rb_basis"] if b["effective_date"] <= day]
        return sorted(candidates, key=lambda b: b["effective_date"])[-1]

    def _grade_map(self) -> dict[tuple[int, str], dict[str, Any]]:
        return {(int(g["point_id"]), g["date"]): g for g in self._tables["rb_grades"]}

    def _readings_by_key(self) -> dict[tuple[int, str], dict[str, Any]]:
        return {(int(r["point_id"]), r["date"]): r for r in self._tables["rb_readings"]}

    def _previous_grade(self, point_id: int, day: str) -> dict[str, Any] | None:
        """该测点在某天之前最近的一日等级，作为抬高/回落的比较基准。"""
        earlier = [
            g for g in self._tables["rb_grades"]
            if int(g["point_id"]) == point_id and g["date"] < day
        ]
        return sorted(earlier, key=lambda g: g["date"])[-1] if earlier else None

    # ------------------------------------------------------------ 等级计算与提醒

    def _apply_grade(self, point_id: int, day: str) -> dict[str, Any] | None:
        """按当天读数与当天生效口径，写入（或刷新）该日等级行；收盘行直接跳过。

        返回等级行；当天没有读数时返回 None（不保留空等级）。
        """
        reading = self._readings_by_key().get((point_id, day))
        if reading is None:
            return None
        grades = self._tables["rb_grades"]
        existing = next(
            (g for g in grades if int(g["point_id"]) == point_id and g["date"] == day),
            None,
        )
        # 已收盘的日子不动：补数、口径调整都不允许改这一行
        if existing is not None and existing.get("closed"):
            return existing

        basis = self._basis_on(day)
        energy = float(reading["energy"])
        stress = float(reading["stress"])
        level_name, level_idx = classify(energy, stress, basis)

        if existing is None:
            existing = {"point_id": point_id, "date": day}
            grades.append(existing)
        existing.update({
            "level": level_name,
            "level_idx": level_idx,
            "energy": energy,
            "stress": stress,
            "basis_id": basis["id"],
            "basis_name": basis["name"],
            "closed": False,
        })
        self._reconcile_reminder(point_id, day)
        return existing

    def _reconcile_reminder(self, point_id: int, day: str) -> None:
        """等级抬高推一条待处置提醒；等级回落则把当天未确认提醒标记已消除。"""
        grade = self._grade_map().get((point_id, day))
        if grade is None or grade.get("closed"):
            return
        previous = self._previous_grade(point_id, day)
        previous_idx = int(previous["level_idx"]) if previous else 0
        current_idx = int(grade["level_idx"])
        reminder = next(
            (r for r in self._tables["rb_reminders"]
             if int(r["point_id"]) == point_id and r["date"] == day),
            None,
        )
        point = self._find_point(point_id)
        code = point["code"] if point else f"测点{point_id}"

        if current_idx > previous_idx:
            prev_name = LEVEL_NAMES[previous_idx]
            message = f"{code} 预警等级抬高：{prev_name} → {LEVEL_NAMES[current_idx]}（{day}）"
            if reminder is None:
                reminder = {
                    "id": self._next_id("rb_reminders"),
                    "point_id": point_id,
                    "date": day,
                    "created_at": f"{day} 随等级判定",
                    "ack_at": None,
                    "ack_operator": None,
                }
                self._tables["rb_reminders"].append(reminder)
            # 补数/换口径导致再次抬高时，已消除的提醒要重新顶上来
            reminder.update({
                "from_level": prev_name,
                "to_level": LEVEL_NAMES[current_idx],
                "level_idx": current_idx,
                "status": "待处置",
                "message": message,
            })
        elif reminder is not None and reminder["status"] == "待处置":
            reminder["status"] = "已消除"
            reminder["message"] = f"{reminder['message']}；当日等级已回落至{LEVEL_NAMES[current_idx]}"

    def _next_id(self, table: str) -> int:
        return max((int(row["id"]) for row in self._tables[table]), default=0) + 1

    # ------------------------------------------------------------ 写操作

    def _assert_open(self, day: str) -> None:
        """收盘冻结：只允许操作最后收盘日之后的日期，且不能越过今天。"""
        last_closed = self.meta["last_closed_date"]
        if day <= last_closed:
            raise ValueError(f"{day} 已收盘，历史等级按当时判定保留，不能补数或重算")
        if day > self.meta["today"]:
            raise ValueError(f"{day} 晚于当前日期，暂不支持登记未来数据")

    def ingest_reading(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """补录/刷新某测点某天的读数，然后只顺着当天重新判定等级。"""
        point_id = payload.get("point_id")
        point = self._find_point(int(point_id)) if point_id is not None else None
        if point is None:
            point = next(
                (p for p in self._points() if p["code"] == str(payload.get("code") or "").strip()),
                None,
            )
        if point is None:
            return None, "测点不存在，请先确认监测编号"
        day = str(payload.get("date") or self.meta["today"]).strip()
        try:
            date_cls.fromisoformat(day)
        except ValueError:
            return None, "日期格式应为 YYYY-MM-DD"
        try:
            self._assert_open(day)
        except ValueError as exc:
            return None, str(exc)

        energy = _to_float(payload.get("energy"))
        stress = _to_float(payload.get("stress"))
        if energy is None or stress is None or energy < 0 or stress < 0:
            return None, "微震能量与应力值必须为不小于 0 的数字"
        count = payload.get("count")
        count = int(count) if _to_float(count) is not None else None

        # 补数只 upsert 当天这一条读数，不新增重复行、不影响其他日期
        reading = self._readings_by_key().get((int(point["id"]), day))
        if reading is None:
            reading = {"id": self._next_id("rb_readings"), "point_id": int(point["id"]), "date": day}
            self._tables["rb_readings"].append(reading)
        reading.update({"energy": energy, "stress": stress, "count": count})

        grade = self._apply_grade(int(point["id"]), day)
        # 补录较早的开放日时，后续开放日的提醒基准可能变化；等级行本身不受影响
        for later_day in sorted(
                d for pid, d in self._grade_map()
                if pid == int(point["id"]) and d > day):
            self._reconcile_reminder(int(point["id"]), later_day)
        return grade, f"{point['code']} {day} 读数已登记，等级判定为{grade['level']}"

    def close_day(self) -> tuple[str | None, str]:
        """按顺序收盘一天：冻结当天所有等级行，交接当天未确认的提醒。"""
        target = _shift_day(self.meta["last_closed_date"], 1)
        if target > self.meta["today"]:
            return None, "当前没有可收盘的日期"
        for grade in self._tables["rb_grades"]:
            if grade["date"] == target:
                grade["closed"] = True
        for reminder in self._tables["rb_reminders"]:
            if reminder["date"] == target and reminder["status"] == "待处置":
                reminder["status"] = "已确认"
                reminder["ack_at"] = f"{target} 收盘"
                reminder["ack_operator"] = "日收盘交接"
        self.meta["last_closed_date"] = target
        return target, f"{target} 已收盘，当天等级与所用口径已冻结"

    def publish_basis(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """发布新口径：只重算未收盘日期，已经收盘的日子保持原样。"""
        effective_date = str(payload.get("effective_date") or self.meta["today"]).strip()
        try:
            date_cls.fromisoformat(effective_date)
        except ValueError:
            return None, "生效日期格式应为 YYYY-MM-DD"
        if effective_date > self.meta["today"]:
            return None, "口径生效日期不能晚于今天"

        values: dict[str, float] = {}
        for key in ENERGY_KEYS + STRESS_KEYS:
            number = _to_float(payload.get(key))
            if number is None or number < 0:
                return None, f"阈值 {key} 必须为不小于 0 的数字"
            values[key] = number
        for keys in (ENERGY_KEYS, STRESS_KEYS):
            thresholds = [values[k] for k in keys]
            if thresholds != sorted(thresholds):
                return None, "同一轴线的阈值需按蓝 < 黄 < 橙 < 红 递增"

        basis = dict(DEFAULT_BASIS)
        new_id = self._next_id("rb_basis")
        basis.update({
            "id": new_id,
            "name": str(payload.get("name") or f"v{new_id}").strip(),
            "note": str(payload.get("note") or "").strip(),
            "effective_date": effective_date,
            "created_at": f"{self.meta['today']} 口径调整",
            **values,
        })
        self._tables["rb_basis"].append(basis)

        # 口径调整后按新口径重算未收盘日；closed 等级行在 _apply_grade 里直接跳过
        open_dates = sorted({
            g["date"] for g in self._tables["rb_grades"] if not g.get("closed")
        })
        for point in self._points():
            for day in open_dates:
                if self._readings_by_key().get((int(point["id"]), day)) is not None:
                    self._apply_grade(int(point["id"]), day)
        return basis, f"口径 {basis['name']} 已生效，未收盘日期已按新口径重算，收盘日不变"

    def register_relief(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        point = self._find_point(int(payload["point_id"])) if payload.get("point_id") is not None else None
        if point is None:
            return None, "测点不存在"
        day = str(payload.get("date") or self.meta["today"]).strip()
        measure = str(payload.get("measure") or "").strip()
        if not measure:
            return None, "请填写解危措施"
        try:
            date_cls.fromisoformat(day)
        except ValueError:
            return None, "日期格式应为 YYYY-MM-DD"
        if day > self.meta["today"]:
            return None, "解危日期不能晚于今天"
        relief = {
            "id": self._next_id("rb_reliefs"),
            "point_id": int(point["id"]),
            "date": day,
            "measure": measure,
            "operator": str(payload.get("operator") or "值班管理员").strip(),
        }
        self._tables["rb_reliefs"].append(relief)
        return relief, f"{point['code']} 已登记解危措施：{measure}"

    # ------------------------------------------------------------ 调度提醒

    def reminders(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = self._tables["rb_reminders"]
        if status:
            rows = [r for r in rows if r["status"] == status]
        points = {int(p["id"]): p for p in self._points()}
        result = []
        for row in sorted(rows, key=lambda r: (r["date"], -int(r["level_idx"])), reverse=True):
            point = points.get(int(row["point_id"]))
            item = dict(row)
            item["code"] = point["code"] if point else ""
            item["area"] = point["area"] if point else ""
            result.append(item)
        return result

    def ack_reminder(self, reminder_id: int, operator: str = "值班调度") -> tuple[dict[str, Any] | None, str]:
        reminder = next((r for r in self._tables["rb_reminders"] if int(r["id"]) == reminder_id), None)
        if reminder is None:
            return None, f"提醒 {reminder_id} 不存在"
        reminder["status"] = "已确认"
        reminder["ack_at"] = f"{self.meta['today']} 值班确认"
        reminder["ack_operator"] = operator
        return reminder, "提醒已确认"

    # ------------------------------------------------------------ 看板与详情

    def board(self, day: str | None = None, level: str | None = None) -> dict[str, Any]:
        """分级看板：按区域聚合，区域内先比等级再比能量，附近两日趋势与最近解危时间。"""
        day = day or self.meta["today"]
        grade_map = self._grade_map()
        reliefs = self._tables["rb_reliefs"]

        point_rows: list[dict[str, Any]] = []
        for point in self._points():
            pid = int(point["id"])
            grade = grade_map.get((pid, day))
            yesterday = grade_map.get((pid, _shift_day(day, -1)))
            before = grade_map.get((pid, _shift_day(day, -2)))
            point_reliefs = [r for r in reliefs if int(r["point_id"]) == pid]
            latest_relief = sorted(point_reliefs, key=lambda r: r["date"])[-1:] or None

            if grade is None:
                row = {
                    "point_id": pid, "code": point["code"], "flow_status": point["flow_status"],
                    "level": NO_DATA, "level_idx": -1, "energy": None, "stress": None,
                    "count": None, "yesterday_level": yesterday["level"] if yesterday else NO_DATA,
                    "yesterday_idx": yesterday["level_idx"] if yesterday else -1,
                    "before_yesterday_level": before["level"] if before else NO_DATA,
                    "trend": "none",
                    "recent_relief": latest_relief[0] if latest_relief else None,
                }
            else:
                y_idx = int(yesterday["level_idx"]) if yesterday else 0
                trend = "flat"
                if int(grade["level_idx"]) > y_idx:
                    trend = "up"
                elif int(grade["level_idx"]) < y_idx:
                    trend = "down"
                row = {
                    "point_id": pid, "code": point["code"], "flow_status": point["flow_status"],
                    "level": grade["level"], "level_idx": int(grade["level_idx"]),
                    "energy": grade["energy"], "stress": grade["stress"],
                    "count": self._readings_by_key().get((pid, day), {}).get("count"),
                    "yesterday_level": yesterday["level"] if yesterday else NO_DATA,
                    "yesterday_idx": int(yesterday["level_idx"]) if yesterday else -1,
                    "before_yesterday_level": before["level"] if before else NO_DATA,
                    "trend": trend,
                    "recent_relief": latest_relief[0] if latest_relief else None,
                }
            point_rows.append(row)

        if level:
            point_rows = [r for r in point_rows if r["level"] == level]

        # 区域聚合：区域顺序由区内最高等级决定；区内等级高者在前，同档能量大者在前
        areas: dict[str, list[dict[str, Any]]] = {}
        for point in self._points():
            areas.setdefault(point["area"], [])
        for row in point_rows:
            area = next(p["area"] for p in self._points() if int(p["id"]) == row["point_id"])
            areas[area].append(row)

        area_blocks = []
        for area, rows in areas.items():
            if not rows:
                continue
            rows.sort(key=lambda r: (r["level_idx"], r["energy"] if r["energy"] is not None else -1), reverse=True)
            top = rows[0]
            area_blocks.append({
                "area": area,
                "max_level": top["level"],
                "max_level_idx": top["level_idx"],
                "points": rows,
            })
        area_blocks.sort(
            key=lambda b: (b["max_level_idx"], b["points"][0]["energy"] or -1),
            reverse=True,
        )

        counts = {name: sum(1 for r in point_rows if r["level"] == name) for name, _ in LEVELS}
        counts[NO_DATA] = sum(1 for r in point_rows if r["level"] == NO_DATA)

        basis = None
        try:
            basis = self._basis_on(day)
        except IndexError:
            basis = None
        return {
            "date": day,
            "today": self.meta["today"],
            "last_closed_date": self.meta["last_closed_date"],
            "is_closed": day <= self.meta["last_closed_date"],
            "basis": basis,
            "counts": counts,
            "pending_reminders": len(self.reminders("待处置")),
            "areas": area_blocks,
        }

    def point_detail(self, point_id: int) -> dict[str, Any] | None:
        point = self._find_point(point_id)
        if point is None:
            return None
        pid = int(point_id)
        grades = sorted(
            (g for g in self._tables["rb_grades"] if int(g["point_id"]) == pid),
            key=lambda g: g["date"],
            reverse=True,
        )
        reading_map = self._readings_by_key()
        grade_rows = []
        for grade in grades:
            reading = reading_map.get((pid, grade["date"]))
            grade_rows.append({
                "date": grade["date"],
                "level": grade["level"],
                "level_idx": int(grade["level_idx"]),
                "energy": grade["energy"],
                "stress": grade["stress"],
                "count": reading.get("count") if reading else None,
                "basis_id": grade["basis_id"],
                "basis_name": grade["basis_name"],
                "closed": bool(grade.get("closed")),
            })
        reliefs = sorted(
            (r for r in self._tables["rb_reliefs"] if int(r["point_id"]) == pid),
            key=lambda r: r["date"],
            reverse=True,
        )
        return {
            "point": point,
            "grades": grade_rows,
            "reliefs": reliefs,
            "latest_relief": reliefs[0] if reliefs else None,
        }

    def basis_list(self) -> list[dict[str, Any]]:
        return sorted(self._tables["rb_basis"], key=lambda b: b["effective_date"])

    # ------------------------------------------------------------ 旧平铺页适配

    def flat_rows(self) -> list[dict[str, Any]]:
        """旧平铺表与看板共用等级结果表，避免两个页面对同一测点读出不同等级。"""
        board = self.board()
        rows_by_id = {row["point_id"]: row for area in board["areas"] for row in area["points"]}
        result = []
        for point in self._points():
            row = rows_by_id.get(int(point["id"]))
            relief = row["recent_relief"] if row else None
            result.append({
                "id": int(point["id"]),
                "监测编号": point["code"],
                "所在区域": point["area"],
                "微震能量": row["energy"] if row and row["energy"] is not None else "",
                "微震频次": row["count"] if row and row["count"] is not None else "",
                "应力值": row["stress"] if row and row["stress"] is not None else "",
                "预警等级": row["level"] if row else NO_DATA,
                "处置措施": relief["measure"] if relief else "",
                "监测状态": point["flow_status"],
                "status": point["flow_status"],
                "pending": point["flow_status"] != "已解危",
                "abnormal": bool(row and row["level_idx"] >= 3),
            })
        return result

    def run_flow_action(self, point_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        point = self._find_point(point_id)
        if point is None:
            return None, f"微震监测 {point_id} 不存在或已归档"
        if action not in FLOW_ACTION_TARGET:
            return None, f"动作「{action}」不属于冲击地压可执行范围"
        target = FLOW_ACTION_TARGET[action]
        point["flow_status"] = target
        if action == "解危确认":
            # 解危确认同步登记一条解危记录，看板上的「最近解危时间」随之更新
            self._tables["rb_reliefs"].append({
                "id": self._next_id("rb_reliefs"),
                "point_id": point_id,
                "date": self.meta["today"],
                "measure": "解危确认",
                "operator": "值班管理员",
            })
        return point, f"微震监测已{action}"

    def create_point(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in ("监测编号", "所在区域") if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing
        code = str(values["监测编号"]).strip()
        if any(p["code"] == code for p in self._points()):
            return None, ["监测编号已存在"]
        point = {
            "id": self._next_id("rb_points"),
            "code": code,
            "area": str(values["所在区域"]).strip(),
            "flow_status": FLOW_ORDER[0],
        }
        self._points().append(point)
        return point, []

    # ------------------------------------------------------------ 演示数据

    def _seed(self) -> None:
        """铺三天数据：前两天已收盘冻结，今天为开放日，覆盖抬高、回落、已解危各情形。"""
        meta = {"today": TODAY, "last_closed_date": "2026-10-01"}
        self._tables["rb_meta"].append(meta)
        self._tables["rb_basis"].append(dict(DEFAULT_BASIS))

        points = [
            ("WB-001", "3301工作面", "预警处置"),
            ("WB-002", "3301工作面", "应力集中"),
            ("WB-003", "3301工作面", "应力集中"),
            ("WB-004", "3302运输巷", "预警处置"),
            ("WB-005", "3302运输巷", "正常监测"),
            ("WB-006", "3302运输巷", "应力集中"),
            ("WB-007", "轨道下山", "预警处置"),
            ("WB-008", "轨道下山", "已解危"),
            ("WB-009", "轨道下山", "正常监测"),
            ("WB-010", "二采区井底", "预警处置"),
            ("WB-011", "二采区井底", "正常监测"),
        ]
        for idx, (code, area, flow) in enumerate(points, start=1):
            self._points().append({"id": idx, "code": code, "area": area, "flow_status": flow})

        # (测点, 日期) -> 能量J, 应力MPa, 频次
        readings: dict[str, tuple[float, float, int]] = {
            "WB-001:2026-10-02": (8_000.0, 13.2, 6),
            "WB-001:2026-10-03": (60_000.0, 15.0, 9),
            "WB-001:2026-10-04": (1_200_000.0, 21.5, 16),
            "WB-002:2026-10-02": (1_200.0, 10.8, 3),
            "WB-002:2026-10-03": (3_500.0, 11.2, 4),
            "WB-002:2026-10-04": (180_000.0, 17.8, 12),
            "WB-003:2026-10-02": (95_000.0, 14.1, 8),
            "WB-003:2026-10-03": (210_000.0, 16.8, 11),
            "WB-003:2026-10-04": (165_000.0, 17.2, 10),
            "WB-004:2026-10-02": (2_200.0, 11.0, 3),
            "WB-004:2026-10-03": (42_000.0, 14.5, 7),
            "WB-004:2026-10-04": (310_000.0, 18.2, 13),
            "WB-005:2026-10-02": (36_000.0, 13.0, 5),
            "WB-005:2026-10-03": (28_000.0, 13.6, 6),
            "WB-005:2026-10-04": (41_000.0, 13.1, 6),
            "WB-006:2026-10-02": (800.0, 9.2, 2),
            "WB-006:2026-10-03": (1_800.0, 10.4, 3),
            "WB-006:2026-10-04": (13_000.0, 12.7, 5),
            "WB-007:2026-10-02": (150_000.0, 17.0, 10),
            "WB-007:2026-10-03": (175_000.0, 18.6, 12),
            "WB-007:2026-10-04": (52_000.0, 15.2, 8),
            "WB-008:2026-10-02": (1_800_000.0, 22.4, 18),
            "WB-008:2026-10-03": (680_000.0, 19.6, 14),
            "WB-008:2026-10-04": (2_600.0, 10.9, 3),
            "WB-009:2026-10-02": (300.0, 8.8, 1),
            "WB-009:2026-10-03": (420.0, 9.1, 2),
            "WB-009:2026-10-04": (600.0, 9.4, 2),
            "WB-010:2026-10-02": (48_000.0, 15.5, 7),
            "WB-010:2026-10-03": (260_000.0, 18.9, 13),
            "WB-010:2026-10-04": (1_450_000.0, 21.8, 17),
            "WB-011:2026-10-02": (120.0, 8.5, 0),
            "WB-011:2026-10-03": (260.0, 9.0, 1),
            "WB-011:2026-10-04": (1_500.0, 10.6, 3),
        }
        # 按天补录再按天收盘：演示数据也必须走同一条判定/冻结路径
        for day in ("2026-10-02", "2026-10-03", "2026-10-04"):
            for code, _, _ in points:
                energy, stress, count = readings[f"{code}:{day}"]
                self.ingest_reading({
                    "code": code, "date": day,
                    "energy": energy, "stress": stress, "count": count,
                })
            if day < TODAY:
                self.close_day()

        for pid, day, measure, operator in [
            (1, "2026-09-20", "底板爆破卸压", "防冲队"),
            (3, "2026-10-03", "大直径卸压钻孔", "防冲队"),
            (6, "2026-09-28", "煤层爆破卸压", "防冲队"),
            (8, "2026-10-04", "煤层注水卸压", "防冲队"),
        ]:
            self._tables["rb_reliefs"].append({
                "id": self._next_id("rb_reliefs"),
                "point_id": pid, "date": day,
                "measure": measure, "operator": operator,
            })

        # 一条今天的抬高提醒已由值班调度确认，清单里保留待处置与已确认两种状态
        acked = next(
            (r for r in self._tables["rb_reminders"]
             if int(r["point_id"]) == 4 and r["date"] == TODAY),
            None,
        )
        if acked is not None:
            acked["status"] = "已确认"
            acked["ack_at"] = f"{TODAY} 09:20"
            acked["ack_operator"] = "值班调度"


domain = RockburstDomain()
