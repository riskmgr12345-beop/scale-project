"""2026-09-05 사용자 요청("순서대로 시작해") ⑦(마지막 항목) 손절 차등화 재검증. ZZ는
100종목/8년(n=4,473) 5일 경로 시뮬레이션으로 "줄다리기 점수>=2(강한이김)는 손절선을 -5%로
풀고, 나머지(<2)는 -2%로 조이면" 균일 -3% 손절(평균 -0.151%)보다 개선(+0.017%)된다고
검증했다(config.py ZZ_TUG_OF_WAR_* 주석 참고). 이 손절은 진입가 기준 고정선이라, 저울이
이미 기각한 "고점대비 트레일링스탑"([[project_scale_project_2026_09_04]] ⑰-2)과는 다른
메커니즘 -- 재검증할 가치가 있다.

단, 저울은 production에서 강한이김(>=2)만 추천하므로, ZZ처럼 "약한이김(<2)도 보유 중인
포지션의 손절 차등"이 의미있으려면 전체 점수대(강한이김/약한이김/비김/짐)를 다 포함해서
검증해야 원래 취지와 맞는다 -- scale_validation_test.py의 전체 모집단(스코어 필터 없음)을
그대로 재사용."""
import pickle
import statistics
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH,
)

STRONG_SCORE_THRESHOLD = 2
STRONG_STOP_PCT = 0.05
WEAK_STOP_PCT = 0.02
UNIFORM_STOP_PCT = 0.03


def _simulate_exit(entry_price, horizon_closes, horizon_lows, stop_pct):
    """진입가 기준 고정 손절 -stop_pct%. 그날 저가가 손절선을 건드리면 그날 손절가(진입가*(1-stop_pct))에
    청산된 것으로 간주(가장 보수적인 가정 -- 실제로는 손절가보다 더 유리하게 체결될 수도 있지만,
    이 세션의 다른 손절 검증(⑰)과 같은 보수적 관례를 따른다). 손절 없이 5일 끝까지 가면
    horizon_closes[-1]로 청산."""
    stop_price = entry_price * (1 - stop_pct)
    for low, close in zip(horizon_lows, horizon_closes):
        if low <= stop_price:
            return (stop_price / entry_price - 1) * 100
    return (horizon_closes[-1] / entry_price - 1) * 100


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

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            horizon_lows = lows[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue

            no_stop_d5 = (horizon_closes[-1] / entry_price - 1) * 100
            uniform_d5 = _simulate_exit(entry_price, horizon_closes, horizon_lows, UNIFORM_STOP_PCT)
            tier_stop_pct = STRONG_STOP_PCT if score >= STRONG_SCORE_THRESHOLD else WEAK_STOP_PCT
            tiered_d5 = _simulate_exit(entry_price, horizon_closes, horizon_lows, tier_stop_pct)
            entry_date = dates_idx[entry_idx]

            all_rows.append({"score": score, "no_stop": no_stop_d5, "uniform": uniform_d5,
                              "tiered": tiered_d5, "date": entry_date})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}", file=sys.stderr)

    def summarize_strategy(rows, key):
        n = len(rows)
        if not n:
            return "표본없음"
        avg = statistics.mean(r[key] for r in rows)
        return f"n={n}, 평균={avg:+.3f}%"

    lines = ["저울 전체 모집단(전 점수대)에서 손절 차등화(강한이김>=2는 -5%, 나머지는 -2%) 재검증",
             f"전체 표본 n={len(all_rows)}", ""]

    lines.append("=== 전체 모집단(스코어 무관, ZZ 원 검증과 동일 범위) ===")
    lines.append(f"손절 없음: {summarize_strategy(all_rows, 'no_stop')}")
    lines.append(f"균일 -3% 손절: {summarize_strategy(all_rows, 'uniform')}")
    lines.append(f"차등 손절(강한이김>=2: -5%, 나머지: -2%): {summarize_strategy(all_rows, 'tiered')}")
    lines.append("")

    lines.append("=== 강한이김(>=2)만 (저울 production이 실제로 추천하는 범위) ===")
    strong_rows = [r for r in all_rows if r["score"] >= STRONG_SCORE_THRESHOLD]
    lines.append(f"손절 없음: {summarize_strategy(strong_rows, 'no_stop')}")
    lines.append(f"균일 -3% 손절: {summarize_strategy(strong_rows, 'uniform')}")
    lines.append(f"차등 손절(-5%, ⑰의 트레일링스탑과 다른 진입가기준 고정): {summarize_strategy(strong_rows, 'tiered')}")
    lines.append("")

    dates_sorted = sorted(r["date"] for r in all_rows)
    if dates_sorted:
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 재현성 (전반부 vs 후반부, 기준일 {mid}, 전체 모집단) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            rows = [r for r in all_rows if cond_date(r["date"])]
            lines.append(f"-- {period_label} (n={len(rows)}) --")
            lines.append(f"  손절없음: {summarize_strategy(rows, 'no_stop')}")
            lines.append(f"  균일-3%: {summarize_strategy(rows, 'uniform')}")
            lines.append(f"  차등손절: {summarize_strategy(rows, 'tiered')}")

    with open("stoploss_tiering_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
