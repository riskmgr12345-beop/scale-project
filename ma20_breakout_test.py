"""2026-09-05 사용자 요청("순서대로 시작해") ⑥ZZ 나머지 부스터 3/N: "20일선 첫 돌파" 재검증.
ZZ가 100종목으로 검증한 신호 중 "가장 강력하고 재현성 좋았다"고 평가한 신호(_is_ma20_breakout_
live, render_holding_zigzags.py) -- 어제까지 20일선 아래였다가 오늘 종가가 20일선을 처음
상향돌파. 저울은 DOWN(하락다리) 방향만 다루므로 이 신호(DOWN 전용)만 검증하면 충분 -- 거울상인
"20일선 이탈"은 UP 방향 전용이라 저울 모집단에 해당 사항 없음."""
import pickle
import statistics
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH, summarize,
)

MA20_WINDOW = 20


def _is_ma20_breakout(hist_closes, live_price, window=MA20_WINDOW):
    """ZZ의 _is_ma20_breakout_live를 그대로 복사. hist_closes는 오늘을 포함 안 한 과거 종가
    (마지막이 어제), live_price는 오늘 종가(백테스트라 실시간가 대신 실제 종가 사용)."""
    if len(hist_closes) < window or not live_price:
        return False
    ma_yesterday = sum(hist_closes[-window:]) / window
    if hist_closes[-1] > ma_yesterday:
        return False
    ma_today_est = (sum(hist_closes[-(window - 1):]) + live_price) / window
    return live_price > ma_today_est


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
            score = extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)
            score = max(-5, min(5, score))
            if score < 2:
                continue  # 강한이김(>=2) 모집단만

            breakout = _is_ma20_breakout(closes[:entry_idx], closes[entry_idx])

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]

            all_rows.append({"reached": reached, "d5": d5_pct, "date": entry_date, "breakout": breakout})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}", file=sys.stderr)

    lines = ["저울 강한이김(>=2) 모집단에서 '20일선 첫 돌파' 부스터 재검증",
             f"전체 표본 n={len(all_rows)}", ""]

    lines.append("=== 20일선 돌파 여부(순수분리) ===")
    for label, cond in [("20일선 첫 돌파", lambda r: r["breakout"]),
                         ("plain", lambda r: not r["breakout"])]:
        rows = [r for r in all_rows if cond(r)]
        lines.append(f"{label}: {summarize(rows)}")
    lines.append("")

    dates_sorted = sorted(r["date"] for r in all_rows)
    if dates_sorted:
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 재현성 (전반부 vs 후반부, 기준일 {mid}) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            lines.append(f"-- {period_label} --")
            for label, cond in [("20일선돌파", lambda r: r["breakout"]), ("plain", lambda r: not r["breakout"])]:
                rows = [r for r in all_rows if cond(r) and cond_date(r["date"])]
                lines.append(f"  {label}: {summarize(rows)}")

    with open("ma20_breakout_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
