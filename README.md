# AQuA 트레이딩 에이전트

2026 2학기 자연어처리 실습 · AQuA(Agent Quant Arena) 모의투자 팀 프로젝트

모멘텀 후보를 규칙으로 고르고, LLM이 뉴스를 읽어 매수·관망·매도를 판단하고, 주문과 청산은 코드 규칙으로 실행합니다. 전체 설계는 팀 설계도 문서를 참고하세요.

## 폴더 구조와 담당

| 폴더 | 에이전트 | 담당 | 쓰는 테이블 | 읽는 테이블 |
| --- | --- | --- | --- | --- |
| `common/` | 공통(AQuA API, DB, 설정, 로그) | 데이터·인프라 (주문 함수는 팀장) | – | – |
| `data_collector/` | 4-1 데이터 수집기 | 데이터·인프라 | daily_candles, news | candidates, 포트폴리오(보유 종목) |
| `candidate_selector/` | 4-2 후보 선정기 | 전략 | candidates | daily_candles |
| `news_judge/` | 4-3 뉴스 분석·판단 (LLM) | NLP·LLM | decisions | candidates, news, 포트폴리오 |
| `order_manager/` | 4-4 주문·리스크 관리자 | 팀장 | orders_log, positions_meta, 요청 status | decisions, sell_requests |
| `exit_monitor/` | 4-5 청산 감시자 | 전략 | sell_requests | positions_meta, 포트폴리오 |

- `main.py`: 스케줄러. 정해진 시각에 위 에이전트를 순서대로 부릅니다.
- `config.yaml`: 모든 매매 수치. 코드에 숫자를 직접 쓰지 않습니다.
- `common/db.py`: 7개 테이블 정의(열 설명 포함).
- `docs/API.md`: AQuA 모의투자 API 정리(엔드포인트, 체결 방식, 수수료, 에러 코드).

## 처음 설정

```bash
git clone https://github.com/AITrader-KNU/AITrader.git
cd AITrader
python -m venv .venv
.venv\Scripts\activate             # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env             # Mac/Linux: cp .env.example .env  →  .env 에 팀 코드·서버 주소 입력
python main.py --once all          # 전체 흐름을 한 번 돌려 보기
python -m pytest                   # 테스트
```

새 터미널을 열 때마다 가상환경을 먼저 켭니다(프롬프트 앞에 `(.venv)`). PowerShell에서 activate가 막히면 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`를 한 번 실행합니다.

`--once all`을 실행하면 각 에이전트가 `TODO` 로그만 남기고 끝납니다. 각자 자기 폴더의 `run()`을 채우면 실제로 동작합니다.

## 실행

```bash
python main.py                     # 스케줄러 (학교 서버에서 계속 띄워 둠)
python main.py --once premarket    # 장 전 작업만: 일봉 갱신 → 후보 선정
python main.py --once judge        # 판단 사이클만: 뉴스 수집 → LLM 판단 → 주문
python main.py --once exit         # 청산 사이클만: 청산 감시 → 주문
```

| 시각 (평일, KST) | 작업 |
| --- | --- |
| 08:00 | 일봉 갱신 → 후보 선정 |
| 09:30 · 11:00 · 13:00 | 뉴스 수집 → LLM 판단 → 주문 |
| 09:00~14:45, 15분마다 | 청산 감시 → 주문 |

## 팀 규칙

1. **에이전트끼리는 DB 테이블로만 주고받습니다.** 다른 사람 폴더의 함수를 직접 부르지 않습니다. 테이블 열을 바꾸려면 팀원에게 먼저 알리고 설계도 5절도 고칩니다.
2. **주문은 `order_manager`만 냅니다.** 다른 사람은 테스트로도 `place_order`를 부르지 않습니다(거래 횟수와 30분 락을 팀이 공유).
3. **비밀값은 `.env`에만.** 팀 코드, 서버 주소, API 키를 코드·`config.yaml`·커밋 메시지에 쓰지 않습니다.
4. **수치는 `config.yaml`에만.** 값을 바꾸면 설계도 5절에도 반영합니다.
   - 매매에는 수수료·세금이 붙습니다(사고팔면 약 0.23%). 잦은 매매는 그만큼 불리합니다.
5. **투자 기간(11월)에는 장중(09:00~15:00)에 서버 코드를 바꾸지 않습니다.** 장 마감 후 반영하고 다음 날 지켜봅니다.

## 브랜치 규칙

- `main`: 학교 서버에서 돌아가는 코드. 직접 푸시하지 않습니다.
- 각자 작업 브랜치(이미 만들어져 있음): `feat/data-collector`, `feat/candidate-selector`, `feat/news-judge`, `feat/order-manager`, `feat/exit-monitor`
- 작업이 끝나면 Pull Request를 열고, 팀장이 확인한 뒤 `main`에 합칩니다.
- 합치기 전에 `python main.py --once all`과 `python -m pytest`가 통과하는지 확인합니다.

```bash
git checkout main && git pull               # 최신 main 받기
git checkout feat/candidate-selector        # 자기 브랜치로 이동 (새로 만들지 않음)
git merge main                              # 최신 main 합치기
git add . && git commit -m "후보 선정기: 유동성 필터 추가"
git push                                    # 이후 GitHub에서 PR 생성
```

다른 사람의 PR이 `main`에 합쳐지면, 다음 작업 전에 위 1~3줄을 다시 실행합니다.
