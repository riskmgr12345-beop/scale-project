"""1차 저울(시장국면 필터) 검증 -- 2026-09-04 사용자 요청("걸러내는 효과가 있는지 따로
확인하고... 진행"). 첫 시도는 캐시 안 전종목 수익률 중앙값으로 합성지수를 만들었는데,
소형/테마주 비중이 큰 유니버스라 실제 코스피 지수와 거의 무관하게 거의 항상 "하락장"으로
나오는 결함이 있어(상승장 판정일이 484일 중 15일뿐) 폐기. 이 버전은 FinanceDataReader로
실제 KOSPI 지수(KS11)를 직접 받아 60일선 국면을 판정한다(로컬 실행 -- 네트워크 접근 가능,
클라우드 라우틴에서는 이 스크립트를 못 씀에 유의).

MIN_DEPTH도 저울 2차(scale_validation_test.py)와 동일하게 7.0으로 맞춰서 공정 비교한다
(첫 시도에서 실수로 2.0을 썼다가 표본 성격 자체가 달라져 숫자가 안 맞았음).
"""
import pickle
import statistics

import FinanceDataReader as fdr

THRESHOLD = 0.03
MIN_DEPTH = 7.0
RISK_RECOVERY_MIN = 3.0
TUG_OF_WAR_RISK_PENALTY = 3
HORIZON = 5
REGIME_MA_WINDOW = 60
CACHE_PATH = "../_상한가전조연구/research_cache/limitup_ohlcv_cache.pkl"


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


def find_touch_entries(closes, lows):
    swings = zigzag_swings(closes)
    entries = []
    for i in range(len(swings) - 1):
        idx0, p0 = swings[i]
        idx1, p1 = swings[i + 1]
        if p1 >= p0:
            continue
        for j in range(idx0, idx1 + 1):
            depth = (p0 - lows[j]) / p0 * 100
            if depth >= MIN_DEPTH:
                entries.append(j)
                break
    return entries


def recent_fast_reversal_active(closes, entry_idx, fast_days=5):
    truncated = closes[: entry_idx + 1]
    swings = zigzag_swings(truncated)
    if len(swings) < 4:
        return False
    idx_m4, _ = swings[-4]
    idx_m3, _ = swings[-3]
    return (idx_m3 - idx_m4) <= fast_days


def volume_ratio_at(volumes, entry_idx):
    if entry_idx < 20:
        return None
    base = volumes[entry_idx - 20: entry_idx]
    base_med = statistics.median(base)
    if not base_med:
        return None
    return volumes[entry_idx] / base_med


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


def summarize(rows):
    n = len(rows)
    if not n:
        return "표본없음"
    reached = sum(r["reached"] for r in rows) / n * 100
    d5 = statistics.mean(r["d5"] for r in rows)
    return f"n={n}, 5일도달률={reached:.1f}%, 5일째평균={d5:+.2f}%"


if __name__ == "__main__":
    kospi = fdr.DataReader("KS11", "2024-06-01")
    kospi_close = kospi["Close"]
    kospi_ma60 = kospi_close.rolling(REGIME_MA_WINDOW).mean()
    regime_by_date = {}
    for d in kospi_close.index:
        ma = kospi_ma60.loc[d]
        if ma != ma:  # NaN
            regime_by_date[d] = None
        else:
            regime_by_date[d] = "up" if kospi_close.loc[d] >= ma else "down"

    regime_counts = {"up": 0, "down": 0, None: 0}
    for v in regime_by_date.values():
        regime_counts[v] += 1
    print(f"국면 분포(KOSPI 60일선 기준): 상승장 {regime_counts['up']}일, "
          f"하락장 {regime_counts['down']}일, 판정불가 {regime_counts[None]}일")

    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    all_rows = []
    for name, df in cache.items():
        try:
            closes = df["Close"].tolist()
            lows = df["Low"].tolist()
            volumes = df["Volume"].tolist()
            dates_idx = df.index
        except Exception:
            continue
        if len(closes) < 60:
            continue
        for entry_idx in find_touch_entries(closes, lows):
            if entry_idx + 1 >= len(closes):
                continue
            entry_price = closes[entry_idx]
            day_low = lows[entry_idx]
            if not day_low or not entry_price:
                continue
            same_day_recovery = (entry_price - day_low) / day_low * 100
            risk_flag = same_day_recovery >= RISK_RECOVERY_MIN

            vr = volume_ratio_at(volumes, entry_idx)
            fast_rev = recent_fast_reversal_active(closes, entry_idx)
            extra = zz_extra_score(vr, fast_rev)
            score = max(-5, min(5, extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)))

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]
            regime = regime_by_date.get(entry_date)

            all_rows.append({"score": score, "reached": reached, "d5": d5_pct, "regime": regime})

    lines = ["=== 시장국면별(KOSPI 60일선) 저울 2차 점수 효과 비교 (MIN_DEPTH=7.0, 2차와 동일모집단) ==="]
    for regime_label in ["up", "down"]:
        lines.append(f"-- 국면={regime_label} --")
        for tier_label, cond in [("강한이김(>=2)", lambda s: s >= 2), ("짐(<0)", lambda s: s < 0)]:
            rows = [r for r in all_rows if r["regime"] == regime_label and cond(r["score"])]
            lines.append(f"  {tier_label}: {summarize(rows)}")
        strong = [r for r in all_rows if r["regime"] == regime_label and r["score"] >= 2]
        lose = [r for r in all_rows if r["regime"] == regime_label and r["score"] < 0]
        if strong and lose:
            gap = statistics.mean(r["d5"] for r in strong) - statistics.mean(r["d5"] for r in lose)
            lines.append(f"  강한이김-짐 평균수익 격차: {gap:+.2f}%p")

    with open("regime_filter_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
