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

### 레이어 1 · 단계 1 — 엑셀 읽기 ✅ (코드 작성 완료, 검증 대기)
- `config.py`: 입력 경로를 `(년도, 월, 학교명)` 파라미터로 조합. 출력/체크포인트 경로 규칙 동일하게 파생.
- `orchestrator.py`의 `read_products(path) -> list[dict]`:
  - pandas로 시트 읽기 (헤더 1행)
  - **B(상품명)가 비어있지 않은 행만** 채택 → 빈 스타일 행(109~) 자동 제외
  - 각 행을 `{"row", "no", "name", "spec", "qty", "search_query"}` 로 정규화
    (`search_query` = 상품명 + (규격 있으면 " " + 규격))
  - 건수 검증(기대 107) 및 수량 이상치 로깅
- 단독 실행으로 파싱된 107건을 콘솔 출력해 검증.

### 레이어 1 · 단계 2 — 큐 생성
- `chunk(products, size=10) -> list[list[dict]]` (107 → 11배치: 10×10 + 7)

### 레이어 1 · 단계 3 — 배치 분배
- `asyncio.gather`로 워커 호출하되 **동시 브라우저 수를 Semaphore로 제한(기본 2~3)** + 요청 간 랜덤 지연.
  네이버 봇 차단 회피 목적 (배치=10 전부 동시 실행 금지).

### 레이어 1 · 단계 4 — 체크포인트
- `checkpoint/{학교명}.json`에 완료된 `row`별 결과 누적 저장. 재시작 시 완료된 row는 건너뜀.

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
- `requirements.txt`: `pandas openpyxl playwright anthropic`
- 설치: `pip install -r requirements.txt` 후 `playwright install chromium`
- API 키: 환경변수 `ANTHROPIC_API_KEY` 사용 (코드에 하드코딩 금지)

## 검증 방법
- 단계 1: `python orchestrator.py --year 2026 --month 06 --school 방림초등학교 --limit 5`
  로 107건 파싱·검색어 생성 확인. 규격 빈 행이 상품명만으로 검색어가 되는지, 앞공백 strip 확인.
- 전체: 소수(예: 3건)만 워커까지 돌려 네이버 검색→파싱→엑셀 1행 저장 end-to-end 확인 후 전량 실행.

## 리스크 / 주의
- 네이버 봇 차단/캡차 → 저동시성 + 지연 + 재시도 + 체크포인트로 완화.
- HTML 구조 변경 → 셀렉터를 `config.py`로 분리.
- 비용 → Haiku 우선, 필요 시 Sonnet. HTML은 결과 영역만 잘라 토큰 절감.

## 현재 진행 상태
- ✅ 레이어 1 · 단계 1 코드 작성 완료 (`config.py`, `orchestrator.py`)
- ⏳ 검증 대기: `pandas`, `openpyxl` 설치 필요 (승인 후 진행)
- ⬜ 단계 2~4 및 레이어 2 미착수
