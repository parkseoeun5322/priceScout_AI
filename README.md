# 교재교구 최저가 검색 자동화 (priceScout_AI)

`origin/{년도}/{월}월/{학교명}_교재교구 및 놀이활동 물품 구입 목록.xlsx`의 각 상품을
**네이버 가격비교**에서 직접 검색하여 실제 최저가(+배송비)를 찾아 결과 엑셀로 저장하는
자동화 프로그램.

- 입력: `origin/{년도}/{월}월/{학교명}_교재교구 및 놀이활동 물품 구입 목록.xlsx`
- 출력: `result/{년도}/{월}월/{학교명}_교재교구_및_놀이활동_물품_구입_최저가목록.xlsx`
- 상세 설계/진행상황: [`plan/init.md`](plan/init.md), 프로젝트 규칙: [`CLAUDE.md`](CLAUDE.md)

---

## 새 PC에서 설치하기

> `.venv`(가상환경)는 git으로 옮기지 않습니다. 각 PC에서 새로 생성합니다.
> git에는 소스코드와 `requirements.txt`(설치 목록)만 올라갑니다.

### 1. Python 설치
- [python.org](https://www.python.org/downloads/)에서 설치. 설치 시 **"Add Python to PATH" 체크** 필수.
- 권장 버전: **Python 3.12** (3.14는 최신이라 레이어 2의 `playwright`/`anthropic` 휠 호환성 이슈 가능).
- 확인: `python --version`

### 2. 프로젝트 받기
```powershell
git clone <저장소 주소>
cd src_priceScout_AI
```

### 3. 가상환경 생성 + 라이브러리 설치
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> 참고(macOS/Linux): `python3 -m venv .venv` 후 `.venv/bin/python -m pip install -r requirements.txt`

### 4. (레이어 2 착수 시에만) 브라우저 + API 키
```powershell
# requirements.txt에서 playwright/anthropic 주석 해제 후 재설치한 뒤:
.\.venv\Scripts\python.exe -m playwright install chromium
```
- Claude API 키는 환경변수로 주입 (코드에 하드코딩 금지):
  ```powershell
  $env:ANTHROPIC_API_KEY = "sk-ant-..."
  ```

---

## 실행

가상환경의 Python으로 실행합니다.

```powershell
# 레이어 1 — 엑셀 읽기(단계1) + 큐 분할(단계2) 검증용 CLI
.\.venv\Scripts\python.exe orchestrator.py --year 2026 --month 06 --school 방림초등학교

# 앞에서 N건만 미리보기
.\.venv\Scripts\python.exe orchestrator.py --year 2026 --month 06 --school 방림초등학교 --limit 5
```

> 매번 `.\.venv\Scripts\python.exe`를 치기 번거로우면 `.\.venv\Scripts\Activate.ps1`로 활성화한 뒤
> `python ...`으로 실행할 수 있습니다. (프롬프트 앞에 `(.venv)` 표시)

---

## 현재 상태
- ✅ 레이어 1 (오케스트레이터) 단계 1~4 구현·검증 완료
  - 단계 1 엑셀 읽기 · 단계 2 큐 생성 · 단계 3 배치 분배 · 단계 4 체크포인트
- ⬜ 레이어 2 (`search_worker.py`: Playwright + Claude API) 미착수

자세한 단계별 진행 상황은 [`plan/init.md`](plan/init.md)를 참조하세요.