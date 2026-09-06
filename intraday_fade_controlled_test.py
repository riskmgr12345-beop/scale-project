"""2026-09-07 후속 검증 -- intraday_fade_test.py에서 발견한 "장중고점 대비 종가 밀림"이 이미
채택된 ⑥고변동 부스터(norm_ratio>=1.0)와 독립적인 신호인지, 아니면 그냥 변동성이 큰 날을 다른
각도에서 다시 잡아낸 착시인지 확인한다(이 세션에서 "조기" 부스트가 사실 "고변동"의 착시였던
것과 같은 종류의 재발 방지 점검). 방법: 고변동 여부로 먼저 나눈 뒤(순수분리), 그 안에서
fade_pct 상/하로 한 번 더 나눠 효과가 남아있는지 확인."""
import pickle
import statistics
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH, summarize,
)

FADE_HIGH_THRESHOLD = 3.0
MIN_RANGE_SAMPLE_DAYS = 60


def _avg_daily_range_pct(highs, lows, closes):
    vals = [(h - l) / c * 100 for h, l, c in zip(highs, lows, closes) if c]
    if len(vals) < MIN_RANGE_SAMPLE_DAYS:
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

        for entry_idx in find_touch_entries(closes, lows):
            if entry_idx + 1 >= len(closes):
                continue
            entry_price = closes[entry_idx]
            day_low = lows[entry_idx]
            day_high = highs[entry_idx]
            if not day_low or not entry_price or not day_high:
                continue
            same_day_recovery = (entry_price - day_low) / day_low * 100
            risk_flag = same_day_recovery >= RISK_RECOVERY_MIN

            vr = volume_ratio_at(volumes, entry_idx)
            fast_rev = recent_fast_reversal_active(closes, entry_idx)
            extra = zz_extra_score(vr, fast_rev)
            score = extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)
            score = max(-5, min(5, score))
            if score < 2:
                continue

            # 고변동 판정(vol_boost_test.py와 동일 정의 -- 오늘 제외한 과거로만 평소 변동폭 계산)
            avg_range = _avg_daily_range_pct(highs[:entry_idx], lows[:entry_idx], closes[:entry_idx])
            swings = zigzag_swings(closes[: entry_idx + 1])
            leg_high = None
            for k in range(len(swings) - 1, -1, -1):
                if swings[k][0] <= entry_idx:
                    leg_high = swings[k][1]
                    break
            if leg_high is None or avg_range is None or avg_range == 0:
                continue
            depth_pct = (leg_high - day_low) / leg_high * 100
            high_vol = (depth_pct / avg_range) >= 1.0

            fade_pct = (day_high - entry_price) / day_high * 100 if day_high else 0.0
            high_fade = fade_pct >= FADE_HIGH_THRESHOLD

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]

            all_rows.append({"reached": reached, "d5": d5_pct, "date": entry_date,
                              "high_fade": high_fade, "high_vol": high_vol})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}", file=sys.stderr)

    lines = ["고변동 통제 후 '장중고점 밀림' 순수분리 재검증",
             f"전체 표본 n={len(all_rows)}", ""]

    for vol_label, vol_cond in [("고변동(norm_ratio>=1.0) 안에서", lambda r: r["high_vol"]),
                                 ("저변동(norm_ratio<1.0) 안에서", lambda r: not r["high_vol"])]:
        lines.append(f"=== {vol_label} ===")
        subset = [r for r in all_rows if vol_cond(r)]
        for label, cond in [("올랐다가 빠짐", lambda r: r["high_fade"]),
                             ("고점 근처 마감", lambda r: not r["high_fade"])]:
            rows = [r for r in subset if cond(r)]
            lines.append(f"  {label}: {summarize(rows)}")
        lines.append("")

    dates_sorted = sorted(r["date"] for r in all_rows)
    if dates_sorted:
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 재현성 (기준일 {mid}) ===")
        for vol_label, vol_cond in [("고변동", lambda r: r["high_vol"]), ("저변동", lambda r: not r["high_vol"])]:
            for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
                subset = [r for r in all_rows if vol_cond(r) and cond_date(r["date"])]
                lines.append(f"-- {vol_label}/{period_label} --")
                for label, cond in [("올랐다가 빠짐", lambda r: r["high_fade"]),
                                     ("고점 근처 마감", lambda r: not r["high_fade"])]:
                    rows = [r for r in subset if cond(r)]
                    lines.append(f"  {label}: {summarize(rows)}")

    with open("intraday_fade_controlled_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
