"""2026-09-08 사용자 요청("자동이의 가장 좋은 수익모델을 검토해줘") -- 지금까지 저울에서
검증한 모든 수치(강한이김 도달률/평균수익, 자산곡선 10슬롯 등)는 전부 "신호일(D) 종가에
즉시매수"를 가정했다. 하지만 자동이(V3의 KIS 모의투자 자동매매)는 저울 리포트가 나온
다음날 아침(D+1) 장시작 직후 시장가로 매수한다 -- 실전에서는 종가를 알아도 그 순간 살
방법이 없으므로 불가피한 차이. 이 실행 갭이 실제 성과에 얼마나 영향을 주는지 이번에
처음으로 검증한다.

비교: ①기존(D종가매수, D+5종가청산) vs ②자동이 실제방식(D+1시가매수, D+1+5종가청산,
자동이의 days_held 카운트와 동일하게 진입일로부터 5회차 청산)."""
import pickle
import sys

import scale_validation_test as svt
from scale_validation_test import (
    zigzag_swings, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH,
)

svt.MIN_DEPTH = 10.0
find_touch_entries = svt.find_touch_entries

from equity_curve_simulation import run_simulation, STARTING_EQUITY


def collect_signals():
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    baseline_signals = []
    nextday_signals = []
    skipped_short = 0
    for name, df in cache.items():
        try:
            opens_raw = df["Open"].tolist()
            closes_raw = df["Close"].tolist()
            lows_raw = df["Low"].tolist()
            volumes_raw = df["Volume"].tolist()
            dates_idx_raw = df.index
        except Exception:
            continue
        keep = [i for i, v in enumerate(volumes_raw) if v and v > 0]
        if len(keep) != len(volumes_raw):
            opens = [opens_raw[i] for i in keep]
            closes = [closes_raw[i] for i in keep]
            lows = [lows_raw[i] for i in keep]
            volumes = [volumes_raw[i] for i in keep]
            dates_idx = [dates_idx_raw[i] for i in keep]
        else:
            opens, closes, lows, volumes = opens_raw, closes_raw, lows_raw, volumes_raw
            dates_idx = list(dates_idx_raw)
        n = len(closes)
        if n < 60:
            skipped_short += 1
            continue

        for entry_idx in find_touch_entries(closes, lows):
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

            swings = zigzag_swings(closes[: entry_idx + 1])
            leg_high = None
            for k in range(len(swings) - 1, -1, -1):
                if swings[k][0] <= entry_idx:
                    leg_high = swings[k][1]
                    break
            depth_pct = (leg_high - day_low) / leg_high * 100 if leg_high else 0.0

            # ①기존(D종가매수)
            if entry_idx + HORIZON < n:
                baseline_signals.append({
                    "entry_date": dates_idx[entry_idx], "exit_date": dates_idx[entry_idx + HORIZON],
                    "name": name, "score": score, "depth_pct": depth_pct,
                    "entry_price": entry_price, "exit_price": closes[entry_idx + HORIZON],
                })

            # ②자동이 실제방식(D+1시가매수, 그로부터 5거래일 후 종가청산)
            entry2_idx = entry_idx + 1
            exit2_idx = entry2_idx + HORIZON
            if entry2_idx < n and exit2_idx < n:
                open_price = opens[entry2_idx]
                if open_price:
                    nextday_signals.append({
                        "entry_date": dates_idx[entry2_idx], "exit_date": dates_idx[exit2_idx],
                        "name": name, "score": score, "depth_pct": depth_pct,
                        "entry_price": open_price, "exit_price": closes[exit2_idx],
                    })

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, "
          f"기존{len(baseline_signals)}건 / 자동이방식{len(nextday_signals)}건", file=sys.stderr)
    baseline_signals.sort(key=lambda s: (s["entry_date"], -s["score"], -s["depth_pct"]))
    nextday_signals.sort(key=lambda s: (s["entry_date"], -s["score"], -s["depth_pct"]))
    return baseline_signals, nextday_signals


def _simple_avg(signals):
    if not signals:
        return 0.0, 0
    rets = [(s["exit_price"] / s["entry_price"] - 1) * 100 for s in signals]
    return sum(rets) / len(rets), len(rets)


if __name__ == "__main__":
    baseline_signals, nextday_signals = collect_signals()

    base_avg, base_n = _simple_avg(baseline_signals)
    next_avg, next_n = _simple_avg(nextday_signals)

    lines = ["기존(D종가매수) vs 자동이 실제방식(D+1시가매수) 비교",
             f"건당 단순평균: 기존 {base_avg:+.2f}%(n={base_n}) vs 자동이방식 {next_avg:+.2f}%(n={next_n})",
             ""]

    for cap_slots in [10, 20]:
        lines.append(f"########## {cap_slots}슬롯 ##########")
        for label, signals in [("기존(D종가매수)", baseline_signals), ("자동이(D+1시가매수)", nextday_signals)]:
            r = run_simulation(signals, cap_slots, cap_slots)
            lines.append(f"--- {label} ---")
            lines.append(f"기간: {r['start_date'].date()} ~ {r['end_date'].date()} ({r['years']:.2f}년)")
            lines.append(f"체결: {r['taken']}건, 슬롯부족 누락: {r['missed']}건")
            lines.append(f"시작자산 {STARTING_EQUITY:,.0f}원 -> 최종자산 {r['final_equity']:,.0f}원 "
                          f"(총 {r['total_return_pct']:+.1f}%)")
            lines.append(f"연환산(CAGR): {r['cagr']:+.1f}%" if r["cagr"] is not None else "")
            lines.append(f"최대낙폭(MDD): {r['mdd']:.1f}%")
            lines.append("")

    # 시기분할 (10슬롯 기준)
    lines.append("=== 시기분할 재현성 (10슬롯) ===")
    base_dates = sorted(s["entry_date"] for s in baseline_signals)
    mid = base_dates[len(base_dates) // 2]
    lines.append(f"기준일: {mid}")
    for label, signals in [("기존(D종가매수)", baseline_signals), ("자동이(D+1시가매수)", nextday_signals)]:
        for period_label, cond in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            sub = [s for s in signals if cond(s["entry_date"])]
            if not sub:
                continue
            r = run_simulation(sub, 10, 10)
            avg, n = _simple_avg(sub)
            cagr_txt = f"{r['cagr']:+.1f}%" if r["cagr"] is not None else "N/A"
            lines.append(f"[{label}][{period_label}] 신호{n}건 평균{avg:+.2f}%, 체결{r['taken']}건, "
                         f"총수익{r['total_return_pct']:+.1f}%, CAGR{cagr_txt}")

    with open("next_day_open_entry_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
