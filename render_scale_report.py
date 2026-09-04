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
import os
import pickle
import statistics
from datetime import datetime

THRESHOLD = 0.03
MIN_DEPTH = 2.0  # 상위 15개를 뽑는 용도라 문턱을 낮게 잡아 후보군을 넓게 본다(2%p+)
RISK_RECOVERY_MIN = 3.0
TUG_OF_WAR_RISK_PENALTY = 3
# 2026-09-04 -- 로컬 데스크톱(_상한가전조연구, 한글 폴더명)과 클라우드 라우틴(같이 클론되는
# limitup-precursor-research, 저장소 이름 그대로)이 서로 다른 폴더명을 쓰므로 둘 다 후보로
# 시도한다 -- 콜라 캐시를 읽기전용 재사용하는 원칙은 같지만 실행 환경마다 경로만 다름.
CACHE_PATH_CANDIDATES = [
    "../_상한가전조연구/research_cache/limitup_ohlcv_cache.pkl",
    "../limitup-precursor-research/research_cache/limitup_ohlcv_cache.pkl",
]
TOP_N = 15


def _resolve_cache_path():
    for path in CACHE_PATH_CANDIDATES:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(
        "콜라 캐시(limitup_ohlcv_cache.pkl)를 찾을 수 없습니다. 시도한 경로: "
        + ", ".join(CACHE_PATH_CANDIDATES)
        + " -- limitup-precursor-research 저장소가 이 저장소와 같은 부모 폴더에 클론돼 있는지 확인하세요."
    )


def zigzag_swings(closes, threshold=THRESHOLD):
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
    return swings


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
    """진행 중인 다리에서 극값 대비 최신 종가의 되돌림 깊이(%p). down이면 저점 대비 반등폭,
    up이면 고점 대비 눌림폭 -- DEPTH_BANDS 방향 정의와 동일."""
    if pos["leg_dir"] == "down":
        return max(0.0, (closes[-1] / lows[-1] - 1) * 100) if lows[-1] else None
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


def year_range_position_pct(closes, highs, lows):
    window = min(len(closes), 252)
    yr_high = max(highs[-window:])
    yr_low = min(lows[-window:])
    cur = closes[-1]
    if yr_high <= yr_low:
        return None
    return max(0.0, min(100.0, (cur - yr_low) / (yr_high - yr_low) * 100))


def build_report():
    with open(_resolve_cache_path(), "rb") as f:
        cache = pickle.load(f)

    rows = []
    for name, df in cache.items():
        try:
            closes = df["Close"].tolist()
            highs = df["High"].tolist()
            lows = df["Low"].tolist()
            volumes = df["Volume"].tolist()
            dates_idx = df.index
        except Exception:
            continue
        if len(closes) < 60:
            continue

        pos = current_position(dates_idx, closes)
        if not pos or pos["leg_dir"] != "down":
            continue
        depth_pct = touch_depth_now(closes, lows, pos)
        if depth_pct is None or depth_pct < MIN_DEPTH:
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

        rows.append({
            "name": name, "score": score, "leg_dir": pos["leg_dir"], "leg_pct": pos["leg_pct"],
            "leg_days": pos["leg_days"], "depth_pct": depth_pct, "cur_price": entry_price,
            "yr_pos": yr_pos, "risk_flag": risk_flag, "vr": vr, "fast_rev": fast_rev,
            "last_date": dates_idx[-1],
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
        lines.append(
            f"{i}. {r['name']} | 저울점수 {r['score']:+d}({tier}) | "
            f"하락다리 {r['leg_days']}일째, {r['leg_pct']:+.1f}% | 되돌림깊이 {r['depth_pct']:.1f}%p | "
            f"52주위치 {r['yr_pos']:.0f}%" if r["yr_pos"] is not None else
            f"{i}. {r['name']} | 저울점수 {r['score']:+d}({tier}) | "
            f"하락다리 {r['leg_days']}일째, {r['leg_pct']:+.1f}% | 되돌림깊이 {r['depth_pct']:.1f}%p"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    top_rows, total = build_report()
    text = render_text(top_rows, total)
    with open("scale_top15_report.txt", "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
