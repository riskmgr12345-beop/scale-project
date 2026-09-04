"""2026-09-04 사용자 요청("모든종목에서 분석을 해보려해 줄다리기로" -> "줄다리기랑 혼선이
올수있으니 여기서는 프로젝트를 저울로 한다") -- V3의 ZZ 되돌림 전략에서 49종목/8년으로만
검증된 "줄다리기" 부스터-위험신호 공식을, 코스피+코스닥 전체(약 2,700종목)에 그대로 적용해도
같은 방향(강한이김>=2점이 더 낫다)이 재현되는지 먼저 검증한다. "저울"이라는 새 이름을 쓰는 건
이 넓은 유니버스에서 검증된 값이 49종목의 "줄다리기"와 다를 수 있어서(콜라의 top-250 시가총액
필터가 유니버스를 좁혀서 신호가 완전히 무너졌던 전례 -- 이번엔 반대로 유니버스를 넓히는 것도
같은 이유로 재검증 없이 그대로 못 믿는다) 미리 구분해두기 위함.

데이터: _상한가전조연구/research_cache/limitup_ohlcv_cache.pkl(콜라 프로젝트가 매주 자동갱신하는
코스피+코스닥 전체 캐시, 읽기전용 재사용 -- V3가 콜라 캐시를 읽기전용으로 쓰는 기존 원칙과 같은
방향). 2024-09-03~2026-09-03(2년)만 있어서 8년 시기분할은 못 하고, 전반부/후반부 1년씩 나눠
재현성만 확인한다.
"""
import pickle
import statistics
import sys

THRESHOLD = 0.03
MIN_DEPTH = 7.0
RISK_RECOVERY_MIN = 3.0
TUG_OF_WAR_RISK_PENALTY = 3
HORIZON = 5
CACHE_PATH = "../_상한가전조연구/research_cache/limitup_ohlcv_cache.pkl"


def zigzag_swings(closes, threshold=THRESHOLD):
    if len(closes) < 2:
        return []
    swings = [(0, closes[0])]
    direction = None
    extreme_idx, extreme_price = 0, closes[0]
    for i in range(1, len(closes)):
        c = closes[i]
        if direction is None:
            if c >= extreme_price * (1 + threshold):
                direction = "up"
            elif c <= extreme_price * (1 - threshold):
                direction = "down"
            if direction:
                swings.append((i, c))
                extreme_idx, extreme_price = i, c
            elif c > extreme_price:
                extreme_idx, extreme_price = i, c
            elif c < extreme_price:
                extreme_idx, extreme_price = i, c
        elif direction == "up":
            if c > extreme_price:
                extreme_idx, extreme_price = i, c
            elif c <= extreme_price * (1 - threshold):
                swings[-1] = (extreme_idx, extreme_price)
                direction = "down"
                swings.append((i, c))
                extreme_idx, extreme_price = i, c
        else:
            if c < extreme_price:
                extreme_idx, extreme_price = i, c
            elif c >= extreme_price * (1 + threshold):
                swings[-1] = (extreme_idx, extreme_price)
                direction = "up"
                swings.append((i, c))
                extreme_idx, extreme_price = i, c
    return swings


def find_touch_entries(closes, lows):
    swings = zigzag_swings(closes)
    entries = []
    for i in range(len(swings) - 1):
        idx0, p0 = swings[i]
        idx1, p1 = swings[i + 1]
        if p1 >= p0:
            continue
        for j in range(idx0, idx1 + 1):
            depth = (p0 - lows[j]) / p0 * 100
            if depth >= MIN_DEPTH:
                entries.append(j)
                break
    return entries


def recent_fast_reversal_active(closes, entry_idx, fast_days=5):
    truncated = closes[: entry_idx + 1]
    swings = zigzag_swings(truncated)
    if len(swings) < 4:
        return False
    idx_m4, _ = swings[-4]
    idx_m3, _ = swings[-3]
    return (idx_m3 - idx_m4) <= fast_days


def volume_ratio_at(volumes, entry_idx):
    if entry_idx < 20:
        return None
    base = volumes[entry_idx - 20: entry_idx]
    base_med = statistics.median(base)
    if not base_med:
        return None
    return volumes[entry_idx] / base_med


def zz_extra_score(vr, fast_rev):
    if vr is None:
        score = 0
    elif vr < 1.0:
        score = -1
    elif vr < 2.0:
        score = 0
    elif vr < 4.0:
        score = 2
    else:
        score = 1
    if fast_rev:
        score += 2
    return score


def summarize(rows):
    n = len(rows)
    if not n:
        return "표본없음"
    reached = sum(r["reached"] for r in rows) / n * 100
    d5 = statistics.mean(r["d5"] for r in rows)
    return f"n={n}, 5일도달률={reached:.1f}%, 5일째평균={d5:+.2f}%"


if __name__ == "__main__":
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    all_rows = []
    skipped_short = 0
    for name, df in cache.items():
        try:
            closes = df["Close"].tolist()
            lows = df["Low"].tolist()
            volumes = df["Volume"].tolist()
            dates_idx = df.index
        except Exception:
            continue
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
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]

            all_rows.append({"score": score, "reached": reached, "d5": d5_pct, "date": entry_date})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}", file=sys.stderr)

    lines = ["코스피+코스닥 전체(2,710종목) '저울' 공식 검증 -- 49종목 '줄다리기'와 같은 로직",
             f"전체 터치 표본 n={len(all_rows)}", ""]

    lines.append("=== 승/무/패 3분류 ===")
    for label, cond in [("이김(양수)", lambda s: s > 0), ("비김(0)", lambda s: s == 0),
                         ("짐(음수)", lambda s: s < 0)]:
        rows = [r for r in all_rows if cond(r["score"])]
        lines.append(f"{label}: {summarize(rows)}")
    lines.append("")

    lines.append("=== 강한이김(>=2) vs 약한이김(==1) vs 나머지 ===")
    for label, cond in [("강한이김(>=2)", lambda s: s >= 2), ("약한이김(==1)", lambda s: s == 1),
                         ("비김(0)", lambda s: s == 0), ("짐(<0)", lambda s: s < 0)]:
        rows = [r for r in all_rows if cond(r["score"])]
        lines.append(f"{label}: {summarize(rows)}")
    lines.append("")

    dates_sorted = sorted(r["date"] for r in all_rows)
    if dates_sorted:
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 재현성 (전반부 vs 후반부, 기준일 {mid.date()}) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            lines.append(f"-- {period_label} --")
            for label, cond in [("강한이김(>=2)", lambda s: s >= 2), ("짐(<0)", lambda s: s < 0)]:
                rows = [r for r in all_rows if cond(r["score"]) and cond_date(r["date"])]
                lines.append(f"  {label}: {summarize(rows)}")

    with open("scale_validation_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
