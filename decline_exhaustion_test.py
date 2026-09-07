"""2026-09-07 사용자 제안(100점 잣대 중 "최근 하락 소진 여부" -- 저울에 없는 새 개념)을
순수분리+시기분할로 검증. 정의: 하락다리를 앞/뒤 절반으로 나눠서, 뒤쪽 절반(최근)의 일평균
하락률이 앞쪽 절반보다 완화됐는지(=하락 속도 둔화="소진") 확인. 다리가 최소 4일은 있어야
의미있게 반으로 나눌 수 있어 그 조건만 적용."""
import pickle
import statistics
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH, summarize,
)

MIN_LEG_DAYS_FOR_SPLIT = 4


def _leg_start_idx(closes, entry_idx):
    """entry_idx가 속한 하락다리의 시작 인덱스(진행중인 다리, swings[-2])를 찾는다."""
    swings = zigzag_swings(closes[: entry_idx + 1])
    if len(swings) < 2:
        return None
    return swings[-2][0]


def _avg_daily_pct_change(closes, start_idx, end_idx):
    """[start_idx, end_idx] 구간의 일별 종가 변화율 평균(부호 있음, 하락이면 음수)."""
    vals = []
    for i in range(start_idx + 1, end_idx + 1):
        if closes[i - 1]:
            vals.append((closes[i] / closes[i - 1] - 1) * 100)
    return statistics.mean(vals) if vals else None


if __name__ == "__main__":
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    all_rows = []
    skipped_short = 0
    for name, df in cache.items():
        try:
            closes_raw = df["Close"].tolist()
            lows_raw = df["Low"].tolist()
            volumes_raw = df["Volume"].tolist()
            dates_idx_raw = df.index
        except Exception:
            continue
        keep = [i for i, v in enumerate(volumes_raw) if v and v > 0]
        if len(keep) != len(volumes_raw):
            closes = [closes_raw[i] for i in keep]
            lows = [lows_raw[i] for i in keep]
            volumes = [volumes_raw[i] for i in keep]
            dates_idx = [dates_idx_raw[i] for i in keep]
        else:
            closes, lows, volumes = closes_raw, lows_raw, volumes_raw
            dates_idx = list(dates_idx_raw)
        if len(closes) < 60:
            skipped_short += 1
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
            if score < 2:
                continue

            leg_start = _leg_start_idx(closes, entry_idx)
            if leg_start is None:
                continue
            leg_days = entry_idx - leg_start
            if leg_days < MIN_LEG_DAYS_FOR_SPLIT:
                continue
            mid = leg_start + leg_days // 2
            first_half_rate = _avg_daily_pct_change(closes, leg_start, mid)
            second_half_rate = _avg_daily_pct_change(closes, mid, entry_idx)
            if first_half_rate is None or second_half_rate is None:
                continue
            # "소진" = 최근(뒤쪽) 하락률이 앞쪽보다 덜 음수(완화됨). 둘 다 음수가 정상(하락다리)
            # 이므로 second_half_rate > first_half_rate면 둔화로 본다.
            exhausted = second_half_rate > first_half_rate

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]

            all_rows.append({"reached": reached, "d5": d5_pct, "date": entry_date, "exhausted": exhausted})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}", file=sys.stderr)

    lines = ["저울 강한이김(>=2) 모집단에서 '최근 하락 소진(다리 후반부 하락률 완화)' 재검증",
             f"전체 표본 n={len(all_rows)} (다리 4일 이상인 것만 대상)", ""]

    lines.append("=== 하락 소진 여부(순수분리) ===")
    for label, cond in [("소진(하락 둔화)", lambda r: r["exhausted"]),
                         ("소진 아님(하락 지속/가속)", lambda r: not r["exhausted"])]:
        rows = [r for r in all_rows if cond(r)]
        lines.append(f"{label}: {summarize(rows)}")
    lines.append("")

    dates_sorted = sorted(r["date"] for r in all_rows)
    if dates_sorted:
        mid_date = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 재현성 (전반부 vs 후반부, 기준일 {mid_date}) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid_date), ("후반부", lambda d: d >= mid_date)]:
            lines.append(f"-- {period_label} --")
            for label, cond in [("소진", lambda r: r["exhausted"]), ("소진아님", lambda r: not r["exhausted"])]:
                rows = [r for r in all_rows if cond(r) and cond_date(r["date"])]
                lines.append(f"  {label}: {summarize(rows)}")

    with open("decline_exhaustion_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
