"""2026-09-05 사용자 요청("v3, 콜라, 환타, zz 등에서 분석 적용한 내용중 저울에 적용 도움되는
내용 모두 검토" -> "순서대로 시작해") -- 콜라 프로젝트에 이미 있는 DART 연동(재무경고+최근공시)
을 저울에 포팅한다. 원풍물산 사례("패턴은 맞는데 회사 자체가 위험한" 개별 종목)처럼, 순수
가격·거래량 통계로는 절대 못 잡는 리스크를 DART 재무제표로 선행 경고한다.

콜라(filter_capital_impairment.py, render_dashboard.py)는 V3 저장소의 infra/dart_client.py를
로컬 절대경로로 import하는데, 그건 이 PC에서만 되고 GHA/클라우드에서는 안 통한다 -- 이 파일은
그 DART API 호출 로직을 자체 포함(self-contained)해서 어디서든(로컬/GHA) 동일하게 동작하게
만들었다.

콜라와 다른 점: 콜라는 하루 후보가 10~20종목대라 매일 전부 조회해도 API 부담이 적은데, 저울은
2,700종목 규모라 **오늘 상위 20개(강한이김 후보)에만** 한정 적용한다.

네트워크(DART API) 호출이 필요해서 클라우드 라우틴 샌드박스에서는 실패한다(FinanceDataReader와
동일한 이유) -- render_scale_report.py는 이 스크립트가 만든 dart_risk_cache.json을 읽기만
하고, 실제 조회/갱신은 별도 GHA(refresh_dart_risk.yml, DART_API_KEY를 저장소 시크릿으로 등록
필요)가 매일 담당한다.
"""
import io
import json
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests

DART_BASE_URL = "https://opendart.fss.or.kr/api"
CORP_CODE_CACHE_FILE = "dart_corp_codes.json"
CORP_CODE_CACHE_DAYS = 30
MANUAL_CORP_CODE_OVERRIDES = {"005935": "005930"}  # 삼성전자우 -> 삼성전자(V3 infra/dart_client.py와 동일)

CACHE_PATH = "dart_risk_cache.json"
CACHE_DAYS = 60  # 재무제표는 분기 단위로만 바뀌므로 매일 재조회할 필요 없음(콜라와 동일 관례)
YEARS_TO_TRY = ["2025", "2024", "2023"]
DISCLOSURE_LOOKBACK_DAYS = 60
DISCLOSURE_ROUTINE_KEYWORDS = ("사업보고서", "반기보고서", "분기보고서", "증권신고서(지분증권)")

KEY_ACCOUNT_IDS = {
    "매출액": "ifrs-full_Revenue", "영업이익": "dart_OperatingIncomeLoss",
    "당기순이익": "ifrs-full_ProfitLoss", "자산총계": "ifrs-full_Assets",
    "부채총계": "ifrs-full_Liabilities", "자본총계": "ifrs-full_Equity",
}
INCOME_STATEMENT_NAMES = ("손익계산서", "포괄손익계산서")


def _api_key():
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise RuntimeError("DART_API_KEY 환경변수가 없습니다 -- opendart.fss.or.kr에서 발급 후 등록하세요.")
    return key


def get_corp_code_map(force_refresh=False):
    if not force_refresh and os.path.exists(CORP_CODE_CACHE_FILE):
        age_days = (time.time() - os.path.getmtime(CORP_CODE_CACHE_FILE)) / 86400
        if age_days < CORP_CODE_CACHE_DAYS:
            with open(CORP_CODE_CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
    resp = requests.get(f"{DART_BASE_URL}/corpCode.xml", params={"crtfc_key": _api_key()}, timeout=20)
    resp.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    root = ET.fromstring(z.read("CORPCODE.xml"))
    mapping = {}
    for item in root.findall("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        if stock_code:
            mapping[stock_code] = {"corp_code": item.findtext("corp_code"), "corp_name": item.findtext("corp_name")}
    with open(CORP_CODE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    return mapping


def get_corp_code(stock_code, corp_map):
    code = MANUAL_CORP_CODE_OVERRIDES.get(stock_code, stock_code)
    entry = corp_map.get(code)
    return entry["corp_code"] if entry else None


def get_financial_statements(corp_code, year, report_code="11011", fs_div="CFS"):
    resp = requests.get(f"{DART_BASE_URL}/fnlttSinglAcntAll.json", params={
        "crtfc_key": _api_key(), "corp_code": corp_code, "bsns_year": year,
        "reprt_code": report_code, "fs_div": fs_div,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "000":
        return []
    return data.get("list", [])


def extract_key_items(statements):
    id_to_label = {v: k for k, v in KEY_ACCOUNT_IDS.items()}

    def to_num(v):
        try:
            return int(str(v).replace(",", "")) if v not in (None, "") else None
        except (ValueError, AttributeError):
            return None

    def prior_amount(item):
        v = item.get("frmtrm_amount")
        return v if v not in (None, "") else item.get("frmtrm_q_amount")

    out = {}
    for item in statements:
        label = id_to_label.get(item.get("account_id"))
        if label and label not in out and item.get("sj_nm") in INCOME_STATEMENT_NAMES:
            out[label] = {"당기": to_num(item.get("thstrm_amount")), "전기": to_num(prior_amount(item)),
                          "전전기": to_num(item.get("bfefrmtrm_amount"))}
    for item in statements:
        label = id_to_label.get(item.get("account_id"))
        if label and label not in out and item.get("sj_nm") == "재무상태표":
            out[label] = {"당기": to_num(item.get("thstrm_amount")), "전기": to_num(prior_amount(item)),
                          "전전기": to_num(item.get("bfefrmtrm_amount"))}
    return out


def check_capital(code, corp_map):
    """완전자본잠식(자본총계<=0) 또는 3년 연속 당기순손실 여부 -- V3 콜라 프로젝트의
    filter_capital_impairment.check_capital()과 동일 로직."""
    corp_code = get_corp_code(code, corp_map)
    if not corp_code:
        return {"warning": None, "reason": "DART 미등록(비상장/코드 불일치 등)"}
    for year in YEARS_TO_TRY:
        stmts = get_financial_statements(corp_code, year, "11011", "CFS")
        if not stmts:
            stmts = get_financial_statements(corp_code, year, "11011", "OFS")
        if not stmts:
            continue
        items = extract_key_items(stmts)
        equity_item = items.get("자본총계", {})
        ni_item = items.get("당기순이익", {})
        equity = equity_item.get("당기")
        net_income = ni_item.get("당기")
        if equity is None and net_income is None:
            continue
        capital_impaired = equity is not None and equity <= 0
        ni_values = [ni_item.get("당기"), ni_item.get("전기"), ni_item.get("전전기")]
        three_year_loss = all(v is not None and v < 0 for v in ni_values)
        warning = capital_impaired or three_year_loss
        if warning:
            reason = "완전자본잠식" if capital_impaired else "3년 연속 당기순손실"
            if capital_impaired and three_year_loss:
                reason = "완전자본잠식 + 3년 연속 당기순손실"
        else:
            reason = None
        return {"warning": warning, "equity": equity, "net_income_3y": ni_values,
                "fiscal_year": year, "reason": reason}
    return {"warning": None, "reason": "재무제표 조회 실패(연도 3개 다 없음)"}


def fetch_recent_disclosures(code, corp_map):
    """render_dashboard.py의 _fetch_recent_disclosures와 동일 로직(정기공시 제외)."""
    try:
        corp_code = get_corp_code(code, corp_map)
        if not corp_code:
            return []
        end = datetime.now().strftime("%Y%m%d")
        begin = (datetime.now() - timedelta(days=DISCLOSURE_LOOKBACK_DAYS)).strftime("%Y%m%d")
        resp = requests.get(f"{DART_BASE_URL}/list.json", params={
            "crtfc_key": _api_key(), "corp_code": corp_code, "bgn_de": begin, "end_de": end,
            "page_count": "20",
        }, timeout=8)
        data = resp.json()
        if data.get("status") != "000":
            return []
        return [
            {"date": item.get("rcept_dt"), "title": item.get("report_nm")}
            for item in data.get("list", [])
            if not any(kw in (item.get("report_nm") or "") for kw in DISCLOSURE_ROUTINE_KEYWORDS)
        ][:5]
    except Exception:
        return []


def _load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def update_for_codes(codes):
    """오늘 상위 20개 종목코드 리스트를 받아 DART 재무경고+최근공시를 조회/캐시 갱신.
    개별 종목 실패는 조용히 건너뛰고 다음 종목으로(콜라와 동일 관례 -- 이 정보가 없다고
    리포트 전체가 죽으면 안 됨)."""
    cache = _load_cache()
    corp_map = get_corp_code_map()
    now = time.time()
    checked = 0
    for code in codes:
        if not code or code == "-":
            continue
        entry = cache.get(code)
        if entry and (now - entry.get("checked_at", 0)) / 86400 < CACHE_DAYS:
            continue
        try:
            capital = check_capital(code, corp_map)
            time.sleep(0.12)
            disclosures = fetch_recent_disclosures(code, corp_map)
            time.sleep(0.12)
            cache[code] = {"checked_at": now, "capital": capital, "disclosures": disclosures}
            checked += 1
        except Exception as e:
            cache[code] = {"checked_at": now, "capital": {"warning": None, "reason": f"조회 오류: {e}"},
                            "disclosures": []}
    _save_cache(cache)
    return checked


def _load_local_dart_key_fallback():
    """로컬 테스트 편의용 -- GHA에서는 DART_API_KEY가 이미 시크릿으로 주입돼 있어 이 함수는
    아무 일도 안 함(os.environ에 이미 있으면 건너뜀). 이 PC에서만 V3의 .env를 찾아본다."""
    if os.environ.get("DART_API_KEY"):
        return
    v3_env = r"C:\Users\82102\Desktop\주식자동매매_V3\.env"
    if os.path.exists(v3_env):
        with open(v3_env, encoding="utf-8") as f:
            for line in f:
                if line.startswith("DART_API_KEY="):
                    os.environ["DART_API_KEY"] = line.split("=", 1)[1].strip()
                    break


if __name__ == "__main__":
    _load_local_dart_key_fallback()
    import render_scale_report as rsr
    cache_data, name_to_code = rsr._load_cache_and_names()
    top_rows, _ = rsr.build_report(cache_data, name_to_code, min_depth=rsr.MIN_DEPTH)
    codes = [r["code"] for r in top_rows]
    checked = update_for_codes(codes)
    print(f"오늘 상위 {len(codes)}종목 중 신규 조회: {checked}건")
