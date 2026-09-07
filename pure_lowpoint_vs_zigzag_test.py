"""2026-09-07 사용자 제안("전체를 대상으로 저점이라는 잣대로 먼저 20개를 뽑아보는건?")에서
이어진 검증 -- "52주 저점 근처"라는 것 자체가 저울의 지그재그+거래량 확인 조건 없이도 반등
신호로 통하는지 확인한다. 순수 저점(yr_pos<=임계값)인 날 전부를 "터치"로 보고 5일 후 결과를
집계해서, 저울이 실제로 검증한 강한이김(>=2점, 도달률 73.7%/평균+1.07%)과 비교한다.

핵심 차이: 저울은 "최근 고점에서 지그재그로 확인된 하락다리 + 10%p+ 되돌림 + 거래량비 확인"을
요구하지만, 이 테스트는 "그냥 52주 범위 안에서 몇 %(연중 위치)에 있는지"만 본다 -- 반전
시도(모멘텀) 여부를 전혀 안 따진다는 게 저울과의 핵심 차이."""
import pickle
import statistics
import sys

CACHE_PATH = "../_상한가전조연구/research_cache/limitup_ohlcv_cache.pkl"
HORIZON = 5
YR_WINDOW = 252
LOWPOINT_THRESHOLDS = [5.0, 10.0]  # 52주위치 몇 % 이하를 "저점 근접"으로 볼지


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

    all_rows = {th: [] for th in LOWPOINT_THRESHOLDS}
    skipped_short = 0
    for name, df in cache.items():
        try:
            closes = df["Close"].tolist()
            highs = df["High"].tolist()
            lows = df["Low"].tolist()
            volumes = df["Volume"].tolist()
            dates_idx = list(df.index)
        except Exception:
            continue
        keep = [i for i, v in enumerate(volumes) if v and v > 0]
        if len(keep) != len(volumes):
            closes = [closes[i] for i in keep]
            highs = [highs[i] for i in keep]
            lows = [lows[i] for i in keep]
            dates_idx = [dates_idx[i] for i in keep]
        if len(closes) < YR_WINDOW + HORIZON + 1:
            skipped_short += 1
            continue

        # 매일(252일 이후부터) 52주위치를 계산 -- 룩어헤드 없음(그 시점까지의 과거 252일만 사용)
        for t in range(YR_WINDOW, len(closes) - HORIZON):
            window_highs = highs[t - YR_WINDOW + 1: t + 1]
            window_lows = lows[t - YR_WINDOW + 1: t + 1]
            yr_high = max(window_highs)
            yr_low = min(window_lows)
            if yr_high <= yr_low:
                continue
            cur = closes[t]
            yr_pos = (cur - yr_low) / (yr_high - yr_low) * 100

            for th in LOWPOINT_THRESHOLDS:
                if yr_pos <= th:
                    entry_price = cur
                    horizon_closes = closes[t + 1: t + 1 + HORIZON]
                    reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
                    d5 = (horizon_closes[-1] / entry_price - 1) * 100
                    all_rows[th].append({"reached": reached, "d5": d5, "date": dates_idx[t]})

    print(f"스캔 종목수: {len(cache)}, 데이터부족 제외: {skipped_short}", file=sys.stderr)

    lines = ["순수 '52주 저점 근접' 신호 재검증 (지그재그/거래량 조건 전혀 없음)", ""]
    lines.append("대조군(저울 실측, 참고): 강한이김(>=2점) 도달률 73.7%, 평균 +1.07% (n=12,695)")
    lines.append("")

    for th in LOWPOINT_THRESHOLDS:
        rows = all_rows[th]
        lines.append(f"=== 52주위치 <= {th:.0f}% ===")
        lines.append(f"전체: {summarize(rows)}")
        dates_sorted = sorted(r["date"] for r in rows)
        if dates_sorted:
            mid = dates_sorted[len(dates_sorted) // 2]
            for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
                sub = [r for r in rows if cond_date(r["date"])]
                lines.append(f"  {period_label}(기준일 {mid}): {summarize(sub)}")
        lines.append("")

    with open("pure_lowpoint_vs_zigzag_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
