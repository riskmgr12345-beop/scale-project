"""2026-09-08 사용자 제안("3%+ 확정만 쓰고 후보 부족한 날은 자금을 최소만 보유, 모두
비율로 분배?") -- 3%다리확정 필터(entry_timing_alternatives 이어서 발견)를 쓰면 하루에
후보가 10개보다 훨씬 적은 날이 많다(전체 대비 약 21%만 통과). 기존 방식(equity/10 고정
슬롯크기)은 후보가 적은 날 유휴현금을 그대로 남겨둔다 -- 대신 그날 실제로 확보 가능한
후보 수만큼만 유휴현금을 나눠서 배치하면(자금을 놀리지 않고 최대한 굴림) 더 나은지 검증."""
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


def collect_confirmed_signals():
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)
    signals = []
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
        n = len(closes)
        if n < 60:
            continue
        for entry_idx in find_touch_entries(closes, lows):
            entry_price0 = closes[entry_idx]
            day_low = lows[entry_idx]
            if not day_low or not entry_price0:
                continue
            same_day_recovery = (entry_price0 - day_low) / day_low * 100
            risk_flag = same_day_recovery >= RISK_RECOVERY_MIN
            vr = volume_ratio_at(volumes, entry_idx)
            fast_rev = recent_fast_reversal_active(closes, entry_idx)
            extra = zz_extra_score(vr, fast_rev)
            score = max(-5, min(5, extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)))
            if score < 2:
                continue
            swings0 = zigzag_swings(closes[: entry_idx + 1])
            leg_high = None
            for k in range(len(swings0) - 1, -1, -1):
                if swings0[k][0] <= entry_idx:
                    leg_high = swings0[k][1]
                    break
            depth_pct = (leg_high - day_low) / leg_high * 100 if leg_high else 0.0
            e1 = entry_idx + 1
            x1 = e1 + HORIZON
            if e1 >= n or x1 >= n or not closes[e1]:
                continue
            swings_3 = zigzag_swings(closes[: e1 + 1], threshold=0.03)
            confirmed_3 = len(swings_3) >= 2 and swings_3[-1][1] > swings_3[-2][1]
            if not confirmed_3:
                continue
            signals.append({
                "name": name, "score": score, "depth_pct": depth_pct,
                "entry_date": dates_idx[e1], "exit_date": dates_idx[x1],
                "entry_price": closes[e1], "exit_price": closes[x1],
            })
    signals.sort(key=lambda s: (s["entry_date"], -s["score"], -s["depth_pct"]))
    return signals


def run_dynamic_simulation(signals, cap_slots, max_per_position_frac=1.0):
    """cap_slots: 최대 동시보유. 그날 신규후보 수와 빈슬롯 수 중 작은 쪽만큼만 받고,
    idle_cash(보유중이지 않은 자금 전부)를 그 건수로 나눠 배분한다(고정 equity/10이 아님).
    max_per_position_frac: 쏠림 방지용 상한(예: 0.3=한 건에 자산의 최대 30%까지만) -- 1.0이면
    무제한(사용자가 제안한 "모두 비율로 분배" 그대로)."""
    equity = STARTING_EQUITY
    open_positions = []
    taken = 0
    missed = 0
    equity_curve = []

    all_dates = sorted(set(s["entry_date"] for s in signals) | set(s["exit_date"] for s in signals))
    signals_by_date = {}
    for s in signals:
        signals_by_date.setdefault(s["entry_date"], []).append(s)

    for d in all_dates:
        still_open = []
        for pos in open_positions:
            if pos["exit_date"] == d:
                realized = pos["allocated"] * (pos["exit_price"] / pos["entry_price"])
                equity += realized - pos["allocated"]
                equity_curve.append((d, equity))
            else:
                still_open.append(pos)
        open_positions = still_open

        todays = signals_by_date.get(d, [])
        if not todays:
            continue
        empty_slots = cap_slots - len(open_positions)
        if empty_slots <= 0:
            missed += len(todays)
            continue
        take_list = todays[:empty_slots]
        missed += len(todays) - len(take_list)

        idle_cash = equity - sum(p["allocated"] for p in open_positions)
        if idle_cash <= 0 or not take_list:
            missed += len(take_list)
            continue
        per_position = idle_cash / len(take_list)
        cap_amount = equity * max_per_position_frac
        per_position = min(per_position, cap_amount)
        for s in take_list:
            open_positions.append({
                "exit_date": s["exit_date"], "allocated": per_position,
                "entry_price": s["entry_price"], "exit_price": s["exit_price"],
            })
            taken += 1

    for pos in open_positions:
        realized = pos["allocated"] * (pos["exit_price"] / pos["entry_price"])
        equity += realized - pos["allocated"]

    total_return_pct = (equity / STARTING_EQUITY - 1) * 100
    n_days = (all_dates[-1] - all_dates[0]).days if all_dates else 0
    years = n_days / 365.25 if n_days else 0
    cagr = ((equity / STARTING_EQUITY) ** (1 / years) - 1) * 100 if years > 0 else None
    peak = STARTING_EQUITY
    mdd = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        mdd = min(mdd, (eq / peak - 1) * 100)
    return {"taken": taken, "missed": missed, "final_equity": equity,
            "total_return_pct": total_return_pct, "cagr": cagr, "mdd": mdd,
            "start_date": all_dates[0], "end_date": all_dates[-1], "years": years}


if __name__ == "__main__":
    signals = collect_confirmed_signals()
    print(f"3%+확정 신호 총수: {len(signals)}", file=sys.stderr)

    lines = ["3%+다리확정 신호 -- 고정10슬롯분배 vs 동적재배분(유휴현금 전부 그날 후보수로 분할)",
             f"신호 총수: {len(signals)}", ""]

    r_fixed = run_simulation(signals, 10, 10)
    lines.append("=== 기존(고정 equity/10) ===")
    lines.append(f"체결{r_fixed['taken']}건, 총수익{r_fixed['total_return_pct']:+.1f}%, "
                 f"CAGR{r_fixed['cagr']:+.1f}%, MDD{r_fixed['mdd']:.1f}%")
    lines.append("")

    for cap_frac, label in [(1.0, "무제한(제안 그대로)"), (0.3, "상한30%"), (0.15, "상한15%")]:
        r = run_dynamic_simulation(signals, 10, max_per_position_frac=cap_frac)
        lines.append(f"=== 동적재배분({label}) ===")
        lines.append(f"체결{r['taken']}건, 총수익{r['total_return_pct']:+.1f}%, "
                     f"CAGR{r['cagr']:+.1f}%, MDD{r['mdd']:.1f}%")
        lines.append("")

    # 시기분할 (상한없는 버전 기준)
    lines.append("=== 시기분할 (동적재배분, 무제한) ===")
    base_dates = sorted(s["entry_date"] for s in signals)
    mid = base_dates[len(base_dates) // 2]
    lines.append(f"기준일: {mid}")
    for label, cond in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
        sub = [s for s in signals if cond(s["entry_date"])]
        rf = run_simulation(sub, 10, 10)
        rd = run_dynamic_simulation(sub, 10, max_per_position_frac=1.0)
        lines.append(f"[{label}] 고정: 총수익{rf['total_return_pct']:+.1f}% MDD{rf['mdd']:.1f}% | "
                     f"동적: 총수익{rd['total_return_pct']:+.1f}% MDD{rd['mdd']:.1f}%")

    with open("dynamic_sizing_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
