"""2026-09-07 사용자 질문("같은날 급반등 위험신호... 그 조짐에 빠질 때 사면 더 유리한거
아닌가?" / "투매반전... 아래꼬리에서 사면 더 수익이 생기는거 아닌가?") -- 지금까지 저울/ZZ의
모든 검증은 "그날 종가에 산다"고 가정했다. 만약 종가 대신 그날 저가(=아래꼬리 끝, 급반등이
일어나기 전 시점) 근처에서 살 수 있었다면 결과가 어떻게 달라지는지 직접 계산해서 확인한다.

**중요한 전제**: 이건 "저울이 앞으로 이렇게 사겠다"는 실행 가능한 전략이 아니라, "종가매수 대비
저가매수가 이론상 얼마나 유리한가"를 보여주는 참고용 계산이다. 저울은 하루 한 번(장 마감 후)
배치로 도는 시스템이라 그날 저가가 얼마였는지는 장이 끝나야 알 수 있다 -- 즉 "오늘이 저가"인
순간을 실시간으로 포착해서 사는 건 이 시스템 구조상 불가능하다(사람이 실시간 호가창을 보고
직접 판단해서 사는 경우에만 실행 가능). 그래도 "종가매수가 실제로 얼마나 손해인지" 정량화하는
가치는 있다."""
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


def summarize_from(rows, price_key):
    """price_key가 가리키는 가격을 진입가로 가정하고 도달률/5일수익 재계산."""
    n = len(rows)
    if not n:
        return "표본없음"
    reached = sum(1 for r in rows if any((c / r[price_key] - 1) * 100 >= 0 for c in r["horizon_closes"])) / n * 100
    d5 = statistics.mean((r["horizon_closes"][-1] / r[price_key] - 1) * 100 for r in rows)
    return f"n={n}, 5일도달률={reached:.1f}%, 5일째평균={d5:+.2f}%"


if __name__ == "__main__":
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    risk_rows = []   # "같은날 급반등(위험신호)" 대상 -- same_day_recovery>=3%
    climax_rows = []  # "투매반전" 대상
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
            raw_score = extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)
            score = max(-5, min(5, raw_score))

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            entry_date = dates_idx[entry_idx]

            # "같은날 급반등" 위험신호 대상 -- risk_flag가 뜬 터치만(강한이김 여부 무관하게,
            # "그 신호 자체"의 저가매수 효과를 보기 위해 score 필터 없이 risk_flag 발생 건 전체)
            if risk_flag:
                risk_rows.append({"close_price": entry_price, "low_price": day_low,
                                   "horizon_closes": horizon_closes, "date": entry_date})

            # 투매반전 대상(강한이김 모집단 안에서, climax_boost_test.py와 동일 조건)
            if score >= 2:
                avg_vol20 = _avg_volume_20d(volumes[:entry_idx])
                is_climax = _is_selling_climax(volumes[entry_idx], avg_vol20, opens[entry_idx],
                                                highs[entry_idx], lows[entry_idx], closes[entry_idx])
                if is_climax:
                    climax_rows.append({"close_price": entry_price, "low_price": day_low,
                                         "horizon_closes": horizon_closes, "date": entry_date})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}", file=sys.stderr)

    lines = ["종가매수 vs 저가매수(이론상) 비교 -- '같은날 급반등(위험신호)' 대상", ""]
    lines.append(f"전체 표본 n={len(risk_rows)}")
    lines.append(f"종가매수(기존 방식): {summarize_from(risk_rows, 'close_price')}")
    lines.append(f"저가매수(이론상 최선): {summarize_from(risk_rows, 'low_price')}")
    lines.append("")

    dates_sorted = sorted(r["date"] for r in risk_rows)
    if dates_sorted:
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 (기준일 {mid}) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            sub = [r for r in risk_rows if cond_date(r["date"])]
            lines.append(f"-- {period_label} --")
            lines.append(f"  종가매수: {summarize_from(sub, 'close_price')}")
            lines.append(f"  저가매수: {summarize_from(sub, 'low_price')}")
    lines.append("")

    lines.append("=" * 60)
    lines.append("종가매수 vs 저가매수(이론상) 비교 -- '투매반전(Selling Climax)' 대상")
    lines.append("")
    lines.append(f"전체 표본 n={len(climax_rows)}")
    lines.append(f"종가매수(기존 방식, climax_boost_test.py와 동일): {summarize_from(climax_rows, 'close_price')}")
    lines.append(f"저가매수(이론상 최선): {summarize_from(climax_rows, 'low_price')}")
    lines.append("")
    dates_sorted2 = sorted(r["date"] for r in climax_rows)
    if dates_sorted2:
        mid2 = dates_sorted2[len(dates_sorted2) // 2]
        lines.append(f"=== 시기분할 (기준일 {mid2}) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid2), ("후반부", lambda d: d >= mid2)]:
            sub = [r for r in climax_rows if cond_date(r["date"])]
            lines.append(f"-- {period_label} --")
            lines.append(f"  종가매수: {summarize_from(sub, 'close_price')}")
            lines.append(f"  저가매수: {summarize_from(sub, 'low_price')}")

    with open("low_entry_vs_close_entry_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
