"""탐지 규칙 단위 테스트.  실행: python -m unittest discover tests"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import detect  # noqa: E402
from common import holiday_adjacent_days, start_minutes, visible_teams  # noqa: E402

CFG = {
    "period": {"start_monday": date(2026, 11, 2), "weeks": 2, "holidays": [date(2026, 10, 9)]},
    "work": {"default_start": "09:00", "late_grace_min": 0},
    "teams": [{"name": "A팀"}, {"name": "B팀", "start": "10:00"}],
    "employee_start": {"E002": "07:00"},
    "thresholds": {
        "weekly_limit": 52, "weekly_danger": 48, "weekly_caution": 44, "chronic_weeks": 3,
        "late_window_weeks": 4, "late_repeat_count": 3, "bias_min_events": 5, "bias_share": 0.6,
        "worsening_ratio": 2.0, "absence_repeat_count": 2, "holiday_adjacent_count": 2,
    },
}


def rec(d, emp, clock_in="09:00", clock_out="18:00", status="출근", team="A팀"):
    return {"date": d, "emp_id": emp, "name": emp, "team": team, "status": status,
            "clock_in": clock_in, "clock_out": clock_out, "break_min": 60 if clock_in else 0}


class RuleTests(unittest.TestCase):
    def test_tier_boundaries(self):
        th = CFG["thresholds"]
        self.assertEqual(detect.tier(52.0, th), "위험")   # 52시간 정각은 초과가 아님
        self.assertEqual(detect.tier(52.1, th), "초과")
        self.assertEqual(detect.tier(48.0, th), "주의")
        self.assertEqual(detect.tier(44.0, th), "정상")

    def test_start_time_priority(self):
        self.assertEqual(start_minutes(CFG, "E001", "A팀"), 9 * 60)    # 기본값
        self.assertEqual(start_minutes(CFG, "E009", "B팀"), 10 * 60)   # 팀 설정
        self.assertEqual(start_minutes(CFG, "E002", "B팀"), 7 * 60)    # 개인 설정이 팀보다 우선

    def test_late_uses_personal_start(self):
        # 같은 08:30 출근이라도 기준이 07:00인 E002만 지각
        emps = [{"emp_id": "E001", "name": "가", "team": "A팀", "role": "팀원"},
                {"emp_id": "E002", "name": "나", "team": "A팀", "role": "팀원"}]
        recs = [rec("2026-11-09", "E001", "08:30", "17:30"), rec("2026-11-09", "E002", "08:30", "17:30")]
        an = detect.analyze(CFG, emps, recs)
        self.assertEqual(an["employees"]["E001"]["late_week"], 0)
        self.assertEqual(an["employees"]["E002"]["late_week"], 1)

    def test_weekly_hours_and_projection(self):
        emps = [{"emp_id": "E001", "name": "가", "team": "A팀", "role": "팀원"}]
        days = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
        recs = [rec(d, "E001", "09:00", "21:00") for d in days]          # 하루 11시간 근로
        a = detect.analyze(CFG, emps, recs)["employees"]["E001"]
        self.assertAlmostEqual(a["hours"], 55.0)
        self.assertEqual(a["tier"], "초과")
        self.assertAlmostEqual(a["projected"], 55.0)                      # 수요일 33h + 11h × 2일
        mid = detect.analyze(CFG, emps, recs, as_of=date(2026, 11, 11))
        self.assertTrue(mid["midweek"])
        self.assertEqual(mid["employees"]["E001"]["proj_tier"], "초과")   # 수요일에 미리 경고

    def test_unexcused_absence_and_leave(self):
        emps = [{"emp_id": "E001", "name": "가", "team": "A팀", "role": "팀원"}]
        recs = [rec("2026-11-09", "E001"),
                rec("2026-11-10", "E001", "", "", "결근"), rec("2026-11-11", "E001", "", "", "결근"),
                rec("2026-11-12", "E001", "", "", "연차"), rec("2026-11-13", "E001")]
        a = detect.analyze(CFG, emps, recs)["employees"]["E001"]
        self.assertEqual(a["abs_run"], 2)                                 # 연속 2일
        self.assertIn("ABSENCE_UNEXCUSED", a["flags"])
        self.assertEqual(len(a["absences"]), 2)                           # 연차는 결근이 아님

    def test_holiday_adjacent_days(self):
        # 10/9(금) 공휴일 → 앞은 10/8(목), 뒤는 주말을 건너 10/12(월)
        self.assertEqual(holiday_adjacent_days(CFG), {date(2026, 10, 8), date(2026, 10, 12)})

    def test_access_scope(self):
        self.assertEqual(visible_teams(CFG, "hr"), ["A팀", "B팀"])
        self.assertEqual(visible_teams(CFG, "leader", "A팀"), ["A팀"])
        self.assertEqual(visible_teams(CFG, "leader", "없는팀"), [])
        self.assertEqual(visible_teams(CFG, "member", "A팀"), [])


if __name__ == "__main__":
    unittest.main()
