"""2026-09-07 후속 검증 -- recovery_type_test.py에서 확인한 즉시반등형/추가하락형 사후라벨은
순환논리라 그 자체로는 못 쓴다. 대신 "매수 시점에 이미 아는 정보(저울점수/고변동/장중반전)로
어느 유형이 될지 예측 가능한가"를 확인한다 -- 이건 순환논리가 아니다(예측변수가 라벨보다
먼저 확정됨). 단, 이미 각 신호들이 5일 성과와 직접 연관된 걸 알고 있어서(고변동/장중반전
채택 등) 새로운 정보라기보단 기존 발견의 재확인이 될 가능성이 높다는 점을 미리 밝혀둔다."""
import pickle
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, CACHE_PATH,
)
from recovery_type_test import _leg_end_idx

MIN_RANGE_SAMPLE_DAYS = 60
INTRADAY_FADE_THRESHOLD = 3.0


def _avg_daily_range_pct(highs, lows, closes):
    vals = [(h - l) / c * 100 for h, l, c in zip(highs[:-1], lows[:-1], closes[:-1]) if c]
    if len(vals) < MIN_RANGE_SAMPLE_DAYS:
        return None
    return sum(vals) / len(vals)


if __name__ == "__main__":
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    all_rows = []
    unresolved = 0
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
            closes, highs, lows, volumes = closes_raw, highs_raw, lows_raw, volumes_raw
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

            leg_end = _leg_end_idx(closes, entry_idx)
            if leg_end is None:
                unresolved += 1
                continue
            went_lower = any(lows[j] < day_low for j in range(entry_idx + 1, leg_end + 1))
            recovery_type = "추가하락형" if went_lower else "즉시반등형"

            # 매수 시점에 이미 아는 예측변수들
            avg_range = _avg_daily_range_pct(highs[:entry_idx], lows[:entry_idx], closes[:entry_idx])
            swings = zigzag_swings(closes[: entry_idx + 1])
            leg_high = None
            for k in range(len(swings) - 1, -1, -1):
                if swings[k][0] <= entry_idx:
                    leg_high = swings[k][1]
                    break
            depth_pct = (leg_high - day_low) / leg_high * 100 if leg_high else 0.0
            high_vol = (avg_range is not None and avg_range > 0 and (depth_pct / avg_range) >= 1.0)
            fade_pct = (day_high - entry_price) / day_high * 100 if day_high else 0.0
            intraday_fade = fade_pct >= INTRADAY_FADE_THRESHOLD

            all_rows.append({
                "recovery_type": recovery_type, "score": score,
                "high_vol": high_vol, "intraday_fade": intraday_fade,
            })

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, "
          f"다리 진행중 라벨링 불가: {unresolved}", file=sys.stderr)

    def pct_immediate(rows):
        n = len(rows)
        if not n:
            return "표본없음"
        immediate = sum(1 for r in rows if r["recovery_type"] == "즉시반등형") / n * 100
        return f"n={n}, 즉시반등형 비율={immediate:.1f}%"

    lines = [f"매수시점 예측변수별 '즉시반등형' 비율 (전체 기준선: {pct_immediate(all_rows)})", ""]

    lines.append("=== 저울점수별 ===")
    for sc in [2, 3, 4, 5]:
        rows = [r for r in all_rows if r["score"] == sc]
        lines.append(f"score={sc:+d}: {pct_immediate(rows)}")
    lines.append("")

    lines.append("=== 고변동 여부 ===")
    for label, cond in [("고변동", lambda r: r["high_vol"]), ("저변동", lambda r: not r["high_vol"])]:
        rows = [r for r in all_rows if cond(r)]
        lines.append(f"{label}: {pct_immediate(rows)}")
    lines.append("")

    lines.append("=== 장중반전 여부 ===")
    for label, cond in [("장중반전 있음", lambda r: r["intraday_fade"]), ("없음", lambda r: not r["intraday_fade"])]:
        rows = [r for r in all_rows if cond(r)]
        lines.append(f"{label}: {pct_immediate(rows)}")

    with open("recovery_type_predictors_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
