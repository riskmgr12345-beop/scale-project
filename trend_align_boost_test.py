"""2026-09-05 사용자 요청("순서대로 시작해") ⑥ZZ 나머지 부스터 4/N(마지막): "추세동반"
(ADX>=25 + DI 방향정렬) 재검증. ZZ가 49종목/2018~ 데이터로 검증(band=3.0%p 순수분리, ADX
단독 +5~6%p, 추세정렬 단독 +5.7~5.9%p, 둘 다 만족시 62.4%->75.3%)한 Wilder 표준 DMI/ADX
(14일 스무딩, 순수 파이썬 구현)를 저울 강한이김(>=2) 모집단에 재검증한다."""
import pickle
import statistics
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH, summarize,
)

DMI_ADX_PERIOD = 14
TREND_ALIGN_ADX_MIN = 25.0


def _dmi_adx_series(highs, lows, closes, period=DMI_ADX_PERIOD):
    """ZZ의 _dmi_adx_series를 그대로 복사(self-contained, Wilder 표준)."""
    n = len(closes)
    tr = [None] * n
    plus_dm = [None] * n
    minus_dm = [None] * n
    for i in range(1, n):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]
        plus_dm[i] = high_diff if (high_diff > low_diff and high_diff > 0) else 0.0
        minus_dm[i] = low_diff if (low_diff > high_diff and low_diff > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

    smoothed_tr, smoothed_plus, smoothed_minus = [None] * n, [None] * n, [None] * n
    plus_di, minus_di, dx, adx = [None] * n, [None] * n, [None] * n, [None] * n
    if n <= period:
        return plus_di, minus_di, adx

    smoothed_tr[period] = sum(tr[1:period + 1])
    smoothed_plus[period] = sum(plus_dm[1:period + 1])
    smoothed_minus[period] = sum(minus_dm[1:period + 1])
    for i in range(period + 1, n):
        smoothed_tr[i] = smoothed_tr[i - 1] - smoothed_tr[i - 1] / period + tr[i]
        smoothed_plus[i] = smoothed_plus[i - 1] - smoothed_plus[i - 1] / period + plus_dm[i]
        smoothed_minus[i] = smoothed_minus[i - 1] - smoothed_minus[i - 1] / period + minus_dm[i]

    for i in range(period, n):
        if smoothed_tr[i]:
            plus_di[i] = 100 * smoothed_plus[i] / smoothed_tr[i]
            minus_di[i] = 100 * smoothed_minus[i] / smoothed_tr[i]
            denom = plus_di[i] + minus_di[i]
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / denom if denom else 0.0

    dx_slice = [v for v in dx[period:period * 2] if v is not None]
    if len(dx_slice) == period:
        adx[period * 2 - 1] = sum(dx_slice) / period
        for i in range(period * 2, n):
            if dx[i] is not None and adx[i - 1] is not None:
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return plus_di, minus_di, adx


def _is_trend_aligned(plus_di, minus_di, adx, i, leg_dir):
    if i >= len(adx) or adx[i] is None or plus_di[i] is None or minus_di[i] is None:
        return False
    if adx[i] < TREND_ALIGN_ADX_MIN:
        return False
    return (minus_di[i] > plus_di[i]) if leg_dir == "down" else (plus_di[i] > minus_di[i])


if __name__ == "__main__":
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    all_rows = []
    skipped_short = 0
    for name, df in cache.items():
        try:
            closes_raw = df["Close"].tolist()
            lows_raw = df["Low"].tolist()
            highs_raw = df["High"].tolist()
            volumes_raw = df["Volume"].tolist()
            dates_idx_raw = df.index
        except Exception:
            continue
        keep = [i for i, v in enumerate(volumes_raw) if v and v > 0]
        if len(keep) != len(volumes_raw):
            closes = [closes_raw[i] for i in keep]
            lows = [lows_raw[i] for i in keep]
            highs = [highs_raw[i] for i in keep]
            volumes = [volumes_raw[i] for i in keep]
            dates_idx = [dates_idx_raw[i] for i in keep]
        else:
            closes, lows, highs, volumes = closes_raw, lows_raw, highs_raw, volumes_raw
            dates_idx = list(dates_idx_raw)
        if len(closes) < 60:
            skipped_short += 1
            continue

        plus_di, minus_di, adx = _dmi_adx_series(highs, lows, closes)

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
            score = extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)
            score = max(-5, min(5, score))
            if score < 2:
                continue  # 강한이김(>=2) 모집단만

            aligned = _is_trend_aligned(plus_di, minus_di, adx, entry_idx, "down")

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]

            all_rows.append({"reached": reached, "d5": d5_pct, "date": entry_date, "aligned": aligned})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}", file=sys.stderr)

    lines = ["저울 강한이김(>=2) 모집단에서 '추세동반(ADX>=25+DI정렬)' 부스터 재검증",
             f"전체 표본 n={len(all_rows)}", ""]

    lines.append("=== 추세동반 여부(순수분리) ===")
    for label, cond in [("추세동반(ADX>=25+DI정렬)", lambda r: r["aligned"]),
                         ("plain", lambda r: not r["aligned"])]:
        rows = [r for r in all_rows if cond(r)]
        lines.append(f"{label}: {summarize(rows)}")
    lines.append("")

    dates_sorted = sorted(r["date"] for r in all_rows)
    if dates_sorted:
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 재현성 (전반부 vs 후반부, 기준일 {mid}) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            lines.append(f"-- {period_label} --")
            for label, cond in [("추세동반", lambda r: r["aligned"]), ("plain", lambda r: not r["aligned"])]:
                rows = [r for r in all_rows if cond(r) and cond_date(r["date"])]
                lines.append(f"  {label}: {summarize(rows)}")

    with open("trend_align_boost_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
