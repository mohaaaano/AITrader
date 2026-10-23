"""뉴스 분석·판단 에이전트(news_judge) 단위 테스트

- 임시 SQLite DB 생성 및 테이블 초기화
- Gemini API 호출은 가짜 함수(Mock)로 대체하여 크레딧 소진 방지
- 주요 검증: 정상 매수/매도 판정, 미래 뉴스 차단, API 에러 시 Fallback(WAIT)
"""
import json
import sqlite3
from unittest.mock import patch
import pytest

from common.db import STATUS_NEW
from news_judge import judge


@pytest.fixture
def test_db_setup(tmp_path, monkeypatch):
    """테스트용 임시 DB 생성 및 공통 환경변수/연결 Mocking"""
    # 1. 임시 디렉토리에 테스트용 DB 파일 생성 (실제 data/aqua.db 보호)
    db_file = tmp_path / "test_aqua.db"
    
    # 2. 필요한 테이블 스키마 생성
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS candidates (
            run_date TEXT,
            symbol TEXT,
            name TEXT,
            momentum_rank INTEGER,
            ret_5d REAL,
            above_ma20 BOOLEAN
        );

        CREATE TABLE IF NOT EXISTS news (
            news_id TEXT PRIMARY KEY,
            symbol TEXT,
            title TEXT,
            published_at TEXT,
            source TEXT,
            url TEXT,
            collected_at TEXT
        );

        CREATE TABLE IF NOT EXISTS positions_meta (
            symbol TEXT PRIMARY KEY,
            bought_at TEXT,
            buy_price REAL,
            reason TEXT,
            decision_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS decisions (
            decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT,
            symbol TEXT,
            action TEXT,
            bad_news BOOLEAN,
            reason TEXT,
            risks TEXT,
            news_ids TEXT,
            status TEXT
        );
    """)
    conn.commit()
    conn.close()

    # 3. judge.py가 테스트용 DB에 연결하도록 connect 함수 패치
    class TestDBConnection:
        def __enter__(self):
            self.conn = sqlite3.connect(str(db_file))
            return self.conn

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.conn.commit()
            self.conn.close()

    monkeypatch.setattr(judge, "connect", lambda: TestDBConnection())
    monkeypatch.setattr(judge, "env", lambda key: "fake_gemini_api_key" if key == "GEMINI_API_KEY" else "")

    return str(db_file)


def test_judge_normal_buy_and_held_sell(test_db_setup):
    """테스트 1: 미보유 종목 BUY 판정 및 보유 종목 악재 시 SELL 판정 검증"""
    db_path = test_db_setup
    fixed_now_iso = "2026-10-24T09:30:00+09:00"
    fixed_date = "2026-10-24"

    # 가짜 데이터 적재: 후보 종목 1개, 보유 종목 1개
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        # 후보 종목: 삼성전자
        c.execute("INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?)",
                  (fixed_date, "005930", "삼성전자", 1, 0.045, 1))
        # 보유 종목: SK하이닉스
        c.execute("INSERT INTO positions_meta (symbol, bought_at, buy_price) VALUES (?, ?, ?)",
                  ("000660", "2026-10-20T09:30:00+09:00", 180000))
        # 과거 뉴스 적재 (판단 시각 이전)
        c.execute("INSERT INTO news (news_id, symbol, title, published_at) VALUES (?, ?, ?, ?)",
                  ("n1", "005930", "삼성전자 수주 확대 호재", "2026-10-24T08:50:00+09:00"))
        c.execute("INSERT INTO news (news_id, symbol, title, published_at) VALUES (?, ?, ?, ?)",
                  ("n2", "000660", "SK하이닉스 공장 가동 일시 중단 악재", "2026-10-24T09:10:00+09:00"))

    # Gemini 호출 시 가짜 응답 설정
    fake_gemini_response = {
        "text": json.dumps([
            {
                "symbol": "005930",
                "action": "BUY",
                "bad_news": False,
                "reason": "대규모 수주 호재 확인",
                "risks": ["단기 매물 출회"],
                "news_ids": ["n1"]
            },
            {
                "symbol": "000660",
                "action": "SELL",
                "bad_news": True,
                "reason": "보유 종목 공장 가동 중단 악재 발생",
                "risks": ["실적 감소 우려"],
                "news_ids": ["n2"]
            }
        ]),
        "prompt_tokens": 1500,
        "candidates_tokens": 200,
        "total_tokens": 1700
    }

    with patch.object(judge, "get_current_kst_iso", return_value=fixed_now_iso), \
         patch.object(judge, "call_gemini_api", return_value=fake_gemini_response):
        
        # 에이전트 실행
        judge.run()

    # 결과 테이블(decisions) 검증
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("SELECT symbol, action, bad_news, status FROM decisions ORDER BY symbol")
        rows = c.fetchall()

    assert len(rows) == 2
    # SK하이닉스: 보유 종목 악재 -> SELL, bad_news=True, status='NEW'
    assert rows[0] == ("000660", "SELL", 1, STATUS_NEW)
    # 삼성전자: 후보 종목 호재 -> BUY, bad_news=False, status='NEW'
    assert rows[1] == ("005930", "BUY", 0, STATUS_NEW)


def test_judge_future_news_exclusion(test_db_setup):
    """테스트 2: 판단 시각 이후의 미래 뉴스가 입력에서 제외되는지 검증"""
    db_path = test_db_setup
    fixed_now_iso = "2026-10-24T09:30:00+09:00"
    fixed_date = "2026-10-24"

    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?)",
                  (fixed_date, "035420", "NAVER", 1, 0.02, 1))
        # 과거 뉴스 (포함되어야 함)
        c.execute("INSERT INTO news (news_id, symbol, title, published_at) VALUES (?, ?, ?, ?)",
                  ("past_1", "035420", "과거 정상 뉴스", "2026-10-24T09:20:00+09:00"))
        # 미래 뉴스 (9시 40분 기사 -> 제외되어야 함)
        c.execute("INSERT INTO news (news_id, symbol, title, published_at) VALUES (?, ?, ?, ?)",
                  ("future_1", "035420", "미래 뉴스 유출 테스트", "2026-10-24T09:40:00+09:00"))

    captured_prompt = {}

    def fake_gemini_call(prompt, model, key):
        captured_prompt["text"] = prompt
        return {
            "text": json.dumps([{
                "symbol": "035420", "action": "WAIT", "bad_news": False,
                "reason": "중립", "risks": [], "news_ids": ["past_1"]
            }]),
            "prompt_tokens": 100, "candidates_tokens": 50, "total_tokens": 150
        }

    with patch.object(judge, "get_current_kst_iso", return_value=fixed_now_iso), \
         patch.object(judge, "call_gemini_api", side_effect=fake_gemini_call):
        
        judge.run()

    # 프롬프트 내용에 과거 기사는 있고 미래 기사는 없어야 함
    assert "과거 정상 뉴스" in captured_prompt["text"]
    assert "미래 뉴스 유출 테스트" not in captured_prompt["text"]


def test_judge_fallback_on_api_error(test_db_setup):
    """테스트 3: Gemini API 오류 발생 시 프로그램이 죽지 않고 WAIT로 Fallback 저장되는지 검증"""
    db_path = test_db_setup
    fixed_now_iso = "2026-10-24T09:30:00+09:00"
    fixed_date = "2026-10-24"

    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?)",
                  (fixed_date, "005380", "현대차", 1, 0.01, 1))

    # Gemini 호출 시 강제로 예외(Exception) 발생
    with patch.object(judge, "get_current_kst_iso", return_value=fixed_now_iso), \
         patch.object(judge, "call_gemini_api", side_effect=Exception("API 연결 시간 초과")):
        
        # 에러로 프로그램이 중단되지 않고 끝까지 돌아야 함
        judge.run()

    # decisions 테이블에 WAIT 상태로 저장되었는지 확인
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("SELECT symbol, action, bad_news, status FROM decisions")
        row = c.fetchone()

    assert row is not None
    assert row[0] == "005380"
    assert row[1] == "WAIT"       # 안전하게 WAIT로 전환됨
    assert row[2] == 0            # bad_news는 False
    assert row[3] == STATUS_NEW   # status는 'NEW'
