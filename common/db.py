"""SQLite DB — 에이전트끼리는 이 7개 테이블로만 데이터를 주고받습니다.

테이블          쓰는 쪽               읽는 쪽
daily_candles   4-1 데이터 수집기      4-2 후보 선정기
news            4-1 데이터 수집기      4-3 판단 에이전트
candidates      4-2 후보 선정기        4-3 판단 에이전트, 4-1 데이터 수집기(뉴스 대상)
decisions       4-3 판단 에이전트      4-4 주문 관리자 (BUY·SELL·WAIT 모두 처리)
sell_requests   4-5 청산 감시자        4-4 주문 관리자
positions_meta  4-4 주문 관리자        4-5 청산 감시자
orders_log      4-4 주문 관리자        점검·분석

요청 테이블(decisions, sell_requests)의 status 는 주문 관리자만 바꿉니다.
positions_meta 에는 지금 보유 중인 종목만 남기고, 전량 매도하면 지웁니다.
열(column)을 바꾸려면 팀원에게 먼저 알리고 설계도 5절도 함께 고칩니다.
"""
import sqlite3
from contextlib import contextmanager

from common.config import ROOT, get_config

# 요청 테이블(decisions, sell_requests)의 처리 상태
STATUS_NEW = "NEW"          # 아직 주문 관리자가 처리 안 함
STATUS_DONE = "DONE"        # 주문 완료
STATUS_SKIPPED = "SKIPPED"  # 규칙 위반 등으로 주문하지 않음

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_candles (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,          -- YYYY-MM-DD
    open        INTEGER,
    high        INTEGER,
    low         INTEGER,
    close       INTEGER,
    volume      INTEGER,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS news (
    news_id      TEXT PRIMARY KEY,      -- 기사 URL 해시 등 (중복 제거 기준)
    symbol       TEXT NOT NULL,
    title        TEXT NOT NULL,
    published_at TEXT NOT NULL,         -- ISO-8601 KST. 판단 시각 이후 기사는 쓰지 않음
    source       TEXT,
    url          TEXT,
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_symbol_time ON news (symbol, published_at);

CREATE TABLE IF NOT EXISTS candidates (
    run_date      TEXT NOT NULL,        -- 후보를 뽑은 날짜
    symbol        TEXT NOT NULL,
    name          TEXT,
    momentum_rank INTEGER,
    ret_5d        REAL,
    above_ma20    INTEGER,              -- 0/1
    PRIMARY KEY (run_date, symbol)
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('BUY', 'WAIT', 'SELL')),
    bad_news    INTEGER NOT NULL DEFAULT 0,
    reason      TEXT,
    risks       TEXT,                   -- JSON 배열 문자열
    news_ids    TEXT,                   -- JSON 배열 문자열
    status      TEXT NOT NULL DEFAULT 'NEW'
);
CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions (status);

CREATE TABLE IF NOT EXISTS sell_requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    why        TEXT NOT NULL CHECK (why IN ('STOP_LOSS', 'TAKE_PROFIT', 'EXPIRED')),
                                        -- 악재·LLM 매도는 decisions 의 SELL 로 처리
    status     TEXT NOT NULL DEFAULT 'NEW'
);
CREATE INDEX IF NOT EXISTS idx_sell_requests_status ON sell_requests (status);

CREATE TABLE IF NOT EXISTS positions_meta (
    symbol      TEXT PRIMARY KEY,       -- 현재 보유 중인 종목만. 전량 매도 시 삭제
    bought_at   TEXT NOT NULL,
    buy_price   INTEGER,
    reason      TEXT,
    decision_id INTEGER REFERENCES decisions (decision_id)
);

CREATE TABLE IF NOT EXISTS orders_log (
    order_id       INTEGER PRIMARY KEY, -- AQuA 가 준 orderId
    symbol         TEXT NOT NULL,
    side           TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity       INTEGER NOT NULL,
    status         TEXT,                -- OPEN / PARTIAL / FILLED / CANCELLED
    executed_price INTEGER,             -- 체결 수량 가중 평균 단가
    total_amount   INTEGER,             -- 체결 금액 합계 (수수료·세금 미포함)
    fee            INTEGER,             -- 수수료 (매수·매도 0.015%)
    tax            INTEGER,             -- 매도 세금 (0.20%)
    source         TEXT,                -- 'decision:<id>' 또는 'sell_request:<id>'
    created_at     TEXT NOT NULL
);
"""


def db_path():
    return ROOT / get_config()["db"]["path"]


@contextmanager
def connect():
    """with connect() as conn: ... — 블록이 끝나면 자동 commit."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # 여러 에이전트가 동시에 읽고 쓰기
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


if __name__ == "__main__":
    init_db()
    print(f"DB 준비 완료: {db_path()}")
