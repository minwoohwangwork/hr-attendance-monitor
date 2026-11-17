"""공통 모듈: 설정 읽기, 날짜·시간 계산, CSV 입출력, 열람 권한."""
from __future__ import annotations

import csv
import tomllib
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "output"
WEEKDAYS_KO = "월화수목금토일"

# 탐지 패턴 코드 → 리포트에 쓰는 이름
PATTERNS = {
    "OVERWORK_CHRONIC": "장시간 근로 반복",
    "OVERWORK_SPIKE": "특정 주 근로시간 급증",
    "LATE_REPEAT": "반복 지각",
    "LATE_WEEKDAY_BIAS": "특정 요일 편중",
    "LATE_WORSENING": "지각 증가 추세",
    "ABSENCE_UNEXCUSED": "무단결근 반복",
    "ABSENCE_HOLIDAY_ADJ": "휴일 전후 결근",
}


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


# ── 시간 ────────────────────────────────────────────────
def to_min(hhmm: str) -> int:
    """'09:30' → 570"""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    """570 → '09:30'"""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def start_minutes(cfg: dict, emp_id: str, team: str) -> int:
    """출근 기준 시각(분). 우선순위: 개인 설정 > 팀 설정 > 기본값."""
    personal = cfg.get("employee_start", {}).get(emp_id)
    if personal:
        return to_min(personal)
    for t in cfg["teams"]:
        if t["name"] == team and "start" in t:
            return to_min(t["start"])
    return to_min(cfg["work"]["default_start"])


# ── 날짜 ────────────────────────────────────────────────
def week_start(d: date) -> date:
    """그 날짜가 속한 주의 월요일."""
    return d - timedelta(days=d.weekday())


def holidays(cfg: dict) -> set[date]:
    return set(cfg["period"].get("holidays", []))


def is_workday(cfg: dict, d: date) -> bool:
    """평일이면서 공휴일이 아닌 날."""
    return d.weekday() < 5 and d not in holidays(cfg)


def period_workdays(cfg: dict) -> list[date]:
    """설정된 기간의 모든 근무일."""
    first = cfg["period"]["start_monday"]
    total = cfg["period"]["weeks"] * 7
    return [d for d in (first + timedelta(days=i) for i in range(total)) if is_workday(cfg, d)]


def holiday_adjacent_days(cfg: dict) -> set[date]:
    """공휴일 바로 앞·뒤 근무일 (주말을 끼면 주말 건너편 근무일)."""
    hol = holidays(cfg)
    result = set()
    for h in hol:
        for step in (-1, 1):
            d = h + timedelta(days=step)
            while d.weekday() >= 5 or d in hol:
                d += timedelta(days=step)
            result.add(d)
    return result


# ── CSV ─────────────────────────────────────────────────
# utf-8-sig 로 저장해야 엑셀에서 한글이 깨지지 않습니다.
def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# ── 열람 권한 ───────────────────────────────────────────
def visible_teams(cfg: dict, role: str, team: str | None = None) -> list[str]:
    """열람 가능한 팀 목록. 인사팀(hr)은 전사, 팀장(leader)은 자기 팀만, 그 외는 없음."""
    all_teams = [t["name"] for t in cfg["teams"]]
    if role == "hr":
        return all_teams
    if role == "leader" and team in all_teams:
        return [team]
    return []
