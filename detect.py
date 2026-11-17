"""2단계: 주 52시간 위험자와 지각·결근 패턴 탐지.

지각 여부는 CSV의 상태값을 믿지 않고, 출근 시각과 config.toml의 출근 기준
시각(개인 > 팀 > 기본값)을 비교해서 직접 계산합니다.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from common import (DATA_DIR, OUT_DIR, PATTERNS, WEEKDAYS_KO, holiday_adjacent_days,
                    is_workday, load_config, read_csv, start_minutes, to_hhmm,
                    to_min, week_start, write_csv)

LATE_FLAGS = {"LATE_REPEAT", "LATE_WEEKDAY_BIAS", "LATE_WORSENING"}
ABSENCE_FLAGS = {"ABSENCE_UNEXCUSED", "ABSENCE_HOLIDAY_ADJ"}

# 정답지 평가 때 "함께 탐지되어도 당연한" 패턴.
# 예: 월요일마다 지각하는 직원은 '반복 지각'에도 걸리는 것이 정상.
SECONDARY_OK = {
    "LATE_WEEKDAY_BIAS": {"LATE_REPEAT"},
    "LATE_WORSENING": {"LATE_REPEAT"},
    "ABSENCE_HOLIDAY_ADJ": {"ABSENCE_UNEXCUSED"},
}


def worked_hours(rec: dict) -> float:
    """실근로시간 = 퇴근 - 출근 - 휴게."""
    if not rec["clock_in"] or not rec["clock_out"]:
        return 0.0
    minutes = to_min(rec["clock_out"]) - to_min(rec["clock_in"]) - int(rec["break_min"] or 0)
    return max(0, minutes) / 60


def tier(hours: float | None, th: dict) -> str:
    """주 실근로시간 → 등급."""
    if hours is None:
        return "정상"
    if hours > th["weekly_limit"]:
        return "초과"
    if hours > th["weekly_danger"]:
        return "위험"
    if hours > th["weekly_caution"]:
        return "주의"
    return "정상"


def project_week(cfg: dict, day_hours: dict[date, float], cut: date) -> float | None:
    """cut 날짜까지의 누적으로 금요일까지의 주 근로시간을 예상한다.

    예상치 = 누적 + (근무한 날의 하루 평균) × (남은 근무일 수)
    """
    ws = week_start(cut)
    done = [h for d, h in day_hours.items() if ws <= d <= cut and h > 0]
    if not done:
        return None
    remaining = sum(1 for i in range(cut.weekday() + 1, 5)
                    if is_workday(cfg, ws + timedelta(days=i)))
    return sum(done) + sum(done) / len(done) * remaining


def analyze(cfg: dict, employees: list[dict], records: list[dict],
            as_of: date | None = None) -> dict:
    """as_of(기준일)까지의 기록으로 직원별 지표와 패턴을 계산한다."""
    th = cfg["thresholds"]
    grace = cfg["work"].get("late_grace_min", 0)
    all_dates = [date.fromisoformat(r["date"]) for r in records]
    as_of = as_of or max(all_dates)
    data_start = week_start(min(all_dates))
    ws = week_start(as_of)
    week_days = [ws + timedelta(days=i) for i in range(5) if is_workday(cfg, ws + timedelta(days=i))]
    midweek = bool(week_days) and as_of < week_days[-1]
    weeks = [data_start + timedelta(weeks=i) for i in range((ws - data_start).days // 7 + 1)]
    full_weeks = weeks[:-1] if midweek else weeks      # 주중이면 진행 중인 주는 패턴 계산에서 제외
    win = th["late_window_weeks"]
    recent_from = ws - timedelta(weeks=win - 1)
    prior_from = recent_from - timedelta(weeks=win)
    adjacent = holiday_adjacent_days(cfg)
    proj_cut = as_of if midweek else min(as_of, ws + timedelta(days=2))   # 결산이면 수요일 시점

    by_emp = defaultdict(list)
    for r, d in zip(records, all_dates):
        if d <= as_of:
            by_emp[r["emp_id"]].append((d, r))

    result = {}
    for e in employees:
        start = start_minutes(cfg, e["emp_id"], e["team"])
        day_hours, lates, absences, leaves = {}, [], [], []
        for d, r in sorted(by_emp[e["emp_id"]], key=lambda x: x[0]):
            if r["status"] == "결근":
                absences.append(d)
            elif r["status"] in ("연차", "반차"):
                leaves.append(d)
            if r["clock_in"]:
                day_hours[d] = day_hours.get(d, 0.0) + worked_hours(r)
                # 지각 판정: 정상 출근일(반차·휴일근무 제외)에 기준 시각보다 늦게 출근
                if r["status"] == "출근" and is_workday(cfg, d):
                    late_min = to_min(r["clock_in"]) - start - grace
                    if late_min > 0:
                        lates.append((d, late_min))

        weekly = {w: 0.0 for w in weeks}
        for d, h in day_hours.items():
            weekly[week_start(d)] = weekly.get(week_start(d), 0.0) + h

        hours = weekly[ws]
        projected = project_week(cfg, day_hours, proj_cut)
        late_days = [d for d, _ in lates]
        late_recent = sum(1 for d in late_days if d >= recent_from)
        late_prior = sum(1 for d in late_days if prior_from <= d < recent_from)
        abs_week = [d for d in absences if d >= ws]
        run = longest = 0
        for d in week_days:
            run = run + 1 if d in abs_week else 0
            longest = max(longest, run)

        # ── 패턴 판정 ──
        flags = {}
        danger_weeks = [w for w in full_weeks if weekly[w] > th["weekly_danger"]]
        over_weeks = [w for w in full_weeks if weekly[w] > th["weekly_limit"]]
        if len(danger_weeks) >= th["chronic_weeks"]:
            flags["OVERWORK_CHRONIC"] = f"{len(full_weeks)}주 중 {len(danger_weeks)}주가 {th['weekly_danger']}h 초과"
        elif over_weeks:
            flags["OVERWORK_SPIKE"] = "52h 초과 주: " + ", ".join(f"{w.month}/{w.day}" for w in over_weeks)
        if late_recent >= th["late_repeat_count"]:
            flags["LATE_REPEAT"] = f"최근 {win}주 지각 {late_recent}회"
        events = late_days + absences
        if len(events) >= th["bias_min_events"]:
            wd, cnt = Counter(d.weekday() for d in events).most_common(1)[0]
            if cnt / len(events) >= th["bias_share"]:
                flags["LATE_WEEKDAY_BIAS"] = f"{WEEKDAYS_KO[wd]}요일 {cnt}/{len(events)}건"
        if (data_start <= prior_from and late_recent >= th["late_repeat_count"]
                and late_recent >= th["worsening_ratio"] * late_prior):
            flags["LATE_WORSENING"] = f"이전 {win}주 {late_prior}회 → 최근 {win}주 {late_recent}회"
        if len(absences) >= th["absence_repeat_count"]:
            flags["ABSENCE_UNEXCUSED"] = f"무단결근 {len(absences)}회"
        adj_abs = [d for d in absences if d in adjacent]
        if len(adj_abs) >= th["holiday_adjacent_count"]:
            flags["ABSENCE_HOLIDAY_ADJ"] = f"휴일 전후 결근 {len(adj_abs)}회"

        result[e["emp_id"]] = {
            **e, "start": to_hhmm(start),
            "weekly": weekly, "hours": hours, "tier": tier(hours, th),
            "prev_hours": weekly.get(ws - timedelta(weeks=1)),
            "recent_hours": [weekly[w] for w in weeks[-4:]],
            "projected": projected, "proj_tier": tier(projected, th),
            "lates": lates, "late_week": sum(1 for d in late_days if d >= ws),
            "late_recent": late_recent, "late_prior": late_prior,
            "absences": absences, "abs_week": abs_week, "abs_run": longest,
            "leaves": leaves, "flags": flags,
        }

    return {"as_of": as_of, "week_start": ws, "week_days": week_days, "midweek": midweek,
            "weeks": weeks, "window_weeks": win, "employees": result}


def evaluate(analysis: dict, truth: list[dict]) -> dict:
    """심어 둔 패턴(정답지)과 탐지 결과를 비교한다."""
    planted = {(r["emp_id"], r["pattern_code"]) for r in truth}
    planted_of = {r["emp_id"]: r["pattern_code"] for r in truth}
    detected = {(emp_id, code) for emp_id, a in analysis["employees"].items() for code in a["flags"]}
    found = planted & detected
    secondary = {(e, c) for e, c in detected - planted
                 if c in SECONDARY_OK.get(planted_of.get(e), ())}
    extra = detected - planted - secondary
    per_pattern = {}
    for code in PATTERNS:
        total = sum(1 for _, c in planted if c == code)
        hit = sum(1 for _, c in found if c == code)
        per_pattern[code] = (hit, total)
    flagged_emps = {e for e, _ in detected}
    return {
        "planted": planted, "detected": detected, "found": found,
        "missed": planted - detected, "secondary": secondary, "extra": extra,
        "per_pattern": per_pattern,
        "flagged_emps": flagged_emps,
        "flagged_planted": flagged_emps & set(planted_of),
        "n_employees": len(analysis["employees"]),
    }


def accuracy_markdown(ev: dict, analysis: dict) -> str:
    emps = analysis["employees"]
    label = lambda e, c: f"{emps[e]['name']}({emps[e]['team']}) - {PATTERNS[c]}"
    n_found, n_planted = len(ev["found"]), len(ev["planted"])
    lines = [
        "# 정답지 대비 탐지 정확도", "",
        f"- 심은 패턴 {n_planted}건 중 **{n_found}건 탐지** (재현율 {n_found / n_planted:.0%})" if n_planted else "- 심은 패턴 없음",
        f"- 탐지된 직원 {len(ev['flagged_emps'])}명 중 패턴을 심은 직원 {len(ev['flagged_planted'])}명, "
        f"심지 않은 직원 {len(ev['flagged_emps'] - ev['flagged_planted'])}명",
        f"- 전체 탐지 {len(ev['detected'])}건 = 심은 패턴 {n_found}건 + 함께 나타나는 부수 패턴 "
        f"{len(ev['secondary'])}건 + 심지 않은 패턴 {len(ev['extra'])}건", "",
        "| 패턴 | 심은 수 | 찾은 수 |", "|---|---|---|",
    ]
    lines += [f"| {PATTERNS[c]} | {total} | {hit} |" for c, (hit, total) in ev["per_pattern"].items()]
    lines += ["", "## 놓친 패턴"]
    lines += [f"- {label(e, c)}" for e, c in sorted(ev["missed"])] or ["- 없음"]
    lines += ["", "## 심지 않았는데 탐지된 패턴"]
    lines += [f"- {label(e, c)}: {emps[e]['flags'][c]}" for e, c in sorted(ev["extra"])] or ["- 없음"]
    return "\n".join(lines) + "\n"


def save_outputs(analysis: dict) -> None:
    """탐지 결과를 CSV로 저장 (엑셀에서 바로 열 수 있음)."""
    weekly_rows, flag_rows = [], []
    for a in analysis["employees"].values():
        weekly_rows.append({
            "emp_id": a["emp_id"], "name": a["name"], "team": a["team"],
            "week_start": analysis["week_start"].isoformat(),
            "hours": f"{a['hours']:.1f}", "tier": a["tier"],
            "projected": "" if a["projected"] is None else f"{a['projected']:.1f}",
            "late_this_week": a["late_week"], "late_recent": a["late_recent"],
            "absences_this_week": len(a["abs_week"]),
            "patterns": "; ".join(PATTERNS[c] for c in a["flags"]),
        })
        for code, detail in a["flags"].items():
            flag_rows.append({"emp_id": a["emp_id"], "name": a["name"], "team": a["team"],
                              "pattern_code": code, "pattern_name": PATTERNS[code], "detail": detail})
    write_csv(OUT_DIR / "weekly_detection.csv", weekly_rows, list(weekly_rows[0]))
    write_csv(OUT_DIR / "pattern_flags.csv", flag_rows,
              ["emp_id", "name", "team", "pattern_code", "pattern_name", "detail"])


def load_inputs() -> tuple[list[dict], list[dict]]:
    return read_csv(DATA_DIR / "employees.csv"), read_csv(DATA_DIR / "attendance.csv")


if __name__ == "__main__":
    config = load_config()
    result = analyze(config, *load_inputs())
    save_outputs(result)
    counts = Counter(a["tier"] for a in result["employees"].values())
    print(f"기준 주 {result['week_start']}: 초과 {counts['초과']}명, 위험 {counts['위험']}명, 주의 {counts['주의']}명")
