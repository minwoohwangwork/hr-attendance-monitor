"""근태 조회 서비스: 권한 확인, 요약 생성, 조회 로그.

MCP와 무관한 순수 로직입니다(표준 라이브러리만 사용). server.py가 이 클래스를 MCP 도구로 노출합니다.

원칙
  1. 역할은 서버를 시작할 때 한 번 정해지고, 조회 요청으로는 바꿀 수 없다.
  2. 팀장은 자기 팀만, 인사팀은 전사를 본다 (common.visible_teams).
  3. 읽기 전용: 근태 데이터를 고치는 기능이 없다. 쓰는 파일은 조회 로그뿐이다.
  4. 일자별 출퇴근 기록은 반환하지 않는다. 주 단위 요약과 근거 수치만 반환한다.
  5. 허용된 조회와 거부된 조회를 모두 로그에 남긴다.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import detect  # noqa: E402
import report  # noqa: E402
from common import PATTERNS, load_config, visible_teams  # noqa: E402

ROLE_NAMES = {"leader": "팀장", "hr": "인사팀"}
NOT_FOUND = "열람 범위에서 해당 구성원을 찾을 수 없습니다."
RAW_NOTICE = "일자별 출퇴근 기록은 제공하지 않으며, 주 단위 요약과 근거 수치만 제공합니다."


class AccessDenied(Exception):
    """열람 범위를 벗어난 조회."""


class AttendanceService:
    def __init__(self, role: str, team: str | None = None, *, cfg: dict | None = None,
                 log_path: Path | None = None):
        self.cfg = cfg or load_config()
        if role not in ROLE_NAMES:
            raise ValueError(f"역할은 leader 또는 hr 이어야 합니다: {role!r}")
        self.role, self.team = role, team if role == "leader" else None
        self.scope = visible_teams(self.cfg, role, team)
        if not self.scope:
            raise ValueError(f"팀장 역할에는 설정에 있는 팀 이름이 필요합니다: {team!r}")
        self.log_path = log_path or ROOT / "logs" / "access_log.jsonl"

    # ── 내부 ────────────────────────────────────────────
    def _log(self, tool: str, args: dict, outcome: str, returned: int = 0, reason: str = "") -> None:
        """조회 한 건을 한 줄(JSON)로 기록한다. 조회 결과 자체는 기록하지 않는다."""
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "role": self.role, "team": self.team, "tool": tool,
            "args": {k: v for k, v in args.items() if v is not None},
            "outcome": outcome, "returned": returned,
        }
        if reason:
            entry["reason"] = reason
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _analysis(self) -> dict:
        """매 조회마다 CSV를 읽어 다시 계산한다 (읽기만 함)."""
        return detect.analyze(self.cfg, *detect.load_inputs())

    def _teams(self, tool: str, args: dict, team: str | None) -> list[str]:
        """요청한 팀이 열람 범위 안인지 확인하고, 조회할 팀 목록을 돌려준다."""
        if team is None:
            return self.scope
        if team not in self.scope:
            self._log(tool, args, "denied", reason="out_of_scope_team")
            raise AccessDenied(f"열람 권한이 없습니다. 현재 역할({ROLE_NAMES[self.role]})의 열람 범위: "
                               + ", ".join(self.scope))
        return [team]

    @staticmethod
    def _week_label(an: dict) -> str:
        ws = an["week_start"]
        return f"{ws:%Y-%m-%d} ~ {ws + timedelta(days=4):%m-%d}"

    @staticmethod
    def _patterns(a: dict) -> list[dict]:
        return [{"패턴": PATTERNS[c], "근거": detail} for c, detail in a["flags"].items()]

    # ── 도구 ────────────────────────────────────────────
    def get_scope(self) -> dict:
        an = self._analysis()
        members = [a for a in an["employees"].values() if a["team"] in self.scope]
        self._log("get_scope", {}, "allowed", len(self.scope))
        return {
            "역할": ROLE_NAMES[self.role],
            "열람_가능한_팀": self.scope,
            "열람_가능한_인원": len(members),
            "기준_주": self._week_label(an),
            "안내": RAW_NOTICE + " 모든 조회는 기록됩니다.",
        }

    def get_weekly_summary(self, team: str | None = None) -> dict:
        args = {"team": team}
        teams = self._teams("get_weekly_summary", args, team)
        an = self._analysis()
        ws = an["week_start"]
        rows = []
        for t in teams:
            members = [a for a in an["employees"].values() if a["team"] == t]
            tiers = Counter(a["tier"] for a in members)
            now, prev = report._week_counts(members, ws), report._week_counts(members, ws - timedelta(weeks=1))
            rows.append({
                "팀": t, "인원": len(members),
                "1인_평균_실근로시간": round(now["avg"], 1),
                "지난주_1인_평균_실근로시간": round(prev["avg"], 1),
                "52시간_초과": tiers["초과"], "위험_48시간_초과": tiers["위험"], "주의_44시간_초과": tiers["주의"],
                "지각_건수": now["lates"], "지각_인원": sum(1 for a in members if a["late_week"]),
                "무단결근_건수": now["absences"], "연차_반차_건수": now["leaves"],
            })
        self._log("get_weekly_summary", args, "allowed", len(rows))
        return {"기준_주": self._week_label(an), "팀별_요약": rows}

    def list_flagged_employees(self, team: str | None = None, category: str = "all") -> dict:
        args = {"team": team, "category": category}
        if category not in ("all", "hours", "attendance"):
            self._log("list_flagged_employees", args, "error", reason="bad_category")
            raise ValueError("category 는 all, hours, attendance 중 하나여야 합니다.")
        teams = self._teams("list_flagged_employees", args, team)
        an = self._analysis()
        rows = []
        for a in an["employees"].values():
            if a["team"] not in teams:
                continue
            hours = report._needs_hours_row(a, an["midweek"])
            attendance = report._needs_attendance_row(a)
            if (category == "hours" and not hours) or (category == "attendance" and not attendance) \
                    or not (hours or attendance):
                continue
            rows.append({
                "이름": a["name"], "사번": a["emp_id"], "팀": a["team"],
                "이번_주_실근로시간": round(a["hours"], 1), "등급": a["tier"],
                "이번_주_지각": a["late_week"], f"최근_{an['window_weeks']}주_지각": a["late_recent"],
                "이번_주_무단결근": len(a["abs_week"]),
                "탐지_패턴": self._patterns(a),
            })
        rows.sort(key=lambda r: -r["이번_주_실근로시간"])
        self._log("list_flagged_employees", args, "allowed", len(rows))
        return {"기준_주": self._week_label(an), "확인이_필요한_구성원": rows,
                "안내": "탐지 결과는 확인이 필요하다는 신호입니다. 본인 확인과 면담 없이 불이익 조치의 근거로 쓰지 마세요."}

    def get_employee_summary(self, name_or_id: str) -> dict:
        args = {"name_or_id": name_or_id}
        an = self._analysis()
        key = name_or_id.strip()
        everyone = an["employees"].values()
        matches = [a for a in everyone if key in (a["name"], a["emp_id"])]
        visible = [a for a in matches if a["team"] in self.scope]
        if not visible:
            # 범위 밖 직원과 없는 직원을 같은 메시지로 거부한다 (존재 여부를 알려 주지 않기 위해).
            self._log("get_employee_summary", args, "denied",
                      reason="out_of_scope_employee" if matches else "not_found")
            raise AccessDenied(NOT_FOUND)
        if len(visible) > 1:
            self._log("get_employee_summary", args, "allowed", len(visible), reason="ambiguous_name")
            return {"안내": "같은 이름이 여러 명입니다. 사번으로 다시 조회해 주세요.",
                    "후보": [{"이름": a["name"], "사번": a["emp_id"], "팀": a["team"]} for a in visible]}
        a = visible[0]
        win = an["window_weeks"]
        self._log("get_employee_summary", args, "allowed", 1)
        return {
            "이름": a["name"], "사번": a["emp_id"], "팀": a["team"], "직책": a["role"],
            "기준_주": self._week_label(an), "출근_기준_시각": a["start"],
            "근로시간": {
                "이번_주_실근로시간": round(a["hours"], 1), "등급": a["tier"],
                "수요일_시점_주간_예상": None if a["projected"] is None else round(a["projected"], 1),
                "최근_주별_실근로시간": [{"주_시작": w.isoformat(), "시간": round(a["weekly"][w], 1)}
                                  for w in an["weeks"][-win:]],
            },
            "지각": {"이번_주": a["late_week"], f"최근_{win}주": a["late_recent"], f"직전_{win}주": a["late_prior"]},
            "결근": {"이번_주_무단결근": len(a["abs_week"]), "이번_주_연속_결근일": a["abs_run"],
                   "전체_기간_무단결근": len(a["absences"])},
            "탐지_패턴": self._patterns(a),
            "권장_조치": report.actions_for(a, an),
            "안내": RAW_NOTICE,
        }

    def get_trend(self, team: str | None = None, weeks: int = 8) -> dict:
        args = {"team": team, "weeks": weeks}
        teams = self._teams("get_trend", args, team)
        an = self._analysis()
        th = self.cfg["thresholds"]
        members = [a for a in an["employees"].values() if a["team"] in teams]
        rows = []
        for w in an["weeks"][-max(1, min(weeks, len(an["weeks"]))):]:
            c = report._week_counts(members, w)
            hours = [a["weekly"].get(w, 0.0) for a in members]
            rows.append({
                "주_시작": w.isoformat(), "1인_평균_실근로시간": round(c["avg"], 1),
                "52시간_초과_인원": sum(h > th["weekly_limit"] for h in hours),
                "위험_인원": sum(th["weekly_danger"] < h <= th["weekly_limit"] for h in hours),
                "지각_건수": c["lates"], "무단결근_건수": c["absences"],
            })
        self._log("get_trend", args, "allowed", len(rows))
        return {"대상_팀": teams, "대상_인원": len(members), "주별_추이": rows,
                "안내": "공휴일이 있는 주는 근무일이 적어 평균 실근로시간이 낮게 나옵니다."}
