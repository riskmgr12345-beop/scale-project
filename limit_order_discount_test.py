"""2026-09-07 사용자 요청("종가보다 몇% 낮은 가격에 지정가 주문을 걸어두면 체결율과
이후수익률은?") -- 저가매수(이론상 최선, low_entry_vs_close_entry_test.py)는 실행 불가능한
수치였다(장 마감 후에야 그날 저가를 알 수 있음). 이건 실제로 실행 가능한 버전: 저울이 신호를
낸 날(D) 종가 대비 X% 낮은 가격에 다음날(D+1) 지정가 매수를 걸어두면 -- 그 지정가가 실제로
체결되는 비율(D+1 저가가 지정가 이하로 내려온 비율)과, 체결됐을 때 그 지정가를 기준으로 한
5일 후 수익률을 확인한다.

강한이김(>=2) 모집단(production과 동일 정의)에서 할인폭(1%/2%/3%/5%)별로 체결율과 결과를
비교, 기존 방식(D일 종가에 바로 매수)과 대조한다."""
import pickle
import statistics
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH, summarize,
)

DISCOUNTS = [0.0, 0.01, 0.02, 0.03, 0.05]  # 0.0 = 기존 방식(당일 종가 매수, 대조군)


if __name__ == "__main__":
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    # discount별로 표본을 따로 쌓는다
    buckets = {d: [] for d in DISCOUNTS}
    n_signals = 0
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
            # 신호 인식일(entry_idx)의 종가로 강한이김(>=2) 여부 판정 -- production과 동일
            if entry_idx + 1 >= len(closes):
                continue
            signal_close = closes[entry_idx]
            day_low = lows[entry_idx]
            if not day_low or not signal_close:
                continue
            same_day_recovery = (signal_close - day_low) / day_low * 100
            risk_flag = same_day_recovery >= RISK_RECOVERY_MIN
            vr = volume_ratio_at(volumes, entry_idx)
            fast_rev = recent_fast_reversal_active(closes, entry_idx)
            extra = zz_extra_score(vr, fast_rev)
            score = max(-5, min(5, extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)))
            if score < 2:
                continue
            n_signals += 1

            # D+1일에 지정가 주문 시도. 기존 방식(discount=0.0)은 D일 종가에 바로 매수(호라이즌은
            # D+1..D+5), 나머지는 D+1일 저가가 지정가 이하로 내려오면 그 지정가에 체결(호라이즌은
            # D+2..D+6). 두 방식의 "며칠 뒤 결과"를 같은 기준(체결/매수 다음날부터 5거래일)으로
            # 맞추기 위해 discount=0일 때도 매수일 자체는 제외하고 그 다음날부터 5일을 본다(기존
            # scale_validation_test.py와 동일 관례).
            base_horizon = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(base_horizon) < HORIZON:
                continue
            entry_date = dates_idx[entry_idx]

            for discount in DISCOUNTS:
                if discount == 0.0:
                    entry_price = signal_close
                    horizon_closes = base_horizon
                    filled = True
                else:
                    if entry_idx + 1 + HORIZON >= len(closes):
                        continue
                    next_day_low = lows[entry_idx + 1]
                    limit_price = signal_close * (1 - discount)
                    filled = next_day_low is not None and next_day_low <= limit_price
                    if not filled:
                        continue
                    entry_price = limit_price
                    horizon_closes = closes[entry_idx + 2: entry_idx + 2 + HORIZON]
                    if len(horizon_closes) < HORIZON:
                        continue

                reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
                d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
                buckets[discount].append({"reached": reached, "d5": d5_pct, "date": entry_date})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, 강한이김 신호 총수: {n_signals}", file=sys.stderr)

    lines = ["강한이김(>=2) 신호에 대해, 신호일 종가 대비 할인된 지정가로 다음날 매수 시도",
             f"강한이김 신호 총수(모수): {n_signals}", ""]

    for discount in DISCOUNTS:
        rows = buckets[discount]
        fill_rate = len(rows) / n_signals * 100 if n_signals else 0
        label = "기존 방식(당일 종가 즉시매수)" if discount == 0.0 else f"종가 대비 -{discount*100:.0f}% 지정가(D+1 체결 시도)"
        lines.append(f"[{label}]")
        lines.append(f"  체결율: {fill_rate:.1f}% ({len(rows)}/{n_signals})")
        lines.append(f"  체결된 것만: {summarize(rows)}")
        lines.append("")

    lines.append("=== 시기분할 재현성 (할인 미적용 vs 2% 할인, 기준일은 각 표본의 중앙값) ===")
    for discount in [0.0, 0.02]:
        rows = buckets[discount]
        if not rows:
            continue
        dates_sorted = sorted(r["date"] for r in rows)
        mid = dates_sorted[len(dates_sorted) // 2]
        label = "기존 방식" if discount == 0.0 else "2% 할인 지정가"
        lines.append(f"-- {label} (기준일 {mid}) --")
        for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            sub = [r for r in rows if cond_date(r["date"])]
            lines.append(f"  {period_label}: {summarize(sub)}")

    with open("limit_order_discount_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
