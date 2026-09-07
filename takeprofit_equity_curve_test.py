"""2026-09-08 사용자 제안("추천 당일 매수, 3~5일 안에 10% 이상 오르면 매도") -- 기존
5일고정보유(손절없음, ⑰에서 채택된 최선)에 "보유중 어느 날이든 장중고가가 +10% 찍으면
그 즉시(그날 목표가에) 조기청산, 5일 안에 못 찍으면 기존대로 5일째 종가청산"이라는 익절
트리거를 추가했을 때 N슬롯 복리 성과가 어떻게 바뀌는지 검증한다.

우려했던 두 힘: (1)꼬리 잘림 -- 5일 평균수익(+1.07%)을 밀어올리는 소수의 큰 승자(10%+ 훨씬
넘게 가는 케이스)를 10%에서 강제로 끊으면 오히려 나빠질 수 있음(⑰-1 "본전즉시매도"가
5일보유보다 나빴던 것과 같은 메커니즘, 이번엔 0% 대신 10%에서 끊는 차이). (2)회전율 이득 --
승자를 일찍 팔면 슬롯이 5일을 안 채우고 비어서 새 신호를 더 빨리 받을 수 있음(2번
지정가할인의 "체결 자체가 준다"와 반대로, 여기는 체결수는 그대로고 보유기간만 짧아짐).
두 힘 중 뭐가 이기는지는 실제로 돌려봐야 확인 가능 -- discount_equity_curve_test.py와
같은 N슬롯 로테이션 복리 프레임(equity_curve_simulation.py)을 재사용한다."""
import pickle
import sys

import scale_validation_test as svt
from scale_validation_test import (
    zigzag_swings, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH, summarize,
)

svt.MIN_DEPTH = 10.0
find_touch_entries = svt.find_touch_entries

from equity_curve_simulation import run_simulation, STARTING_EQUITY

TARGET_PCT = 10.0  # +10% 조기익절 트리거


def collect_signals(use_takeprofit):
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    signals = []
    early_exit_count = 0
    skipped_short = 0
    for name, df in cache.items():
        try:
            closes_raw = df["Close"].tolist()
            highs_raw = df["High"].tolist()
            lows_raw = df["Low"].tolist()
            volumes_raw = df["Volume"].tolist()
            dates_idx_raw = df.index
        except Exception:
            continue
        keep = [i for i, v in enumerate(volumes_raw) if v and v > 0]
        if len(keep) != len(volumes_raw):
            closes = [closes_raw[i] for i in keep]
            highs = [highs_raw[i] for i in keep]
            lows = [lows_raw[i] for i in keep]
            volumes = [volumes_raw[i] for i in keep]
            dates_idx = [dates_idx_raw[i] for i in keep]
        else:
            closes, lows, volumes = closes_raw, lows_raw, volumes_raw
            highs = highs_raw
            dates_idx = list(dates_idx_raw)
        if len(closes) < 60:
            skipped_short += 1
            continue

        for entry_idx in find_touch_entries(closes, lows):
            if entry_idx + HORIZON >= len(closes):
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

            swings = zigzag_swings(closes[: entry_idx + 1])
            leg_high = None
            for k in range(len(swings) - 1, -1, -1):
                if swings[k][0] <= entry_idx:
                    leg_high = swings[k][1]
                    break
            depth_pct = (leg_high - day_low) / leg_high * 100 if leg_high else 0.0

            exit_idx = entry_idx + HORIZON
            exit_price = closes[exit_idx]
            exit_date = dates_idx[exit_idx]

            if use_takeprofit:
                target_price = entry_price * (1 + TARGET_PCT / 100)
                for j in range(entry_idx + 1, exit_idx + 1):
                    if highs[j] is not None and highs[j] >= target_price:
                        exit_price = target_price
                        exit_date = dates_idx[j]
                        early_exit_count += 1
                        break

            signals.append({
                "entry_date": dates_idx[entry_idx], "exit_date": exit_date,
                "name": name, "score": score, "depth_pct": depth_pct,
                "entry_price": entry_price, "exit_price": exit_price,
            })

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, 강한이김 신호 총수: {len(signals)}, "
          f"조기익절 발동: {early_exit_count}건", file=sys.stderr)
    signals.sort(key=lambda s: (s["entry_date"], -s["score"], -s["depth_pct"]))
    return signals


def population_avg(signals):
    rets = [(s["exit_price"] / s["entry_price"] - 1) * 100 for s in signals]
    return sum(rets) / len(rets) if rets else 0.0, len(rets)


if __name__ == "__main__":
    baseline_signals = collect_signals(use_takeprofit=False)
    tp_signals = collect_signals(use_takeprofit=True)

    base_avg, base_n = population_avg(baseline_signals)
    tp_avg, tp_n = population_avg(tp_signals)

    lines = ["+10% 조기익절 트리거 검증 (기존 5일고정보유 vs 5일 내 +10% 찍으면 조기청산)",
             f"강한이김(>=2) 신호 총수: {base_n}", "",
             "=== 건당 단순평균 (population, N슬롯 무관) ===",
             f"기존(5일고정): 평균 {base_avg:+.2f}%",
             f"+10%조기익절: 평균 {tp_avg:+.2f}%", ""]

    for cap_slots in [10, 20]:
        lines.append(f"########## {cap_slots}슬롯 ##########")
        for label, signals in [("기존(5일고정)", baseline_signals), ("+10%조기익절", tp_signals)]:
            r = run_simulation(signals, cap_slots, cap_slots)
            lines.append(f"--- {label} ---")
            lines.append(f"기간: {r['start_date'].date()} ~ {r['end_date'].date()} ({r['years']:.2f}년)")
            lines.append(f"체결: {r['taken']}건, 슬롯부족 누락: {r['missed']}건")
            lines.append(f"시작자산 {STARTING_EQUITY:,.0f}원 -> 최종자산 {r['final_equity']:,.0f}원 "
                          f"(총 {r['total_return_pct']:+.1f}%)")
            lines.append(f"연환산(CAGR): {r['cagr']:+.1f}%" if r["cagr"] is not None else "")
            lines.append(f"최대낙폭(MDD): {r['mdd']:.1f}%")
            lines.append("")

    # 시기분할 재현성 (10슬롯 기준)
    lines.append("=== 시기분할 재현성 (10슬롯) ===")
    base_dates = sorted(s["entry_date"] for s in baseline_signals)
    mid = base_dates[len(base_dates) // 2]
    lines.append(f"기준일: {mid}")
    for label, signals in [("기존(5일고정)", baseline_signals), ("+10%조기익절", tp_signals)]:
        for period_label, cond in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            sub = [s for s in signals if cond(s["entry_date"])]
            if not sub:
                continue
            r = run_simulation(sub, 10, 10)
            avg, n = population_avg(sub)
            cagr_txt = f"{r['cagr']:+.1f}%" if r["cagr"] is not None else "N/A"
            lines.append(f"[{label}][{period_label}] 신호{n}건 평균{avg:+.2f}%, 체결{r['taken']}건, "
                          f"총수익{r['total_return_pct']:+.1f}%, CAGR{cagr_txt}")

    with open("takeprofit_equity_curve_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
