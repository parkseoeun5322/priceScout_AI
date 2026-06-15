"""레이어 1 — 오케스트레이터.

순수 Python 로직 (Claude/Playwright 없음).
레이어 1의 4개 단계가 모두 구현되어 있다. 각 함수가 어느 단계에 해당하는지는
아래 구역(banner) 주석으로 표시한다.

  - 단계 1: 엑셀 읽기        → read_products (+ _clean, _to_int 헬퍼)
  - 단계 2: 큐 생성          → chunk
  - 단계 3: 배치 분배        → _process_one, process_batch, distribute
  - 단계 4: 체크포인트       → load_checkpoint, save_checkpoint (+ distribute에 통합)

레이어 2(search_worker.py: Playwright + Claude API)는 `Worker` 타입으로 주입된다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path
from typing import Awaitable, Callable

import pandas as pd

import config

# 워커 타입: 정규화된 상품 dict를 받아 검색 결과 dict를 반환하는 async 함수.
# 레이어 2(search_worker.py)에서 실제 구현을 주입한다.
Worker = Callable[[dict], Awaitable[dict]]


# ===========================================================================
# 단계 1 — 엑셀 읽기
#   원본 xlsx를 읽어 상품 행을 정규화(검색어 생성). 빈 행 제외, 규격 strip.
# ===========================================================================
def read_products(path: Path) -> list[dict]:
    """원본 엑셀을 읽어 상품 행을 정규화된 dict 리스트로 반환한다.

    - 헤더 1행, 데이터 2행부터.
    - 상품명(B)이 비어있는 행은 제외 (109행 이후의 빈 스타일 행 자동 제거).
    - 규격(C)이 비면 검색어는 상품명만 사용. C의 앞/뒤 공백은 strip.

    반환 항목 구조:
        {
            "row": 원본 엑셀 행번호(1-base),
            "no": 순번(A),
            "name": 상품명(B, strip),
            "spec": 규격(C, strip, 없으면 ""),
            "qty": 수량(D, int),
            "search_query": name + (" " + spec if spec else ""),
        }
    """
    if not path.exists():
        raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {path}")

    # header=0 → 1행을 헤더로. dtype=str 로 읽어 숫자 자동변환/지수표기 방지.
    df = pd.read_excel(path, header=config.HEADER_ROW, dtype=str)

    products: list[dict] = []
    skipped = 0

    for pos, row in df.iterrows():
        # df의 위치 인덱스 pos는 0-base(헤더 제외) → 원본 엑셀 행번호 = pos + 2
        excel_row = int(pos) + 2

        name = _clean(row.iloc[config.COL_NAME])
        if not name:
            skipped += 1
            continue

        spec = _clean(row.iloc[config.COL_SPEC])
        no = _clean(row.iloc[config.COL_NO])
        qty = _to_int(_clean(row.iloc[config.COL_QTY]))

        search_query = f"{name} {spec}".strip() if spec else name

        if qty is None:
            print(
                f"  [경고] row {excel_row} '{name}': 수량 파싱 실패 "
                f"(원값={row.iloc[config.COL_QTY]!r})",
                file=sys.stderr,
            )

        products.append(
            {
                "row": excel_row,
                "no": no,
                "name": name,
                "spec": spec,
                "qty": qty,
                "search_query": search_query,
            }
        )

    print(f"[read_products] 채택 {len(products)}건 / 빈 상품명 제외 {skipped}건")
    return products


# ===========================================================================
# 단계 2 — 큐 생성
#   상품 리스트를 BATCH_SIZE(기본 10)개 단위 배치로 분할 (107 → 11배치).
# ===========================================================================
def chunk(products: list[dict], size: int = config.BATCH_SIZE) -> list[list[dict]]:
    """상품 리스트를 size개 단위 배치로 분할한다.

    예: 107건, size=10 → 11배치 (10건짜리 10개 + 7건짜리 1개)

    마지막 배치는 size보다 작을 수 있다. 빈 입력이면 빈 리스트를 반환한다.
    """
    if size <= 0:
        raise ValueError(f"size는 1 이상이어야 합니다: {size}")
    return [products[i : i + size] for i in range(0, len(products), size)]


# ===========================================================================
# 단계 4 — 체크포인트 (입출력 함수)
#   완료된 결과를 {row: 결과} JSON으로 저장/복원. 중단 후 재시작 지원.
#   ※ distribute(단계 3)에서 배치마다 호출해 사용한다.
# ===========================================================================
def load_checkpoint(path: Path) -> dict[int, dict]:
    """체크포인트 JSON을 읽어 {row(int): 결과 dict} 로 반환한다.

    파일이 없으면 빈 dict. 손상되어 파싱 불가하면 경고 후 빈 dict로 시작
    (기존 결과를 잃더라도 처음부터 다시 진행할 수 있도록).
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  [경고] 체크포인트 읽기 실패({exc!r}) → 처음부터 시작", file=sys.stderr)
        return {}
    # JSON 키는 문자열이므로 int로 복원
    return {int(k): v for k, v in raw.items()}


def save_checkpoint(path: Path, results_by_row: dict[int, dict]) -> None:
    """결과를 체크포인트 JSON으로 원자적으로 저장한다.

    임시파일에 먼저 쓴 뒤 os.replace로 교체 → 저장 도중 중단되어도
    기존 체크포인트가 손상되지 않는다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(results_by_row, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


# ===========================================================================
# 단계 3 — 배치 분배 (+ 단계 4 체크포인트 통합)
#   asyncio.gather로 워커에 위임하되 세마포어로 동시 브라우저 수 제한,
#   요청 간 랜덤 지연으로 봇 차단 회피. distribute가 배치마다 체크포인트 저장.
# ===========================================================================
async def _process_one(
    product: dict,
    worker: Worker,
    sem: asyncio.Semaphore,
    *,
    min_delay: float,
    max_delay: float,
) -> dict:
    """상품 1건을 처리한다. (세마포어 점유 → 랜덤 지연 → 워커 호출)

    - `sem`으로 동시 실행(동시 브라우저) 수를 제한한다.
    - 워커 호출 전 랜덤 지연으로 네이버 봇 차단을 회피한다.
    - 워커가 예외를 던지면 한 건의 실패가 전체를 중단시키지 않도록
      status='error'인 결과 dict로 변환해 반환한다.

    반환: 원본 product 필드 + 워커 결과 필드(+status)를 병합한 dict.
    """
    async with sem:
        await asyncio.sleep(random.uniform(min_delay, max_delay))
        try:
            result = await worker(product)
            merged = {**product, **result}
            merged.setdefault("status", "ok")
            return merged
        except Exception as exc:  # noqa: BLE001 - 개별 실패 격리
            print(
                f"  [오류] row {product.get('row')} "
                f"'{product.get('name')}': {exc!r}",
                file=sys.stderr,
            )
            return {**product, "status": "error", "error": repr(exc)}


async def process_batch(
    batch: list[dict],
    worker: Worker,
    sem: asyncio.Semaphore,
    *,
    min_delay: float,
    max_delay: float,
) -> list[dict]:
    """한 배치(기본 10건)를 asyncio.gather로 동시 위임한다.

    동시 실행 수는 `sem`이 제한하므로 배치 10건이 한꺼번에 실행되지 않는다.
    """
    tasks = [
        _process_one(p, worker, sem, min_delay=min_delay, max_delay=max_delay)
        for p in batch
    ]
    return await asyncio.gather(*tasks)


async def distribute(
    products: list[dict],
    worker: Worker,
    *,
    max_concurrent: int = config.MAX_CONCURRENT_BROWSERS,
    batch_size: int = config.BATCH_SIZE,
    min_delay: float = config.REQUEST_DELAY_MIN,
    max_delay: float = config.REQUEST_DELAY_MAX,
    checkpoint: Path | None = None,
) -> list[dict]:
    """전체 상품을 배치 단위로 워커에 분배하고 결과를 수집한다.

    - 배치 단위로 순차 처리하고, `checkpoint` 경로가 주어지면 **배치마다 저장**.
    - 재시작 시 체크포인트에 이미 있는 row는 건너뛴다.
    - 배치 내부는 동시 처리하되 전역 세마포어로 동시 실행 수를 제한.
    - 반환: 기존(체크포인트) + 신규 결과를 **원본 products 순서**로 합친 리스트.
    """
    sem = asyncio.Semaphore(max_concurrent)

    # 체크포인트 로드 → 완료된 row 건너뛰기
    done: dict[int, dict] = load_checkpoint(checkpoint) if checkpoint else {}
    pending = [p for p in products if p["row"] not in done]
    if done:
        print(
            f"[checkpoint] 기존 완료 {len(done)}건 발견 → "
            f"건너뜀, 남은 {len(pending)}건 처리"
        )

    batches = chunk(pending, batch_size)
    for i, batch in enumerate(batches, start=1):
        batch_results = await process_batch(
            batch, worker, sem, min_delay=min_delay, max_delay=max_delay
        )
        for r in batch_results:
            done[r["row"]] = r
        if checkpoint:
            # 성공(ok) row만 저장 → 실패(error) row는 재시작 시 재시도된다.
            persisted = {
                row: r for row, r in done.items() if r.get("status") == "ok"
            }
            save_checkpoint(checkpoint, persisted)
        ok = sum(1 for r in batch_results if r.get("status") == "ok")
        print(
            f"[distribute] 배치 {i}/{len(batches)} 완료 "
            f"({ok}/{len(batch)} 성공) · 누적 {len(done)}/{len(products)}"
            + (" · 저장됨" if checkpoint else "")
        )

    # 원본 순서로 정렬해 반환 (건너뛴 행 포함)
    return [done[p["row"]] for p in products if p["row"] in done]


# ---------------------------------------------------------------------------
# 단계 1 보조 헬퍼 (셀 값 정규화 / 수량 파싱)
# ---------------------------------------------------------------------------
def _clean(value) -> str:
    """셀 값을 문자열로 정규화 (NaN/None → '', 앞뒤 공백 제거)."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() == "nan":
        return ""
    return s


def _to_int(value: str):
    """수량 문자열을 int로. 실패 시 None."""
    if not value:
        return None
    try:
        # '20', '20.0' 모두 허용
        return int(float(value))
    except (ValueError, TypeError):
        return None


# ===========================================================================
# 진입점 — 단계 1·2 단독 검증용 CLI
#   엑셀 읽기(단계1) + 큐 분할(단계2) 결과를 콘솔 출력.
#   ※ 단계 3·4(distribute)는 워커(레이어 2)가 필요하므로 여기서 호출하지 않음.
# ===========================================================================
def main() -> None:
    config.setup_utf8_output()

    parser = argparse.ArgumentParser(
        description="레이어 1 단계 1: 원본 엑셀을 읽어 상품/검색어를 파싱한다."
    )
    parser.add_argument("--year", required=True, help="년도 (예: 2026)")
    parser.add_argument("--month", required=True, help="월 2자리 (예: 06)")
    parser.add_argument("--school", required=True, help="학교명 (예: 방림초등학교)")
    parser.add_argument(
        "--limit", type=int, default=None, help="앞에서 N건만 출력 (검증용)"
    )
    args = parser.parse_args()

    path = config.input_path(args.year, args.month, args.school)
    print(f"입력 파일: {path}")

    products = read_products(path)

    preview = products if args.limit is None else products[: args.limit]
    for p in preview:
        print(
            f"  row{p['row']:>4} | no={p['no']:>3} | qty={p['qty']} "
            f"| query={p['search_query']!r}"
        )

    # 단계 2 — 큐 생성: 배치 분할 결과 요약
    batches = chunk(products)
    print(
        f"\n[chunk] {len(products)}건 → {len(batches)}배치 "
        f"(배치당 {config.BATCH_SIZE}건)"
    )
    for i, batch in enumerate(batches, start=1):
        rows = f"row {batch[0]['row']}~{batch[-1]['row']}"
        print(f"  배치 {i:>2}/{len(batches)} | {len(batch):>2}건 | {rows}")


if __name__ == "__main__":
    main()
