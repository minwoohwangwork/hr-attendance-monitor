"""1단계: 가상 직원과 근태 기록 생성.

모든 이름과 기록은 무작위로 만든 가상 데이터입니다.
일부 직원에게는 탐지 대상 패턴을 일부러 심고, 누구에게 무엇을 심었는지
data/ground_truth.csv (정답지)에 남깁니다.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from common import (DATA_DIR, PATTERNS, holiday_adjacent_days, load_config,
                    period_workdays, start_minutes, to_hhmm, to_min, write_csv)

SURNAMES = list("김이박최정강조윤장임한오서신권황안송류홍")
GIVEN = [
    "민준", "서연", "도윤", "지우", "하준", "서윤", "시우", "하은", "지호", "수아",
    "예준", "지민", "주원", "채원", "건우", "유진", "현우", "소율", "지훈", "다은",
    "준서", "예린", "우진", "수빈", "선우", "지유", "연우", "가은", "정우", "나연",
    "승현", "유나", "민재", "서현", "태윤", "하린", "시윤", "은서", "동현", "윤아",
    "재원", "소연", "성민", "혜원", "준혁", "미래", "경민", "보람", "영훈", "세영",
]

# 팀 순서별로 "팀 내 몇 번째 직원에게 어떤 패턴을 심을지" (0번은 팀장, 항상 정상)
PERSONA_PLAN = [
    {1: "OVERWORK_CHRONIC", 2: "OVERWORK_CHRONIC", 3: "OVERWORK_SPIKE",
     4: "OVERWORK_SPIKE", 5: "LATE_REPEAT"},
    {1: "OVERWORK_CHRONIC", 2: "OVERWORK_SPIKE", 3: "OVERWORK_SPIKE",
     4: "LATE_WEEKDAY_BIAS", 5: "LATE_WORSENING"},
    {3: "LATE_REPEAT", 4: "LATE_REPEAT", 5: "ABSENCE_UNEXCUSED",
     6: "ABSENCE_UNEXCUSED", 7: "ABSENCE_HOLIDAY_ADJ", 8: "LATE_WEEKDAY_BIAS"},
    {1: "OVERWORK_CHRONIC", 2: "LATE_REPEAT", 3: "LATE_WORSENING",
     4: "ABSENCE_UNEXCUSED", 5: "LATE_WEEKDAY_BIAS"},
    {6: "ABSENCE_HOLIDAY_ADJ"},
]
# 마감 급증형의 급증 주(0부터 셈). 첫 팀은 마지막 2주, 둘째 팀은 4~5주차
SPIKE_WEEKS = [(6, 7), (3, 4)]
# 팀별 평소 야근 발생 확률 (QA 쪽이 조금 높고 경영지원이 낮음)
OVERTIME_P = [0.20, 0.15, 0.10, 0.10, 0.05]


def _forced_absences(rng, persona, days, adjacent, is_first_absent) -> set[date]:
    """결근형 직원의 결근일을 미리 정한다."""
    if persona == "ABSENCE_UNEXCUSED":
        if is_first_absent and len(days) >= 5:
            # 마지막 주 화·수 연속 결근 + 그 이전 하루
            return {days[-4], days[-3], rng.choice(days[:-5])}
        return set(rng.sample(days, 3))
    if persona == "ABSENCE_HOLIDAY_ADJ":
        return set(rng.sample(adjacent, 3 if is_first_absent else 2))
    return set()


def _simulate_day(rng, persona, d, week_idx, n_weeks, start, overtime_p, spiking):
    """하루치 기록 (status, clock_in, clock_out, break_min)을 만든다."""
    late_p, hours_mu, hours_sd, leave_p = 0.02, 8.1, 0.3, 0.03
    if persona == "OVERWORK_CHRONIC":
        hours_mu, hours_sd, overtime_p, leave_p = 10.0, 0.6, 0.0, 0.01
    elif spiking:
        hours_mu, hours_sd, overtime_p, leave_p = 10.9, 0.4, 0.0, 0.0
    elif persona == "LATE_REPEAT":
        late_p = 0.35
    elif persona == "LATE_WEEKDAY_BIAS":
        late_p = 0.85 if d.weekday() == 0 else 0.03
    elif persona == "LATE_WORSENING":
        late_p = 0.35 if week_idx >= n_weeks // 2 else 0.03

    r = rng.random()
    if persona is None and r < 0.002:          # 정상 직원의 아주 드문 결근
        return "결근", "", "", 0
    if r < 0.002 + leave_p:
        return "연차", "", "", 0
    if r < 0.002 + leave_p + 0.015:            # 반차: 4시간 근무
        early = int(rng.triangular(0, 20, 5))
        if rng.random() < 0.5:                 # 오후 반차
            clock_in = start - early
        else:                                  # 오전 반차
            clock_in = start + 300 - early
        return "반차", to_hhmm(clock_in), to_hhmm(clock_in + 240 + early), 0

    if rng.random() < late_p:
        clock_in = start + int(rng.triangular(1, 45, 8))
    else:
        clock_in = start - int(rng.triangular(0, 30, 8))
    hours = max(6.0, rng.gauss(hours_mu, hours_sd))
    if rng.random() < overtime_p:
        hours += rng.uniform(1.0, 2.5)
    clock_out = min(clock_in + round(hours * 60) + 60, 23 * 60 + 59)
    return "출근", to_hhmm(clock_in), to_hhmm(clock_out), 60


def generate(cfg: dict) -> tuple[list[dict], list[dict], list[dict]]:
    rng = random.Random(cfg["generate"]["seed"])
    days = period_workdays(cfg)
    first = cfg["period"]["start_monday"]
    n_weeks = cfg["period"]["weeks"]
    adjacent = sorted(holiday_adjacent_days(cfg) & set(days))
    given = GIVEN[:]
    rng.shuffle(given)

    employees, truth, records = [], [], []
    seen_absent_types = set()
    n = 0
    for pos, team in enumerate(cfg["teams"]):
        plan = PERSONA_PLAN[pos] if pos < len(PERSONA_PLAN) else {}
        spike_weeks = SPIKE_WEEKS[pos] if pos < len(SPIKE_WEEKS) else (n_weeks - 2, n_weeks - 1)
        overtime_p = OVERTIME_P[pos] if pos < len(OVERTIME_P) else 0.10
        for idx in range(team.get("size", 10)):
            n += 1
            emp_id = f"E{n:03d}"
            name = rng.choice(SURNAMES) + given[(n - 1) % len(given)]
            persona = plan.get(idx)
            if persona == "ABSENCE_HOLIDAY_ADJ" and len(adjacent) < 3:
                persona = None                  # 공휴일 설정이 없으면 심을 수 없음
            start = start_minutes(cfg, emp_id, team["name"])
            employees.append({
                "emp_id": emp_id, "name": name, "team": team["name"],
                "role": "팀장" if idx == 0 else "팀원",
            })
            if persona:
                truth.append({
                    "emp_id": emp_id, "name": name, "team": team["name"],
                    "pattern_code": persona, "pattern_name": PATTERNS[persona],
                })
            absent_days = _forced_absences(rng, persona, days, adjacent,
                                           persona not in seen_absent_types)
            seen_absent_types.add(persona)

            for d in days:
                week_idx = (d - first).days // 7
                spiking = persona == "OVERWORK_SPIKE" and week_idx in spike_weeks
                if d in absent_days:
                    row = ("결근", "", "", 0)
                else:
                    row = _simulate_day(rng, persona, d, week_idx, n_weeks,
                                        start, overtime_p, spiking)
                records.append(_record(d, emp_id, name, team["name"], row))

            # 토요일 근무 (과로형·급증 주)
            for week_idx in range(n_weeks):
                spiking = persona == "OVERWORK_SPIKE" and week_idx in spike_weeks
                sat_p = 0.35 if persona == "OVERWORK_CHRONIC" else 0.5 if spiking else 0.0
                if rng.random() < sat_p:
                    sat = first + timedelta(days=week_idx * 7 + 5)
                    clock_in = to_min("10:00") - int(rng.triangular(0, 20, 5))
                    clock_out = clock_in + round(rng.uniform(4.0, 6.0) * 60) + 30
                    row = ("출근", to_hhmm(clock_in), to_hhmm(clock_out), 30)
                    records.append(_record(sat, emp_id, name, team["name"], row))

    records.sort(key=lambda r: (r["date"], r["emp_id"]))
    return employees, records, truth


def _record(d, emp_id, name, team, row) -> dict:
    status, clock_in, clock_out, break_min = row
    return {
        "date": d.isoformat(), "emp_id": emp_id, "name": name, "team": team,
        "status": status, "clock_in": clock_in, "clock_out": clock_out,
        "break_min": break_min,
    }


def save(employees, records, truth) -> None:
    write_csv(DATA_DIR / "employees.csv", employees, ["emp_id", "name", "team", "role"])
    write_csv(DATA_DIR / "attendance.csv", records,
              ["date", "emp_id", "name", "team", "status", "clock_in", "clock_out", "break_min"])
    write_csv(DATA_DIR / "ground_truth.csv", truth,
              ["emp_id", "name", "team", "pattern_code", "pattern_name"])


if __name__ == "__main__":
    emps, recs, gt = generate(load_config())
    save(emps, recs, gt)
    print(f"직원 {len(emps)}명, 근태 기록 {len(recs)}행, 심은 패턴 {len(gt)}건 생성")
