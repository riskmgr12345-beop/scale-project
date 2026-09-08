"""2026-09-08 사용자 질문("저울에서 추천종목을 3% 자동이가 추적해야 하지 않나?") -- 지금
자동이는 안전박스에 뜬 날(D+1 종가) 딱 하루만 3%다리확정 여부를 확인하고, 그날 미확정이면
그 후보는 그냥 버려진다(render_scale_report.get_safe_box_rows는 "오늘의 fresh 강한이김
신호"만 반환하는 구조라, 내일 그 종목이 다시 fresh 신호로 뜨지 않는 한 자동이가 다시 보지
않음 -- 즉 "추적"이 아니라 "1회성 확인"). 이 검증은 "며칠간 계속 지켜보다가 3%확정되는 날
바로 사는" 방식이 지금의 "D+1 하루만 확인, 안 되면 영구 폐기" 방식보다 나은지 확인한다.

방법: 각 터치신호(score>=2)에 대해 entry_idx+1일부터 시작해 최대 max_wait_days 동안 매일
그날 종가까지의 지그재그를 다시 계산 -- 그 창 안에서 "처음 3%확정되는 날"을 찾아 그날 종가에
매수한 것으로 취급(못 찾으면 신호 폐기, 지금 방식과 동일하게 자금 안 묶임). hold_days=5(현재
자동이 설정) 그대로 유지."""
import pickle
import statistics
import sys

import scale_validation_test as svt
from scale_validation_test import (
    zigzag_swings, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, CACHE_PATH,
)
svt.MIN_DEPTH = 10.0
find_touch_entries = svt.find_touch_entries

from dynamic_sizing_test import run_dynamic_simulation

HOLD_DAYS = 5


def collect_with_wait(max_wait_days):
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

            confirmed_day = None
            for wait in range(1, max_wait_days + 1):
                e = entry_idx + wait
                if e >= n or not closes[e]:
                    break
                swings_3 = zigzag_swings(closes[: e + 1], threshold=0.03)
                if len(swings_3) >= 2 and swings_3[-1][1] > swings_3[-2][1]:
                    confirmed_day = e
                    break
            if confirmed_day is None:
                continue
            x1 = confirmed_day + HOLD_DAYS
            if x1 >= n or not closes[confirmed_day]:
                continue
            signals.append({
                "name": name, "score": score, "depth_pct": depth_pct,
                "entry_date": dates_idx[confirmed_day], "exit_date": dates_idx[x1],
                "entry_price": closes[confirmed_day], "exit_price": closes[x1],
                "wait_days": confirmed_day - entry_idx,
            })
    signals.sort(key=lambda s: (s["entry_date"], -s["score"], -s["depth_pct"]))
    return signals


if __name__ == "__main__":
    lines = ["안전박스 후보 추적창(watch window) 검증 -- D+1 하루만 확인(현재) vs 며칠 더 지켜보다 "
              "3%확정되는 날 매수(추적), hold_days=5 고정",
              ""]

    for max_wait in [1, 2, 3, 5, 7, 10]:
        signals = collect_with_wait(max_wait)
        rets = [(s["exit_price"] / s["entry_price"] - 1) * 100 for s in signals]
        n = len(rets)
        mean = statistics.mean(rets) if n else 0
        reach = sum(1 for r in rets if r > 0) / n * 100 if n else 0
        wait_dist = statistics.mean(s["wait_days"] for s in signals) if n else 0
        r = run_dynamic_simulation(signals, 10, max_per_position_frac=0.15)
        label = "현재(D+1만)" if max_wait == 1 else f"최대{max_wait}일 추적"
        lines.append(f"=== {label} ===")
        lines.append(f"신호수 n={n}, 평균대기{wait_dist:.2f}일, 평균수익{mean:+.2f}%, 도달률{reach:.1f}%")
        lines.append(f"체결{r['taken']}건, 총수익{r['total_return_pct']:+.1f}%, CAGR{r['cagr']:+.1f}%, MDD{r['mdd']:.1f}%")
        base_dates = sorted(s["entry_date"] for s in signals)
        if base_dates:
            mid = base_dates[len(base_dates) // 2]
            sub_e = [s for s in signals if s["entry_date"] < mid]
            sub_l = [s for s in signals if s["entry_date"] >= mid]
            re = run_dynamic_simulation(sub_e, 10, max_per_position_frac=0.15)
            rl = run_dynamic_simulation(sub_l, 10, max_per_position_frac=0.15)
            lines.append(f"시기분할(기준{mid.date()}): 전반부{re['total_return_pct']:+.1f}% | 후반부{rl['total_return_pct']:+.1f}%")
        lines.append("")
        print(f"{label} 완료: n={n}", file=sys.stderr)

    with open("watch_window_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
