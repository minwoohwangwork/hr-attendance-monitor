"""MCP 서버 연결 점검: 실제로 서버를 띄워 MCP 프로토콜로 도구를 호출해 본다.

실행 (MCP 가상환경의 파이썬으로):
    <가상환경>\\Scripts\\python.exe mcp_server\\smoke_test.py

점검이 끝나면 이 PC의 경로가 들어간 Claude Desktop 설정 예시를 출력합니다.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent
SERVER = str(ROOT / "mcp_server" / "server.py")
LOG = ROOT / "logs" / "access_log.jsonl"
SERVERS = {
    "attendance-qa-leader": ["--role", "leader", "--team", "게임QA"],
    "attendance-hr": ["--role", "hr"],
}
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'통과' if ok else '실패'}] {label}" + (f" - {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def data(result) -> dict:
    """도구 결과에서 본문(JSON)을 꺼낸다."""
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


def text(result) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)


async def run(args: list[str]):
    params = StdioServerParameters(command=sys.executable, args=[SERVER, *args])
    return Client(params)


async def main() -> None:
    log_before = len(LOG.read_text(encoding="utf-8").splitlines()) if LOG.exists() else 0

    print("팀장(게임QA) 서버")
    async with await run(SERVERS["attendance-qa-leader"]) as client:
        tools = (await client.list_tools()).tools
        check("도구 5개 등록", len(tools) == 5, ", ".join(t.name for t in tools))
        check("모든 도구가 읽기 전용 표시", all(t.annotations and t.annotations.read_only_hint for t in tools))
        scope = data(await client.call_tool("get_scope", {}))
        check("열람 범위가 게임QA뿐", scope["열람_가능한_팀"] == ["게임QA"])
        flagged = data(await client.call_tool("list_flagged_employees", {"category": "hours"}))["확인이_필요한_구성원"]
        check("확인 필요 구성원 조회", bool(flagged) and {r["팀"] for r in flagged} == {"게임QA"}, f"{len(flagged)}명")
        one = data(await client.call_tool("get_employee_summary", {"name_or_id": flagged[0]["사번"]}))
        check("개인 요약에 근거와 권장 조치 포함", bool(one["탐지_패턴"]) and bool(one["권장_조치"]))
        denied = await client.call_tool("get_weekly_summary", {"team": "모바일QA"})
        check("다른 팀 조회는 거부", bool(denied.is_error), text(denied))
        denied = await client.call_tool("get_employee_summary", {"name_or_id": "E012"})
        check("다른 팀 직원 조회는 거부", bool(denied.is_error), text(denied))

    print("인사팀 서버")
    async with await run(SERVERS["attendance-hr"]) as client:
        summary = data(await client.call_tool("get_weekly_summary", {}))["팀별_요약"]
        check("전사 5개 팀 조회", len(summary) == 5, ", ".join(r["팀"] for r in summary))
        trend = data(await client.call_tool("get_trend", {"weeks": 8}))
        check("8주 추이 조회", len(trend["주별_추이"]) == 8 and trend["대상_인원"] == 50)
        one = await client.call_tool("get_employee_summary", {"name_or_id": "E012"})
        check("인사팀은 모바일QA 직원 조회 가능", not one.is_error)

    print("조회 로그")
    entries = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines()][log_before:]
    outcomes = [e["outcome"] for e in entries]
    check("도구 호출 8건이 모두 기록됨", len(entries) == 8, f"허용 {outcomes.count('allowed')}건, 거부 {outcomes.count('denied')}건")
    check("거부 2건이 기록됨", outcomes.count("denied") == 2)

    print()
    if failures:
        sys.exit(f"실패 {len(failures)}건: {failures}")
    print("모두 통과했습니다. Claude Desktop 설정 파일의 mcpServers 에 아래 내용을 넣으세요.\n")
    config = {"mcpServers": {name: {"command": sys.executable, "args": [SERVER, *args]}
                             for name, args in SERVERS.items()}}
    print(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
