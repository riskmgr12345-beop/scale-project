"""2026-09-05 사용자 요청("순서대로 시작해") ⑥ZZ 나머지 부스터 2/N: "투매반전"(Selling
Climax & Reversal) 재검증. ZZ는 확정률(3%선 돌파) 기준으로는 최고(99%)였지만, 2026-08-30
정밀검증(-3% 손절 반영 실제 D+5 승률)에서 오히려 기준(35.2%)보다 낮은 32.2%(n=506)로 나와
"짧게 반짝 튀고 다시 꺾이는" 패턴으로 판단해 ZZ 프로덕션에서는 폐기됐다.

저울은 손절 자체를 안 쓰기로 확정한 상태([[project_scale_project_2026_09_04]] ⑰ 참고,
"손절 없음이 최선"으로 5종 매도전략 전부 기각) -- ZZ가 폐기한 이유(손절에 걸려 짧은 반등을
놓침)가 저울에는 애초에 적용 안 되는 조건이라, 저울 자신의 방법론(5일도달률/5일평균, 손절
없음)으로 독립적으로 재검증할 가치가 있다고 판단해 진행."""
import pickle
import statistics
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH, summarize,
)

CLIMAX_VOL_RATIO_MIN = 2.0


def _avg_volume_20d(volumes):
    if not volumes or len(volumes) < 20:
        return None
    vals = volumes[-20:]
    return sum(vals) / len(vals) if vals else None


def _is_climax_candle(open_price, high, low, close):
    rng = high - low
    if rng <= 0:
        return False
    lower_wick_ratio = (min(open_price, close) - low) / rng
    close_pos = (close - low) / rng
    long_lower_wick = lower_wick_ratio >= 0.4 and close_pos >= 0.5
    strong_bull = (open_price > 0 and (close / open_price - 1) >= 0.03) and ((high - close) / rng) <= 0.3
    return long_lower_wick or strong_bull


def _is_selling_climax(volume, avg_volume_20d, open_price, high, low, close, vol_ratio_min=CLIMAX_VOL_RATIO_MIN):
    if not avg_volume_20d or not volume:
        return False
    if (volume / avg_volume_20d) < vol_ratio_min:
        return False
    return _is_climax_candle(open_price, high, low, close)


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
            opens_raw = df["Open"].tolist()
            volumes_raw = df["Volume"].tolist()
            dates_idx_raw = df.index
        except Exception:
            continue
        keep = [i for i, v in enumerate(volumes_raw) if v and v > 0]
        if len(keep) != len(volumes_raw):
            closes = [closes_raw[i] for i in keep]
            lows = [lows_raw[i] for i in keep]
            highs = [highs_raw[i] for i in keep]
            opens = [opens_raw[i] for i in keep]
            volumes = [volumes_raw[i] for i in keep]
            dates_idx = [dates_idx_raw[i] for i in keep]
        else:
            closes, lows, highs, opens, volumes = closes_raw, lows_raw, highs_raw, opens_raw, volumes_raw
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
            score = extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)
            score = max(-5, min(5, score))
            if score < 2:
                continue  # 강한이김(>=2) 모집단만

            avg_vol20 = _avg_volume_20d(volumes[:entry_idx])
            is_climax = _is_selling_climax(volumes[entry_idx], avg_vol20, opens[entry_idx],
                                            highs[entry_idx], lows[entry_idx], closes[entry_idx])

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]

            all_rows.append({"reached": reached, "d5": d5_pct, "date": entry_date, "is_climax": is_climax})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}", file=sys.stderr)

    lines = ["저울 강한이김(>=2) 모집단에서 '투매반전(Selling Climax)' 부스터 재검증"
             " -- 저울은 손절 없음을 채택했으므로 ZZ가 우려한 손절-휩쏘 문제와 무관하게 순수 측정",
             f"전체 표본 n={len(all_rows)}", ""]

    lines.append("=== 투매반전 여부(순수분리) ===")
    for label, cond in [("투매반전(대량거래량+반전캔들)", lambda r: r["is_climax"]),
                         ("plain", lambda r: not r["is_climax"])]:
        rows = [r for r in all_rows if cond(r)]
        lines.append(f"{label}: {summarize(rows)}")
    lines.append("")

    dates_sorted = sorted(r["date"] for r in all_rows)
    if dates_sorted:
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 재현성 (전반부 vs 후반부, 기준일 {mid}) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            lines.append(f"-- {period_label} --")
            for label, cond in [("투매반전", lambda r: r["is_climax"]), ("plain", lambda r: not r["is_climax"])]:
                rows = [r for r in all_rows if cond(r) and cond_date(r["date"])]
                lines.append(f"  {label}: {summarize(rows)}")

    with open("climax_boost_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
