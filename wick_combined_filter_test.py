"""2026-09-08 사용자 제안("'아래꼬리'를 1차 필터로 잡고, 거래량·종가위치·전일 저점 대비
저점상승을 결합해서 3일 보유 종목을 선별") -- wick_confirmation_test.py에서 "다음날
저점상승 확인 후 진입"만으로는 -1.23%(여전히 마이너스지만 확인 전보다 개선)까지 왔다.
여기에 거래량 강도(vol_ratio)와 종가위치(closing strength, 그날 고저 구간에서 종가가
어디쯤인지)를 추가로 결합해서, 더 좁혀도 되는 스윗스팟이 있는지, 그리고 보유기간을
5일이 아니라 3일로 바꿨을 때 어떤지 검증한다.

파이프라인: ①아래꼬리(risk_flag=True) ->②D+1 저점상승 확인(entry는 D+1 종가) ->
③그 확인일(D+1)의 거래량비/종가위치로 추가 필터/구간화 -> ④그로부터 3일 보유 성과."""
import pickle
import sys
import statistics

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, CACHE_PATH, summarize,
)

MIN_DEPTH = 10.0
HOLD_DAYS = 3


def collect():
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    rows = []
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
            closes, lows, volumes = closes_raw, lows_raw, volumes_raw
            highs = highs_raw
            dates_idx = list(dates_idx_raw)
        n = len(closes)
        if n < 60:
            skipped_short += 1
            continue

        for entry_idx in find_touch_entries(closes, lows):
            confirm_idx = entry_idx + 1
            exit_idx = confirm_idx + HOLD_DAYS
            if exit_idx >= n:
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
                continue
            vr0 = volume_ratio_at(volumes, entry_idx)
            fast_rev0 = recent_fast_reversal_active(closes, entry_idx)
            extra = zz_extra_score(vr0, fast_rev0)
            if extra < 2:
                continue

            next_low = lows[confirm_idx]
            if next_low is None or next_low <= day_low:
                continue  # 저점상승 확인 안 됨

            # 확인일(D+1)의 거래량비/종가위치를 추가 신호로 계산
            confirm_vr = volume_ratio_at(volumes, confirm_idx)
            day_high = highs[confirm_idx]
            day_range = day_high - next_low if (day_high is not None and next_low is not None) else None
            close_pos_pct = (
                (closes[confirm_idx] - next_low) / day_range * 100
                if day_range else None
            )
            if confirm_vr is None or close_pos_pct is None:
                continue

            entry2_price = closes[confirm_idx]
            ret_3d = (closes[exit_idx] / entry2_price - 1) * 100
            reached = any(
                (c / entry2_price - 1) * 100 >= 0
                for c in closes[confirm_idx + 1: exit_idx + 1]
            )
            rows.append({
                "date": dates_idx[confirm_idx], "reached": reached, "d5": ret_3d,
                "confirm_vr": confirm_vr, "close_pos_pct": close_pos_pct,
            })

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, "
          f"저점상승 확인된 아래꼬리 신호: {len(rows)}건", file=sys.stderr)
    return rows


if __name__ == "__main__":
    rows = collect()

    lines = [f"아래꼬리+저점상승확인 신호에 거래량비/종가위치 결합 필터 ({HOLD_DAYS}일 보유)",
             f"전체(추가필터 없음): {summarize(rows)}", ""]

    lines.append("=== 확인일 거래량비(confirm_vr) 구간별 ===")
    vr_vals = sorted(r["confirm_vr"] for r in rows)
    n = len(vr_vals)
    vr_terciles = [vr_vals[0], vr_vals[n // 3], vr_vals[2 * n // 3], vr_vals[-1] + 0.01]
    for i in range(3):
        lo, hi = vr_terciles[i], vr_terciles[i + 1]
        sub = [r for r in rows if lo <= r["confirm_vr"] < hi]
        lines.append(f"[거래량비 {lo:.2f}~{hi:.2f}] {summarize(sub)}")
    lines.append("")

    lines.append("=== 확인일 종가위치(close_pos_pct, 그날 고저구간에서 종가 위치) 구간별 ===")
    cp_buckets = [(0, 33, "하위1/3(약한마감)"), (33, 66, "중간1/3"), (66, 101, "상위1/3(고가근접마감)")]
    for lo, hi, label in cp_buckets:
        sub = [r for r in rows if lo <= r["close_pos_pct"] < hi]
        lines.append(f"[{label}, {lo}-{hi}%] {summarize(sub)}")
    lines.append("")

    lines.append("=== 결합: 거래량비 상위1/3 + 종가위치 상위1/3 (둘 다 강한 신호) ===")
    vr_hi_thresh = vr_terciles[2]
    combo = [r for r in rows if r["confirm_vr"] >= vr_hi_thresh and r["close_pos_pct"] >= 66]
    lines.append(f"{summarize(combo)}")
    if combo:
        dates_sorted = sorted(r["date"] for r in combo)
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"시기분할(기준일 {mid}):")
        for label, cond in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            sub = [r for r in combo if cond(r["date"])]
            lines.append(f"  [{label}] {summarize(sub)}")

    with open("wick_combined_filter_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
