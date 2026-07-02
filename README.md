# NDX 듀얼 스크리너 — 나스닥100 일일 모멘텀 스크리너 (PWA + 백엔드 자동화)

매 거래일 자동으로 **Nasdaq 100** 종목을 두 가지 전략으로 스크리닝하고,
폰에서 탭하면 즉시 두 결과를 구분해 볼 수 있는 앱입니다.

- **스크리닝1 (반전 초기 포착)** — "방금 위로 돌기 시작한 종목"
- **스크리닝2 (추세 눌림목)** — "이미 강한 종목이 쉬었다가 재시동하는 지점"

> ⚠ **면책**: 개인용 분석 도구이며 투자 자문이 아닙니다. 결과는 후보일 뿐,
> 매매 판단과 책임은 사용자의 몫입니다. (앱 상단에도 항상 표시됩니다.)

---

## 만들지 않는 것 (OUT 스코프)

- 백테스트, 수익률 검증, 포지션 사이징
- 실시간/장중 데이터 (일봉 종가 기준, 하루 1회 갱신)
- 자동 주문, 브로커 연동, 푸시 알림
- 어닝 캘린더 필터 (yfinance 캘린더가 불안정 → 1차 범위 밖. D16으로 예약만)
- S&P500 등 다른 유니버스
- 네이티브 APK 빌드 (필요해지면 PWA를 TWA/Capacitor로 감싸는 탈출구만 예약)

---

## 두 스크린의 철학 차이

| | 스크리닝1 · 반전 초기 | 스크리닝2 · 추세 눌림목 |
|---|---|---|
| 노리는 것 | 하락/횡보에서 **막 돌아선** 종목 | **이미 강한** 종목의 재시동 지점 |
| 핵심 신호 | MACD 골든크로스 (오늘) | 3~8% 눌림 후 단기 고점 돌파 + 거래량 |
| 배경 필터 | SlowK ≥ 50, 거래대금 | QQQ 체제 + 상대강도 리더 + 정배열 + 고점 -15% 이내 |
| 결과 개수 | 보통 0~수십 | **0~수 개가 정상** (4층 필터) |
| 정렬 기본 | 거래대금 ↓ | RS(3m) ↓ |
| 성격 | 공격적, 소음 많음 | 보수적, 신호 드묾 |

**겹침 탭**: 두 전략이 동시에 가리키는 종목 — 반전과 추세가 겹치는 드문 지점.

### "스크리닝2 결과가 적은데 고장인가요?" — 아닙니다

층이 4개(체제→상대강도→구조→트리거)라 통과 종목이 0인 날이 많습니다.
QQQ가 200일선 아래면 그날은 **후보 0이 정상 동작**이고, 앱 S2 탭에
"시장 체제 필터: 오늘 진입 없음" 안내가 뜹니다 (에러 아님 — 스크린이 일한 결과).

항상 0이 계속되어 완화하고 싶다면 `config.json`에서 이 순서로:
1. `s2.rs.top_pct`: 10 → 25 (설계 원값) → 40 (상대강도 상위 % 완화)
2. `s2.pullback`: min 0.03 / max 0.08 → 범위 확대 (예: 0.02~0.10)

---

## 아키텍처

```
[GitHub Actions — 매 거래일 23:00 UTC 자동 실행]
  파이썬: NDX ~101종목 + QQQ 수집 → 지표 계산 → 스크리닝1·2 판정
    → docs/data/results.json 갱신·커밋 → GitHub Pages 자동 재배포
[폰 — PWA 앱]
  탭 → results.json 즉시 표시(계산은 이미 끝나 있음)
  오프라인이면 마지막 캐시본. 갱신되면 "업데이트됨" 토스트.
```

계산은 전부 서버측(무료 GitHub Actions)에서, 폰은 결과만 봅니다.
백엔드와 앱은 `results.json` 스키마로만 결합 → 한쪽을 통째로 교체 가능.

---

## GitHub 셋업 (초보용 단계별)

### 1) GitHub 가입
[github.com](https://github.com) → Sign up. 이메일 인증까지 마치세요.
> 팁: 사용자명은 앱 주소(`사용자명.github.io/저장소명`)에 들어가니 짧고 무난하게.

### 2) 공개 저장소 만들기
GitHub 우상단 **+** → **New repository** → 이름 예: `nasdaq100-dual-screener`
→ **Public** 선택 → Create repository.
> 팁: 무료 GitHub Pages는 **Public** 저장소에서만 됩니다. 이 프로젝트에는
> 비밀키가 전혀 없으므로 공개해도 안전합니다.

### 3) 코드 푸시
이 폴더에서 (Git 설치 필요 — [git-scm.com](https://git-scm.com)):
```bash
git remote add origin https://github.com/<사용자명>/nasdaq100-dual-screener.git
git push -u origin main
```
> 팁: 푸시할 때 브라우저 로그인 창이 뜹니다. 비밀번호 대신 로그인 승인만 하면 됩니다.

### 4) GitHub Pages 켜기
저장소 → **Settings** → 왼쪽 **Pages** →
Source: **Deploy from a branch** → Branch: `main`, 폴더: **/docs** → Save.
> 팁: 첫 배포는 1~5분 걸립니다. 초록 체크가 뜬 뒤
> `https://<사용자명>.github.io/nasdaq100-dual-screener/` 로 접속하세요.
> 404가 나오면 조금 더 기다렸다가 새로고침(Ctrl+F5).

### 5) Actions 활성화 + 수동 1회 실행
저장소 → **Actions** 탭 → 안내가 뜨면 "I understand… enable" 클릭 →
왼쪽 **daily-screen** → 오른쪽 **Run workflow** 버튼 → Run.
> 팁: 첫 실행은 3~10분. 성공하면 `docs/data/results.json`이 봇 커밋으로 갱신되고
> Pages가 자동 재배포됩니다. 실패하면 로그의 빨간 스텝을 클릭해 원인을 확인하세요.
> 참고: 무료 계정은 60일간 저장소 활동이 없으면 예약 실행이 잠들 수 있습니다 —
> 가끔 커밋하거나 Actions 탭에서 수동 실행 한 번이면 다시 깨어납니다.

### 6) 폰에 설치 — 두 가지 방법

**방법 A: APK 설치 (진짜 앱처럼)**
1. 저장소 → **Actions** 탭 → 왼쪽 **build-apk** → **Run workflow** → Run (3~5분)
2. 성공하면 저장소 → **Releases** 에 `NDX 스크리너 APK vX` 가 생김
3. **폰 Chrome**으로 Releases 페이지 접속 → `ndx-screener.apk` 탭 → 다운로드
4. 다운로드한 파일 열기 → "출처를 알 수 없는 앱" 허용 → 설치
> 팁: APK는 웹앱을 원격으로 띄우는 껍데기라, 이후 UI가 바뀌어도 **재설치가 필요 없습니다**.
> 다시 빌드해 설치할 일이 생기면(드묾) 기존 앱을 삭제한 뒤 설치하세요 —
> 빌드마다 서명 키가 새로 생성되기 때문입니다 (데이터 손실 없음).

**방법 B: PWA 홈 화면 추가 (설치 파일 없이)**
안드로이드 **Chrome**으로 앱 주소 접속 → 메뉴(⋮) → **"홈 화면에 추가"**.
> 팁: 어느 방법이든 첫 실행에서 한 번 온라인이면, 이후 오프라인에서도 마지막 결과가 보입니다.

---

## config.json — 전부 노브 (하드코딩 없음)

| 키 | 기본값 | 의미 |
|---|---|---|
| `history_days` | 400 | 수집 캘린더일 (SMA200+52주+RS 6개월 버퍼) |
| `s1.macd.gc_lookback_days` | 1 | 골든크로스 인정 기간(1~3) |
| `s1.stochastic.min` | 50 | SlowK 최소값 |
| `s1.volume.min_avg_dollar_volume` | $50M | 20일 평균 거래대금 하한 |
| `s1.trend/volume_surge/adx.enabled` | false | 선택 필터 3종 (켜면 AND 참여) |
| `s2.regime.enabled` | true | QQQ>SMA200 체제 스위치 (D13) |
| `s2.rs.top_pct` | 10 | 상대강도 상위 % (D14 — 백테스트 D18 결과로 25→10 조정, 인샘플 주의) |
| `s2.max_drawdown` | -0.15 | 52주 고점 대비 허용 하락 |
| `s2.pullback.min/max` | 3%/8% | 눌림 인정 범위 (D15) |
| `s2.trigger.mode` | breakout | `macd_gc`로 바꾸면 트리거 실험 가능 |
| `debug_show_all` | false | true면 탈락 종목도 items에 포함(디버그) |

---

## 결정 요약 (D0~D16 — 코드 주석에 이유·비용·탈출구 명시)

| ID | 결정 |
|---|---|
| D0 | 백엔드 자동(Actions) + 가벼운 PWA. APK 필요 시 TWA/Capacitor 탈출구 |
| D1 | yfinance 일봉(키 불필요). DataProvider 인터페이스로 교체 가능 |
| D2 | 위키피디아 NDX 표 + 캐시. 종목수 95~110 가드, override.csv 최우선 |
| D7 | as_of는 데이터 마지막 봉에서 재조립(시계 불신). stale 플래그 |
| D8 | GICS→한글 섹터 + 고정색 (위키 ICB 표는 ICB→GICS 1:1 폴백) |
| D9 | GitHub Pages(docs/) + Actions. 비밀키 없음, 무료 |
| D10~D12 | S1 선택 필터: SMA200 추세 / 교차봉 거래량 급증 / ADX (기본 off) |
| D13 | S2 1층 체제 필터: QQQ>SMA200. **약점**: 톱니장에서 신호 지연·반전 가능 |
| D14 | S2 2층 상대강도: rs_3m·rs_6m>1 + 유니버스 상위 10% (설계 25 → 백테스트 D18 채택으로 10, 탈출구: top_pct 노브) |
| D15 | S2 3·4층: 정배열 + 고점-15% 이내 + 3~8% 눌림 후 돌파 트리거 |
| D16 | (예약) 어닝 캘린더 필터 — yfinance 캘린더 안정화 전까지 보류 |
| D17 | APK = 의존성 0개 WebView 래퍼(`android/`), Actions에서 빌드→Release 발행. 서명 키는 즉석 생성(기본)·시크릿 고정(탈출구). 스토어 배포 시 TWA 교체 탈출구 |

---

## 개발자용

```bash
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements.txt pytest py_mini_racer
.venv/Scripts/python -m pytest backend -q     # 테스트 (프런트 렌더 테스트 포함)
.venv/Scripts/python backend/run.py --limit 20  # 소규모 실데이터
.venv/Scripts/python backend/run.py             # 전체 NDX
```

- 테스트: 경계값 4종(정상/매핑/None/변조) + 스키마 계약 + 앱 mock 4종 렌더
  (Node 없이 py_mini_racer 로 실제 app.js 를 실행해 검증).
- `results.json` 스키마는 **기존 필드 의미 변경 금지, 추가만 허용** (백엔드↔앱 계약).
- 키 위생: 현재 API 키 불필요. 유료 소스 추가 시 키는 Actions Secret 으로만.
