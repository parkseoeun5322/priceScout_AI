# 교재교구 최저가 검색 자동화 — 구현 계획

## 배경 (Context)
`origin/2026/06월/방림초등학교_교재교구 및 놀이활동 물품 구입 목록.xlsx`의 107개 상품에 대해,
기존 예상단가/구입처 링크에 의존하지 않고 네이버에서 직접 검색하여 실제 최저가를
자동으로 찾는다. 수동 검색이 물량 한계로 실패했다.

> **⚠️ 접근 방식 변경 (2026-06-16)**: 당초 `Playwright(브라우저 조종) → Claude API(HTML 파싱)`
> 파이프라인을 설계했으나, 정찰 결과 **네이버 쇼핑이 자동화 브라우저를 강력히 차단**(아래
> "레이어 2" 참조)해 사실상 불가능. → **네이버 검색 오픈 API**(`/v1/search/shop`) 기반으로 전환.
> 봇 차단·CAPTCHA 없이 JSON으로 최저가·상품명·URL을 받는다.

### 엑셀 실측 구조 (직접 분석 완료)
- 시트: `sheet1`, 헤더 1행, 상품 데이터 **2~108행 (107개, 연속)**, 109행 이후는 빈 스타일 행
- 컬럼: `A=순번 · B=상품명 · C=규격 · D=수량 · E=예상단가 · F=예상금액 · G=구입처(미사용)`
- 주의: **C(규격)는 자주 비어 있음** → 비면 상품명만으로 검색 / C에 **앞 공백**(`' 50x70cm'`) 존재 → strip 필요

### 확정된 결정사항
- **검색 경로**: 네이버 검색 오픈 API `GET https://openapi.naver.com/v1/search/shop.json`
  (Playwright 미사용 — 봇 차단으로 폐기). 인증은 헤더 `X-Naver-Client-Id`/`X-Naver-Client-Secret`.
- **정렬**: `sort=asc` (가격 오름차순 = 낮은 가격순). 동일상품 정확도는 검색어 토큰 매칭으로 직접 구현.
- **배송비**: **3안 — 배송비 칸은 비워두고 "확인필요"로 표시** (오픈 API에 배송비 필드 없음).
  → 따라서 합계도 배송비 미포함(추후 보강). 최종 단가 = `lprice`.
- 동일상품 매칭: 우선 **토큰 기반(순수 Python)** 으로 시작. 품질 부족 시 Claude API 도입 검토.
- 출력: **새 결과 시트로 깔끔하게** (원본 컬럼 나열 X, 결과 전용 표)

## 프로젝트 레이아웃 (생성 예정)
```
src_shopping/
  orchestrator.py        # 레이어 1: 엑셀 읽기 → 큐 → 배치 → 체크포인트
  search_worker.py       # 레이어 2: 네이버 검색 오픈 API 호출 → 결과 dict 반환
  config.py              # 경로/동시성/API 엔드포인트 등 설정
  requirements.txt       # pandas, openpyxl, httpx (playwright/anthropic 미사용)
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

### 레이어 2 — search_worker.py  (⬜ 미착수 / API 키 발급 대기)

#### 폐기된 접근: Playwright (정찰 결과 차단 확인, 2026-06-16)
`playwright`+`chromium` 설치 후 `probe_naver.py`로 실제 네이버 쇼핑 접근을 시험한 결과 **전부 차단**:
| 시도 | 결과 |
|---|---|
| Headless Chromium 딥링크(`search/all?query=`) | **HTTP 418** — "접속이 일시적으로 제한" |
| Headed Chromium 홈→검색 | **로그인 페이지로 강제 리다이렉트** |
| 실제 Chrome(`channel=chrome`) 딥링크 | **HTTP 405 + 이미지 CAPTCHA**(사람이 풀어야 함) |
stealth 옵션(`--disable-blink-features=AutomationControlled`, `navigator.webdriver` 제거, ko-KR/UA)을
적용해도 동일. → Playwright 경로 폐기. (`probe_naver.py`는 정찰용 임시 파일, 정리 예정.)

#### 채택된 접근: 네이버 검색 오픈 API
1. **API 호출**(`httpx` 권장, async): `GET https://openapi.naver.com/v1/search/shop.json`
   - 헤더: `X-Naver-Client-Id`, `X-Naver-Client-Secret` (환경변수로 주입)
   - 파라미터: `query`(=search_query), `display`(예: 30~100), `sort=asc`(낮은 가격순)
   - 응답 `items[]`: `title`(상품명, `<b>`태그·HTML엔티티 정리 필요), `link`(상품 URL),
     `lprice`(최저가), `mallName`, `productId`, `brand`, `maker`, `category1~4`
2. **동일상품 정확도 매칭**(순수 Python 토큰 비교):
   - 검색어(상품명+규격) 토큰이 `title`에 많이 포함된 후보를 선별,
   - 그 중 `lprice` 최소(이미 `sort=asc`) 항목 채택. 후보 없으면 null + 사유.
3. 결과 dict 반환 → 오케스트레이터(`distribute`)가 `Worker` 타입으로 주입받아 수집.
   - 반환 예: `{lprice, title(정리본), link, mallName, status}` (배송비 미포함 → 빈칸/"확인필요")

### 결과 저장 (openpyxl)
- 새 워크북/시트 헤더: `순번 · 상품명 · 규격 · 수량 · 검색어 · 최저가(단가) · 배송비(="확인필요") · 합계 · 매칭상품명 · 상품URL · 쇼핑몰 · 상태`
  - 배송비 칸은 비워두고 "확인필요" 표기, 합계는 배송비 미포함(추후 보강).
- `result/2026/06월/방림초등학교_교재교구_및_놀이활동_물품_구입_최저가목록.xlsx` 로 저장 (디렉토리 없으면 생성).

## 사전 준비 / 의존성
- **가상환경**: 프로젝트 루트에 `.venv/` 생성 완료 (Python 3.14.5). venv 자동 생성 `.gitignore`로 git 추적 제외됨.
  - 실행: `.\.venv\Scripts\python.exe ...` (또는 `.\.venv\Scripts\Activate.ps1` 후 `python ...`)
- **설치 완료(레이어 1)**: `pandas`(3.0.3), `openpyxl`(3.1.5) ✅
- **설치 완료(레이어 2)**: `httpx`(0.28.1), `python-dotenv`(1.2.2) ✅ (`playwright`는 봇 차단으로 폐기.
  `anthropic`은 토큰 매칭으로 시작하므로 당장 불필요.)
- **API 키 주입 환경 구성 완료** ✅: 네이버 개발자센터 앱 등록(사용 API=**검색**, 환경=WEB,
  서비스URL=`http://localhost`)으로 발급받은 값을 환경변수로 주입 (코드 하드코딩 금지):
  - 환경변수: `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`
  - `config.py`: 프로젝트 루트 `.env` 자동 로드(python-dotenv) → `naver_credentials()`(누락 시
    어떤 변수가 빠졌는지 에러) / `naver_api_headers()`(`X-Naver-Client-Id`/`X-Naver-Client-Secret` 헤더).
    상수 `NAVER_SHOP_API_URL`·`NAVER_DISPLAY`(30)·`NAVER_SORT`("asc") 추가.
  - `.env`는 `.gitignore`로 추적 제외, 템플릿 `env.example` 제공(`Copy-Item env.example .env`).
  - **사용자가 실제 키로 `.env` 생성 완료** ✅
- `requirements.txt`: `pandas`/`openpyxl`/`httpx`/`python-dotenv` 등록 완료.
- **콘솔 한글 깨짐 방지**: `config.setup_utf8_output()` 추가 → 각 진입점 `main()`에서 호출 (PowerShell cp949 대응).

## 검증 방법
- 단계 1: `python orchestrator.py --year 2026 --month 06 --school 방림초등학교 --limit 5`
  로 107건 파싱·검색어 생성 확인. 규격 빈 행이 상품명만으로 검색어가 되는지, 앞공백 strip 확인.
- 전체: 소수(예: 3건)만 워커까지 돌려 네이버 검색→파싱→엑셀 1행 저장 end-to-end 확인 후 전량 실행.

## 리스크 / 주의
- **배송비 누락**: 오픈 API에 배송비 필드 없음 → 3안(빈칸+"확인필요"). 정확 합계는 추후 보강 과제.
- **API 한도**: 검색 오픈 API 기본 하루 25,000건 → 107건은 여유. 그래도 저동시성·재시도·체크포인트 유지.
- **매칭 정확도**: 토큰 매칭만으로 동일상품 식별이 약할 수 있음 → 품질 부족 시 Claude API 도입 검토.
- API는 봇 차단이 없으므로 `MAX_CONCURRENT_BROWSERS`/지연 의미는 약해지나, 예의상·한도상 유지.

## 현재 진행 상태 (2026-06-18 업데이트)
- ✅ `.venv` 생성 + `pandas`/`openpyxl` 설치, `requirements.txt`/`README.md`/`.gitignore` 작성
- ✅ **레이어 1 전 단계(1~4) 구현·검증 완료** (`config.py`, `orchestrator.py`)
  - 단계 1 엑셀 읽기 / 단계 2 큐 생성 / 단계 3 배치 분배 / 단계 4 체크포인트
  - 각 함수에 단계 구역(banner) 주석, `config.py` 설정도 단계 표기, `setup_utf8_output()` 한글 출력
- ✅ **레이어 2 접근 방식 정찰·전환 결정**: Playwright 차단 확인(`probe_naver.py`) → **네이버 검색 오픈 API**로 전환
- ✅ **배송비 처리 = 3안(빈칸+"확인필요")** 확정
- ✅ **API 키 주입 환경 구성 완료**: `httpx`/`python-dotenv` 설치, `config.naver_api_headers()`/
  `naver_credentials()` + 오픈 API 상수 추가, `.env` 자동 로드, `env.example`/`.gitignore` 정비.
  사용자가 실제 키로 `.env` 생성 완료. (env 로드→헤더 생성→키 누락 에러 3항목 검증 통과)
- ⬜ **다음**: 레이어 2 `search_worker.py` — `httpx`로 오픈 API 호출(`naver_api_headers()`,
  `NAVER_SHOP_API_URL`, `sort=asc`) + `title` 정리 + 토큰 매칭으로 동일상품 선별, `Worker` 주입.
- ⬜ 결과 저장(openpyxl) 및 오케스트레이터↔워커 통합(end-to-end) 미착수
