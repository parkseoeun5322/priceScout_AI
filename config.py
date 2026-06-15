"""프로젝트 전역 설정.

경로 규칙, 모델, 동시성, 엑셀 컬럼 매핑 등 변경 가능성이 있는 값을 한곳에 모은다.
레이어 1(오케스트레이터)과 레이어 2(워커)가 공통으로 참조한다.
"""

from __future__ import annotations

import sys
from pathlib import Path


def setup_utf8_output() -> None:
    """콘솔(stdout/stderr) 출력을 UTF-8로 강제한다.

    Windows PowerShell 기본 코드페이지(cp949)에서 한글이 깨지는 것을 방지.
    각 실행 진입점(main 등)에서 가장 먼저 호출한다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 경로
# ---------------------------------------------------------------------------
# 이 파일이 위치한 디렉토리를 프로젝트 루트로 간주한다.
PROJECT_ROOT = Path(__file__).resolve().parent
ORIGIN_DIR = PROJECT_ROOT / "origin"
RESULT_DIR = PROJECT_ROOT / "result"
CHECKPOINT_DIR = PROJECT_ROOT / "checkpoint"

# 입력 파일명 접미사 (학교명 뒤에 붙는 고정 부분)
INPUT_SUFFIX = "_교재교구 및 놀이활동 물품 구입 목록.xlsx"
# 출력 파일명 접미사
OUTPUT_SUFFIX = "_교재교구_및_놀이활동_물품_구입_최저가목록.xlsx"


def input_path(year: str, month: str, school: str) -> Path:
    """[단계 1] 원본 엑셀 경로를 (년도, 월, 학교명)으로 조합한다.

    예: input_path("2026", "06", "방림초등학교")
        -> origin/2026/06월/방림초등학교_교재교구 및 놀이활동 물품 구입 목록.xlsx
    """
    return ORIGIN_DIR / year / f"{month}월" / f"{school}{INPUT_SUFFIX}"


def output_path(year: str, month: str, school: str) -> Path:
    """[레이어 2 결과 저장] 결과 엑셀 경로를 (년도, 월, 학교명)으로 조합한다. (입력과 동일한 디렉토리 구조)"""
    return RESULT_DIR / year / f"{month}월" / f"{school}{OUTPUT_SUFFIX}"


def checkpoint_path(school: str) -> Path:
    """[단계 4] 체크포인트(진행상태) JSON 경로."""
    return CHECKPOINT_DIR / f"{school}.json"


# ---------------------------------------------------------------------------
# 엑셀 컬럼 매핑 (0-base 인덱스) — 헤더 1행, 데이터 2행부터
# [레이어 1 · 단계 1 — 엑셀 읽기에서 사용]
# A=순번 · B=상품명 · C=규격 · D=수량 · E=예상단가 · F=예상금액 · G=구입처(미사용)
# ---------------------------------------------------------------------------
COL_NO = 0       # A: 순번
COL_NAME = 1     # B: 상품명
COL_SPEC = 2     # C: 규격 (자주 비어 있음)
COL_QTY = 3      # D: 수량
COL_PRICE = 4    # E: 예상단가
COL_AMOUNT = 5   # F: 예상금액
COL_VENDOR = 6   # G: 구입처 (사용하지 않음)

HEADER_ROW = 0   # pandas header= 인자 (0-base): 1행이 헤더

# ---------------------------------------------------------------------------
# 배치 / 동시성
# [BATCH_SIZE → 단계 2(큐 생성) / 나머지 → 단계 3(배치 분배)에서 사용]
# ---------------------------------------------------------------------------
BATCH_SIZE = 10              # (단계 2) 큐를 10개 단위로 분할
MAX_CONCURRENT_BROWSERS = 2  # (단계 3) 네이버 봇 차단 회피: 동시 브라우저 수 제한

# (단계 3) 요청 간 랜덤 지연(초) — 봇 차단 회피. 각 워커 호출 전 이 범위에서 랜덤 대기.
REQUEST_DELAY_MIN = 1.0
REQUEST_DELAY_MAX = 3.0

# ---------------------------------------------------------------------------
# Claude API (레이어 2에서 사용)
# ---------------------------------------------------------------------------
# Haiku로 시작, 매칭 품질 부족 시 "claude-sonnet-4-6"로 상향
PARSE_MODEL = "claude-haiku-4-5"
# API 키는 환경변수 ANTHROPIC_API_KEY 로 주입 (코드에 하드코딩 금지)

# ---------------------------------------------------------------------------
# 네이버 가격비교 (레이어 2에서 사용)
# ---------------------------------------------------------------------------
NAVER_SHOPPING_HOME = "https://search.shopping.naver.com/home"
