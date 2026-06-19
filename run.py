"""전체 파이프라인 실행 진입점.

레이어 1(orchestrator) + 레이어 2(search_worker)를 연결해
원본 엑셀 → 네이버 검색 → 결과 엑셀까지 전 과정을 실행한다.

사용:
  .venv\\Scripts\\python.exe run.py --year 2026 --month 06 --school 방림초등학교
  .venv\\Scripts\\python.exe run.py --year 2026 --month 06 --school 방림초등학교 --limit 5
"""
from __future__ import annotations

import argparse
import asyncio

import httpx

import config
import orchestrator as orch
import search_worker as sw


async def _run(year: str, month: str, school: str, limit: int | None) -> None:
    in_path = config.input_path(year, month, school)
    out_path = config.output_path(year, month, school)
    cp_path  = config.checkpoint_path(school)

    print(f"입력  : {in_path}")
    print(f"출력  : {out_path}")
    print(f"체크포인트: {cp_path}")
    print()

    # 레이어 1 — 단계 1: 엑셀 읽기
    products = orch.read_products(in_path)
    if limit is not None:
        products = products[:limit]
        print(f"[limit] 앞 {limit}건만 처리\n")

    # 레이어 1 — 단계 2~4 + 레이어 2: 배치 분배 (공유 클라이언트로 커넥션 풀 재사용)
    async with httpx.AsyncClient() as client:
        worker = sw.make_worker(client=client)
        results = await orch.distribute(products, worker, checkpoint=cp_path)

    # 레이어 2 — 결과 저장
    sw.save_results(results, out_path)

    # 최종 요약
    ok  = sum(1 for r in results if r.get("status") == "ok")
    low = sum(1 for r in results if r.get("status") == "low_match")
    no  = sum(1 for r in results if r.get("status") == "no_match")
    err = sum(1 for r in results if r.get("status") == "error")
    total = len(results)
    print(f"\n{'='*55}")
    print(f"완료: {total}건")
    print(f"  정상 매칭   (ok)        : {ok}건")
    print(f"  저신뢰 채택 (low_match) : {low}건  ← 확인 필요")
    print(f"  미매칭      (no_match)  : {no}건  ← 확인 필요")
    print(f"  오류        (error)     : {err}건")
    print(f"{'='*55}")
    print(f"출력 파일: {out_path}")


def main() -> None:
    config.setup_utf8_output()

    parser = argparse.ArgumentParser(description="교재교구 최저가 검색 자동화 — 전체 실행")
    parser.add_argument("--year",   required=True, help="년도 (예: 2026)")
    parser.add_argument("--month",  required=True, help="월 2자리 (예: 06)")
    parser.add_argument("--school", required=True, help="학교명 (예: 방림초등학교)")
    parser.add_argument("--limit",  type=int, default=None, help="앞 N건만 처리 (테스트용)")
    args = parser.parse_args()

    asyncio.run(_run(args.year, args.month, args.school, args.limit))


if __name__ == "__main__":
    main()