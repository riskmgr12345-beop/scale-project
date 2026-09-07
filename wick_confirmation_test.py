"""2026-09-08 사용자 제안("조정 -> 장중급락 -> 거래량증가 -> 아래꼬리 -> 종가회복 -> 다음날
저점상승") -- risk_flag_isolation_test.py에서 아래꼬리(risk_flag=True) 자체는 순수분리로도
확실히 나쁜 신호(도달률34.7%/평균-6.40%)임을 확인했다. 사용자는 여기에 "다음날 저점상승"이라는
확인(confirmation) 조건을 하나 더 얹으면 옥석이 가려지는지 묻고 있다 -- 즉, 아래꼬리 신호가
나온 종목들 중에서도 "다음날 그 저가를 다시 안 깨고 오히려 저점을 높이는" 종목만 골라내면
성과가 나아지는지 검증.

두 갈래로 검증한다: ①이론상(D일 종가에 이미 산 걸로 치고, D+1 저점상승 여부로 사후분류만
해봄 -- 실행 불가능, 참고용) ②실행가능(D+1 저점상승이 확인된 뒤 D+1 종가에 진입 -- 확인
지연비용까지 반영한 현실적 버전)."""
import pickle
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, CACHE_PATH, summarize,
)

MIN_DEPTH = 10.0
FWD_DAYS = 5  # 확인 이후 며칠을 더 볼지(진입 시점 기준 5거래일)


def collect():
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    sof_confirmed, sof_unconfirmed = [], []   # ①이론상: D종가 매수, D+1저점상승 여부로 사후분류
    real_confirmed = []                        # ②실행가능: D+1저점상승 확인 후 D+1종가 매수
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
        n = len(closes)
        if n < 60:
            skipped_short += 1
            continue

        for entry_idx in find_touch_entries(closes, lows):
            # D+1저점상승 확인 + 그 후 FWD_DAYS일을 더 보려면 entry_idx+1+FWD_DAYS까지 필요
            if entry_idx + 1 + FWD_DAYS >= n:
                continue
            entry_price = closes[entry_idx]
            day_low = lows[entry_idx]
            if not day_low or not entry_price:
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
            same_day_recovery = (entry_price - day_low) / day_low * 100
            risk_flag = same_day_recovery >= RISK_RECOVERY_MIN
            if not risk_flag:
                continue  # 이번 검증은 아래꼬리(risk_flag=True) 집단 안에서만
            vr = volume_ratio_at(volumes, entry_idx)
            fast_rev = recent_fast_reversal_active(closes, entry_idx)
            extra = zz_extra_score(vr, fast_rev)
            if extra < 2:
                continue

            next_low = lows[entry_idx + 1]
            confirmed = next_low is not None and next_low > day_low  # 다음날 저점상승(전일저가 안 깨짐)

            # ①이론상: D종가 매수, D+1~D+5 수익률(사후에 confirmed로 분류)
            d5_from_d = (closes[entry_idx + FWD_DAYS] / entry_price - 1) * 100
            reached_from_d = any(
                (c / entry_price - 1) * 100 >= 0
                for c in closes[entry_idx + 1: entry_idx + 1 + FWD_DAYS]
            )
            row_sof = {"date": dates_idx[entry_idx], "reached": reached_from_d, "d5": d5_from_d}
            (sof_confirmed if confirmed else sof_unconfirmed).append(row_sof)

            # ②실행가능: 다음날 저점상승 확인된 것만, D+1 종가에 진입해서 그로부터 FWD_DAYS일
            if confirmed:
                entry2_idx = entry_idx + 1
                entry2_price = closes[entry2_idx]
                exit2_idx = entry2_idx + FWD_DAYS
                if exit2_idx < n:
                    d5_from_d1 = (closes[exit2_idx] / entry2_price - 1) * 100
                    reached_from_d1 = any(
                        (c / entry2_price - 1) * 100 >= 0
                        for c in closes[entry2_idx + 1: exit2_idx + 1]
                    )
                    real_confirmed.append({
                        "date": dates_idx[entry2_idx], "reached": reached_from_d1, "d5": d5_from_d1,
                    })

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, "
          f"아래꼬리 총 {len(sof_confirmed)+len(sof_unconfirmed)}건 "
          f"(다음날저점상승 {len(sof_confirmed)}건 / 안 됨 {len(sof_unconfirmed)}건)", file=sys.stderr)
    return sof_confirmed, sof_unconfirmed, real_confirmed


if __name__ == "__main__":
    sof_confirmed, sof_unconfirmed, real_confirmed = collect()

    lines = ["아래꼬리(risk_flag=True) 집단 안에서 '다음날 저점상승' 확인 효과 검증", "",
             "=== ①이론상(D종가매수, 사후 D+1저점상승 여부로 분류 -- 참고용, 실행불가) ===",
             f"D+1 저점상승(확인됨): {summarize(sof_confirmed)}",
             f"D+1 저점상승 안 됨: {summarize(sof_unconfirmed)}", "",
             "=== ②실행가능(D+1 저점상승 확인 후 D+1 종가에 진입, 그로부터 5일) ===",
             f"확인 후 진입: {summarize(real_confirmed)}", "",
             "=== 대조군(아래꼬리 없는 정상 진입, risk_flag_isolation_test 결과 재인용) ===",
             "아래꼬리없음(D종가매수): n=384, 5일도달률=83.6%, 5일째평균=+4.62%", ""]

    if sof_confirmed:
        dates_sorted = sorted(r["date"] for r in sof_confirmed)
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 (①이론상 확인됨 그룹, 기준일 {mid}) ===")
        for label, cond in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            sub = [r for r in sof_confirmed if cond(r["date"])]
            lines.append(f"[{label}]: {summarize(sub)}")

    if real_confirmed:
        dates_sorted2 = sorted(r["date"] for r in real_confirmed)
        mid2 = dates_sorted2[len(dates_sorted2) // 2]
        lines.append(f"=== 시기분할 (②실행가능 확인후진입, 기준일 {mid2}) ===")
        for label, cond in [("전반부", lambda d: d < mid2), ("후반부", lambda d: d >= mid2)]:
            sub = [r for r in real_confirmed if cond(r["date"])]
            lines.append(f"[{label}]: {summarize(sub)}")

    with open("wick_confirmation_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
