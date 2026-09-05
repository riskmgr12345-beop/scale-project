"""'저울' 프로젝트 -- 코스피+코스닥 전체(_상한가전조연구의 캐시를 읽기전용 재사용) 종목별
"오를 이유(부스터) vs 내릴 이유(위험신호)"를 저울질해서 상위 15개를 뽑아 보고한다.

2026-09-04 사용자 요청 -- "에이전트를 통해 보고받고 판단 결과로 추천, 상위 점수 15개 종목으로"
-- 실시간(장중 KIS) 조회는 2,700종목 규모에서 불가능해서, 콜라 프로젝트처럼 캐시 기반 일일
스냅샷으로 동작한다(scale_validation_test.py로 이미 검증한 것과 같은 공식).

이번 버전에서 포함한 것: 방향(상승/하락다리), 진행기간, 등락%, 저울점수(부스터-위험신호),
52주 가격위치(캐시 히스토리로 직접 계산 가능).
이번 버전에서 제외한 것(추가 데이터소스 필요, 후속 작업): 수급(투자자별매매동향)·PER·관리종목
여부 -- 이 캐시엔 OHLCV만 있고 재무/투자자 데이터가 없어서, V3의 "객관가치" 배지처럼 전부
넣으려면 별도 데이터소스(DART, KRX 관리종목 리스트 등)를 새로 연결해야 한다.
"""
import json
import os
import pickle
import statistics
from datetime import datetime

import verification_tracker

# 2026-09-04 사용자 요청("①번 시장국면 배지부터 화면에 반영") -- regime_filter_test.py로
# 검증된 실측치(강한이김>=2 기준, 시기분할 재현 확인). build_kospi_regime_cache.py(GHA로
# 매일 17:00 KST 갱신)가 만든 캐시에서 오늘 국면만 읽어와 이 표로 매칭한다.
REGIME_STATS = {
    "up": {"label": "상승장(코스피 60일선 위)", "reach": 69.7, "d5": -0.26,
           "verdict": "더 약한 국면"},
    "down": {"label": "하락장(코스피 60일선 아래)", "reach": 79.3, "d5": 2.06,
             "verdict": "더 좋은 국면"},
}
REGIME_CACHE_PATH = "research_cache/kospi_regime_cache.json"


def _load_kospi_regime():
    """캐시가 없거나(GHA 첫 실행 전) 오래됐어도 리포트 생성 자체는 죽지 않게 None을 돌려준다
    -- render_html이 "국면 데이터 없음"으로 정직하게 표시."""
    try:
        with open(REGIME_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


DART_RISK_CACHE_PATH = "dart_risk_cache.json"


def _load_dart_risk_cache():
    """2026-09-05 사용자 요청("콜라에 적용한 DART 재무경고를 저울에도") -- dart_risk_check.py
    (별도 GHA refresh_dart_risk.yml이 매일 갱신, 네트워크 필요해 클라우드 라우틴 샌드박스에선
    실행 안 함)가 만든 캐시를 읽기전용으로 읽는다. 캐시가 없거나(첫 실행 전) 특정 종목이
    아직 조회 안 됐으면 조용히 빈 배지 -- 리포트 생성 자체를 막지 않는다."""
    try:
        with open(DART_RISK_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _dart_badge_html(code, dart_cache):
    entry = dart_cache.get(code)
    if not entry:
        return ""
    cap = entry.get("capital", {})
    if cap.get("warning") is not True:
        return ""
    reason = cap.get("reason") or "재무 경고"
    return (f'<span class="dart-badge" title="DART 재무제표 기반 선행경고, '
            f'공식 관리종목 지정과는 별개">💸 {reason}</span>')


def _market_status_badge_html(code, dart_cache):
    """2026-09-05 사용자 요청("순서대로 시작해") ③PER/관리종목 여부 -- ZZ의 "객관가치" 배지
    중 관리종목/PER 부분을 저울에 포팅. ZZ는 KIS 실시간 API(인증 세션 필요)로 얻지만 저울은
    GHA 배치라 그 세션이 없어서, dart_risk_check.py가 FinanceDataReader.StockListing의 Dept
    컬럼(관리종목/투자주의환기 여부는 KRX 공식 지정이라 DART 재무제표 기반 경고와는 성격이
    다름 -- 둘 다 있으면 별개로 나란히 표시)과 PER(시총/DART당기순이익)을 대신 계산해둔다."""
    entry = dart_cache.get(code)
    if not entry:
        return ""
    ms = entry.get("market_status") or {}
    parts = []
    if ms.get("management_issue"):
        parts.append('<span style="color:#a01818;font-weight:700;">⚠관리종목</span>')
    elif ms.get("caution_issue"):
        parts.append('<span style="color:#a05818;font-weight:700;">⚠투자주의환기</span>')
    per = ms.get("per")
    if per is not None:
        parts.append(f'PER {per:.1f}배' if per > 0 else 'PER 적자')
    if not parts:
        return ""
    return f'<span class="market-status-badge">{" · ".join(parts)}</span>'


def _high_vol_badge_html(high_vol):
    """2026-09-05 -- ⑥ZZ '고변동' 부스터 배지. DART/관리종목 배지와 달리 리스크가 아니라
    "이 터치가 통계적으로 더 믿을만한 신호"라는 긍정적 정보라 색을 초록 계열로 구분."""
    if not high_vol:
        return ""
    return '<span class="high-vol-badge" title="이 종목 평소 변동폭 대비 오늘 되돌림이 더 큼(재검증 재현됨)">⚡고변동</span>'


def _disclosure_html(code, dart_cache):
    """2026-09-05 사용자 요청("이어해" -- ②최근 60일 공시 화면표시) -- 콜라
    render_dashboard.py의 _disclosure_html과 같은 톤. 확대차트 라이트박스 안에 같이 넣어서
    (새 인터랙션 없이 기존 "클릭하면 확대" 동선 재사용) 종목별 최근 이벤트성 공시를 보여준다."""
    entry = dart_cache.get(code)
    if not entry:
        return ""
    disclosures = entry.get("disclosures") or []
    if not disclosures:
        return ""
    rows = "".join(
        f'<div>{d["date"][:4]}-{d["date"][4:6]}-{d["date"][6:]} · {d["title"]}</div>'
        for d in disclosures[:5]
    )
    return (f'<div class="disclosure-box">'
            f'<b>📋 최근 60일 주요공시(DART)</b>{rows}</div>')

THRESHOLD = 0.03
# 2026-09-05 사용자 요청("저울1 자체를 2%->10%로 바꾸고 탭 하나로 통일해줘") -- 원래 2.0%는
# "후보군을 넓게 본 뒤 점수로 거른다"는 취지로 최초 커밋(52d48c7)부터 있던 값인데, 검증(원래
# scale_validation_test.py의 7%)과 헷갈린다는 사용자 지적 + 10%가 시기분할 재현성 있게
# 확률/수익 둘 다 개선됨을 실측 확인(72.8%->73.7%, +0.49%->+1.07%)한 뒤 이 값 자체를 10.0으로
# 올리고 별도 탭 구조는 제거했다(2%+/10%+ 두 탭 실험은 이 커밋에서 되돌림).
MIN_DEPTH = 10.0
RISK_RECOVERY_MIN = 3.0
TUG_OF_WAR_RISK_PENALTY = 3
# 2026-09-04 -- 로컬 데스크톱(_상한가전조연구, 한글 폴더명)과 클라우드 라우틴(같이 클론되는
# limitup-precursor-research, 저장소 이름 그대로)이 서로 다른 폴더명을 쓰므로 둘 다 후보로
# 시도한다 -- 콜라 캐시를 읽기전용 재사용하는 원칙은 같지만 실행 환경마다 경로만 다름.
CACHE_PATH_CANDIDATES = [
    "../_상한가전조연구/research_cache/limitup_ohlcv_cache.pkl",
    "../limitup-precursor-research/research_cache/limitup_ohlcv_cache.pkl",
]
UNIVERSE_PATH_CANDIDATES = [
    "../_상한가전조연구/research_cache/universe_full.json",
    "../limitup-precursor-research/research_cache/universe_full.json",
]
TOP_N = 20  # 2026-09-04 사용자 요청("추천 종목 20개로 확장") -- 기존 15개에서 확대
SPARKLINE_DAYS = 40  # 종목별 최근 가격 흐름 미니 그래프용
DETAIL_CHART_DAYS = 25  # 2026-09-04 사용자 요청("그래프를 클릭하면 확대") -- 라벨(날짜/가격/등락%)이
# 겹치지 않도록 미니 스파크라인(40일)보다 짧게 잡는다. V3 ZZ 화면의 확대차트(recent_two_legs_svg)
# 참고 -- 저울은 band/부스터 라벨 체계가 없어(DEPTH_BANDS 미보유) "다리"/"터치+등급"만 표시.


def _resolve_path(candidates, label):
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(
        f"{label}을(를) 찾을 수 없습니다. 시도한 경로: " + ", ".join(candidates)
        + " -- limitup-precursor-research 저장소가 이 저장소와 같은 부모 폴더에 클론돼 있는지 확인하세요."
    )


def _resolve_cache_path():
    return _resolve_path(CACHE_PATH_CANDIDATES, "콜라 캐시(limitup_ohlcv_cache.pkl)")


def _load_name_to_code():
    """2026-09-04 사용자 요청("각종목 코드번호 추가") -- 콜라 유니버스 목록(name/code/market)을
    읽기전용 재사용해서 name->code 매핑을 만든다. 못 찾으면 빈 dict(코드 칸은 "-"로 표시)."""
    import json
    try:
        path = _resolve_path(UNIVERSE_PATH_CANDIDATES, "콜라 유니버스 목록(universe_full.json)")
    except FileNotFoundError:
        return {}
    with open(path, encoding="utf-8") as f:
        universe = json.load(f)
    return {item["name"]: item["code"] for item in universe if item.get("name") and item.get("code")}


def zigzag_swings(closes, threshold=THRESHOLD):
    """2026-09-04 사용자 발견("오늘 3% 다리가 생긴게 3개 맞지?" 확인 과정에서 라메디텍의
    "다리 5일째 -4.0%" 표시가 실제(-9%대)와 다른 걸 발견) -- 진행 중인(아직 반전 미확정) 다리의
    끝점(swings[-1])이, 예전엔 "-3% 문턱을 처음 넘긴 날"에 얼어붙어서 그 이후 더 깊어져도 화면에
    반영이 안 됐다. V3의 진짜 프로덕션 지그재그(reporting/charts.py._zigzag_swings)와 대조해보니
    거기는 루프가 끝난 뒤 마지막에 무조건 한 번 더 (extreme_idx, extreme_price)를 append해서
    "지금까지의 최신 극값"을 항상 최신 상태로 유지하고 있었다 -- 이 저장소로 옮겨 적을 때 그
    마지막 한 줄을 빠뜨린 게 원인. V3(실계좌)는 처음부터 이 버그가 없었음(별도 확인 완료).
    아래 return 직전 append로 V3와 동일하게 맞춘다."""
    if len(closes) < 2:
        return []
    swings = [(0, closes[0])]
    direction = None
    extreme_idx, extreme_price = 0, closes[0]
    for i in range(1, len(closes)):
        c = closes[i]
        if direction is None:
            if c >= extreme_price * (1 + threshold):
                direction = "up"
            elif c <= extreme_price * (1 - threshold):
                direction = "down"
            if direction:
                swings.append((i, c))
                extreme_idx, extreme_price = i, c
            elif c > extreme_price:
                extreme_idx, extreme_price = i, c
            elif c < extreme_price:
                extreme_idx, extreme_price = i, c
        elif direction == "up":
            if c > extreme_price:
                extreme_idx, extreme_price = i, c
            elif c <= extreme_price * (1 - threshold):
                swings[-1] = (extreme_idx, extreme_price)
                direction = "down"
                swings.append((i, c))
                extreme_idx, extreme_price = i, c
        else:
            if c < extreme_price:
                extreme_idx, extreme_price = i, c
            elif c >= extreme_price * (1 + threshold):
                swings[-1] = (extreme_idx, extreme_price)
                direction = "up"
                swings.append((i, c))
                extreme_idx, extreme_price = i, c
    if (extreme_idx, extreme_price) != swings[-1]:
        swings.append((extreme_idx, extreme_price))
    return swings


def _historical_touch_points(closes, lows, min_depth=MIN_DEPTH):
    """2026-09-04 사용자 요청("과거에도 터치가 일어났었지?" -> "해줘") -- 이 창(window) 안에서
    지그재그 하락다리마다 처음으로 depth_pct>=min_depth를 넘긴 지점(=검증 스크립트의
    find_touch_entries와 같은 정의)을 전부 찾아 돌려준다. 마지막 다리(진행 중)는 오늘 자체가
    이미 별도로(원 마커) 표시되므로 여기선 제외."""
    swings = zigzag_swings(closes)
    touches = []
    for i in range(len(swings) - 1):
        idx0, p0 = swings[i]
        idx1, p1 = swings[i + 1]
        if p1 >= p0:
            continue
        for j in range(idx0, idx1 + 1):
            depth = (p0 - lows[j]) / p0 * 100 if p0 else 0.0
            if depth >= min_depth:
                touches.append((j, depth))
                break
    return touches


def current_position(dates_idx, closes):
    """지금(캐시 마지막 날) 기준 진행 중인 다리 정보. swings[-2]->swings[-1]이 진행 중인 구간."""
    swings = zigzag_swings(closes)
    if len(swings) < 2:
        return None
    idx_start, price_start = swings[-2]
    idx_end, price_end = swings[-1]
    leg_dir = "up" if price_end >= price_start else "down"
    leg_pct = (price_end / price_start - 1) * 100
    leg_days = idx_end - idx_start
    return {
        "leg_dir": leg_dir, "leg_pct": leg_pct, "leg_days": leg_days,
        "leg_start_date": dates_idx[idx_start], "leg_start_price": price_start,
        "leg_end_price": price_end,
    }


def touch_depth_now(closes, lows, pos):
    """진행 중인 다리에서 고점(leg_start_price) 대비 오늘 저가의 되돌림 깊이(%p).
    2026-09-04 사용자 발견("1번과 4번, 2번과 3번 터치 격이 다른가?") -- 원래 여기가
    (closes[-1]/lows[-1]-1)*100(오늘 종가가 오늘 저가 대비 얼마나 반등했는지)로 잘못 짜여
    있었다. 이건 scale_validation_test.py/find_touch_entries가 실제 검증한 정의
    ((peak-low)/peak*100, 다리 고점 대비 그날 저가 되돌림)와 다른 공식이라, 상위 20개
    필터링·표시에 쓰인 depth_pct가 검증값과 안 맞는 진짜 버그였다. leg_start_price(=다리
    시작 고점, current_position이 이미 계산해둠)를 기준으로 고쳐서 _historical_touch_points와
    동일한 공식으로 통일한다."""
    if pos["leg_dir"] == "down":
        peak = pos["leg_start_price"]
        return max(0.0, (peak - lows[-1]) / peak * 100) if peak else None
    return None  # 이번 버전은 하락다리(반등기대)만 다룬다 -- 저울 공식이 이 방향으로만 검증됨


def volume_ratio_at(volumes):
    if len(volumes) < 21:
        return None
    base = volumes[-21:-1]
    base_med = statistics.median(base)
    if not base_med:
        return None
    return volumes[-1] / base_med


def recent_fast_reversal_active(closes, fast_days=5):
    swings = zigzag_swings(closes)
    if len(swings) < 4:
        return False
    idx_m4, _ = swings[-4]
    idx_m3, _ = swings[-3]
    return (idx_m3 - idx_m4) <= fast_days


def zz_extra_score(vr, fast_rev):
    if vr is None:
        score = 0
    elif vr < 1.0:
        score = -1
    elif vr < 2.0:
        score = 0
    elif vr < 4.0:
        score = 2
    else:
        score = 1
    if fast_rev:
        score += 2
    return score


MIN_RANGE_SAMPLE_DAYS = 60


def _is_high_volatility_touch(highs, lows, closes, depth_pct):
    """2026-09-05 사용자 요청("순서대로 시작해") ⑥ZZ 나머지 부스터 중 "고변동"(종목별 변동성
    정규화) 포팅. ZZ가 49종목/8년으로 검증한 정의(그 종목의 평소 평균 일일변동폭(고가-저가/종가)
    대비 오늘 터치 깊이가 1.0배 이상)를 저울 2,700종목 모집단(n=13,932)에 순수분리로 재검증
    (`vol_boost_test.py`)한 결과 재현됨 -- 전반부 78.8%/+1.45% vs plain 74.0%/+0.79%, 후반부
    71.7%/+0.63% vs plain 70.1%/+0.06%, 양쪽 다 방향 일치. (더블바텀 부스터는 같은 방식으로
    검증했으나 시기분할에서 방향이 뒤집혀 기각됨 -- README 참고.) 오늘(마지막 행) 자체는
    평균 계산에서 제외해 룩어헤드를 막는다(과거 데이터만으로 "평소" 변동폭을 정의)."""
    vals = [(h - l) / c * 100 for h, l, c in zip(highs[:-1], lows[:-1], closes[:-1]) if c]
    if len(vals) < MIN_RANGE_SAMPLE_DAYS or depth_pct is None:
        return False
    avg_range = sum(vals) / len(vals)
    if not avg_range:
        return False
    return (depth_pct / avg_range) >= 1.0


def year_range_position_pct(closes, highs, lows):
    window = min(len(closes), 252)
    yr_high = max(highs[-window:])
    yr_low = min(lows[-window:])
    cur = closes[-1]
    if yr_high <= yr_low:
        return None
    return max(0.0, min(100.0, (cur - yr_low) / (yr_high - yr_low) * 100))


def _drop_zero_volume_days(closes, highs, lows, volumes, dates):
    """2026-09-04 사용자 발견("터치가 1번과 3번, 3번과는 다른거 같은데?") -- 엑사이엔씨 08-07이
    O=H=L=C=760, 거래량=0인데 전후일(08-06/08-10/08-11)은 전부 3,800원으로 완전히 동일했던 걸
    직접 짚어내서 발견한 데이터 결함. 거래정지 등으로 실제 거래가 없었던 날에 데이터공급사가
    임의/오류값을 채워넣은 것으로 보임(V=0인데 가격만 있는 날). 지그재그/터치 계산 전체가 이런
    날을 진짜 가격으로 오인하면 가짜 대폭락/급등 터치가 생길 수 있어, 거래량<=0인 날은 아예
    시계열에서 제거하고(그 자리 자체가 없었던 것처럼) 나머지로만 계산한다."""
    keep = [i for i, v in enumerate(volumes) if v and v > 0]
    if len(keep) == len(volumes):
        return closes, highs, lows, volumes, dates
    return ([closes[i] for i in keep], [highs[i] for i in keep], [lows[i] for i in keep],
            [volumes[i] for i in keep], [dates[i] for i in keep])


HALT_MIN_TRAILING_ZERO_DAYS = 3


def _trailing_zero_volume_run(volumes):
    """2026-09-04 사용자 발견(대교 -- 실시간 화면에서 거래량0/매도호가0/매수호가0인데도 저울
    상위 20개·강한이김 박스에 들어가 있었음) -- 원인 진단: 캐시를 보니 대교는 08-13(마지막
    정상거래) 이후 08-14~09-02까지 15거래일 연속 O=H=L=C=1,000·거래량0(엑사이엔씨처럼 하루짜리
    가짜값이 아니라 진짜 장기 거래정지로 보임, 다른 정상 종목은 09-03까지 데이터가 있는데
    대교는 09-02에서 끊김). `_drop_zero_volume_days`가 이 정지일들을 지우면 "마지막 남은 날"이
    자동으로 08-13이 되면서 그게 마치 오늘의 살아있는 신호처럼 보고서에 나왔다 -- 거래 자체가
    불가능한 종목을 추천에 넣은 셈. 원본(필터링 전) 시계열 끝에서부터 거래량0이 몇 거래일
    연속됐는지 세서, 그 종목이 "지금도 정지 중"인지 판별하는 데 쓴다."""
    run = 0
    for v in reversed(volumes):
        if v and v > 0:
            break
        run += 1
    return run


def _load_cache_and_names():
    with open(_resolve_cache_path(), "rb") as f:
        cache = pickle.load(f)
    return cache, _load_name_to_code()


def build_report(cache=None, name_to_code=None, min_depth=MIN_DEPTH):
    """2026-09-05 사용자 요청("저울에 15% 이상 버튼을 만드는건 어때?") -- 오늘 검증한
    MIN_DEPTH 스윕(7%->10~12%는 안전한 개선, 15%는 경계선, 20%+는 표본부족/거래정지
    이상치로 기각)을 실제로 눈으로 비교해볼 수 있게, 문턱을 인자로 받도록 리팩터링.
    기본값(2.0%)은 기존 동작 그대로 유지 -- 후보군을 넓게 봐서 저울점수로 거르는 용도라
    검증치(7%)보다 낮게 잡은 원래 설계는 안 바꾼다. cache/name_to_code를 인자로 받게 해서
    "전체(2%+)"와 "깊은눌림(15%+)" 두 화면을 한 번의 캐시 로딩으로 같이 만들 수 있다."""
    if cache is None or name_to_code is None:
        cache, name_to_code = _load_cache_and_names()

    rows = []
    for name, df in cache.items():
        try:
            closes = df["Close"].tolist()
            highs = df["High"].tolist()
            lows = df["Low"].tolist()
            volumes = df["Volume"].tolist()
            dates_idx = list(df.index)
        except Exception:
            continue
        if _trailing_zero_volume_run(volumes) >= HALT_MIN_TRAILING_ZERO_DAYS:
            continue  # 현재 거래정지 추정 -- 실제로 사고팔 수 없는 종목은 아예 후보에서 제외
        closes, highs, lows, volumes, dates_idx = _drop_zero_volume_days(
            closes, highs, lows, volumes, dates_idx)
        if len(closes) < 60:
            continue

        pos = current_position(dates_idx, closes)
        if not pos or pos["leg_dir"] != "down":
            continue
        depth_pct = touch_depth_now(closes, lows, pos)
        if depth_pct is None or depth_pct < min_depth:
            continue

        entry_price = closes[-1]
        day_low = lows[-1]
        same_day_recovery = (entry_price - day_low) / day_low * 100 if day_low else 0.0
        risk_flag = same_day_recovery >= RISK_RECOVERY_MIN

        vr = volume_ratio_at(volumes)
        fast_rev = recent_fast_reversal_active(closes)
        extra = zz_extra_score(vr, fast_rev)
        score = max(-5, min(5, extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)))

        yr_pos = year_range_position_pct(closes, highs, lows)
        high_vol = _is_high_volatility_touch(highs, lows, closes, depth_pct)

        rows.append({
            "name": name, "code": name_to_code.get(name, "-"), "score": score,
            "leg_dir": pos["leg_dir"], "leg_pct": pos["leg_pct"],
            "leg_days": pos["leg_days"], "leg_start_date": pos["leg_start_date"],
            "depth_pct": depth_pct, "cur_price": entry_price,
            "yr_pos": yr_pos, "risk_flag": risk_flag, "vr": vr, "fast_rev": fast_rev,
            "high_vol": high_vol,
            "last_date": dates_idx[-1], "recent_closes": closes[-SPARKLINE_DAYS:],
            "recent_dates": list(dates_idx[-SPARKLINE_DAYS:]), "recent_lows": lows[-SPARKLINE_DAYS:],
        })

    rows.sort(key=lambda r: (-r["score"], -r["depth_pct"]))
    return rows[:TOP_N], len(rows)


def render_text(top_rows, total_candidates):
    lines = [
        f"저울 -- 코스피+코스닥 전체 상위 {len(top_rows)}개 (하락다리·반등기대 후보, "
        f"전체 후보 {total_candidates}종목 중)",
        f"기준일: {top_rows[0]['last_date'].date() if top_rows else '-'}",
        "",
    ]
    for i, r in enumerate(top_rows, 1):
        tier = "강한이김" if r["score"] >= 2 else ("약한이김" if r["score"] == 1 else
                                                  ("비김" if r["score"] == 0 else "짐"))
        yr_pos_part = f" | 52주위치 {r['yr_pos']:.0f}%" if r["yr_pos"] is not None else ""
        lines.append(
            f"{i}. {r['name']}({r['code']}) | 저울점수 {r['score']:+d}({tier}) | "
            f"하락다리 {r['leg_days']}일째, {r['leg_pct']:+.1f}% | 되돌림깊이 {r['depth_pct']:.1f}%p"
            f"{yr_pos_part}"
        )
    return "\n".join(lines)


TIER_COLOR = {"강한이김": ("#0a8a3c", "#e8f5eb"), "약한이김": ("#7b6b1a", "#fbf3d8"),
              "비김": ("#898781", "#f0efe9"), "짐": ("#c0392b", "#fbe9e7")}


def _tier_of(score):
    if score >= 2:
        return "강한이김"
    if score == 1:
        return "약한이김"
    if score == 0:
        return "비김"
    return "짐"


def _seesaw_svg(score):
    """2026-09-04 사용자 요청("사이트에 맞는 그래프... 저울 시소") -- 점수(-5~+5)를 실제
    시소(받침점+빔) 기울기로 시각화. 오른쪽(양수, 오를 이유)이 무거우면 오른쪽이 내려가고,
    왼쪽(음수, 내릴 이유)이 무거우면 왼쪽이 내려간다 -- 기존 줄다리기 가로게이지(ZZ)와 달리
    "저울" 이름과 직접 맞는 형태로 새로 디자인."""
    clamped = max(-5, min(5, score))
    angle = -clamped * 3.2  # 점수 5당 16도 정도 기울임(양수=오른쪽이 아래로 -> 화면상 시계반대)
    color = "#0a8a3c" if clamped > 0 else ("#c0392b" if clamped < 0 else "#898781")
    left_r = 3 + max(0, -clamped) * 0.9
    right_r = 3 + max(0, clamped) * 0.9
    return f'''<svg width="64" height="40" viewBox="0 0 64 40">
      <polygon points="32,24 27,36 37,36" fill="#c9c6ba"/>
      <g transform="rotate({angle:.1f} 32 22)">
        <line x1="6" y1="22" x2="58" y2="22" stroke="{color}" stroke-width="2.5" stroke-linecap="round"/>
        <circle cx="6" cy="22" r="{left_r:.1f}" fill="{'#c0392b' if clamped < 0 else '#c9c6ba'}"/>
        <circle cx="58" cy="22" r="{right_r:.1f}" fill="{'#0a8a3c' if clamped > 0 else '#c9c6ba'}"/>
      </g>
    </svg>'''


def _sparkline_svg(closes):
    """최근 가격 흐름(최대 SPARKLINE_DAYS일) 미니 선그래프. 별도 라이브러리 없이 순수 SVG
    polyline -- V3/콜라 다른 화면들도 자체완결형 SVG 차트를 쓰는 것과 같은 톤."""
    if len(closes) < 2:
        return ""
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1.0
    w, h, pad = 120, 32, 3
    step = (w - 2 * pad) / (len(closes) - 1)
    points = []
    for i, c in enumerate(closes):
        x = pad + i * step
        y = pad + (1 - (c - lo) / span) * (h - 2 * pad)
        points.append(f"{x:.1f},{y:.1f}")
    color = "#0a8a3c" if closes[-1] >= closes[0] else "#c0392b"
    last_x, last_y = points[-1].split(",")
    return f'''<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">
      <polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="1.5"/>
      <circle cx="{last_x}" cy="{last_y}" r="2" fill="{color}"/>
    </svg>'''


def _detail_chart_svg(r):
    """2026-09-04 사용자 요청("그래프를 클릭하면 [V3 ZZ 확대차트 스크린샷]처럼 나왔으면") --
    미니 스파크라인을 클릭하면 열리는 확대 차트. V3 ZZ의 recent_two_legs_svg와 같은 정신(날짜+
    가격+등락% 라벨을 점마다 교대로 위/아래 배치)이되, 저울은 DEPTH_BANDS(등급별 문구) 체계가
    없어서 그 대신 "다리 시작일"과 "터치(오늘, 저울점수/등급/깊이)"만 표시한다."""
    dates = r["recent_dates"][-DETAIL_CHART_DAYS:]
    closes = r["recent_closes"][-DETAIL_CHART_DAYS:]
    lows = r.get("recent_lows", closes)[-DETAIL_CHART_DAYS:]
    if len(closes) < 2:
        return "<div>데이터 부족</div>"

    # 2026-09-04 사용자 요청("과거에도 터치가 일어났었지?" -> "해줘") -- 이 창 안에서 오늘(마지막
    # 점) 이전에 있었던 다른 터치들도 같이 표시. 마지막 점은 이미 별도 원형 마커로 표시되므로 제외.
    past_touch_idxs = {idx for idx, _ in _historical_touch_points(closes, lows) if idx < len(closes) - 1}

    w, h = 1000, 420
    pad_x, top_pad, bottom_pad = 40, 110, 95
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1.0
    plot_h = h - top_pad - bottom_pad
    step = (w - 2 * pad_x) / (len(closes) - 1)

    xs, ys = [], []
    for i, c in enumerate(closes):
        xs.append(pad_x + i * step)
        ys.append(top_pad + (1 - (c - lo) / span) * plot_h)

    leg_start_date = r.get("leg_start_date")
    leg_start_idx = None
    for i, d in enumerate(dates):
        if leg_start_date is not None and d == leg_start_date:
            leg_start_idx = i
            break

    tier = _tier_of(r["score"])
    tier_color, _ = TIER_COLOR[tier]

    segs = []
    for i in range(1, len(closes)):
        seg_color = "#0a8a3c" if closes[i] >= closes[i - 1] else "#c0392b"
        segs.append(f'<line x1="{xs[i-1]:.1f}" y1="{ys[i-1]:.1f}" x2="{xs[i]:.1f}" y2="{ys[i]:.1f}" '
                     f'stroke="{seg_color}" stroke-width="2"/>')

    labels = []
    dots = []
    for i, (x, y, c, d) in enumerate(zip(xs, ys, closes, dates)):
        is_last = i == len(closes) - 1
        pct = ((c / closes[i - 1] - 1) * 100) if i > 0 else 0.0
        pct_color = "#0a8a3c" if pct >= 0 else "#c0392b"
        above = (i % 2 == 0)
        # 2026-09-04 사용자 지적("겹쳐 보임") -- 이전엔 above일 때도 아래쪽으로 줄이 파고들어
        # 점을 가로질러 겹쳤다. 이제 위/아래 어느 쪽이든 3줄 블록이 점 반대편으로만 쌓이게 한다.
        if above:
            y1, y2, y3 = y - 36, y - 23, y - 10
        else:
            y1, y2, y3 = y + 16, y + 29, y + 42
        date_label = f"{d.month:02d}-{d.day:02d}"
        if is_last:
            # 2026-09-04 사용자 지적("겹쳐 보임") -- 여기 종목명 옆에 텍스트로 넣으면 근처 점들
            # 라벨과(특히 다리가 짧아 마지막 며칠이 붙어있을 때) 겹치기 쉬워서, 점 마커만 강조
            # 표시하고 등급 문구는 라이트박스 제목(팝업 상단, _chart_lightbox_html 호출부)으로
            # 옮겼다 -- 겹칠 공간 자체가 없는 자리라 안전하다.
            dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="none" stroke="{tier_color}" '
                         f'stroke-width="2.5"/>')
        elif i in past_touch_idxs:
            dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="none" stroke="#c9860a" '
                         f'stroke-width="2"/>')
            dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{pct_color}"/>')
        else:
            dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{pct_color}"/>')
        labels.append(f'<text x="{x:.1f}" y="{y1:.1f}" font-size="12" fill="#5a5650" text-anchor="middle">'
                       f'{date_label}</text>')
        labels.append(f'<text x="{x:.1f}" y="{y2:.1f}" font-size="12" fill="#1c1d1f" '
                       f'text-anchor="middle">{c:,.0f}</text>')
        labels.append(f'<text x="{x:.1f}" y="{y3:.1f}" font-size="12" fill="{pct_color}" '
                       f'text-anchor="middle">{pct:+.1f}%</text>')

    leg_marker = ""
    if leg_start_idx is not None:
        lx = xs[leg_start_idx]
        # 2026-09-04 -- 다리 시작점 라벨이 그 점 자신의 위쪽 3줄 스택(최악의 경우 top_pad-36)과
        # 겹칠 수 있어(다리 시작=보통 국지적 고점이라 above 배치의 y1 최솟값 근처에 자주 위치),
        # top_pad를 넉넉히 키우고 이 라벨을 그보다 더 위(top_pad-45)에 고정해 항상 비켜가게 한다.
        leg_marker = (f'<line x1="{lx:.1f}" y1="{top_pad - 6:.1f}" x2="{lx:.1f}" y2="{h - bottom_pad + 6:.1f}" '
                      f'stroke="#5b3fa0" stroke-width="1" stroke-dasharray="3,3"/>'
                      f'<text x="{lx:.1f}" y="{top_pad - 45:.1f}" font-size="11" font-weight="700" '
                      f'fill="#5b3fa0" text-anchor="middle">다리 시작</text>')

    past_touch_note = (
        '<div style="font-size:11px;color:#c9860a;margin-bottom:2px;">'
        '○ 주황 테두리 = 이 구간 안의 과거 터치(오늘 것 말고도 이 종목은 반복적으로 터치가 일어남)'
        '</div>') if past_touch_idxs else ""
    return f'''{past_touch_note}<svg width="100%" viewBox="0 0 {w} {h}">
      {leg_marker}
      {"".join(segs)}
      {"".join(dots)}
      {"".join(labels)}
    </svg>'''


MULTI_THRESHOLDS = [(0.03, "3%", "#2f6fd6"), (0.05, "5%", "#e0692f"), (0.07, "7%", "#1f9e6e"),
                     (0.10, "10%", "#6b3fa0"), (0.20, "20%", "#d63d7a")]


def _multi_threshold_svg(closes):
    """2026-09-04 사용자 요청("3,5,7,10,20% 도 넣어줘") -- 여러 지그재그 임계값을 겹쳐 그려서,
    지금 보는 다리가 더 큰 기준으로 봐도 여전히 같은 국면인지 비교. V3 ZZ 확대화면의 멀티임계값
    비교차트와 같은 취지."""
    n = len(closes)
    if n < 2:
        return ""
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1.0
    w, h, pad_x, top_pad, bottom_pad = 1000, 190, 40, 34, 14
    plot_h = h - top_pad - bottom_pad
    step = (w - 2 * pad_x) / (n - 1)

    def xy(idx, price):
        x = pad_x + idx * step
        y = top_pad + (1 - (price - lo) / span) * plot_h
        return x, y

    legend = []
    lines = []
    for i, (threshold, label, color) in enumerate(MULTI_THRESHOLDS):
        lx = pad_x + i * 90
        legend.append(f'<line x1="{lx:.1f}" y1="12" x2="{lx+18:.1f}" y2="12" stroke="{color}" stroke-width="2.5"/>'
                       f'<text x="{lx+22:.1f}" y="16" font-size="11" fill="#5a5650">{label}</text>')
        swings = zigzag_swings(closes, threshold=threshold)
        if len(swings) < 2:
            continue
        pts = [f"{xy(idx, price)[0]:.1f},{xy(idx, price)[1]:.1f}" for idx, price in swings]
        lines.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.6" '
                      f'opacity="0.85"/>')
        for idx, price in swings:
            x, y = xy(idx, price)
            lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" fill="{color}"/>')

    return f'''<div style="margin-top:10px;font-size:11.5px;color:#5a5650;font-weight:600;">
    여러 기준선(3/5/7/10/20%) 비교 -- 큰 기준으로 보면 지금이 이미 다른 국면일 수 있음</div>
    <svg width="100%" viewBox="0 0 {w} {h}">
      {"".join(legend)}
      {"".join(lines)}
    </svg>'''


def _chart_lightbox_html(anchor_id, title, big_svg):
    """V3 ZZ 대시보드의 확대차트 라이트박스(jobs.render_holding_zigzags._chart_lightbox_html)와
    같은 방식 -- JS 없이 CSS :target만으로 여닫힘(href로 이 id를 가리키면 열리고, 배경/✕ 클릭시
    href="#"로 닫힘)."""
    return f'''<div id="{anchor_id}" class="zz-lightbox">
      <a href="#" class="zz-lightbox-backdrop" aria-label="닫기"></a>
      <div class="zz-lightbox-panel">
        <a href="#" class="zz-lightbox-close" aria-label="닫기">✕</a>
        <div class="zz-lightbox-title">{title}</div>
        {big_svg}
      </div>
    </div>'''


def _regime_badge_html(regime_data):
    """2026-09-04 사용자 요청 -- 오늘 코스피 국면과, regime_filter_test.py로 검증된 그 국면의
    강한이김(>=2) 실측 성과를 배지로 보여준다. 국면 자체는 캐시 없이 판단 못하는 정보라(개별
    종목 OHLCV로는 못 만듦), 캐시가 비어 있으면 정직하게 "데이터 없음"으로 표시한다."""
    if not regime_data or regime_data.get("regime") not in REGIME_STATS:
        return ('<div class="regime-badge regime-unknown">'
                '📊 시장국면 데이터 없음 (캐시 갱신 전 -- 다음 GHA 실행 후 표시됩니다)</div>')
    regime = regime_data["regime"]
    stat = REGIME_STATS[regime]
    icon = "📉" if regime == "down" else "📈"
    return f'''<div class="regime-badge regime-{regime}">
      {icon} 오늘({regime_data.get("date", "-")}) 시장국면: <b>{stat["label"]}</b> --
      강한이김(≥2점) 신호가 <b>{stat["verdict"]}</b>
      (5일도달률 {stat["reach"]:.1f}% · 평균 {stat["d5"]:+.2f}%, 코스피 {regime_data.get("kospi_close", 0):,.1f}
      / 60일선 {regime_data.get("kospi_ma60", 0):,.1f})
    </div>'''


def _verification_section_html():
    """2026-09-04 사용자 요청("오늘 추천종목 6일 후 다음주 금요일 79.3% 결과 보고해주고,
    다리넘은 종목 계속 결과 추적?") -- ZZ의 검증추적 페이지와 같은 개념. verification_tracker.
    json(별도 GHA refresh_verification_tracker.yml이 매일 FinanceDataReader로 개별종목 최신가를
    받아 갱신)을 읽어서 진행중/완료 현황을 보여준다. 트래커의 "다리전환"은 진입가(신호 뜬 날
    종가) 기준 3%+ 반등이지, 신호 이전부터 있던 다리의 진짜 저점 기준이 아님 -- 실제로 그 가격에
    샀다고 가정했을 때의 반등이라 더 실전적인 정의(그래서 "본전회복"과 함께 이걸 보여준다)."""
    tracker = verification_tracker._load_tracker()
    stats = verification_tracker.summary_stats(tracker)

    active_rows = []
    for name, e in sorted(tracker["active"].items(), key=lambda kv: kv[1]["entry_date"]):
        badges = []
        if e["leg_flipped"]:
            badges.append(f'<span class="verif-badge verif-badge-flip">다리전환 {e["leg_flip_date"]}</span>')
        elif e["reached_breakeven"]:
            badges.append(f'<span class="verif-badge verif-badge-reach">본전회복 {e["reached_date"]}</span>')
        else:
            badges.append('<span class="verif-badge verif-badge-wait">대기중</span>')
        latest = e["history"][-1]["close"] if e.get("history") else e["entry_price"]
        chg = (latest / e["entry_price"] - 1) * 100
        active_rows.append(f'''<tr>
          <td>{name}</td><td>{e["entry_date"]}</td>
          <td style="text-align:right;">{e["entry_price"]:,.0f}</td>
          <td style="text-align:right;">{latest:,.0f}</td>
          <td style="text-align:right;color:{'#0a8a3c' if chg >= 0 else '#c0392b'};">{chg:+.2f}%</td>
          <td>{e.get("days_elapsed", 0)}일차</td>
          <td>{"".join(badges)}</td>
        </tr>''')

    if stats["n"] >= 15:
        stat_line = (f'실측 n={stats["n"]} · 본전회복률 {stats["reached_pct"]:.1f}% · '
                     f'다리전환률 {stats["flipped_pct"]:.1f}% · 평균수익 {stats["avg_return"]:+.2f}%')
    elif stats["n"] > 0:
        stat_line = f'실측 n={stats["n"]}(최소표본 15 미만이라 통계로 쓰기엔 아직 이름) -- 참고만'
    else:
        stat_line = '아직 완료된 추적 건 없음'

    return f'''<div class="verif-section">
      <div class="verif-title">📋 검증추적 -- 진행중 {len(tracker["active"])}개 · 완료 {len(tracker["completed"])}개</div>
      <div class="verif-stat">{stat_line}</div>
      <div class="table-scroll">
      <table class="verif-table">
        <tr><th>종목</th><th>진입일</th><th style="text-align:right;">진입가</th>
            <th style="text-align:right;">최근가</th><th style="text-align:right;">등락</th>
            <th>경과</th><th>상태</th></tr>
        {"".join(active_rows) if active_rows else '<tr><td colspan="7">진행중인 추적 없음</td></tr>'}
      </table>
      </div>
      <div class="verif-note">"본전회복"=진입가 이상 회복, "다리전환"=진입가 대비 3%+ 반등(더
        엄격). 5거래일 지나면 완료로 이동합니다. 매일 GHA로 개별종목 실시간가 갱신.</div>
    </div>'''


STAT_BY_DEPTH = {
    10.0: {"reach": 73.7, "avg": 1.07, "n": "12,695"},
}


def _candidates_panel_html(top_rows, total_candidates, panel_id, min_depth):
    """2026-09-05 사용자 요청("저울에 15% 이상 버튼을 만드는건 어때?") -- 기존 render_html의
    표+강한이김박스+라이트박스 생성 로직을 재사용 가능하게 분리. panel_id로 chart_id를
    구분해서(예: chart-default-005930 vs chart-deep-005930) 두 패널이 같은 종목을 동시에
    보여줄 때도 라이트박스 앵커가 서로 충돌하지 않게 한다."""
    strong_rows = [r for r in top_rows if r["score"] >= 2]
    dart_cache = _load_dart_risk_cache()
    rows_html = []
    lightboxes_html = []
    for i, r in enumerate(top_rows, 1):
        tier = _tier_of(r["score"])
        color, bg = TIER_COLOR[tier]
        yr_pos_html = f"{r['yr_pos']:.0f}%" if r["yr_pos"] is not None else "-"
        dart_badge = _dart_badge_html(r["code"], dart_cache)
        market_badge = _market_status_badge_html(r["code"], dart_cache)
        high_vol_badge = _high_vol_badge_html(r.get("high_vol"))
        chart_id = f"chart-{panel_id}-{r['code']}"
        detail_closes = r["recent_closes"][-DETAIL_CHART_DAYS:]
        panel_content = (_detail_chart_svg(r) + _multi_threshold_svg(detail_closes)
                          + _disclosure_html(r["code"], dart_cache))
        title = (f"{r['name']}({r['code']}) · 최근 구간 · "
                 f'<span style="color:{color};">터치 {tier}{r["score"]:+d} · 깊이{r["depth_pct"]:.1f}%p</span>')
        lightboxes_html.append(_chart_lightbox_html(chart_id, title, panel_content))
        rows_html.append(f'''<tr>
      <td>{i}</td>
      <td style="font-weight:700;">{r['name']}{dart_badge}{market_badge}{high_vol_badge}</td>
      <td style="color:#898781;font-variant-numeric:tabular-nums;">{r['code']}</td>
      <td><span style="color:{color};background:{bg};border-radius:6px;padding:2px 8px;font-weight:700;">
          {r['score']:+d} {tier}</span></td>
      <td><a href="#{chart_id}" class="zz-chart-link" title="클릭하면 확대">
          {_seesaw_svg(r['score'])}</a></td>
      <td><a href="#{chart_id}" class="zz-chart-link" title="클릭하면 확대">
          {_sparkline_svg(r.get('recent_closes') or [])}</a></td>
      <td>하락다리 {r['leg_days']}일째</td>
      <td style="color:{'#0a8a3c' if r['leg_pct'] >= 0 else '#c0392b'};">{r['leg_pct']:+.1f}%</td>
      <td>{r['depth_pct']:.1f}%p</td>
      <td>{yr_pos_html}</td>
      <td style="text-align:right;">{r['cur_price']:,.0f}</td>
    </tr>''')

    strong_items_html = []
    for r in strong_rows:
        color, bg = TIER_COLOR["강한이김"]
        chart_id = f"chart-{panel_id}-{r['code']}"
        item_dart_badge = _dart_badge_html(r["code"], dart_cache)
        item_market_badge = _market_status_badge_html(r["code"], dart_cache)
        item_high_vol_badge = _high_vol_badge_html(r.get("high_vol"))
        strong_items_html.append(f'''<a href="#{chart_id}" class="strong-item">
          <span class="strong-item-top">
            <span class="strong-item-name">{r['name']}</span>
            <span class="strong-item-code">{r['code']}</span>
            <span class="strong-item-score">{r['score']:+d}</span>
          </span>
          <span class="strong-item-sub">되돌림 {r['depth_pct']:.1f}%p · 하락다리 {r['leg_days']}일째 · {r['cur_price']:,.0f}원</span>
          {f'<span class="strong-item-dart">{item_dart_badge}</span>' if item_dart_badge else ''}
          {f'<span class="strong-item-dart">{item_market_badge}</span>' if item_market_badge else ''}
          {f'<span class="strong-item-dart">{item_high_vol_badge}</span>' if item_high_vol_badge else ''}
        </a>''')
    stat = STAT_BY_DEPTH.get(min_depth, {"reach": None, "avg": None, "n": "?"})
    stat_txt = (f"5일 도달률 {stat['reach']:.1f}% · 평균 {stat['avg']:+.2f}% (2,700종목/n={stat['n']} 검증)"
                if stat["reach"] is not None else "검증치 준비중")
    strong_box_html = f'''<div class="strong-box">
      <div class="strong-box-title">🟢 강한이김(저울점수 ≥2점) -- 실측상 신뢰 가능한 신호
        <span class="strong-box-stat">{stat_txt}</span>
      </div>
      <div class="strong-box-grid">{"".join(strong_items_html)}</div>
    </div>''' if strong_rows else (
        '<div class="strong-box strong-box-empty">🟢 강한이김(≥2점) 신호 -- 오늘은 해당 종목 없음'
        '</div>'
    )

    # 2026-09-05 사용자 요청("추가 추천박스 위에 저울로 걸러내서 뱃지 안다는 종목만... 위로
    # 올려주면 좋겠어") -- 강한이김 중에서도 DART 재무경고(💸 배지)가 없는 종목만 한 번 더
    # 걸러서, 기존 강한이김 박스보다 위에 별도 박스로 보여준다. "저울점수로 걸렀는데 회사
    # 자체는 위험한" 경우(원풍물산류)를 이 박스에서는 아예 제외 -- 리스크까지 감안한 최종
    # 추천 목록의 성격.
    # 2026-09-05(③PER/관리종목 여부 포팅 시 확장) -- KRX 공식 관리종목/투자주의환기 지정도
    # DART 재무경고와 별개의 진짜 리스크 신호라, 같은 최종후보 박스에서 같이 걸러낸다.
    safe_rows = [
        r for r in strong_rows
        if not _dart_badge_html(r["code"], dart_cache)
        and not (dart_cache.get(r["code"], {}).get("market_status") or {}).get("management_issue")
        and not (dart_cache.get(r["code"], {}).get("market_status") or {}).get("caution_issue")
    ]
    safe_items_html = []
    for r in safe_rows:
        chart_id = f"chart-{panel_id}-{r['code']}"
        safe_item_market_badge = _market_status_badge_html(r["code"], dart_cache)
        safe_items_html.append(f'''<a href="#{chart_id}" class="strong-item safe-item">
          <span class="strong-item-top">
            <span class="strong-item-name">{r['name']}</span>
            <span class="strong-item-code">{r['code']}</span>
            <span class="strong-item-score">{r['score']:+d}</span>
          </span>
          <span class="strong-item-sub">되돌림 {r['depth_pct']:.1f}%p · 하락다리 {r['leg_days']}일째 · {r['cur_price']:,.0f}원</span>
          {f'<span class="strong-item-dart">{safe_item_market_badge}</span>' if safe_item_market_badge else ''}
        </a>''')
    safe_box_html = f'''<div class="strong-box safe-box">
      <div class="strong-box-title">✅ 강한이김 + 재무경고 없음 -- 리스크까지 거른 최종 후보
        <span class="strong-box-stat">DART 재무경고·관리종목·투자주의환기 없는 것만 ({len(safe_rows)}/{len(strong_rows)}개)</span>
      </div>
      <div class="strong-box-grid">{"".join(safe_items_html)}</div>
    </div>''' if strong_rows else ""

    base_date = top_rows[0]["last_date"].date() if top_rows else "-"
    body = f'''<div class="sub">기준일 {base_date} · 하락다리(반등기대) 후보 {total_candidates}종목 중 상위 {len(top_rows)} ·
    강한이김(≥2점) {len(strong_rows)}개</div>
  {safe_box_html}
  {strong_box_html}
  <div class="table-scroll">
  <table>
    <tr><th>#</th><th>종목명</th><th>코드</th><th>저울점수</th><th>시소</th><th>최근흐름</th>
        <th>기간</th><th>다리등락%</th>
        <th>되돌림깊이</th><th>52주위치</th><th style="text-align:right;">현재가</th></tr>
    {"".join(rows_html)}
  </table>
  </div>'''
    return body, "".join(lightboxes_html)


def render_html(top_rows, total_candidates, deep_rows=None, deep_total=None):
    """2026-09-04 사용자 요청("사이트를 만들어서 거기서 볼 수 있게") -- GitHub Pages로 배포할
    정적 HTML. V3/콜라 대시보드 계열과 같은 톤(단순 표+색 배지)으로 통일, 별도 프레임워크 없이
    자체완결형 파일 하나.

    2026-09-05 사용자 요청("저울에 15% 이상 버튼을 만드는건 어때?") -- deep_rows/deep_total을
    넘기면 "전체(2%+)"/"깊은눌림(15%+)" 두 탭을 CSS만으로(라디오버튼+:checked, 기존
    라이트박스와 같은 무자바스크립트 원칙) 전환할 수 있게 만든다. 안 넘기면(None) 기존처럼
    탭 없이 단일 화면 -- 호출부(테스트 등) 하위호환 유지."""
    regime_html = _regime_badge_html(_load_kospi_regime())
    default_body, default_lightboxes = _candidates_panel_html(top_rows, total_candidates, "d", MIN_DEPTH)

    tabs_html = ""
    deep_body = deep_lightboxes = ""
    if deep_rows is not None:
        deep_body, deep_lightboxes = _candidates_panel_html(deep_rows, deep_total, "deep", 10.0)
        # 2026-09-05 -- CSS :checked ~ 형제선택자는 "같은 부모 밑의 형제"에만 걸리므로, 라디오
        # 버튼과 두 패널을 전부 같은 레벨(depth-tabs 바로 아래)에 나란히 둬야 한다(라이트박스의
        # :target 패턴과 같은 무자바스크립트 원칙, 대신 부모-자식 구조를 한 단만 맞추면 됨).
        tabs_html = f'''<div class="depth-tabs">
      <input type="radio" name="depth-tab" id="tab-default" class="depth-tab-input" checked>
      <input type="radio" name="depth-tab" id="tab-deep" class="depth-tab-input">
      <div class="depth-tab-bar">
        <label for="tab-default" class="depth-tab-label">저울1 · 전체(고점대비 2%+ 눌림)</label>
        <label for="tab-deep" class="depth-tab-label">저울2 · 깊은눌림(10%+ 눌림)</label>
      </div>
      <div class="depth-panel depth-panel-default">{default_body}</div>
      <div class="depth-panel depth-panel-deep">{deep_body}</div>
    </div>'''

    return f'''<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>저울 -- 코스피+코스닥 상위 {len(top_rows)}</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#faf9f6; color:#1c1d1f;
         max-width: 1180px; margin: 0 auto; padding: 24px 16px; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  .sub {{ color:#70706a; font-size: 13px; margin-bottom: 20px; }}
  .table-scroll {{ overflow-x: auto; }}
  table {{ width: 100%; min-width: 900px; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align:left; border-bottom: 2px solid #1c1d1f; padding: 8px 6px; color:#5a5650;
       white-space: nowrap; }}
  td {{ border-bottom: 1px solid #e5e3dc; padding: 8px 6px; vertical-align: middle; }}
  .note {{ margin-top: 20px; font-size: 12.5px; color:#898781; line-height:1.6; }}
  .strong-box {{ border:2px solid #0a8a3c; background:#e8f5eb; border-radius:12px;
                  padding:14px 16px 16px; margin-bottom:20px; }}
  .strong-box-empty {{ padding:14px 16px; font-size:13px; color:#5a6b5e; }}
  .strong-box-title {{ font-size:14px; font-weight:700; color:#0a5c29; margin-bottom:10px;
                        display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }}
  .strong-box-stat {{ font-size:12px; font-weight:600; color:#3d7a52; }}
  .strong-box-grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr));
                       gap:8px; }}
  .strong-item {{ display:block; background:#fff; border:1px solid #bfe3cb; border-radius:8px;
                   padding:8px 10px; text-decoration:none; color:#1c1d1f; cursor:zoom-in; }}
  .strong-item:hover {{ border-color:#0a8a3c; box-shadow:0 2px 8px rgba(10,138,60,0.15); }}
  .strong-item-top {{ display:flex; align-items:center; gap:6px; font-size:13.5px; }}
  .strong-item-name {{ font-weight:700; }}
  .strong-item-code {{ color:#898781; font-size:11.5px; font-variant-numeric:tabular-nums; }}
  .strong-item-score {{ margin-left:auto; color:#0a8a3c; font-weight:700; font-variant-numeric:tabular-nums; }}
  .strong-item-sub {{ display:block; margin-top:3px; font-size:11.5px; color:#5a6b5e; }}
  .regime-badge {{ border-radius:8px; padding:9px 14px; margin-bottom:14px; font-size:12.5px;
                    line-height:1.5; }}
  .regime-down {{ background:#e8f5eb; color:#0a5c29; border:1px solid #bfe3cb; }}
  .regime-up {{ background:#fbf3d8; color:#7b6b1a; border:1px solid #ecdca0; }}
  .regime-unknown {{ background:#f0efe9; color:#70706a; border:1px solid #e5e3dc; }}
  .verif-section {{ margin-top:28px; border-top:1px solid #e5e3dc; padding-top:16px; }}
  .verif-title {{ font-size:15px; font-weight:700; margin-bottom:4px; }}
  .verif-stat {{ font-size:12.5px; color:#5a6b5e; margin-bottom:10px; }}
  .verif-table {{ width:100%; min-width:640px; border-collapse:collapse; font-size:12.5px; }}
  .verif-table th {{ text-align:left; border-bottom:2px solid #1c1d1f; padding:6px; color:#5a5650; }}
  .verif-table td {{ border-bottom:1px solid #e5e3dc; padding:6px; }}
  .verif-badge {{ display:inline-block; border-radius:5px; padding:1px 7px; font-size:11px; font-weight:700; }}
  .verif-badge-flip {{ background:#e8f5eb; color:#0a8a3c; }}
  .verif-badge-reach {{ background:#f3f2ea; color:#7b6b1a; }}
  .verif-badge-wait {{ background:#f0efe9; color:#898781; }}
  .verif-note {{ margin-top:8px; font-size:11.5px; color:#898781; line-height:1.6; }}
  .dart-badge {{ display:inline-block; margin-left:6px; font-size:10.5px; font-weight:700;
                  color:#a05a1a; background:#fbeed8; border-radius:5px; padding:1px 6px; }}
  .market-status-badge {{ display:inline-block; margin-left:6px; font-size:10.5px; font-weight:700;
                  color:#5a5650; background:#f0efe9; border-radius:5px; padding:1px 6px; }}
  .high-vol-badge {{ display:inline-block; margin-left:6px; font-size:10.5px; font-weight:700;
                  color:#0a7a4a; background:#e3f5ea; border-radius:5px; padding:1px 6px; }}
  .strong-item-dart {{ display:block; margin-top:4px; }}
  .safe-box {{ border-color:#0a8a3c; }}
  .disclosure-box {{ margin-top:10px; padding:8px 10px; background:#f7f6f2; border:1px solid #e5e3dc;
                      border-radius:8px; font-size:11.5px; color:#52514e; line-height:1.6; }}
  .disclosure-box b {{ color:#3d3550; }}
  .zz-chart-link {{ display:inline-block; cursor:zoom-in; border-radius:6px; }}
  .zz-chart-link:hover {{ outline:2px solid #cfe0f5; }}
  .zz-lightbox {{ display:none; position:fixed; inset:0; z-index:1000; }}
  .zz-lightbox:target {{ display:grid; place-items:center; }}
  .zz-lightbox-backdrop {{ position:absolute; inset:0; background:rgba(20,20,18,0.75); }}
  .zz-lightbox-panel {{ position:relative; background:#fff; border-radius:12px; padding:20px 28px 24px;
                         width:91vw; max-width:1425px; max-height:92vh; overflow:auto;
                         box-shadow:0 8px 40px rgba(0,0,0,0.35); }}
  .zz-lightbox-title {{ font-size:14px; font-weight:700; margin-bottom:10px; padding-right:24px; }}
  .zz-lightbox-close {{ position:absolute; top:10px; right:14px; font-size:20px; line-height:1;
                         color:#898781; text-decoration:none; }}
  .zz-lightbox-close:hover {{ color:#1c1d1f; }}
  .depth-tabs {{ margin-bottom: 4px; }}
  .depth-tab-input {{ position:absolute; opacity:0; pointer-events:none; }}
  .depth-tab-bar {{ display:flex; gap:6px; margin-bottom:4px; }}
  .depth-tab-label {{ cursor:pointer; padding:6px 14px; border-radius:8px 8px 0 0;
                       font-size:13px; font-weight:600; color:#898781; background:#efeee7;
                       border:1px solid #e5e3dc; border-bottom:none; }}
  .depth-panel {{ display:none; }}
  #tab-default:checked ~ .depth-tab-bar label[for="tab-default"],
  #tab-deep:checked ~ .depth-tab-bar label[for="tab-deep"] {{
    color:#1c1d1f; background:#fff; }}
  #tab-default:checked ~ .depth-panel-default,
  #tab-deep:checked ~ .depth-panel-deep {{ display:block; }}
</style>
</head><body>
  <h1>⚖ 저울 -- 코스피+코스닥 상위 {len(top_rows)}</h1>
  {regime_html}
  {tabs_html if deep_rows is not None else f'<div class="depth-panel" style="display:block;">{default_body}</div>'}
  <div class="note">
    ≥2점(강한이김)만 실측상 신뢰할 수 있는 신호입니다(고점대비 10%p+ 눌림 기준, 도달률
    73.7%/평균 +1.07%, 2,700종목/n=12,695 검증) -- 1점 이하는 평균이 오히려 마이너스였습니다.
    매일 17:10(KST) 자동 갱신됩니다.
    <br>공식 검증 근거: <a href="https://github.com/riskmgr12345-beop/scale-project">scale-project 저장소</a>
  </div>
  {_verification_section_html()}
  {default_lightboxes}
  {deep_lightboxes}
</body></html>'''


if __name__ == "__main__":
    cache, name_to_code = _load_cache_and_names()
    top_rows, total = build_report(cache, name_to_code, min_depth=MIN_DEPTH)
    # 2026-09-05 사용자 요청 흐름: "저울에 15% 이상 버튼" -> "10%로" -> "2%/10% 탭 헷갈리니
    # 저울1 자체를 10%로 바꾸고 탭 하나로 통일해줘" -- 두 탭 실험(deep_rows 별도 계산)은
    # 되돌리고, MIN_DEPTH 자체를 10.0으로 올려서 단일 화면으로 되돌렸다. render_html의
    # deep_rows 인자는 그대로 두되(향후 재사용 가능하게) 여기서는 넘기지 않는다.

    # 2026-09-04 사용자 요청("오늘 추천종목... 다리넘은 종목 계속 결과 추적?") -- 오늘 새로
    # 뽑힌 강한이김 종목을 검증추적 트래커에 심는다(네트워크 불필요, seed만). 실제 진행상황
    # 갱신(FinanceDataReader 필요)은 별도 GHA(refresh_verification_tracker.yml)가 매일 담당 --
    # 여기서 하지 않는 이유는 클라우드 라우틴 샌드박스에서 네트워크가 막혀 있기 때문(ZZ/콜라와
    # 동일한 제약).
    tracker = verification_tracker._load_tracker()
    added = verification_tracker.seed_from_top_rows(tracker, top_rows)
    verification_tracker._save_tracker(tracker)
    if added:
        print(f"검증추적에 신규 추가: {added}")

    text = render_text(top_rows, total)
    with open("scale_top15_report.txt", "w", encoding="utf-8") as f:
        f.write(text)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(render_html(top_rows, total))
    print(text)
