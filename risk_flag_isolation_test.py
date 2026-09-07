"""2026-09-08 사용자 질문("이래(아래) 꼬리털기를 하고 있는 종목은 어때?" -> "난 새롭게
아랫꼬리 종목을 검토하는거야?" -> "해봐") -- 아래꼬리(risk_flag, 당일급반등위험)는 저울이
ZZ의 "줄다리기" 공식을 통째로 포팅해올 때부터 -3점 감점 요인으로 들어있었지만, 고변동(⑥)/
장중반전(㉕)/이중바닥(⑤)과 달리 저울 2,700종목 규모에서 순수분리로 독립검증한 적이 없다.
장중반전(위꼬리)이 "나쁠 것 같다"는 직관과 반대로 좋은 신호였던 전례가 있어서, 아래꼬리도
같은 방식으로 재검증한다.

방법론 주의: risk_flag는 score 공식 안에 이미 -3점으로 박혀있어서(score = extra - 3 if
risk_flag), 기존 "강한이김(score>=2) 모집단을 risk_flag로 나눈다"는 방식(고변동/장중반전
때 썼던 방식)을 그대로 쓰면 편향된다 -- risk_flag=True 쪽만 살아남으려면 extra가 5+여야
하므로(5-3=2), 두 그룹이애초에 다른 extra 분포를 갖게 된다. 그래서 이번엔 감점 적용 전의
raw 점수(extra, zz_extra_score)를 기준으로 모집단을 잡고(extra>=2, 감점 없는 "강한이김
전신"), 그 안에서 risk_flag로만 순수분리한다."""
import pickle
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, HORIZON, CACHE_PATH, summarize,
)

MIN_DEPTH = 10.0


def collect():
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    with_risk, without_risk = [], []
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
            if entry_idx + HORIZON >= len(closes):
                continue
            entry_price = closes[entry_idx]
            day_low = lows[entry_idx]
            if not day_low or not entry_price:
                continue

            # MIN_DEPTH(10%) 문턱은 find_touch_entries가 모듈상수(기본 7.0)로 이미 걸렀으므로
            # 여기서 다리깊이를 직접 재계산해서 10%로 다시 거른다(scale_validation_test의
            # 기본값을 바꾸지 않고 이 스크립트만 10%로 맞추기 위함).
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

            vr = volume_ratio_at(volumes, entry_idx)
            fast_rev = recent_fast_reversal_active(closes, entry_idx)
            extra = zz_extra_score(vr, fast_rev)  # 감점 적용 전 raw 점수
            if extra < 2:
                continue

            exit_price = closes[entry_idx + HORIZON]
            reached = any(
                (c / entry_price - 1) * 100 >= 0
                for c in closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            )
            d5_pct = (exit_price / entry_price - 1) * 100
            row = {"date": dates_idx[entry_idx], "reached": reached, "d5": d5_pct,
                   "extra": extra, "same_day_recovery": same_day_recovery}
            (with_risk if risk_flag else without_risk).append(row)

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, "
          f"아래꼬리(risk_flag) {len(with_risk)}건, 비아래꼬리 {len(without_risk)}건", file=sys.stderr)
    return with_risk, without_risk


if __name__ == "__main__":
    with_risk, without_risk = collect()

    lines = ["아래꼬리(risk_flag=당일급반등위험) 순수분리 검증",
             "감점 적용 전 raw점수(extra)>=2 모집단 안에서 risk_flag로만 분리(score공식 편향 방지)",
             "", "=== 전체 ===",
             f"아래꼬리 있음(risk_flag=True): {summarize(with_risk)}",
             f"아래꼬리 없음(risk_flag=False): {summarize(without_risk)}", ""]

    # extra 점수대별 통제검증(2/3/4/5+) -- 순수 risk_flag 효과인지, extra점수 분포 차이로
    # 인한 착시인지 재확인(장중반전 때 고변동 통제검증했던 것과 같은 원칙)
    lines.append("=== extra 점수대별 통제검증 ===")
    for lo, hi, label in [(2, 3, "extra=2"), (3, 4, "extra=3"), (4, 5, "extra=4"), (5, 999, "extra>=5")]:
        wr = [r for r in with_risk if lo <= r["extra"] < hi]
        wo = [r for r in without_risk if lo <= r["extra"] < hi]
        lines.append(f"[{label}] 아래꼬리있음: {summarize(wr)}")
        lines.append(f"[{label}] 아래꼬리없음: {summarize(wo)}")
    lines.append("")

    lines.append("=== 시기분할 재현성 ===")
    all_dates = sorted(r["date"] for r in with_risk + without_risk)
    mid = all_dates[len(all_dates) // 2]
    lines.append(f"기준일: {mid}")
    for label, group in [("아래꼬리있음", with_risk), ("아래꼬리없음", without_risk)]:
        for period_label, cond in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            sub = [r for r in group if cond(r["date"])]
            lines.append(f"[{label}][{period_label}]: {summarize(sub)}")

    with open("risk_flag_isolation_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
