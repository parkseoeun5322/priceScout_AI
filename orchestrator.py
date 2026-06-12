"""레이어 1 — 오케스트레이터.

순수 Python 로직 (Claude/Playwright 없음).
이 파일은 현재 **단계 1: 엑셀 읽기**만 구현되어 있다.
이후 단계(큐 생성 / 배치 분배 / 체크포인트)는 승인 후 추가한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

import config


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


def main() -> None:
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


if __name__ == "__main__":
    main()
