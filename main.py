"""전체 실행: 데이터 생성 → 탐지 → 리포트 작성.

사용법
    python main.py                      # 전체 실행 (주간 결산 리포트)
    python main.py --as-of 2026-11-11   # 수요일 기준 주중 점검 리포트 (사전 경고)
    python main.py --keep-data          # data/ 폴더의 CSV를 새로 만들지 않고 그대로 분석
"""
from __future__ import annotations

import argparse
import time
from collections import Counter
from datetime import date

import detect
import generate_data
import report
from common import DATA_DIR, OUT_DIR, load_config, read_csv


def main() -> None:
    ap = argparse.ArgumentParser(description="근태 모니터링 데모")
    ap.add_argument("--as-of", type=date.fromisoformat, help="기준일 (YYYY-MM-DD). 생략하면 데이터의 마지막 날")
    ap.add_argument("--keep-data", action="store_true", help="데이터를 새로 만들지 않고 data/ 의 CSV 사용")
    args = ap.parse_args()
    cfg = load_config()

    t0 = time.perf_counter()
    if not args.keep_data:
        employees, records, truth = generate_data.generate(cfg)
        generate_data.save(employees, records, truth)
    employees, records = detect.load_inputs()
    t1 = time.perf_counter()

    an = detect.analyze(cfg, employees, records, args.as_of)
    detect.save_outputs(an)
    t2 = time.perf_counter()

    paths = report.write_reports(cfg, an)
    t3 = time.perf_counter()

    key = "proj_tier" if an["midweek"] else "tier"
    tiers = Counter(a[key] for a in an["employees"].values())
    mode = "주중 점검(금요일 예상 기준)" if an["midweek"] else "주간 결산"
    print(f"[1] 데이터   직원 {len(employees)}명, 근태 기록 {len(records)}행  ({t1 - t0:.2f}초)")
    print(f"[2] 탐지     {an['week_start']} 주 {mode}: 초과 {tiers['초과']}명, 위험 {tiers['위험']}명, "
          f"주의 {tiers['주의']}명 / 패턴 {sum(len(a['flags']) for a in an['employees'].values())}건  ({t2 - t1:.2f}초)")
    print(f"[3] 리포트   {len(paths)}개 파일 → {paths[0].parent.relative_to(OUT_DIR.parent)}  ({t3 - t2:.2f}초)")

    truth_path = DATA_DIR / "ground_truth.csv"
    if truth_path.exists() and not an["midweek"]:
        ev = detect.evaluate(an, read_csv(truth_path))
        (OUT_DIR / "accuracy.md").write_text(detect.accuracy_markdown(ev, an), encoding="utf-8")
        print(f"[4] 정확도   심은 패턴 {len(ev['planted'])}건 중 {len(ev['found'])}건 탐지, "
              f"심지 않은 패턴 탐지 {len(ev['extra'])}건 → output/accuracy.md")
    print(f"총 소요 시간 {time.perf_counter() - t0:.2f}초")


if __name__ == "__main__":
    main()
