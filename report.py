"""3단계: 팀장용 주간 근태 요약 리포트와 인사팀용 전사 요약 작성.

문장은 규칙 기반 템플릿으로 만듭니다(같은 데이터면 항상 같은 리포트).
권장 조치는 징계가 아니라 면담·업무 재분배·휴식 권고를 중심으로 씁니다.
열람 권한: 팀장용 리포트에는 그 팀 구성원만, 인사팀용에는 전사가 들어갑니다.
"""
from __future__ import annotations

import html
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path

from common import OUT_DIR, PATTERNS, visible_teams
from detect import ABSENCE_FLAGS, LATE_FLAGS

DISCLAIMER = ("이 리포트는 자동 생성된 참고 자료입니다. 탐지 결과만으로 인사평가나 불이익 조치를 "
              "판단하지 말고, 반드시 본인 확인과 면담을 거쳐 주세요.")


# ── 문서 조립기: 같은 내용을 Markdown과 HTML로 내보낸다 ──
class Doc:
    def __init__(self, title: str):
        self.title = title
        self.blocks: list[tuple] = []

    def h2(self, text): self.blocks.append(("h2", text))
    def p(self, text): self.blocks.append(("p", text))
    def ul(self, items): self.blocks.append(("ul", list(items)))
    def table(self, headers, rows): self.blocks.append(("table", headers, [list(map(str, r)) for r in rows]))

    def to_md(self) -> str:
        out = [f"# {self.title}", ""]
        for b in self.blocks:
            if b[0] == "h2":
                out += [f"## {b[1]}", ""]
            elif b[0] == "p":
                out += [b[1], ""]
            elif b[0] == "ul":
                out += [f"- {item}" for item in b[1]] + [""]
            elif b[0] == "table":
                out.append("| " + " | ".join(b[1]) + " |")
                out.append("|" + "---|" * len(b[1]))
                out += ["| " + " | ".join(row) + " |" for row in b[2]] + [""]
        return "\n".join(out)

    def to_html(self) -> str:
        def inline(text):
            return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(text))

        def cell(text):
            if text in ("초과", "위험", "주의"):
                return f'<span class="tier t-{text}">{text}</span>'
            return inline(text)

        body = [f"<h1>{inline(self.title)}</h1>"]
        for b in self.blocks:
            if b[0] == "h2":
                body.append(f"<h2>{inline(b[1])}</h2>")
            elif b[0] == "p":
                body.append(f"<p>{inline(b[1])}</p>")
            elif b[0] == "ul":
                body.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in b[1]) + "</ul>")
            elif b[0] == "table":
                head = "".join(f"<th>{inline(h)}</th>" for h in b[1])
                rows = "".join("<tr>" + "".join(f"<td>{cell(c)}</td>" for c in r) + "</tr>" for r in b[2])
                body.append(f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>')
        return HTML_PAGE.format(title=html.escape(self.title), body="\n".join(body))


HTML_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: "Malgun Gothic", "Apple SD Gothic Neo", sans-serif; color: #1f2933;
       background: #f5f6f8; margin: 0; padding: 24px 16px; line-height: 1.6; }}
main {{ max-width: 860px; margin: 0 auto; background: #fff; padding: 28px 32px;
       border: 1px solid #e1e4e8; border-radius: 8px; }}
h1 {{ font-size: 1.4rem; margin: 0 0 12px; }}
h2 {{ font-size: 1.05rem; margin: 28px 0 8px; padding-bottom: 4px; border-bottom: 2px solid #e1e4e8; }}
.scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.92rem; }}
th, td {{ border: 1px solid #e1e4e8; padding: 6px 10px; text-align: left; white-space: nowrap; }}
th {{ background: #f0f2f5; }}
li {{ margin: 6px 0; }}
.tier {{ padding: 1px 8px; border-radius: 10px; font-weight: 700; }}
.t-초과 {{ background: #fde2e1; color: #a61b1b; }}
.t-위험 {{ background: #ffe8cc; color: #9a4a00; }}
.t-주의 {{ background: #fff6bf; color: #6b5900; }}
</style></head>
<body><main>
{body}
</main></body></html>
"""


# ── 문장 템플릿 ─────────────────────────────────────────
def fmt_h(hours) -> str:
    return "-" if hours is None else f"{hours:.1f}h"


def trend(a) -> str:
    return " → ".join(f"{h:.0f}" for h in a["recent_hours"])


def pattern_names(a, only=None) -> str:
    names = [PATTERNS[c] for c in a["flags"] if only is None or c in only]
    return ", ".join(names) or "-"


def actions_for(a: dict, an: dict) -> list[str]:
    """직원 한 명에 대한 권장 조치 문장 (면담·업무 재분배·휴식 권고 중심)."""
    out, flags, win = [], a["flags"], an["window_weeks"]
    if an["midweek"]:
        if a["proj_tier"] == "초과":
            out.append(f"현재 누적 {fmt_h(a['hours'])}로, 이 추세면 금요일까지 약 {fmt_h(a['projected'])}가 되어 "
                       "주 52시간을 넘을 것으로 예상됩니다. 남은 요일의 업무를 다른 구성원과 나누고 정시 퇴근을 권고해 주세요.")
        elif a["proj_tier"] == "위험":
            out.append(f"이 추세면 금요일까지 약 {fmt_h(a['projected'])}로 52시간에 근접합니다. "
                       "남은 일정의 우선순위를 조정해 추가 연장근로가 생기지 않도록 살펴봐 주세요.")
    elif a["tier"] == "초과":
        out.append(f"이번 주 실근로가 {fmt_h(a['hours'])}로 주 52시간을 넘었습니다. 담당 업무 일부를 재분배하고, "
                   "다음 주 초 대체휴무나 연차 사용을 권고해 주세요.")
    elif a["tier"] == "위험":
        out.append(f"이번 주 실근로가 {fmt_h(a['hours'])}로 52시간에 근접했습니다. 다음 주 일정의 우선순위를 조정해 "
                   "연장근로가 이어지지 않도록 업무 분담을 검토해 주세요.")
    elif a["tier"] == "주의":
        out.append(f"이번 주 실근로가 {fmt_h(a['hours'])}입니다. 연장근로가 늘고 있는지 업무량 추이를 살펴봐 주세요.")

    if "OVERWORK_CHRONIC" in flags:
        out.append(f"{flags['OVERWORK_CHRONIC']}로, 일시적 야근이 아니라 구조적인 업무 과부하일 수 있습니다. "
                   "1:1 면담에서 업무량과 인력 배치를 함께 점검해 주세요.")
    elif "OVERWORK_SPIKE" in flags and (a["tier"] == "초과" or a["proj_tier"] == "초과"):
        out.append("특정 주에 근로시간이 몰리는 유형입니다. 마감 일정을 분산하고 마감 직후 휴식을 보장하는 방안을 검토해 주세요.")

    if "LATE_WORSENING" in flags:
        out.append(f"지각이 {flags['LATE_WORSENING']}로 늘었습니다. 최근 달라진 사정이 있는지 "
                   "편한 분위기의 1:1 면담으로 먼저 안부를 확인해 주세요.")
    elif "LATE_REPEAT" in flags:
        out.append(f"최근 {win}주간 지각이 {a['late_recent']}회입니다. 통근·건강·돌봄 등 사정이 있는지 면담으로 먼저 듣고, "
                   "필요하면 시차 출근 적용을 검토해 주세요.")
    if "LATE_WEEKDAY_BIAS" in flags:
        out.append(f"지각·결근이 특정 요일에 몰려 있습니다({flags['LATE_WEEKDAY_BIAS']}). "
                   "해당 요일의 일정이나 전날 근무 부담을 함께 살펴봐 주세요.")

    if a["abs_run"] >= 2:
        out.append(f"이번 주 {a['abs_run']}일 연속 결근이 있었습니다. 건강이나 가정에 어려움이 없는지 안부 확인을 겸해 면담하고, "
                   "사용할 수 있는 휴가·휴직 제도를 안내해 주세요.")
    elif a["abs_week"] or "ABSENCE_UNEXCUSED" in flags:
        out.append(f"사전 신청 없는 결근이 지금까지 {len(a['absences'])}회 있었습니다. 사정을 먼저 듣고, "
                   "연차·반차 사전 신청 방법을 다시 안내해 주세요.")
    if "ABSENCE_HOLIDAY_ADJ" in flags:
        out.append("결근이 휴일 전후에 몰려 있습니다. 연휴 전후 휴가는 연차로 미리 신청할 수 있도록 안내해 주세요.")
    return out


def _needs_hours_row(a, midweek) -> bool:
    key = a["proj_tier"] if midweek else a["tier"]
    return key != "정상" or "OVERWORK_CHRONIC" in a["flags"]


def _needs_attendance_row(a) -> bool:
    return bool(a["abs_week"]) or any(c in LATE_FLAGS | ABSENCE_FLAGS for c in a["flags"])


def _hours_table(doc: Doc, rows: list[dict], midweek: bool, with_team=False) -> None:
    lead = ["팀", "이름"] if with_team else ["이름"]
    who = lambda a: [a["team"], a["name"]] if with_team else [a["name"]]
    over = lambda a: pattern_names(a, {"OVERWORK_CHRONIC", "OVERWORK_SPIKE"})
    if midweek:
        doc.table(lead + ["현재 누적", "금요일 예상", "예상 등급", "최근 4주 추이(h)", "탐지 패턴"],
                  [who(a) + [fmt_h(a["hours"]), fmt_h(a["projected"]), a["proj_tier"], trend(a), over(a)] for a in rows])
    else:
        doc.table(lead + ["이번 주 실근로", "등급", "수요일 시점 예상", "최근 4주 추이(h)", "탐지 패턴"],
                  [who(a) + [fmt_h(a["hours"]), a["tier"], fmt_h(a["projected"]), trend(a), over(a)] for a in rows])


def _week_counts(members: list[dict], ws) -> dict:
    """한 주의 팀 지표."""
    we = ws + timedelta(days=6)
    in_week = lambda d: ws <= d <= we
    return {
        "avg": sum(a["weekly"].get(ws, 0.0) for a in members) / len(members),
        "lates": sum(1 for a in members for d, _ in a["lates"] if in_week(d)),
        "absences": sum(1 for a in members for d in a["absences"] if in_week(d)),
        "leaves": sum(1 for a in members for d in a["leaves"] if in_week(d)),
    }


def _period(an: dict) -> str:
    ws = an["week_start"]
    return f"{ws:%Y-%m-%d} ~ {ws + timedelta(days=4):%m-%d}"


def _summary_line(members: list[dict], an: dict) -> str:
    midweek = an["midweek"]
    tiers = Counter(a["proj_tier"] if midweek else a["tier"] for a in members)
    late_people = sum(1 for a in members if a["late_week"])
    late_total = sum(a["late_week"] for a in members)
    abs_total = sum(len(a["abs_week"]) for a in members)
    abs_people = sum(1 for a in members if a["abs_week"])
    if not (tiers["초과"] or tiers["위험"] or tiers["주의"] or late_total or abs_total):
        return "이번 주에는 확인이 필요한 사항이 없습니다."
    prefix = f"{an['as_of']:%m/%d} 누적 기준, 금요일까지 52시간 초과 예상" if midweek else "이번 주 52시간 초과"
    return (f"{prefix} **{tiers['초과']}명**, 위험 {tiers['위험']}명, 주의 {tiers['주의']}명 · "
            f"지각 {late_total}건({late_people}명) · 무단결근 {abs_total}건({abs_people}명)")


# ── 팀장용 리포트 ───────────────────────────────────────
def build_team_report(an: dict, team: str) -> Doc:
    members = [a for a in an["employees"].values() if a["team"] == team]   # 자기 팀만
    midweek = an["midweek"]
    leader = next((a["name"] for a in members if a["role"] == "팀장"), "")
    doc = Doc(f"[{team}] {'주중 근태 점검' if midweek else '주간 근태 요약'} ({_period(an)})")
    doc.p(f"수신: {leader} 팀장 · 기준일: {an['as_of']} · 열람 범위: {team} 구성원 {len(members)}명")

    doc.h2("1. 한 줄 요약")
    doc.p(_summary_line(members, an))

    doc.h2("2. 근로시간 (주 52시간)")
    rows = sorted((a for a in members if _needs_hours_row(a, midweek)), key=lambda a: -a["hours"])
    if rows:
        _hours_table(doc, rows, midweek)
        if not midweek:
            doc.p("'수요일 시점 예상'은 수요일까지의 누적으로 계산한 주간 예상치입니다. "
                  "수요일에 이 도구를 실행하면 초과 전에 미리 조정할 수 있습니다.")
    else:
        doc.p("주 44시간을 넘은 구성원이 없습니다.")

    doc.h2("3. 지각·결근")
    rows = [a for a in members if _needs_attendance_row(a)]
    if rows:
        doc.table(["이름", "출근 기준", "이번 주 지각", f"최근 {an['window_weeks']}주 지각", "이번 주 무단결근", "탐지 패턴"],
                  [[a["name"], a["start"], f"{a['late_week']}회", f"{a['late_recent']}회",
                    f"{len(a['abs_week'])}회", pattern_names(a, LATE_FLAGS | ABSENCE_FLAGS)] for a in rows])
        doc.p("일회성 지각은 개별로 표시하지 않고 아래 팀 현황에만 집계합니다.")
    else:
        doc.p("반복되는 지각·결근 패턴이 없습니다.")

    doc.h2("4. 팀 현황")
    ws = an["week_start"]
    now, prev = _week_counts(members, ws), _week_counts(members, ws - timedelta(weeks=1))
    has_prev = ws - timedelta(weeks=1) in an["weeks"]
    prev_of = lambda key, unit, f="{:.0f}": (f.format(prev[key]) + unit) if has_prev else "-"
    doc.table(["항목", "이번 주", "지난주"], [
        ["1인 평균 실근로", f"{now['avg']:.1f}h", prev_of("avg", "h", "{:.1f}")],
        ["지각", f"{now['lates']}건", prev_of("lates", "건")],
        ["무단결근", f"{now['absences']}건", prev_of("absences", "건")],
        ["연차·반차 사용", f"{now['leaves']}건", prev_of("leaves", "건")],
    ])

    doc.h2("5. 권장 조치")
    items = []
    for a in members:
        if _needs_hours_row(a, midweek) or _needs_attendance_row(a):
            acts = actions_for(a, an)
            if acts:
                items.append(f"**{a['name']}**: " + " ".join(acts))
    if items:
        doc.ul(items)
    else:
        doc.p("이번 주에는 별도 조치가 필요하지 않습니다.")

    doc.h2("참고")
    doc.p(DISCLAIMER)
    return doc


# ── 인사팀용 전사 요약 ──────────────────────────────────
def build_hr_report(cfg: dict, an: dict) -> Doc:
    teams = visible_teams(cfg, "hr")
    everyone = list(an["employees"].values())
    midweek, ws = an["midweek"], an["week_start"]
    key = "proj_tier" if midweek else "tier"
    doc = Doc(f"[전사] {'주중 근태 점검' if midweek else '주간 근태 요약'} ({_period(an)})")
    doc.p(f"수신: 인사팀 · 기준일: {an['as_of']} · 열람 범위: 전사 {len(everyone)}명 ({len(teams)}개 팀)")

    doc.h2("1. 한 줄 요약")
    doc.p(_summary_line(everyone, an))

    doc.h2("2. 팀별 현황")
    rows = []
    for team in teams:
        members = [a for a in everyone if a["team"] == team]
        tiers = Counter(a[key] for a in members)
        c = _week_counts(members, ws)
        rows.append([team, len(members), f"{c['avg']:.1f}h", tiers["초과"], tiers["위험"], tiers["주의"],
                     f"{c['lates']}건", f"{c['absences']}건"])
    doc.table(["팀", "인원", "1인 평균 실근로", "초과", "위험", "주의", "지각", "무단결근"], rows)

    doc.h2("3. 52시간 초과·위험 대상자")
    rows = sorted((a for a in everyone if a[key] in ("초과", "위험")), key=lambda a: -a["hours"])
    if rows:
        _hours_table(doc, rows, midweek, with_team=True)
    else:
        doc.p("해당자가 없습니다.")

    doc.h2("4. 패턴 탐지 명단")
    rows = [[a["team"], a["name"], PATTERNS[c], detail]
            for a in everyone for c, detail in a["flags"].items()]
    if rows:
        doc.table(["팀", "이름", "패턴", "근거"], rows)
    else:
        doc.p("탐지된 패턴이 없습니다.")

    doc.h2("5. 주별 추이")
    th = cfg["thresholds"]
    rows = []
    for w in an["weeks"]:
        c = _week_counts(everyone, w)
        over = sum(1 for a in everyone if a["weekly"].get(w, 0) > th["weekly_limit"])
        danger = sum(1 for a in everyone if th["weekly_danger"] < a["weekly"].get(w, 0) <= th["weekly_limit"])
        rows.append([f"{w:%m/%d} 주", f"{c['avg']:.1f}h", f"{over}명", f"{danger}명", f"{c['lates']}건", f"{c['absences']}건"])
    doc.table(["주", "1인 평균 실근로", "52h 초과", "위험", "지각", "무단결근"], rows)
    if midweek:
        doc.p("마지막 주는 진행 중이라 기준일까지의 누적입니다.")

    doc.h2("참고")
    doc.p(DISCLAIMER)
    return doc


def write_reports(cfg: dict, an: dict, out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or OUT_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_주중점검" if an["midweek"] else ""
    prefix = f"{an['week_start']:%Y-%m-%d}"
    docs = [(f"{prefix}_전사_인사팀용{suffix}", build_hr_report(cfg, an))]
    docs += [(f"{prefix}_{team}_팀장용{suffix}", build_team_report(an, team))
             for team in visible_teams(cfg, "hr")]
    paths = []
    for name, doc in docs:
        for ext, text in ((".md", doc.to_md()), (".html", doc.to_html())):
            path = out_dir / (name + ext)
            path.write_text(text, encoding="utf-8")
            paths.append(path)
    return paths


if __name__ == "__main__":
    from common import load_config
    from detect import analyze, load_inputs
    config = load_config()
    written = write_reports(config, analyze(config, *load_inputs()))
    print(f"리포트 {len(written)}개 작성: {written[0].parent}")
