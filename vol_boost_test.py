"""2026-09-05 사용자 요청("순서대로 시작해") ⑥ZZ 나머지 부스터 중 첫 번째: "고변동"(종목별
변동성 정규화) 재검증. ZZ는 49종목/8년으로 "그 종목의 최근 평균 일일변동폭 대비 오늘 터치
깊이가 1.0배 이상(norm_ratio>=1.0)"이면 3.0/4.0/5.0등급에서 확정률이 뚜렷하게 개선된다고
검증했다(_avg_daily_range_pct, render_holding_zigzags.py). 저울의 강한이김(>=2) 모집단
(2,700종목)에서도 재현되는지 순수분리로 확인한다.

_avg_daily_range_pct는 순수 OHLC 함수라 self-contained 복사만으로 충분(외부 API 불필요,
④수급비율과 달리 인프라 문제 없음)."""
import pickle
import statistics
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH, summarize,
)

MIN_SAMPLE_DAYS = 60  # ZZ의 _avg_daily_range_pct와 동일 최소 표본 기준


def _avg_daily_range_pct(highs, lows, closes):
    """ZZ의 render_holding_zigzags._avg_daily_range_pct를 그대로 복사(self-contained)."""
    vals = [(h - l) / c * 100 for h, l, c in zip(highs, lows, closes) if c]
    if len(vals) < MIN_SAMPLE_DAYS:
        return None
    return sum(vals) / len(vals)


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

        # 룩어헤드 방지: 그 시점까지의 과거 데이터만으로 평균 변동폭 계산(전체 시계열 평균을
        # 쓰면 미래 변동성까지 섞여 들어가는 이 세션 초반의 실수를 반복하지 않기 위함).
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

            avg_range = _avg_daily_range_pct(highs[:entry_idx], lows[:entry_idx], closes[:entry_idx])
            if avg_range is None or avg_range == 0:
                continue
            # find_touch_entries와 같은 depth 정의((다리고점-그날저가)/다리고점*100)로 계산
            swings = zigzag_swings(closes[: entry_idx + 1])
            leg_high = None
            for k in range(len(swings) - 1, -1, -1):
                if swings[k][0] <= entry_idx:
                    leg_high = swings[k][1]
                    break
            if leg_high is None:
                continue
            depth_pct = (leg_high - day_low) / leg_high * 100
            norm_ratio = depth_pct / avg_range

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]

            all_rows.append({"reached": reached, "d5": d5_pct, "date": entry_date,
                              "high_vol": norm_ratio >= 1.0})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}", file=sys.stderr)

    lines = ["저울 강한이김(>=2) 모집단에서 '고변동(norm_ratio>=1.0)' 부스터 재검증",
             f"전체 표본 n={len(all_rows)}", ""]

    lines.append("=== 고변동 여부(순수분리) ===")
    for label, cond in [("고변동(norm_ratio>=1.0)", lambda r: r["high_vol"]),
                         ("저변동(plain)", lambda r: not r["high_vol"])]:
        rows = [r for r in all_rows if cond(r)]
        lines.append(f"{label}: {summarize(rows)}")
    lines.append("")

    dates_sorted = sorted(r["date"] for r in all_rows)
    if dates_sorted:
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 재현성 (전반부 vs 후반부, 기준일 {mid}) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            lines.append(f"-- {period_label} --")
            for label, cond in [("고변동", lambda r: r["high_vol"]), ("plain", lambda r: not r["high_vol"])]:
                rows = [r for r in all_rows if cond(r) and cond_date(r["date"])]
                lines.append(f"  {label}: {summarize(rows)}")

    with open("vol_boost_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
