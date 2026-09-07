"""2026-09-08 사용자 실계좌 실현손익 상위 6종목(SHD/두산에너빌리티/원풍물산/SK이노베이션/
오가닉티코스메틱/포스코퓨처엠)을 매수시점 OHLCV로 직접 뜯어본 결과, 5/6이 거의 같은 각본을
공유했다: "최근 며칠~2주 사이 크게(±15~150%) 움직인 고변동성 종목이, 조정/급락을 보이는 날
거래량이 평소의 3배+ 터지면서 반전 -- 그 시점에 매수해서 1~2일 안에 짧게 먹고 나옴."

저울의 기존 신호(다리 되돌림 7~15%+거래량비, 5일보유)와 개념은 같지만 규모(되돌림 폭)와
보유기간(1~2일)이 다르다. 이걸 콜라 캐시(코스피+코스닥 전체) 전체에서 순수분리+시기분할로
검증한다.

신호 정의(둘 다 만족해야 함, 룩어헤드 없음):
- pullback_pct: 최근 15거래일(D-15~D-1) 최고종가 대비 오늘(D) 종가 하락폭 >= PULLBACK_MIN(%)
- vol_ratio: 오늘(D) 거래량 / 최근 20거래일(D-20~D-1) 평균거래량 >= VOL_RATIO_MIN(배)

진입: D일 종가 매수. 청산: D+1일 종가(1일보유), D+2일 종가(2일보유) 각각 비교, 기존 저울
5일보유(강한이김>=2, MIN_DEPTH=10%)와도 병기해서 대조."""
import pickle
import sys

CACHE_PATH = "../_상한가전조연구/research_cache/limitup_ohlcv_cache.pkl"

PULLBACK_MIN = 15.0   # 최근 15일 고점 대비 하락폭(%)
VOL_RATIO_MIN = 3.0   # 최근 20일 평균거래량 대비 배수
LOOKBACK_HIGH = 15
LOOKBACK_VOL = 20


def summarize(rows, key):
    if not rows:
        return "표본없음"
    n = len(rows)
    reach = sum(1 for r in rows if r[key] >= 0) / n * 100
    avg = sum(r[key] for r in rows) / n
    return f"n={n}, 도달률(0%+) {reach:.1f}%, 평균 {avg:+.2f}%"


def collect_signals():
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    rows = []
    skipped_short = 0
    for name, df in cache.items():
        try:
            closes_raw = df["Close"].tolist()
            volumes_raw = df["Volume"].tolist()
            dates_idx_raw = df.index
        except Exception:
            continue
        keep = [i for i, v in enumerate(volumes_raw) if v and v > 0]
        if len(keep) != len(volumes_raw):
            closes = [closes_raw[i] for i in keep]
            volumes = [volumes_raw[i] for i in keep]
            dates_idx = [dates_idx_raw[i] for i in keep]
        else:
            closes, volumes = closes_raw, volumes_raw
            dates_idx = list(dates_idx_raw)
        n = len(closes)
        if n < 60:
            skipped_short += 1
            continue

        for d in range(LOOKBACK_VOL, n - 2):  # D+2까지 필요하니 끝에서 2일 여유
            recent_high = max(closes[d - LOOKBACK_HIGH:d])
            if not recent_high:
                continue
            pullback_pct = (recent_high - closes[d]) / recent_high * 100
            if pullback_pct < PULLBACK_MIN:
                continue
            avg_vol = sum(volumes[d - LOOKBACK_VOL:d]) / LOOKBACK_VOL
            if not avg_vol:
                continue
            vol_ratio = volumes[d] / avg_vol
            if vol_ratio < VOL_RATIO_MIN:
                continue

            entry_price = closes[d]
            ret_1d = (closes[d + 1] / entry_price - 1) * 100
            ret_2d = (closes[d + 2] / entry_price - 1) * 100
            rows.append({
                "name": name, "date": dates_idx[d], "pullback_pct": pullback_pct,
                "vol_ratio": vol_ratio, "ret_1d": ret_1d, "ret_2d": ret_2d,
            })

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, 신호 총수: {len(rows)}", file=sys.stderr)
    rows.sort(key=lambda r: r["date"])
    return rows


if __name__ == "__main__":
    rows = collect_signals()

    lines = [f"극단적 고변동 되돌림+거래량폭증 신호 검증 (조정폭>={PULLBACK_MIN}%, "
             f"거래량비>={VOL_RATIO_MIN}배, 룩백 {LOOKBACK_HIGH}/{LOOKBACK_VOL}일)",
             f"신호 총수: {len(rows)}", "",
             "=== 전체 ===",
             f"1일보유: {summarize(rows, 'ret_1d')}",
             f"2일보유: {summarize(rows, 'ret_2d')}", ""]

    if rows:
        dates_sorted = sorted(r["date"] for r in rows)
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 (기준일 {mid}) ===")
        for period_label, cond in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            sub = [r for r in rows if cond(r["date"])]
            lines.append(f"[{period_label}] 1일보유: {summarize(sub, 'ret_1d')}")
            lines.append(f"[{period_label}] 2일보유: {summarize(sub, 'ret_2d')}")
        lines.append("")

        # 실제 6종목이 어느 정도 강도였는지 참고용으로 상위 표시
        lines.append("=== 참고: pullback_pct 상위 10건 ===")
        for r in sorted(rows, key=lambda r: -r["pullback_pct"])[:10]:
            lines.append(f"  {r['date'].date()} {r['name']} 조정{r['pullback_pct']:.1f}% "
                         f"거래량비{r['vol_ratio']:.1f}배 1일{r['ret_1d']:+.2f}% 2일{r['ret_2d']:+.2f}%")

    with open("extreme_volatility_reversal_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
