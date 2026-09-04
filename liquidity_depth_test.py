"""2026-09-04 사용자 요청("②유동성 하한 이나 ③깊이 구간을 검증") -- 강한이김(>=2) 모집단
안에서 (a) 유동성(평균거래대금)을 더 걸러내면 확률이 더 오르는지, (b) 되돌림깊이(depth_pct)
구간별로 확률이 다르게 작동하는지 검증한다.

scale_validation_test.py와 동일 공식/동일 MIN_DEPTH=7.0(그 검증에서 확정된 값)을 그대로
써서 모집단을 통일한다 -- 다른 문턱을 쓰면 비교 자체가 무의미해짐.

방법론(이 세션 전체에서 지켜온 원칙): 최소표본 15건, 시기분할(전반부/후반부) 재현성 확인,
승률(도달률)뿐 아니라 실제수익률(5일째 종가 기준)도 같이 본다.
"""
import pickle
import statistics
import sys

THRESHOLD = 0.03
MIN_DEPTH = 7.0
RISK_RECOVERY_MIN = 3.0
TUG_OF_WAR_RISK_PENALTY = 3
HORIZON = 5
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
    """(entry_idx, depth_pct) 쌍으로 돌려준다 -- 검증③(깊이구간)에 depth_pct가 필요."""
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
                entries.append((j, depth))
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


def avg_trading_value_krw(closes, volumes, entry_idx, window=20):
    """검증②(유동성) -- 진입일 직전 window일간 평균 거래대금(원). 종가*거래량의 median으로
    극단적 하루 급등락(거래정지 해제일 등)에 덜 흔들리게 한다."""
    if entry_idx < window:
        return None
    vals = [closes[i] * volumes[i] for i in range(entry_idx - window, entry_idx)]
    vals = [v for v in vals if v]
    if not vals:
        return None
    return statistics.median(vals)


def summarize(rows):
    n = len(rows)
    if not n:
        return "표본없음(n<1)"
    if n < 15:
        return f"n={n} (최소표본 15 미만 -- 결론 보류)"
    reached = sum(r["reached"] for r in rows) / n * 100
    d5 = statistics.mean(r["d5"] for r in rows)
    return f"n={n}, 5일도달률={reached:.1f}%, 5일째평균={d5:+.2f}%"


if __name__ == "__main__":
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
        for entry_idx, depth_pct in find_touch_entries(closes, lows):
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
            if score < 2:
                continue  # 이번 검증은 강한이김(>=2) 모집단 안에서만 세분화

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]
            liq = avg_trading_value_krw(closes, volumes, entry_idx)

            all_rows.append({
                "score": score, "reached": reached, "d5": d5_pct, "date": entry_date,
                "depth_pct": depth_pct, "liq": liq,
            })

    print(f"강한이김(>=2) 전체 표본: n={len(all_rows)}", file=sys.stderr)

    lines = ["강한이김(>=2) 모집단 안에서 유동성/깊이 세분화 검증 (MIN_DEPTH=7.0, "
             "scale_validation_test.py와 동일 모집단)", f"전체 n={len(all_rows)}", ""]

    # === ② 유동성(평균거래대금) 하한 필터 ===
    lines.append("=== ② 유동성(진입 직전 20일 평균거래대금) 3분위 ===")
    liq_rows = [r for r in all_rows if r["liq"] is not None]
    liq_sorted = sorted(r["liq"] for r in liq_rows)
    n = len(liq_sorted)
    t1 = liq_sorted[n // 3]
    t2 = liq_sorted[2 * n // 3]
    lines.append(f"(3분위 경계값: 하위/중위 {t1:,.0f}원, 중위/상위 {t2:,.0f}원)")
    buckets = [
        ("하위1/3(유동성낮음)", lambda r: r["liq"] < t1),
        ("중위1/3", lambda r: t1 <= r["liq"] < t2),
        ("상위1/3(유동성높음)", lambda r: r["liq"] >= t2),
    ]
    for label, cond in buckets:
        rows = [r for r in liq_rows if cond(r)]
        lines.append(f"  {label}: {summarize(rows)}")
    lines.append("")

    lines.append("=== ② 유동성 하한선 스윕(거래대금 X원 이상만) ===")
    for floor_label, floor_val in [("1억원+", 1e8), ("3억원+", 3e8), ("5억원+", 5e8),
                                    ("10억원+", 1e9), ("30억원+", 3e9)]:
        rows = [r for r in liq_rows if r["liq"] >= floor_val]
        lines.append(f"  {floor_label}: {summarize(rows)}")
    lines.append("")

    # 시기분할 재현성 (유동성 상위1/3 vs 하위1/3)
    dates_sorted = sorted(r["date"] for r in all_rows)
    mid = dates_sorted[len(dates_sorted) // 2]
    lines.append(f"=== ② 시기분할 재현성 (기준일 {mid.date()}) ===")
    for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
        lines.append(f"-- {period_label} --")
        for label, cond in [("유동성 상위1/3", lambda r: r["liq"] is not None and r["liq"] >= t2),
                             ("유동성 하위1/3", lambda r: r["liq"] is not None and r["liq"] < t1)]:
            rows = [r for r in all_rows if cond(r) and cond_date(r["date"])]
            lines.append(f"  {label}: {summarize(rows)}")
    lines.append("")

    # === ③ 되돌림깊이(depth_pct) 구간별 ===
    lines.append("=== ③ 되돌림깊이(depth_pct) 구간별 ===")
    depth_bins = [("7~10%p", 7.0, 10.0), ("10~15%p", 10.0, 15.0), ("15~20%p", 15.0, 20.0),
                  ("20~30%p", 20.0, 30.0), ("30%p+", 30.0, 999.0)]
    for label, lo, hi in depth_bins:
        rows = [r for r in all_rows if lo <= r["depth_pct"] < hi]
        lines.append(f"  {label}: {summarize(rows)}")
    lines.append("")

    lines.append(f"=== ③ 시기분할 재현성 (기준일 {mid.date()}) ===")
    for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
        lines.append(f"-- {period_label} --")
        for label, lo, hi in [("7~10%p(얕음)", 7.0, 10.0), ("20%p+(깊음)", 20.0, 999.0)]:
            rows = [r for r in all_rows if lo <= r["depth_pct"] < hi and cond_date(r["date"])]
            lines.append(f"  {label}: {summarize(rows)}")

    with open("liquidity_depth_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
