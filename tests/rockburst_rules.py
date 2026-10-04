"""分级看板业务规则验证：收盘冻结、补数只动当天、口径版本、提醒推送、看板一致。

运行：PYTHONPATH=. python3 ../tests/rockburst_rules.py
"""
from __future__ import annotations

import sys
import traceback

sys.path.insert(0, ".")
sys.path.insert(0, ".pylibs")

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" —— {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def fresh():
    # 每次取一个全新领域对象：store 是模块级单例，用新表替换掉
    from app import store as store_mod
    for key in ("rb_points", "rb_readings", "rb_grades",
                "rb_reliefs", "rb_basis", "rb_reminders", "rb_meta"):
        store_mod.store._tables[key] = []
    from app.services import rockburst_domain as mod
    return mod.RockburstDomain()


def grade_at(d, pid, day):
    g = next(
        (x for x in d._tables["rb_grades"] if int(x["point_id"]) == pid and x["date"] == day),
        None,
    )
    return g


def main() -> int:
    try:
        # 1. 双轴取高 + 阈值边界
        d = fresh()
        from app.services import rockburst_domain as mod
        b = d._basis_on("2026-10-04")
        check("能量达到红色但应力低 => 红色",
              d.ingest_reading({"code": "WB-009", "date": "2026-10-04",
                                "energy": 1_000_000, "stress": 0})[1].endswith("红色"))
        check("能量低但应力达到红色 => 红色",
              mod.classify(0, 20.0, b)[0] == "红色")
        check("恰等阈值计入该档（>= 语义）",
              mod.classify(1_000.0, 9.9, b)[0] == "蓝色")
        check("两轴不同档取高者",
              mod.classify(12_000.0, 17.0, b)[0] == "橙色")
    except Exception:
        FAILURES.append("分级基础异常")
        traceback.print_exc()

    try:
        # 2. 收盘冻结：已收盘日拒绝补数，历史等级原样
        d = fresh()
        g03_before = dict(grade_at(d, 1, "2026-10-03"))
        _, msg = d.ingest_reading({"code": "WB-001", "date": "2026-10-03",
                                   "energy": 9_999_999, "stress": 99})
        check("已收盘日补数被拒", "已收盘" in msg, msg)
        g03_after = grade_at(d, 1, "2026-10-03")
        check("拒绝后收盘等级行不被改写",
              g03_before["level"] == g03_after["level"] == "黄色"
              and g03_before["energy"] == g03_after["energy"])
        check("收盘行带冻结标记", g03_after["closed"] is True)
    except Exception:
        FAILURES.append("收盘冻结异常")
        traceback.print_exc()

    try:
        # 3. 补数只动当天：不跳级覆盖前面的行；当天等级随补数重算
        d = fresh()
        before_today = dict(grade_at(d, 1, "2026-10-04"))
        grades_for_1 = len([g for g in d._tables["rb_grades"] if int(g["point_id"]) == 1])
        _, m1 = d.ingest_reading({"code": "WB-001", "date": "2026-10-04",
                                  "energy": 500, "stress": 5})
        check("补数后当天降到无预警", m1.endswith("无预警"), m1)
        check("等级行总数不变（只 upsert 当天这一行，不新增不碰别的日期）",
              len([g for g in d._tables["rb_grades"] if int(g["point_id"]) == 1]) == grades_for_1)
        check("10-03 收盘等级仍是黄色（补今天不能改掉前面）",
              grade_at(d, 1, "2026-10-03")["level"] == "黄色")
        check("10-02 收盘等级仍是黄色",
              grade_at(d, 1, "2026-10-02")["level"] == "黄色")
        # 再补回去
        _, m2 = d.ingest_reading({"code": "WB-001", "date": "2026-10-04",
                                  "energy": 1_200_000, "stress": 21.5})
        check("补回后当天重新判定为红色", m2.endswith("红色"), m2)
        check("再次抬高时提醒重新顶到待处置",
              next(r for r in d.reminders("待处置") if r["code"] == "WB-001")["to_level"] == "红色")
    except Exception:
        FAILURES.append("补数规则异常")
        traceback.print_exc()

    try:
        # 4. 口径调整：新口径只重算未收盘日，收盘日照旧；口径是唯一判定入口
        d = fresh()
        closed_level = grade_at(d, 5, "2026-10-03")["level"]
        basis, bm = d.publish_basis({
            "name": "v2", "note": "黄橙阈值收紧",
            "energy_blue": 1_000, "energy_yellow": 10_000,
            "energy_orange": 30_000, "energy_red": 1_000_000,
            "stress_blue": 10, "stress_yellow": 12,
            "stress_orange": 14, "stress_red": 20,
        })
        check("口径发布成功", basis is not None, bm)
        check("收盘日等级未被新口径改动",
              grade_at(d, 5, "2026-10-03")["level"] == closed_level)
        check("收盘日仍快照旧口径 v1",
              grade_at(d, 5, "2026-10-03")["basis_name"] == "v1")
        # WB-005 今天 E=41000 应力13.1：旧口径黄色，新口径能量超 30000 => 橙色
        check("开放日按新口径重算（黄→橙）",
              grade_at(d, 5, "2026-10-04")["level"] == "橙色"
              and grade_at(d, 5, "2026-10-04")["basis_name"] == "v2")
        check("阈值非递增被拒",
              d.publish_basis({"energy_blue": 9, "energy_yellow": 8,
                               "energy_orange": 16, "energy_red": 20,
                               "stress_blue": 9, "stress_yellow": 8,
                               "stress_orange": 16, "stress_red": 20})[0] is None)
    except Exception:
        FAILURES.append("口径版本异常")
        traceback.print_exc()

    try:
        # 5. 收盘顺序：只能收 last_closed+1；未来日期拒绝
        d = fresh()
        _, cm = d.close_day()
        check("收盘目标是 10-04", cm.startswith("2026-10-04"), cm)
        _, cm2 = d.close_day()
        check("已是最新日期，无盘可收", "没有可收盘" in cm2, cm2)
        _, rm = d.ingest_reading({"code": "WB-001", "date": "2026-10-05",
                                  "energy": 1, "stress": 1})
        check("未来日期拒绝登记", "晚于当前日期" in rm, rm)
        check("收盘后今天也被冻结", grade_at(d, 1, "2026-10-04")["closed"] is True)
    except Exception:
        FAILURES.append("收盘顺序异常")
        traceback.print_exc()

    try:
        # 6. 提醒：抬高推送、回落消除、确认接口
        d = fresh()
        pending = {r["code"]: r for r in d.reminders("待处置")}
        check("等级抬高进入待处置清单（WB-001）", "WB-001" in pending)
        check("未抬高不推送（WB-005 平级）", "WB-005" not in pending)
        # WB-007 今天回落，其当天若有过抬高提醒应已消除
        rid = pending["WB-001"]["id"]
        acked, am = d.ack_reminder(rid)
        check("确认提醒成功", acked["status"] == "已确认", am)
        check("确认后不再出现在待处置清单",
              all(r["code"] != "WB-001" for r in d.reminders("待处置")))
        # 回落场景：WB-007 昨天橙今天黄
        d.ingest_reading({"code": "WB-009", "date": "2026-10-04", "energy": 12_000, "stress": 13})
        check("等级抬高（无预警->黄）推送黄提醒",
              any(r["code"] == "WB-009" and r["to_level"] == "黄色"
                  for r in d.reminders("待处置")))
        d.ingest_reading({"code": "WB-009", "date": "2026-10-04", "energy": 600, "stress": 9.4})
        check("等级回落时提醒标记已消除（留痕，不删除）",
              any(int(r["point_id"]) == 9 and r["status"] == "已消除"
                  for r in d._tables["rb_reminders"]))
    except Exception:
        FAILURES.append("提醒规则异常")
        traceback.print_exc()

    try:
        # 7. 看板与详情一致；区域/等级/能量排序；最近解危时间
        d = fresh()
        board = d.board()
        for area in board["areas"]:
            idxs = [p["level_idx"] for p in area["points"]]
            check(f"{area['area']} 区内等级降序", idxs == sorted(idxs, reverse=True))
            for p in area["points"]:
                detail = d.point_detail(p["point_id"])
                today_grade = next((g for g in detail["grades"] if g["date"] == board["date"]), None)
                if p["level"] == "无数据":
                    check(f"{p['code']} 无数据与详情一致", today_grade is None)
                else:
                    check(f"{p['code']} 看板等级={p['level']} 与详情一致",
                          today_grade is not None and today_grade["level"] == p["level"])
        max_idxs = [a["max_level_idx"] for a in board["areas"]]
        check("区域按区内最高等级降序", max_idxs == sorted(max_idxs, reverse=True))
        filtered = d.board(level="红色")
        check("等级过滤清单只含红色",
              all(p["level"] == "红色" for a in filtered["areas"] for p in a["points"]))
        check("红色清单计数=2", filtered["counts"]["红色"] == 2)
        wb008 = next(p for a in board["areas"] for p in a["points"] if p["code"] == "WB-008")
        check("最近解危时间显示 2026-10-04",
              wb008["recent_relief"] and wb008["recent_relief"]["date"] == "2026-10-04")
        # 详情里收盘行口径快照
        detail = d.point_detail(1)
        closed_row = next(g for g in detail["grades"] if g["date"] == "2026-10-03")
        check("详情可读出历史行收盘标记与口径", closed_row["closed"] and closed_row["basis_name"] == "v1")
        # 平铺表同样一致
        flat = next(r for r in d.flat_rows() if r["id"] == 1)
        check("旧平铺表等级与看板一致", flat["预警等级"] == "红色")
    except Exception:
        FAILURES.append("看板一致性异常")
        traceback.print_exc()

    print()
    if FAILURES:
        print(f"共 {len(FAILURES)} 组失败：{FAILURES}")
        return 1
    print("全部规则验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
