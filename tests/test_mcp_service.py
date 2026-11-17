"""MCP 서버의 권한·반환 내용·로그 테스트 (mcp 패키지 없이 실행 가능).

실행: python -m unittest discover tests
data/ 폴더의 가상 데이터를 사용하므로 먼저 python main.py 를 한 번 실행해 두어야 합니다.
"""
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common import DATA_DIR, read_csv  # noqa: E402
from mcp_server.service import NOT_FOUND, AccessDenied, AttendanceService  # noqa: E402

EMPLOYEES = read_csv(DATA_DIR / "employees.csv")
QA = [e for e in EMPLOYEES if e["team"] == "게임QA"]
MOBILE = [e for e in EMPLOYEES if e["team"] == "모바일QA"]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "access_log.jsonl"
        self.leader = AttendanceService("leader", "게임QA", log_path=self.log)
        self.hr = AttendanceService("hr", log_path=self.log)

    def tearDown(self):
        self.tmp.cleanup()

    def log_entries(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    # ── 역할별 조회 범위 ──
    def test_leader_sees_only_own_team(self):
        self.assertEqual(self.leader.get_scope()["열람_가능한_팀"], ["게임QA"])
        self.assertEqual([r["팀"] for r in self.leader.get_weekly_summary()["팀별_요약"]], ["게임QA"])
        flagged = self.leader.list_flagged_employees()["확인이_필요한_구성원"]
        self.assertTrue(flagged)
        self.assertEqual({r["팀"] for r in flagged}, {"게임QA"})
        self.assertEqual(self.leader.get_trend()["대상_인원"], len(QA))

    def test_hr_sees_all_teams(self):
        self.assertEqual(len(self.hr.get_weekly_summary()["팀별_요약"]), 5)
        self.assertEqual(self.hr.get_trend()["대상_인원"], len(EMPLOYEES))
        self.assertEqual(self.hr.get_employee_summary(MOBILE[1]["emp_id"])["팀"], "모바일QA")

    def test_leader_denied_other_team(self):
        for call in (lambda: self.leader.get_weekly_summary("모바일QA"),
                     lambda: self.leader.list_flagged_employees("모바일QA"),
                     lambda: self.leader.get_trend("모바일QA")):
            with self.assertRaises(AccessDenied):
                call()

    def test_other_team_employee_looks_like_not_found(self):
        # 다른 팀 직원과 존재하지 않는 직원에게 같은 메시지를 준다 (존재 여부를 알려 주지 않음)
        messages = []
        for key in (MOBILE[1]["name"], MOBILE[1]["emp_id"], "없는사람"):
            with self.assertRaises(AccessDenied) as ctx:
                self.leader.get_employee_summary(key)
            messages.append(str(ctx.exception))
        self.assertEqual(set(messages), {NOT_FOUND})

    def test_role_is_fixed_at_startup(self):
        with self.assertRaises(ValueError):
            AttendanceService("admin", log_path=self.log)
        with self.assertRaises(ValueError):
            AttendanceService("leader", "없는팀", log_path=self.log)
        with self.assertRaises(ValueError):
            AttendanceService("leader", log_path=self.log)

    # ── 요약만 반환 ──
    def test_no_raw_clock_records_returned(self):
        results = [self.hr.get_scope(), self.hr.get_weekly_summary(), self.hr.list_flagged_employees(),
                   self.hr.get_trend()] + [self.hr.get_employee_summary(e["emp_id"]) for e in EMPLOYEES]
        text = json.dumps(results, ensure_ascii=False)
        self.assertNotIn("clock_in", text)
        self.assertNotIn("clock_out", text)
        # 결과에 나오는 시각(HH:MM)은 설정값인 '출근 기준 시각'뿐이어야 한다
        starts = {r["출근_기준_시각"] for r in results if "출근_기준_시각" in r}
        self.assertEqual(set(re.findall(r"\b\d{2}:\d{2}\b", text)), starts)
        # 날짜는 주 시작일(월요일)만 나온다. 개별 근무일 날짜는 없다.
        weeks = {w["주_시작"] for w in self.hr.get_trend()["주별_추이"]}
        self.assertTrue(set(re.findall(r"\d{4}-\d{2}-\d{2}", text)) <= weeks)

    def test_employee_summary_has_evidence_and_actions(self):
        flagged = self.leader.list_flagged_employees(category="hours")["확인이_필요한_구성원"]
        summary = self.leader.get_employee_summary(flagged[0]["사번"])
        self.assertEqual(summary["근로시간"]["등급"], "초과")
        self.assertTrue(summary["탐지_패턴"][0]["근거"])
        self.assertTrue(summary["권장_조치"])

    # ── 읽기 전용, 로그 ──
    def test_read_only(self):
        files = sorted(DATA_DIR.glob("*.csv")) + [ROOT / "config.toml"]
        digest = lambda: [hashlib.sha256(f.read_bytes()).hexdigest() for f in files]
        before = digest()
        self.hr.get_weekly_summary()
        self.hr.list_flagged_employees()
        self.hr.get_employee_summary(QA[1]["emp_id"])
        self.hr.get_trend()
        self.assertEqual(before, digest())
        write_like = [n for n in dir(AttendanceService)
                      if n.startswith(("set_", "update_", "delete_", "add_", "write_", "save_"))]
        self.assertEqual(write_like, [])

    def test_access_log_records_allowed_and_denied(self):
        self.leader.get_weekly_summary()
        with self.assertRaises(AccessDenied):
            self.leader.get_weekly_summary("모바일QA")
        with self.assertRaises(AccessDenied):
            self.leader.get_employee_summary(MOBILE[1]["name"])
        entries = self.log_entries()
        self.assertEqual([e["outcome"] for e in entries], ["allowed", "denied", "denied"])
        self.assertEqual(entries[0]["role"], "leader")
        self.assertEqual(entries[0]["team"], "게임QA")
        self.assertEqual(entries[1]["reason"], "out_of_scope_team")
        self.assertEqual(entries[2]["reason"], "out_of_scope_employee")
        self.assertEqual(entries[2]["args"], {"name_or_id": MOBILE[1]["name"]})
        self.assertTrue(all("time" in e and "tool" in e for e in entries))


if __name__ == "__main__":
    unittest.main()
