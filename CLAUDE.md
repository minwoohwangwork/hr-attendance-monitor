# CLAUDE.md

이 파일은 이 저장소에서 작업하는 Claude Code를 위한 안내입니다. 사람용 설명은 README.md에 있습니다.

## 프로젝트 한눈에

- **무엇**: 가상 직원 50명의 근태 기록에서 주 52시간 초과 위험과 지각·결근 패턴을 탐지하고, 팀장용·인사팀용 주간 리포트를 자동 작성하는 데모. 2단계로 역할별 조회 범위를 제한한 읽기 전용 MCP 서버를 붙였다.
- **왜**: HR 인사운영·AX/DX 인턴 지원용 포트폴리오. 보여 주려는 것은 "AI를 잘 다룬다"가 아니라 "인사운영 업무를 이해하고, 사람이 판단할 부분을 직접 정했다"이다.
- **독자**: 개발자가 아닌 인사팀 실무자. 문서와 리포트는 짧은 문장, 표 위주로 쓴다.
- **데이터**: 모두 무작위로 만든 가상 데이터. 실제 회사·실존 인물 정보를 넣지 않는다.

## 작업 방식 (사용자 선호)

- 한국어로 답한다. 짧은 문장, 표와 목록 위주.
- 새 기능이나 구조 변경은 **설계를 먼저 보여 주고 승인받은 뒤** 구현한다. 단계가 나뉜 요청은 단계마다 멈추고 결과를 보고한다.
- 수치는 반드시 **실제로 실행한 결과**에서 가져온다. 추정치는 추정치라고 밝힌다(예: 업무 절감 150분 → 16분은 단계별 가정치).
- 검증하지 못한 것은 검증하지 못했다고 쓴다(예: Claude Desktop 화면에서의 동작).
- 커밋과 푸시는 요청받을 때만 한다. 문서 변경은 별도 브랜치에서 작업한 뒤 main에 합친 적이 있다.

## 구조

| 경로 | 역할 |
|---|---|
| `config.toml` | 기간, 팀, 출근 기준 시각(개인 > 팀 > 기본 09:00), 탐지 기준값 |
| `common.py` | 설정 읽기, 날짜·시간 계산, CSV 입출력, 열람 권한(`visible_teams`) |
| `generate_data.py` | 가상 데이터 생성, 정답지(`data/ground_truth.csv`) 저장 |
| `detect.py` | 52시간 등급, 7가지 패턴 탐지, 정답지 평가 |
| `report.py` | 리포트 작성 (Markdown·HTML, 규칙 기반 템플릿) |
| `main.py` | 전체 실행 (`--as-of`로 주중 점검, `--keep-data`로 데이터 재생성 생략) |
| `evaluate_seeds.py` | 시드 200개로 정확도 재측정, 오탐 분류 |
| `mcp_server/service.py` | 권한 확인·요약·조회 로그 (표준 라이브러리만) |
| `mcp_server/server.py` | MCP 도구 5개 정의 |
| `mcp_server/smoke_test.py` | 서버를 실제로 띄워 MCP로 호출하는 연결 점검 (12개 항목) |
| `tests/` | 단위 테스트 16개 (탐지 규칙 7, MCP 서비스 9) |
| `data/`, `output/` | 생성 데이터와 리포트. 시드가 고정이라 다시 실행해도 내용이 같다 |
| `logs/` | MCP 조회 로그. `.gitignore`로 제외 |

## 명령

```powershell
python main.py                          # 전체 실행
python main.py --as-of 2026-11-11       # 수요일 기준 주중 점검(사전 경고)
python -m unittest discover tests       # 단위 테스트 16개
python evaluate_seeds.py                # 시드 200개 정확도 (약 2초)
```

MCP 서버는 별도 가상환경을 쓴다. 동기화 폴더 밖에 둔다.

```powershell
python -m venv "$env:LOCALAPPDATA\hr-attendance-mcp\venv"
& "$env:LOCALAPPDATA\hr-attendance-mcp\venv\Scripts\python.exe" -m pip install -r mcp_server\requirements.txt
& "$env:LOCALAPPDATA\hr-attendance-mcp\venv\Scripts\python.exe" mcp_server\smoke_test.py
```

연결 점검이 끝나면 그 PC 경로가 들어간 Claude Desktop 설정을 출력한다.

## 환경 주의사항 (Windows)

- 셸은 Windows PowerShell 5.1. 한글 출력이 깨지면 `[Console]::OutputEncoding = [Text.Encoding]::UTF8; $env:PYTHONIOENCODING='utf-8'`를 먼저 실행한다.
- Python 3.11 이상(`tomllib` 사용). 개발은 3.15에서 했다. 1단계는 외부 패키지가 없다. MCP 서버에만 `mcp` 패키지를 쓴다.
- `git`이 PATH에 없을 수 있다. 그때는 `"$env:ProgramFiles\Git\cmd\git.exe"`로 직접 부른다.
- CSV는 엑셀 호환을 위해 `utf-8-sig`로 저장한다.
- `mcp` 2.x는 1.x와 API가 다르다. `FastMCP`가 아니라 `from mcp.server.mcpserver import MCPServer`를 쓴다. 인터넷의 1.x 예제를 그대로 쓰지 말고 설치된 패키지를 먼저 확인한다.
- stdio MCP 서버(`server.py`)에서는 `print()`를 쓰지 않는다. 표준 출력이 MCP 통신에 쓰인다.
- PowerShell `Compress-Archive`는 숨김 폴더 `.git`을 빼먹는다. 압축은 `tar`로 한다.

```powershell
tar.exe -a -cf C:\dev\HR.zip --exclude=__pycache__ --exclude=logs -C C:\dev HR
```

## 바꾸면 안 되는 원칙

사용자가 직접 정한 원칙이다. 바꿔야 할 이유가 생기면 먼저 묻는다.

1. **역할별 조회 범위**: 팀장은 자기 팀만, 인사팀은 전사. 역할은 서버 시작 인자로만 정하고 도구 인자로는 바꿀 수 없다.
2. **존재 여부 비공개**: 다른 팀 직원 조회와 없는 직원 조회에 같은 거부 메시지를 준다.
3. **읽기 전용**: 데이터를 고치는 도구를 만들지 않는다. 서버가 쓰는 파일은 조회 로그뿐이다.
4. **모든 조회 기록**: 허용·거부 모두 `logs/access_log.jsonl`에 남긴다. 조회 결과 자체는 남기지 않는다.
5. **요약만 반환**: 일자별 출퇴근 시각과 원본 기록은 반환하지 않는다. 주 단위 수치와 근거만.
6. **권장 조치 문구**: 징계가 아니라 면담·업무 재분배·휴식 권고 중심. 모든 리포트 하단에 "탐지 결과만으로 불이익 판단 금지" 안내를 둔다.
7. **가상 데이터만 사용**, 커밋되는 파일에 개인 경로(`C:\Users\...`)나 실제 개인정보를 넣지 않는다.

`tests/test_mcp_service.py`가 1~5를 검사한다. 이 원칙을 건드리는 변경 뒤에는 테스트와 연결 점검을 둘 다 돌린다.

## 공개된 수치와 출처

아래 값은 README, 노션 포트폴리오 페이지, 지원서에 그대로 쓰였다. 기준은 시드 42.

| 항목 | 값 | 확인 방법 |
|---|---|---|
| 데이터 | 50명 · 5개 팀 · 8주 · 1,821행 | `python main.py` |
| 탐지 | 심은 패턴 22건 중 20건 (91%) | `output/accuracy.md` |
| 정상 근무자 오탐 | 28명 중 0명 | `output/accuracy.md` ("심지 않은 직원 0명") |
| 수요일 사전 경고 | 최종 초과자 4명 중 3명, 잘못된 경고 0명 | `detect.analyze`를 기준일 2026-11-11과 결산일로 각각 실행해 비교 |
| 리포트 | 팀장용 5개 + 인사팀용 1개 | `output/reports/` |
| MCP 도구 | 5개, 모두 읽기 전용 | `smoke_test.py` |
| 테스트 | 단위 16개, 연결 점검 12개 | 위 명령 |
| 시드 200개 | 평균 21.6건 탐지, 심지 않은 탐지 평균 2.6건 | `python evaluate_seeds.py` |

**`config.toml`의 기준값, `generate_data.py`, `detect.py`를 바꾸면 위 수치가 달라진다.** 바꾸기 전에 사용자에게 알리고, 바꾼 뒤에는 다시 실행해서 README의 "30초 요약"과 "정답지 대비 탐지 정확도" 섹션을 함께 고친다. 노션 페이지와 지원서는 사용자가 직접 고쳐야 하므로 달라진 값을 보고한다.

## 남은 일과 알려진 한계

- **GitHub**: 원격 저장소가 아직 없다(비공개 저장소로 올릴 예정). 커밋 작성자는 저장소 로컬 설정에 들어 있다.
- **노션**: 포트폴리오 페이지 「근태·주 52시간 사전 경고 도구 (가상 데이터 데모)」와 「인사운영 업무 지도 & HR AX 후보 (공고 기반 가설)」 모두 비공개다. 노션 MCP로 제목을 검색하면 찾을 수 있다. 포트폴리오 페이지 부록의 GitHub 링크는 자리 표시로 비어 있다.
- **캡처 이미지**: 노션에 올린 리포트 PNG 3장과 생성 스크립트는 저장소 밖(원래 PC의 `C:\dev\HR-notion-images`)에 있어 이 저장소에 없다. 다시 만들려면 `output/reports/`의 HTML을 Edge 헤드리스(`--headless=new --screenshot`)로 캡처한다. MCP 조회 예시 이미지는 실제 도구 응답을 화면용으로 정리한 것이며 Claude Desktop 캡처가 아니다.
- **Claude Desktop 연결**: MCP SDK 클라이언트로만 검증했다. 실제 앱에서의 대화 조회는 확인하지 않았다.
- **'지각 증가 추세' 규칙**: 시드 200개 기준 잘못 짚은 탐지의 대부분이 이 규칙에서 나온다. 근무일당 비율로 비교하는 개선안을 실험만 했고(374건 → 209건) 반영하지 않았다. 반영하면 공개 수치가 바뀔 수 있으니 사용자와 상의한다.
- **규정 범위**: 주 단위 52시간만 계산한다. 탄력적·선택적 근로시간제, 휴일근로 가산은 반영하지 않았다.
