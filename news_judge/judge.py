"""4-3 뉴스 분석·판단 에이전트 (LLM) — 담당: NLP·LLM

읽는 것: candidates, news 테이블, 포트폴리오(보유 종목)
쓰는 테이블: decisions (종목마다 한 행, status='NEW')

Gemini 출력 형식(JSON) — 형식이 깨지거나 필드가 빠지면 해당 종목은 WAIT 로 저장
  {"symbol": "005930", "action": "BUY | WAIT | SELL", "bad_news": false,
   "reason": "...", "risks": ["..."], "news_ids": ["..."]}
"""
"""4-3 뉴스 분석·판단 에이전트 (LLM) — 담당: NLP·LLM

읽는 것: candidates, news 테이블, 포트폴리오(보유 종목)
쓰는 테이블: decisions (종목마다 한 행, status='NEW')

Gemini 출력 형식(JSON) — 형식이 깨지거나 필드가 빠지면 해당 종목은 WAIT 로 저장
  {"symbol": "005930", "action": "BUY | WAIT | SELL", "bad_news": false,
   "reason": "...", "risks": ["..."], "news_ids": ["..."]}
"""
import json
import sqlite3
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Set

from common.logger import get_logger
from common.config import get_config, env
from common.db import connect, STATUS_NEW

log = get_logger("news_judge")

# KST 타임존 (+09:00)
KST = timezone(timedelta(hours=9))

SYSTEM_INSTRUCTION = """
당신은 한국 주식 퀀트 트레이딩 에이전트의 뉴스 분석 및 매매 판단 엔진입니다.
제공된 종목 정보와 최근 뉴스 헤드라인만을 근거로 객관적인 판단을 내려야 합니다.

[핵심 매매 원칙 및 피드백 규칙]
1. 미래 정보 차단: 기준 시각 이전의 뉴스만 고려하며, 제공되지 않은 외부 정보로 지레짐작하지 마십시오.
2. 미보유 종목 (후보군):
   - 대형 수주, 실적 호조, 신사업 확장 등 명확한 호재가 있으면 BUY를 검토하십시오.
   - 횡령, 배임, 소송, 일반공모 유상증자, 분식회계 등 악재 발생 시 action은 WAIT, bad_news는 true로 설정하여 신규 매수를 금지하십시오.
   - 뉴스가 없거나 모호한 찌라시인 경우 WAIT로 판단하십시오.
3. 보유 중인 종목:
   - [필수 규칙] 보유 중인 종목에 확인된 악재(소송, 유상증자, 횡령, 실적 쇼크 등)가 발생한 경우, 반드시 action을 SELL로 내리고 bad_news를 true로 설정하십시오.
   - 특별한 악재가 없거나 상승 추세가 유지된다면 WAIT(홀딩 유지)로 판단하십시오.
4. 출력 형식: 반드시 마크다운 기호 없이 순수 JSON 리스트 포맷으로만 출력하십시오.
"""


def get_current_kst_iso() -> str:
    """AQuA API 및 DB 표준인 ISO-8601 KST 시각 문자열 반환 (예: 2026-10-20T09:30:00+09:00)"""
    return datetime.now(KST).isoformat(timespec="seconds")


def call_gemini_api(prompt: str, model_name: str, api_key: str) -> Dict[str, Any]:
    """Gemini REST API 호출 및 응답(텍스트 + 토큰 메타데이터) 반환"""
    clean_model = model_name.replace("models/", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}: {response.text}")

    res_json = response.json()
    text_content = res_json["candidates"][0]["content"]["parts"][0]["text"]
    usage_metadata = res_json.get("usageMetadata", {})

    return {
        "text": text_content,
        "prompt_tokens": usage_metadata.get("promptTokenCount", 0),
        "candidates_tokens": usage_metadata.get("candidatesTokenCount", 0),
        "total_tokens": usage_metadata.get("totalTokenCount", 0)
    }


def validate_decision_item(item: Any, symbol: str) -> Dict[str, Any]:
    """Gemini 개별 출력 항목의 형식 및 필수 필드 검증 (깨지거나 빠지면 WAIT 처리)"""
    required_fields = ["symbol", "action", "bad_news", "reason", "risks", "news_ids"]
    
    if not isinstance(item, dict):
        return {
            "symbol": symbol,
            "action": "WAIT",
            "bad_news": False,
            "reason": "JSON 응답 타입 오류로 인한 안전 관망",
            "risks": ["형식 오류"],
            "news_ids": []
        }

    # 필수 필드 누락 검사
    for field in required_fields:
        if field not in item:
            return {
                "symbol": symbol,
                "action": "WAIT",
                "bad_news": False,
                "reason": f"필수 필드({field}) 누락으로 인한 안전 관망",
                "risks": ["필드 누락"],
                "news_ids": []
            }

    # action 값 유효성 검사
    action = str(item.get("action", "")).upper().strip()
    if action not in ["BUY", "WAIT", "SELL"]:
        action = "WAIT"

    return {
        "symbol": symbol,
        "action": action,
        "bad_news": bool(item.get("bad_news", False)),
        "reason": str(item.get("reason", "")),
        "risks": item.get("risks", []) if isinstance(item.get("risks"), list) else [],
        "news_ids": item.get("news_ids", []) if isinstance(item.get("news_ids"), list) else []
    }


def run() -> None:
    """후보·보유 종목을 한 번의 Gemini 호출로 판단해 decisions 에 저장.
    - 판단 시각 이후 게시된 기사는 입력에서 제외(미래 정보 방지)
    - 호출마다 입력·출력 토큰 수를 로그로 남기기(비용 추적)
    """
    log.info("news_judge.run: 실행 시작")

    config = get_config()
    llm_cfg = config.get("llm", {})
    model_name = llm_cfg.get("model", "gemini-3.8-flash")
    max_headlines = llm_cfg.get("max_headlines_per_stock", 5)
    api_key = env("GEMINI_API_KEY")

    if not api_key:
        log.error("news_judge.run: GEMINI_API_KEY가 설정되지 않아 판단을 중단합니다.")
        return

    # 피드백 2: AQuA API 표준 ISO-8601 KST 시각 사용
    run_at = get_current_kst_iso()
    run_date = run_at[:10]  # YYYY-MM-DD

    with connect() as conn:
        cursor = conn.cursor()

        # 1. 포트폴리오(보유 종목) 조회 (피드백 1: positions_meta 활용)
        cursor.execute("SELECT symbol FROM positions_meta")
        held_symbols: Set[str] = set(row[0] for row in cursor.fetchall() if row[0])
        log.info(f"news_judge.run: 현재 보유 종목 {len(held_symbols)}개 감지 -> {held_symbols}")

        # 2. 당일 후보 종목 조회 (candidates)
        cursor.execute("""
            SELECT symbol, name, momentum_rank, ret_5d, above_ma20 
            FROM candidates 
            WHERE run_date = ?
        """, (run_date,))
        candidates = cursor.fetchall()

        if not candidates and not held_symbols:
            log.info("news_judge.run: 분석 대상(후보 및 보유 종목)이 없습니다.")
            return

        # 분석 대상 종목 목록 통합
        target_dict: Dict[str, Dict[str, Any]] = {}
        for row in candidates:
            target_dict[row[0]] = {
                "name": row[1],
                "rank": row[2],
                "ret_5d": row[3],  # DB에는 비율(0.045)로 저장됨
                "above_ma20": bool(row[4]),
                "is_held": (row[0] in held_symbols)
            }

        # 보유 중인데 후보에 없는 종목도 악재 매도 검사를 위해 추가
        for sym in held_symbols:
            if sym not in target_dict:
                target_dict[sym] = {
                    "name": sym,
                    "rank": None,
                    "ret_5d": None,
                    "above_ma20": None,
                    "is_held": True
                }

        # 3. 뉴스 조회 (피드백 2: ISO-8601 KST 문자열 직접 비교로 미래 기사 차단)
        stocks_payload = []
        for symbol, info in target_dict.items():
            cursor.execute("""
                SELECT news_id, title, published_at
                FROM news
                WHERE symbol = ? AND published_at <= ?
                ORDER BY published_at DESC
                LIMIT ?
            """, (symbol, run_at, max_headlines))
            news_rows = cursor.fetchall()

            stocks_payload.append({
                "symbol": symbol,
                "name": info["name"],
                "is_held": info["is_held"],
                "ret_5d": info["ret_5d"],
                "above_ma20": info["above_ma20"],
                "news": [{"news_id": r[0], "title": r[1], "published_at": r[2]} for r in news_rows]
            })

    # 4. 프롬프트 조립
    prompt = f"기준 시각: {run_at}\n\n아래 대상 종목들의 상태와 최근 뉴스를 분석하여 매수·관망·매도를 판단하십시오:\n\n"
    for s in stocks_payload:
        held_label = "【현재 보유 중】" if s["is_held"] else "【미보유 후보】"
        # 피드백 3: 비율(0.045)을 퍼센트(+4.50%)로 변환하여 전달
        ret_str = f"{s['ret_5d'] * 100:+.2f}%" if s["ret_5d"] is not None else "수익률 정보 없음"
        trend_str = "20일선 위" if s["above_ma20"] else "20일선 아래"

        prompt += f"■ {held_label} {s['name']} ({s['symbol']})\n"
        prompt += f"  - 최근 5일 수익률: {ret_str}, 추세: {trend_str}\n"
        prompt += "  - 최근 뉴스 헤드라인:\n"
        if s["news"]:
            for n in s["news"]:
                prompt += f"    * [{n['news_id']}] {n['title']} ({n['published_at']})\n"
        else:
            prompt += "    * (기준 시각 이전 관련 뉴스 없음)\n"
        prompt += "\n"

    prompt += """
[출력 요구사항]
마크다운 없이 오직 아래 구조의 JSON 리스트만 반환하십시오:
[
  {
    "symbol": "종목코드",
    "action": "BUY | WAIT | SELL",
    "bad_news": true 또는 false,
    "reason": "판단 근거 요약",
    "risks": ["식별된 위험 요인"],
    "news_ids": ["판단에 직접 참고한 news_id 목록"]
  }
]
"""

    # 5. Gemini 1회 배치 호출 및 토큰 사용량 로깅
    parsed_results_by_symbol = {}
    try:
        call_res = call_gemini_api(prompt, model_name, api_key)
        # 독스트링 요구사항: 토큰 수 로그 남기기 (비용 추적)
        log.info(
            f"news_judge.run: 토큰 사용량 - 입력: {call_res['prompt_tokens']} 토큰, "
            f"출력: {call_res['candidates_tokens']} 토큰, 합계: {call_res['total_tokens']} 토큰"
        )
        raw_list = json.loads(call_res["text"])
        if isinstance(raw_list, list):
            for item in raw_list:
                if isinstance(item, dict) and "symbol" in item:
                    parsed_results_by_symbol[item["symbol"]] = item

    except Exception as e:
        log.error(f"news_judge.run: Gemini 호출 또는 응답 파싱 실패 ({e}). 전체 Fallback 적용")

    # 6. 종목별 유효성 검증 및 누락/파싱 에러 시 WAIT 처리
    final_decisions = []
    for s in stocks_payload:
        sym = s["symbol"]
        item = parsed_results_by_symbol.get(sym)
        validated = validate_decision_item(item, sym)
        final_decisions.append(validated)

    # 7. decisions 테이블에 저장 (status = STATUS_NEW)
    with connect() as conn:
        cursor = conn.cursor()
        for d in final_decisions:
            cursor.execute("""
                INSERT INTO decisions (run_at, symbol, action, bad_news, reason, risks, news_ids, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_at,
                d["symbol"],
                d["action"],
                1 if d["bad_news"] else 0,
                d["reason"],
                json.dumps(d["risks"], ensure_ascii=False),
                json.dumps(d["news_ids"], ensure_ascii=False),
                STATUS_NEW
            ))

    log.info(f"news_judge.run: decisions 테이블에 {len(final_decisions)}건 저장 완료 (status='{STATUS_NEW}')")
