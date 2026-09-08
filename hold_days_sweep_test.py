"""2026-09-08 사용자 요청("보유기간을 한번더 검토해줘 5일이 제일 좋은지?") -- HOLD_DAYS=5는
이 프로젝트 최초(scale_validation_test.HORIZON=5, "5일 내 본전 도달 확률")부터 그냥 주어진
값이었고, 다른 보유기간과 직접 비교(스윕)해 본 적이 없었다(지금까지의 모든 관련 연구는
"5일 고정보유 위에 조기청산/손절을 얹을지"만 검증했지, 보유기간 자체를 바꿔보지 않았음).

오늘 확정한 자동이의 실제 매매규칙(entry_timing_alternatives_test.py의 D+1종가매수 +
3%다리확정 필터 + dynamic_sizing_test.py의 15%상한 동적사이징, jobs/auto_scale_trader.py에
반영됨)을 그대로 쓰되, 보유기간(청산까지 며칠)만 3~15일로 바꿔가며 같은 방식으로 재검증한다.
진입 필터(3%다리확정)는 D+1 종가 시점 데이터로 고정 -- 보유기간과 무관.
"""
import pickle
import sys

import scale_validation_test as svt
from scale_validation_test import (
    zigzag_swings, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, CACHE_PATH,
)
svt.MIN_DEPTH = 10.0
find_touch_entries = svt.find_touch_entries

from equity_curve_simulation import STARTING_EQUITY
from dynamic_sizing_test import run_dynamic_simulation

CANDIDATE_HOLD_DAYS = [3, 4, 5, 6, 7, 8, 10, 15]


def collect_confirmed_signals_for_hold(hold_days):
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
            x1 = e1 + hold_days
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


if __name__ == "__main__":
    lines = ["보유기간(HOLD_DAYS) 스윕 -- 3%다리확정+D+1종가매수+15%상한 동적사이징(자동이 실제 규칙) 고정, 청산일만 변경",
              ""]

    results = {}
    for hd in CANDIDATE_HOLD_DAYS:
        signals = collect_confirmed_signals_for_hold(hd)
        r = run_dynamic_simulation(signals, 10, max_per_position_frac=0.15)
        results[hd] = (signals, r)
        lines.append(f"=== 보유 {hd}일 ===")
        lines.append(f"신호수: {len(signals)}, 체결{r['taken']}건, 총수익{r['total_return_pct']:+.1f}%, "
                     f"CAGR{r['cagr']:+.1f}%, MDD{r['mdd']:.1f}%")
        lines.append("")
        print(f"보유{hd}일 완료: 신호{len(signals)}건", file=sys.stderr)

    # 시기분할 재현성 (신호 population 자체가 hold_days마다 달라지므로 각자 기준일로 분할)
    lines.append("=== 시기분할 재현성 ===")
    for hd in CANDIDATE_HOLD_DAYS:
        signals, r_full = results[hd]
        if not signals:
            lines.append(f"[보유{hd}일] 신호 없음")
            continue
        base_dates = sorted(s["entry_date"] for s in signals)
        mid = base_dates[len(base_dates) // 2]
        sub_early = [s for s in signals if s["entry_date"] < mid]
        sub_late = [s for s in signals if s["entry_date"] >= mid]
        r_early = run_dynamic_simulation(sub_early, 10, max_per_position_frac=0.15)
        r_late = run_dynamic_simulation(sub_late, 10, max_per_position_frac=0.15)
        lines.append(f"[보유{hd}일] 기준일{mid.date()} | 전반부 총수익{r_early['total_return_pct']:+.1f}% "
                     f"MDD{r_early['mdd']:.1f}% | 후반부 총수익{r_late['total_return_pct']:+.1f}% "
                     f"MDD{r_late['mdd']:.1f}%")

    with open("hold_days_sweep_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
