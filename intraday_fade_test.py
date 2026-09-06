"""2026-09-07 사용자 질문("개장 시작에 올랐다가 빠지는 경우가 많잖아, 오르는 종목과 오르다
빠지는 종목의 차이점이 있나? 확률은?") -- 저울은 분봉(장중 틱) 데이터가 없어 "몇 시에 얼마나
올랐다가 몇 시에 빠졌는지"는 직접 못 보지만, 일봉 시가/고가/종가로 "그날 장중 고점 대비
종가가 얼마나 밀렸는지"(intraday_fade_pct)는 계산 가능하다 -- "장중엔 올랐는데 종가는 밀린
날"과 "장중 고점 근처에서 종가가 마감된 날"을 구분해서, 터치 당일의 이 패턴이 이후 5일
수익률과 관계있는지 순수분리로 검증한다.

정의: intraday_fade_pct = (day_high - close) / day_high * 100 (클수록 "올랐다가 많이 빠짐").
scale_validation_test.py의 강한이김(>=2) 모집단에서, 터치 당일의 fade_pct로 상위/하위를
나눠 비교. 시기분할 재현성 확인은 이 세션의 다른 검증과 동일 기준."""
import pickle
import statistics
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH, summarize,
)

FADE_HIGH_THRESHOLD = 3.0  # 장중고점 대비 종가 3%p+ 밀리면 "올랐다가 빠짐"으로 분류


if __name__ == "__main__":
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    all_rows = []
    skipped_short = 0
    for name, df in cache.items():
        try:
            closes_raw = df["Close"].tolist()
            lows_raw = df["Low"].tolist()
            highs_raw = df["High"].tolist()
            volumes_raw = df["Volume"].tolist()
            dates_idx_raw = df.index
        except Exception:
            continue
        keep = [i for i, v in enumerate(volumes_raw) if v and v > 0]
        if len(keep) != len(volumes_raw):
            closes = [closes_raw[i] for i in keep]
            lows = [lows_raw[i] for i in keep]
            highs = [highs_raw[i] for i in keep]
            volumes = [volumes_raw[i] for i in keep]
            dates_idx = [dates_idx_raw[i] for i in keep]
        else:
            closes, lows, highs, volumes = closes_raw, lows_raw, highs_raw, volumes_raw
            dates_idx = list(dates_idx_raw)
        if len(closes) < 60:
            skipped_short += 1
            continue

        for entry_idx in find_touch_entries(closes, lows):
            if entry_idx + 1 >= len(closes):
                continue
            entry_price = closes[entry_idx]
            day_low = lows[entry_idx]
            day_high = highs[entry_idx]
            if not day_low or not entry_price or not day_high:
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

            fade_pct = (day_high - entry_price) / day_high * 100 if day_high else 0.0
            high_fade = fade_pct >= FADE_HIGH_THRESHOLD

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]

            all_rows.append({"reached": reached, "d5": d5_pct, "date": entry_date,
                              "high_fade": high_fade, "fade_pct": fade_pct})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}", file=sys.stderr)

    lines = ["저울 강한이김(>=2) 모집단에서 '터치 당일 장중고점 대비 종가 밀림' 재검증",
             f"전체 표본 n={len(all_rows)}", ""]

    lines.append(f"=== 밀림 여부(순수분리, 기준 {FADE_HIGH_THRESHOLD}%p) ===")
    for label, cond in [(f"올랐다가 빠짐(밀림>={FADE_HIGH_THRESHOLD}%p)", lambda r: r["high_fade"]),
                         ("고점 근처 마감(밀림 적음)", lambda r: not r["high_fade"])]:
        rows = [r for r in all_rows if cond(r)]
        lines.append(f"{label}: {summarize(rows)}")
    lines.append("")

    avg_fade_all = statistics.mean(r["fade_pct"] for r in all_rows) if all_rows else 0
    lines.append(f"전체 평균 밀림폭: {avg_fade_all:.2f}%p")
    lines.append("")

    dates_sorted = sorted(r["date"] for r in all_rows)
    if dates_sorted:
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 재현성 (전반부 vs 후반부, 기준일 {mid}) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            lines.append(f"-- {period_label} --")
            for label, cond in [("올랐다가 빠짐", lambda r: r["high_fade"]),
                                 ("고점 근처 마감", lambda r: not r["high_fade"])]:
                rows = [r for r in all_rows if cond(r) and cond_date(r["date"])]
                lines.append(f"  {label}: {summarize(rows)}")

    with open("intraday_fade_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
