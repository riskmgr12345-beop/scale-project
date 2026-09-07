"""2026-09-08 사용자 제안("하락추세 지지선에서 아랫꼬리가 망치캔들이 생기고 거래량 증가 시
추천") -- risk_flag_isolation_test.py에서 검증한 "아래꼬리"(당일 저가 대비 종가 3%+회복)는
느슨한 정의였다(시가/몸통/윗꼬리를 전혀 안 봄). 이번엔 고전적 캔들패턴 정의 그대로 세 조건을
전부 결합해서 따로 검증한다:

①하락추세: 이미 하락다리 진행중(find_touch_entries의 문턱, MIN_DEPTH=10%) -- 기존과 동일.
②지지선: 오늘 저가가 최근 60거래일 최저가 근처(±5% 이내)인 경우 -- 다리 자체의 시작점
  대비가 아니라(1차 시도했더니 find_touch_entries가 "그 다리의 첫 10%+ 도달일"을 고르는
  구조상 그 시점은 정의상 다리 자체의 신저가일 때가 압도적으로 많아 표본이 0.2%로 사실상
  0에 가까웠음 -- 재설계), 더 넓은 기간(최근 60일)의 지지구간에 있는지로 재정의.
③망치캔들: 몸통(시가-종가 차)은 작고, 아래꼬리는 당일 고저폭의 60%+, 위꼬리는 10% 이하 --
  교과서적 망치형 정의 그대로.
④거래량증가: 20일 중앙값 대비 2배 이상(기존 volume_ratio_at 재사용).

①~④ 전부 만족하는 집단 vs 나머지(①만 만족)를 순수분리로 비교, 5일 도달률/평균수익."""
import pickle
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, HORIZON, CACHE_PATH, summarize,
)

MIN_DEPTH = 10.0
SUPPORT_LOOKBACK = 60       # 지지구간 판단 기준(최근 N거래일 최저가)
SUPPORT_TOLERANCE = 5.0    # 그 최저가 대비 ±5% 이내면 "지지구간"
HAMMER_LOWER_SHADOW_MIN = 0.6   # 아래꼬리 >= 당일 고저폭의 60%
HAMMER_UPPER_SHADOW_MAX = 0.10  # 위꼬리 <= 당일 고저폭의 10%
HAMMER_BODY_MAX = 0.30          # 몸통 <= 당일 고저폭의 30%
VOL_RATIO_MIN = 2.0


def collect():
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    matched, unmatched = [], []
    skipped_short = 0
    for name, df in cache.items():
        try:
            opens_raw = df["Open"].tolist()
            closes_raw = df["Close"].tolist()
            highs_raw = df["High"].tolist()
            lows_raw = df["Low"].tolist()
            volumes_raw = df["Volume"].tolist()
            dates_idx_raw = df.index
        except Exception:
            continue
        keep = [i for i, v in enumerate(volumes_raw) if v and v > 0]
        if len(keep) != len(volumes_raw):
            opens = [opens_raw[i] for i in keep]
            closes = [closes_raw[i] for i in keep]
            highs = [highs_raw[i] for i in keep]
            lows = [lows_raw[i] for i in keep]
            volumes = [volumes_raw[i] for i in keep]
            dates_idx = [dates_idx_raw[i] for i in keep]
        else:
            opens, closes, highs, lows, volumes = opens_raw, closes_raw, highs_raw, lows_raw, volumes_raw
            dates_idx = list(dates_idx_raw)
        n = len(closes)
        if n < 60:
            skipped_short += 1
            continue

        for entry_idx in find_touch_entries(closes, lows):
            if entry_idx + HORIZON >= n:
                continue
            entry_price = closes[entry_idx]
            day_low = lows[entry_idx]
            day_high = highs[entry_idx]
            day_open = opens[entry_idx]
            if not day_low or not entry_price or day_high is None or day_open is None:
                continue

            swings = zigzag_swings(closes[: entry_idx + 1])
            leg_high = None
            for k in range(len(swings) - 1, -1, -1):
                if swings[k][0] <= entry_idx:
                    leg_high = swings[k][1]
                    break
            depth_pct = (leg_high - day_low) / leg_high * 100 if leg_high else 0.0
            if depth_pct < MIN_DEPTH:
                continue
            if entry_idx < SUPPORT_LOOKBACK:
                continue

            # ②지지선: 최근 60거래일(오늘 제외) 최저가 대비 오늘 저가가 근접한지
            support_retest = False
            lookback_low = min(lows[entry_idx - SUPPORT_LOOKBACK:entry_idx])
            if lookback_low:
                support_retest = abs(day_low - lookback_low) / lookback_low * 100 <= SUPPORT_TOLERANCE

            # ③망치캔들
            day_range = day_high - day_low
            if day_range <= 0:
                is_hammer = False
            else:
                body = abs(entry_price - day_open)
                lower_shadow = min(day_open, entry_price) - day_low
                upper_shadow = day_high - max(day_open, entry_price)
                is_hammer = (
                    lower_shadow >= HAMMER_LOWER_SHADOW_MIN * day_range
                    and upper_shadow <= HAMMER_UPPER_SHADOW_MAX * day_range
                    and body <= HAMMER_BODY_MAX * day_range
                )

            # ④거래량증가
            vr = volume_ratio_at(volumes, entry_idx)
            vol_up = vr is not None and vr >= VOL_RATIO_MIN

            exit_price = closes[entry_idx + HORIZON]
            reached = any(
                (c / entry_price - 1) * 100 >= 0
                for c in closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            )
            d5_pct = (exit_price / entry_price - 1) * 100
            row = {"date": dates_idx[entry_idx], "reached": reached, "d5": d5_pct}

            if support_retest and is_hammer and vol_up:
                matched.append(row)
            else:
                unmatched.append(row)

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, "
          f"①②③④ 전부충족 {len(matched)}건 / 나머지 {len(unmatched)}건", file=sys.stderr)
    return matched, unmatched


if __name__ == "__main__":
    matched, unmatched = collect()

    lines = ["하락추세+지지선재테스트+망치캔들+거래량증가 결합 신호 검증",
             f"조건: 지지선재테스트(±{SUPPORT_TOLERANCE}%p)+망치(아래꼬리≥{HAMMER_LOWER_SHADOW_MIN*100:.0f}%,"
             f"위꼬리≤{HAMMER_UPPER_SHADOW_MAX*100:.0f}%,몸통≤{HAMMER_BODY_MAX*100:.0f}%)"
             f"+거래량비≥{VOL_RATIO_MIN}배", "",
             "=== 전체 ===",
             f"①②③④ 전부충족: {summarize(matched)}",
             f"나머지(다리 진행중이지만 조건 미충족): {summarize(unmatched)}", ""]

    if matched:
        dates_sorted = sorted(r["date"] for r in matched)
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 (매칭그룹, 기준일 {mid}) ===")
        for label, cond in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            sub = [r for r in matched if cond(r["date"])]
            lines.append(f"[{label}]: {summarize(sub)}")

    with open("hammer_support_volume_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
