"""근태 조회 MCP 서버 (읽기 전용, mcp 2.x 기준).

실행 (별도 가상환경의 파이썬으로):
    python mcp_server/server.py --role leader --team 게임QA     # 팀장: 자기 팀만
    python mcp_server/server.py --role hr                       # 인사팀: 전사

역할은 여기 실행 인자로만 정해집니다. 도구의 인자로는 역할이나 열람 범위를 바꿀 수 없습니다.
stdio 방식이라 표준 출력은 MCP 통신에 쓰입니다. 이 파일에서 print()를 쓰면 안 됩니다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp_types import ToolAnnotations  # noqa: E402

from mcp_server.service import ROLE_NAMES, AccessDenied, AttendanceService  # noqa: E402

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


def build_server(service: AttendanceService) -> MCPServer:
    scope = ", ".join(service.scope)
    server = MCPServer(
        "attendance",
        instructions=(
            f"근태 요약 조회 서버입니다. 현재 역할은 {ROLE_NAMES[service.role]}이고 열람 범위는 {scope}입니다. "
            "읽기 전용이며, 일자별 출퇴근 기록이 아니라 주 단위 요약과 근거 수치만 제공합니다. "
            "탐지 결과는 확인이 필요하다는 신호이지 징계 근거가 아닙니다. "
            "조치를 제안할 때는 면담, 업무 재분배, 휴식 권고를 중심으로 답하세요."
        ),
    )

    def guarded(fn, *args, **kwargs) -> dict[str, Any]:
        """권한 거부와 잘못된 인자를 모델이 읽을 수 있는 오류 메시지로 바꾼다."""
        try:
            return fn(*args, **kwargs)
        except (AccessDenied, ValueError) as e:
            raise ToolError(str(e)) from None

    @server.tool(annotations=READ_ONLY)
    def get_scope() -> dict[str, Any]:
        """내 역할과 열람할 수 있는 팀, 기준 주를 알려 줍니다. 다른 조회 전에 먼저 호출하면 좋습니다."""
        return guarded(service.get_scope)

    @server.tool(annotations=READ_ONLY)
    def get_weekly_summary(team: str | None = None) -> dict[str, Any]:
        """팀별 이번 주 요약: 52시간 초과·위험·주의 인원, 지각·무단결근 건수, 1인 평균 실근로시간.
        team 을 생략하면 열람 가능한 모든 팀을 반환합니다."""
        return guarded(service.get_weekly_summary, team)

    @server.tool(annotations=READ_ONLY)
    def list_flagged_employees(team: str | None = None, category: str = "all") -> dict[str, Any]:
        """확인이 필요한 구성원 목록과 등급, 탐지 패턴, 근거 수치.
        category: all(전체), hours(근로시간), attendance(지각·결근)."""
        return guarded(service.list_flagged_employees, team, category)

    @server.tool(annotations=READ_ONLY)
    def get_employee_summary(name_or_id: str) -> dict[str, Any]:
        """구성원 한 명의 요약: 최근 주별 실근로시간, 지각·결근 횟수, 탐지 패턴과 근거, 권장 조치.
        이름 또는 사번(예: E004)으로 조회합니다. 일자별 출퇴근 시각은 제공하지 않습니다."""
        return guarded(service.get_employee_summary, name_or_id)

    @server.tool(annotations=READ_ONLY)
    def get_trend(team: str | None = None, weeks: int = 8) -> dict[str, Any]:
        """주별 추이: 1인 평균 실근로시간, 52시간 초과·위험 인원, 지각·무단결근 건수. 최대 8주."""
        return guarded(service.get_trend, team, weeks)

    return server


def main() -> None:
    ap = argparse.ArgumentParser(description="근태 조회 MCP 서버 (읽기 전용)")
    ap.add_argument("--role", required=True, choices=["leader", "hr"], help="leader=팀장, hr=인사팀")
    ap.add_argument("--team", help="팀장 역할일 때 담당 팀 이름 (예: 게임QA)")
    args = ap.parse_args()
    try:
        service = AttendanceService(args.role, args.team)
    except ValueError as e:
        sys.exit(f"시작할 수 없습니다: {e}")      # 표준 오류로 출력
    build_server(service).run("stdio")


if __name__ == "__main__":
    main()
