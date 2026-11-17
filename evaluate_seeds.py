"""탐지 규칙이 특정 시드에만 맞춰진 것이 아닌지 확인: 시드를 바꿔 가며 정확도를 다시 잰다.

사용법:  python evaluate_seeds.py        (기본 200개 시드)
         python evaluate_seeds.py 50
파일은 만들지 않고 화면에 결과만 출력합니다.

'심지 않았는데 탐지된 패턴'은 두 가지로 나눕니다.
  (가) 기록상 사실  : 횟수·시간을 그대로 말하는 규칙(반복 지각, 무단결근 반복, 근로시간)이
                      울렸고, 원본 기록을 다시 세어 보니 실제로 그 횟수·시간이 있는 경우.
                      정답지에 없었을 뿐 규칙은 맞게 짚은 것.
  (나) 우연을 경향으로 읽음 : '증가 추세', '요일 편중', '휴일 전후'처럼 경향을 해석하는 규칙이
                      울렸지만, 생성기에서 그 직원의 확률은 시간·요일·휴일과 무관했던 경우.
                      규칙이 잘못 짚은 것.
"""
from __future__ import annotations

import copy
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import detect
import generate_data
from common import PATTERNS, is_workday, load_config, start_minutes, to_min

DESCRIPTIVE = {"LATE_REPEAT", "ABSENCE_UNEXCUSED", "OVERWORK_CHRONIC", "OVERWORK_SPIKE"}
INFERENTIAL = {"LATE_WORSENING", "LATE_WEEKDAY_BIAS", "ABSENCE_HOLIDAY_ADJ"}


def recount(cfg: dict, records: list[dict], emp: dict, an: dict, code: str) -> bool:
    """detect.py를 거치지 않고 원본 기록을 다시 세어, 규칙이 말한 사실이 실제로 있는지 확인."""
    th = cfg["thresholds"]
    rows = [r for r in records if r["emp_id"] == emp["emp_id"]]
    if code == "ABSENCE_UNEXCUSED":
        return sum(r["status"] == "결근" for r in rows) >= th["absence_repeat_count"]
    if code == "LATE_REPEAT":
        since = an["week_start"] - timedelta(weeks=th["late_window_weeks"] - 1)
        limit = start_minutes(cfg, emp["emp_id"], emp["team"]) + cfg["work"].get("late_grace_min", 0)
        lates = sum(1 for r in rows
                    if r["status"] == "출근" and date.fromisoformat(r["date"]) >= since
                    and is_workday(cfg, date.fromisoformat(r["date"])) and to_min(r["clock_in"]) > limit)
        return lates >= th["late_repeat_count"]
    weekly = defaultdict(float)                      # 근로시간: ISO 주차별 합계
    for r in rows:
        if r["clock_in"]:
            minutes = to_min(r["clock_out"]) - to_min(r["clock_in"]) - int(r["break_min"])
            weekly[date.fromisoformat(r["date"]).isocalendar()[:2]] += minutes / 60
    danger = sum(h > th["weekly_danger"] for h in weekly.values())
    over = sum(h > th["weekly_limit"] for h in weekly.values())
    if code == "OVERWORK_CHRONIC":
        return danger >= th["chronic_weeks"]
    return over >= 1 and danger < th["chronic_weeks"]   # OVERWORK_SPIKE


def worsening_by_rate(cfg: dict, an: dict, a: dict) -> bool:
    """개선안 실험: 지각 '횟수'가 아니라 근무일당 '비율'로 직전 4주와 비교."""
    th = cfg["thresholds"]
    recent_from = an["week_start"] - timedelta(weeks=th["late_window_weeks"] - 1)
    prior_from = recent_from - timedelta(weeks=th["late_window_weeks"])
    days = lambda start, end: sum(is_workday(cfg, start + timedelta(days=i)) for i in range((end - start).days))
    wd_recent = days(recent_from, an["week_start"] + timedelta(days=5))
    wd_prior = days(prior_from, recent_from)
    if a["late_recent"] < th["late_repeat_count"] or not wd_prior:
        return False
    return a["late_recent"] / wd_recent >= th["worsening_ratio"] * a["late_prior"] / wd_prior


def main(n_seeds: int = 200) -> None:
    cfg = load_config()
    found, extra, planted_n = [], [], 0
    by_code = defaultdict(Counter)        # code → {"정상 직원": n, "다른 패턴을 심은 직원": n}
    fact_ok = fact_bad = 0
    alt = Counter()
    for seed in range(1, n_seeds + 1):
        c = copy.deepcopy(cfg)
        c["generate"]["seed"] = seed
        employees, records, truth = generate_data.generate(c)
        an = detect.analyze(c, employees, records)
        ev = detect.evaluate(an, truth)
        planted_of = {r["emp_id"]: r["pattern_code"] for r in truth}
        planted_n = len(ev["planted"])
        found.append(len(ev["found"]))
        extra.append(len(ev["extra"]))
        for emp_id, code in ev["extra"]:
            by_code[code]["다른 패턴을 심은 직원" if emp_id in planted_of else "정상 직원"] += 1
            if code in DESCRIPTIVE:
                if recount(c, records, an["employees"][emp_id], an, code):
                    fact_ok += 1
                else:
                    fact_bad += 1
        for emp_id, a in an["employees"].items():   # 개선안: 비율 비교
            is_planted = planted_of.get(emp_id) == "LATE_WORSENING"
            alt["planted"] += is_planted
            alt["old_found"] += is_planted and "LATE_WORSENING" in a["flags"]
            alt["old_extra"] += (not is_planted) and "LATE_WORSENING" in a["flags"]
            hit = worsening_by_rate(c, an, a)
            alt["new_found"] += is_planted and hit
            alt["new_extra"] += (not is_planted) and hit

    total = sum(extra)
    print(f"시드 {n_seeds}개 기준 (시드당 심은 패턴 {planted_n}건)")
    print(f"- 찾은 패턴: 평균 {statistics.mean(found):.1f}건, 최소 {min(found)}건, 최대 {max(found)}건")
    print(f"- 심지 않았는데 탐지된 패턴: 평균 {statistics.mean(extra):.1f}건, 최대 {max(extra)}건, 합계 {total}건")
    print()
    print("심지 않았는데 탐지된 패턴의 분류")
    for label, codes in (("(가) 기록상 사실 - 규칙이 맞게 짚음", DESCRIPTIVE),
                         ("(나) 우연을 경향으로 읽음 - 규칙이 잘못 짚음", INFERENTIAL)):
        subtotal = sum(sum(by_code[code].values()) for code in codes)
        print(f"{label}: {subtotal}건 ({subtotal / total:.0%})")
        for code in sorted(codes, key=lambda x: -sum(by_code[x].values())):
            n = sum(by_code[code].values())
            if n:
                print(f"    {PATTERNS[code]}: {n}건 "
                      f"(정상 직원 {by_code[code]['정상 직원']}, 다른 패턴을 심은 직원 {by_code[code]['다른 패턴을 심은 직원']})")
    print(f"(가)의 원본 기록 재집계: {fact_ok}건 일치, {fact_bad}건 불일치")
    print()
    print("개선안 실험: '지각 증가 추세'를 횟수 대신 근무일당 비율로 비교하면")
    print(f"- 심은 {alt['planted']}건 중 찾은 수: {alt['old_found']} → {alt['new_found']}")
    print(f"- 심지 않았는데 탐지: {alt['old_extra']} → {alt['new_extra']} (부수 패턴 포함한 전체 기준)")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 200)
