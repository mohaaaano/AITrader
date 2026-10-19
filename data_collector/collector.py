"""4-1 데이터 수집기 — 담당: 데이터·인프라

쓰는 테이블: daily_candles, news
읽는 것: candidates (뉴스를 모을 종목), 포트폴리오 API (보유 종목, get_portfolio)
"""
from common.logger import get_logger

log = get_logger("data_collector")


def update_daily_candles() -> None:
    """장 전: 유니버스 종목의 일봉을 daily_candles 에 갱신.
    - 처음 한 번은 최근 1년치를 적재(get_candles 이어받기)
    - 이후 매일 전날 캔들만 추가
    TODO(데이터·인프라)"""
    log.info("update_daily_candles: TODO")


def collect_news() -> None:
    """후보·보유 종목의 최근 헤드라인을 news 에 저장.
    - 게시 시각(published_at) 반드시 저장, 같은 기사는 news_id 로 중복 제거
    - 종목명↔코드 매핑 규칙 적용('현대'·'삼성' 단독 매칭 금지 등)
    TODO(데이터·인프라, 뉴스 API는 NLP·LLM 담당이 선정)"""
    log.info("collect_news: TODO")
