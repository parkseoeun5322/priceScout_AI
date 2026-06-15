# 교재교구 최저가 검색 자동화 — 구현 계획

## 배경 (Context)
`origin/2026/06월/방림초등학교_교재교구 및 놀이활동 물품 구입 목록.xlsx`의 107개 상품에 대해,
기존 예상단가/구입처 링크에 의존하지 않고 **네이버 가격비교**에서 직접 검색하여 실제 최저가(+배송비)를
자동으로 찾는다. 수동 검색이 물량 한계로 실패했기에
`Playwright(브라우저 조종) → Claude API(HTML 파싱) → openpyxl(저장)` 파이프라인으로 대체한다.

### 엑셀 실측 구조 (직접 분석 완료)
- 시트: `sheet1`, 헤더 1행, 상품 데이터 **2~108행 (107개, 연속)**, 109행 이후는 빈 스타일 행
- 컬럼: `A=순번 · B=상품명 · C=규격 · D=수량 · E=예상단가 · F=예상금액 · G=구입처(미사용)`
- 주의: **C(규격)는 자주 비어 있음** → 비면 상품명만으로 검색 / C에 **앞 공백**(`' 50x70cm'`) 존재 → strip 필요

### 확정된 결정사항
- HTML 파싱 모델: **`claude-haiku-4-5`** 로 시작, 매칭 품질 부족 시 `claude-sonnet-4-6`로 상향
- 출력: **새 결과 시트로 깔끔하게** (원본 컬럼 나열 X, 결과 전용 표)
- 합계: **(최저가 + 배송비) × 수량(D열)**

## 프로젝트 레이아웃 (생성 예정)
```
src_shopping/
  orchestrator.py        # 레이어 1: 엑셀 읽기 → 큐 → 배치 → 체크포인트
  search_worker.py       # 레이어 2: Playwright → Claude API → 결과 dict 반환
  config.py              # 경로/모델/동시성/네이버 셀렉터 등 설정
  requirements.txt       # pandas, openpyxl, playwright, anthropic
  checkpoint/            # 진행상태 JSON (재시작용)
  origin/2026/06월/...xlsx
  result/2026/06월/방림초등학교_교재교구_및_놀이활동_물품_구입_최저가목록.xlsx
```

## 구현 단계 (레이어 1 / 단계 1 부터 점진적)

### 레이어 1 · 단계 1 — 엑셀 읽기 ✅ (검증 완료)
- `config.py`: 입력 경로를 `(년도, 월, 학교명)` 파라미터로 조합. 출력/체크포인트 경로 규칙 동일하게 파생.
- `orchestrator.py`의 `read_products(path) -> list[dict]`:
  - pandas로 시트 읽기 (헤더 1행)
  - **B(상품명)가 비어있지 않은 행만** 채택 → 빈 스타일 행(109~) 자동 제외
  - 각 행을 `{"row", "no", "name", "spec", "qty", "search_query"}` 로 정규화
    (`search_query` = 상품명 + (규격 있으면 " " + 규격))
  - 건수 검증(기대 107) 및 수량 이상치 로깅
- 단독 실행으로 파싱된 107건을 콘솔 출력해 검증.

### 레이어 1 · 단계 2 — 큐 생성 ✅ (검증 완료)
- `chunk(products, size=10) -> list[list[dict]]` (107 → 11배치: 10×10 + 7)
- 검증: 빈 입력/균등·비균등 분할/size=1/size≥전체/size≤0 예외/**원소 보존**(실데이터 107건 flatten==원본)

### 레이어 1 · 단계 3 — 배치 분배 ✅ (검증 완료)
- `distribute(products, worker, ...)`: 배치 단위 순차 + 배치 내부 `asyncio.gather` 동시 처리.
  **동시 브라우저 수를 Semaphore로 제한**(`MAX_CONCURRENT_BROWSERS`, 기본 2) + 요청 간 랜덤 지연
  (`REQUEST_DELAY_MIN/MAX`)으로 네이버 봇 차단 회피.
- 워커는 **주입(injection)** 방식(`Worker` 타입) → 레이어 2 없이 stub으로 검증 가능.
- 개별 워커 예외는 `status='error'`로 격리해 전체 중단 방지. 결과는 원본 순서 보존.
- 검증: 동시성 제한(peak≤max)/순서 보존/예외 격리/랜덤 지연/**실데이터 107건 11배치 통과**.

### 레이어 1 · 단계 4 — 체크포인트 ✅ (검증 완료)
- `checkpoint/{학교명}.json`에 완료된 `row`별 결과 누적 저장. 재시작 시 완료된 row는 건너뜀.
- `load_checkpoint`/`save_checkpoint`(임시파일→`os.replace` **원자적 교체**)로 입출력. `distribute`가 배치마다 저장.
- **성공(ok) row만 영속화** → 실패(error) row는 재시작 시 자동 재시도 (네이버 일시 차단 대비).
- 손상된 JSON은 경고 후 처음부터 시작.
- 검증: 최초실행/완료분 건너뜀/부분완료 재시작/실패 재시도/손상파일 복구 5항목 통과.

### 레이어 2 — search_worker.py
1. **Playwright**: `https://search.shopping.naver.com/home` 검색 → 정렬 '네이버 랭킹순' →
   **'낮은 가격순'** 변경 → 동적 로딩 대기 후 결과 영역 HTML 추출.
   (셀렉터는 `config.py`로 분리.)
2. **Claude API**(`anthropic`, `claude-haiku-4-5`): HTML을 받아 규칙 적용
   - **동일 상품 정확도 우선** → 검색어 토큰이 상품명에 많이 포함된 후보들 중에서
   - **낮은 가격순 첫 번째** 채택 (단순 1번째 X)
   - 추출: `{최저가, 배송비, 상품명, 상품URL}` JSON (없으면 null + 사유)
3. 결과 dict 반환 → 오케스트레이터가 수집.

### 결과 저장 (openpyxl)
- 새 워크북/시트 헤더: `순번 · 상품명 · 규격 · 수량 · 검색어 · 최저가(단가) · 배송비 · 합계((최저가+배송비)×수량) · 매칭상품명 · 상품URL · 상태`
- `result/2026/06월/방림초등학교_교재교구_및_놀이활동_물품_구입_최저가목록.xlsx` 로 저장 (디렉토리 없으면 생성).

## 사전 준비 / 의존성
- **가상환경**: 프로젝트 루트에 `.venv/` 생성 완료 (Python 3.14.5). venv 자동 생성 `.gitignore`로 git 추적 제외됨.
  - 실행: `.\.venv\Scripts\python.exe ...` (또는 `.\.venv\Scripts\Activate.ps1` 후 `python ...`)
- **설치 완료(레이어 1)**: `pandas`(3.0.3), `openpyxl`(3.1.5) ✅
- **미설치(레이어 2)**: `playwright`, `anthropic` — 착수 시 설치 + `playwright install chromium` 필요.
  - ⚠️ Python 3.14는 최신이라 `playwright`/`anthropic` 휠 호환성 확인 필요. 막히면 3.12로 venv 재생성 고려.
- API 키: 환경변수 `ANTHROPIC_API_KEY` 사용 (코드에 하드코딩 금지)
- `requirements.txt`: 아직 미생성 (레이어 2 착수 시 `pandas openpyxl playwright anthropic`로 작성 예정)
- **콘솔 한글 깨짐 방지**: `config.setup_utf8_output()` 추가 → 각 진입점 `main()`에서 호출 (PowerShell cp949 대응).

## 검증 방법
- 단계 1: `python orchestrator.py --year 2026 --month 06 --school 방림초등학교 --limit 5`
  로 107건 파싱·검색어 생성 확인. 규격 빈 행이 상품명만으로 검색어가 되는지, 앞공백 strip 확인.
- 전체: 소수(예: 3건)만 워커까지 돌려 네이버 검색→파싱→엑셀 1행 저장 end-to-end 확인 후 전량 실행.

## 리스크 / 주의
- 네이버 봇 차단/캡차 → 저동시성 + 지연 + 재시도 + 체크포인트로 완화.
- HTML 구조 변경 → 셀렉터를 `config.py`로 분리.
- 비용 → Haiku 우선, 필요 시 Sonnet. HTML은 결과 영역만 잘라 토큰 절감.

## 현재 진행 상태 (2026-06-15 업데이트)
- ✅ `.venv` 생성 + `pandas`/`openpyxl` 설치
- ✅ **레이어 1 전 단계(1~4) 구현·검증 완료** (`config.py`, `orchestrator.py`)
  - 단계 1 엑셀 읽기 / 단계 2 큐 생성 / 단계 3 배치 분배 / 단계 4 체크포인트
  - 각 함수에 단계 구역(banner) 주석 부착, `config.py` 설정도 단계 표기
  - `config.setup_utf8_output()`로 콘솔 한글 출력 처리
- ⬜ **다음**: 레이어 2 `search_worker.py` (Playwright + Claude API)
  - 선행: `playwright`/`anthropic` 설치(3.14 호환성 확인), `ANTHROPIC_API_KEY` 설정
- ⬜ 결과 저장(openpyxl) 및 오케스트레이터↔워커 통합(end-to-end) 미착수
