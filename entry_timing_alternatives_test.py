"""2026-09-08 사용자 요청("매수시점부터 찾자") 이어서 -- next_day_open_entry_test.py에서
자동이의 실제 방식(D+1시가매수)이 백테스트 기준(D종가매수)보다 MDD 악화+시기분할 재현성
붕괴를 보였다. 이번엔 대안 진입시점 후보들을 같이 비교한다:

①기존(D종가매수, 실행불가능한 이론값, 참고용)
②D+1시가매수(자동이 현재 실제 방식)
③D+1종가매수(하루 더 정보를 보고 그날 종가에 진입 -- 여전히 실행가능, 시가보다 하루 늦게 확정)
④D+2시가매수(하루 더 기다렸다가 진입 -- "성급한 진입" 자체가 문제인지 확인)

매도는 전부 "진입일로부터 5거래일 후 종가"로 통일(자동이의 실제 회차 카운트 방식과 일치)."""
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

    variants = {"D종가": [], "D+1시가": [], "D+1종가": [], "D+2시가": []}
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

            swings = zigzag_swings(closes[: entry_idx + 1])
            leg_high = None
            for k in range(len(swings) - 1, -1, -1):
                if swings[k][0] <= entry_idx:
                    leg_high = swings[k][1]
                    break
            depth_pct = (leg_high - day_low) / leg_high * 100 if leg_high else 0.0
            common = {"name": name, "score": score, "depth_pct": depth_pct}

            # ①D종가매수
            if entry_idx + HORIZON < n:
                variants["D종가"].append({
                    **common, "entry_date": dates_idx[entry_idx],
                    "exit_date": dates_idx[entry_idx + HORIZON],
                    "entry_price": entry_price0, "exit_price": closes[entry_idx + HORIZON],
                })

            # ②D+1시가매수
            e1 = entry_idx + 1
            x1 = e1 + HORIZON
            if e1 < n and x1 < n and opens[e1]:
                variants["D+1시가"].append({
                    **common, "entry_date": dates_idx[e1], "exit_date": dates_idx[x1],
                    "entry_price": opens[e1], "exit_price": closes[x1],
                })

            # ③D+1종가매수
            if e1 < n and x1 < n and closes[e1]:
                variants["D+1종가"].append({
                    **common, "entry_date": dates_idx[e1], "exit_date": dates_idx[x1],
                    "entry_price": closes[e1], "exit_price": closes[x1],
                })

            # ④D+2시가매수
            e2 = entry_idx + 2
            x2 = e2 + HORIZON
            if e2 < n and x2 < n and opens[e2]:
                variants["D+2시가"].append({
                    **common, "entry_date": dates_idx[e2], "exit_date": dates_idx[x2],
                    "entry_price": opens[e2], "exit_price": closes[x2],
                })

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, "
          + ", ".join(f"{k}={len(v)}건" for k, v in variants.items()), file=sys.stderr)
    for v in variants.values():
        v.sort(key=lambda s: (s["entry_date"], -s["score"], -s["depth_pct"]))
    return variants


def _simple_avg(signals):
    if not signals:
        return 0.0, 0
    rets = [(s["exit_price"] / s["entry_price"] - 1) * 100 for s in signals]
    return sum(rets) / len(rets), len(rets)


if __name__ == "__main__":
    variants = collect_signals()

    lines = ["진입시점 대안 4종 비교 (매도는 전부 진입일+5거래일 후 종가)", ""]
    for label, signals in variants.items():
        avg, n = _simple_avg(signals)
        lines.append(f"{label}: 건당평균 {avg:+.2f}%(n={n})")
    lines.append("")

    for cap_slots in [10]:
        lines.append(f"########## {cap_slots}슬롯 ##########")
        for label, signals in variants.items():
            r = run_simulation(signals, cap_slots, cap_slots)
            lines.append(f"--- {label} ---")
            lines.append(f"기간: {r['start_date'].date()} ~ {r['end_date'].date()} ({r['years']:.2f}년)")
            lines.append(f"체결: {r['taken']}건, 슬롯부족 누락: {r['missed']}건")
            lines.append(f"시작자산 {STARTING_EQUITY:,.0f}원 -> 최종자산 {r['final_equity']:,.0f}원 "
                          f"(총 {r['total_return_pct']:+.1f}%)")
            lines.append(f"연환산(CAGR): {r['cagr']:+.1f}%" if r["cagr"] is not None else "")
            lines.append(f"최대낙폭(MDD): {r['mdd']:.1f}%")
            lines.append("")

    # 시기분할 (10슬롯 기준, D종가 신호일 기준으로 중앙값 산출)
    lines.append("=== 시기분할 재현성 (10슬롯) ===")
    base_dates = sorted(s["entry_date"] for s in variants["D종가"])
    mid = base_dates[len(base_dates) // 2]
    lines.append(f"기준일: {mid}")
    for label, signals in variants.items():
        for period_label, cond in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            sub = [s for s in signals if cond(s["entry_date"])]
            if not sub:
                continue
            r = run_simulation(sub, 10, 10)
            avg, n = _simple_avg(sub)
            cagr_txt = f"{r['cagr']:+.1f}%" if r["cagr"] is not None else "N/A"
            lines.append(f"[{label}][{period_label}] 신호{n}건 평균{avg:+.2f}%, 체결{r['taken']}건, "
                         f"총수익{r['total_return_pct']:+.1f}%, CAGR{cagr_txt}, MDD{r['mdd']:.1f}%")

    with open("entry_timing_alternatives_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
