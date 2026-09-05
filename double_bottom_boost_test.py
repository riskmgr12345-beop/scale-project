"""2026-09-05 사용자 요청("v3, 콜라, 환타, zz 등에서 분석 적용한 내용중 저울에 적용 도움되는
내용 모두 검토" -> "순서대로 시작해") ⑤이중바닥 부스터 재검증 -- ZZ가 49종목/8년으로 검증한
"이중바닥(detect_double_bottom, strategy/rsi_side.py) 확정 후 10거래일 이내 터치는 확정률이
더 높다"는 발견이, 저울의 2,700종목 모집단에서도 재현되는지 확인한다.

detect_double_bottom은 순수 OHLCV DataFrame 함수라 V3 의존성 없이 이 파일에 그대로 복사해도
동작(config.py의 DOUBLE_BOTTOM_* 기본값도 하드코딩 -- 콜라/DART 포팅 때와 같은 self-contained
원칙). scale_validation_test.py의 zigzag_swings/find_touch_entries/_drop_zero_volume_days도
그대로 재사용해서 "저울 강한이김(>=2)" 모집단 정의를 통일한다(다른 검증공식을 새로 만들지
않음).

방법론(이 세션에서 확립된 "순수분리" 원칙): 강한이김(>=2) 표본을 고정한 뒤, 터치 시점 기준
과거 10거래일 이내 이중바닥 확정 여부로만 나눠 도달률/평균수익을 비교. 시기분할(전반부/후반부)
재현성도 같이 확인 -- 재현 안 되면 채택하지 않는다(이 세션의 다른 가설들과 동일 기준)."""
import pickle
import statistics
import sys

import pandas as pd

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, _drop_zero_volume_days, MIN_DEPTH, RISK_RECOVERY_MIN,
    TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH, summarize,
)

DOUBLE_BOTTOM_VOL_DRY_RATIO = 0.7
DOUBLE_BOTTOM_HIGHER_LOW_MAX = 0.15
DOUBLE_BOTTOM_MIN_GAP_DAYS = 5
DOUBLE_BOTTOM_MAX_GAP_DAYS = 60
DOUBLE_BOTTOM_REBOUND_MIN = 0.05
DOUBLE_BOTTOM_DOJI_BODY_RATIO = 0.15
DOUBLE_BOTTOM_HAMMER_WICK_MULT = 2.0
DOUBLE_BOTTOM_SWING_WINDOW = 5
DOUBLE_BOTTOM_NEAR_WINDOW = 10  # ZZ가 검증한 "근접" 정의: 확정일로부터 10거래일 이내


def _find_swing_lows(df, window=DOUBLE_BOTTOM_SWING_WINDOW):
    low = df["Low"]
    return low == low.rolling(window * 2 + 1, center=True, min_periods=window + 1).min()


def detect_double_bottom(df):
    """strategy/rsi_side.py의 detect_double_bottom을 그대로 복사(self-contained)."""
    swing_low = _find_swing_lows(df)
    swing_dates = df.index[swing_low.fillna(False)]
    signals = []
    for i in range(1, len(swing_dates)):
        low1_date, low2_date = swing_dates[i - 1], swing_dates[i]
        gap = df.index.get_loc(low2_date) - df.index.get_loc(low1_date)
        if not (DOUBLE_BOTTOM_MIN_GAP_DAYS <= gap <= DOUBLE_BOTTOM_MAX_GAP_DAYS):
            continue
        low1_price, low2_price = df.loc[low1_date, "Low"], df.loc[low2_date, "Low"]
        if not (low1_price <= low2_price <= low1_price * (1 + DOUBLE_BOTTOM_HIGHER_LOW_MAX)):
            continue
        between = df.loc[low1_date:low2_date]
        peak = between["High"].max()
        if peak < low1_price * (1 + DOUBLE_BOTTOM_REBOUND_MIN):
            continue
        vol1, vol2 = df.loc[low1_date, "Volume"], df.loc[low2_date, "Volume"]
        if vol1 == 0 or not (vol2 <= vol1 * DOUBLE_BOTTOM_VOL_DRY_RATIO):
            continue
        o, h, l, c = df.loc[low2_date, ["Open", "High", "Low", "Close"]]
        rng = h - l
        if rng == 0:
            continue
        body = abs(c - o)
        is_doji = (body / rng) < DOUBLE_BOTTOM_DOJI_BODY_RATIO
        lower_wick = min(o, c) - l
        upper_wick = h - max(o, c)
        is_hammer = (c >= o) and (lower_wick >= DOUBLE_BOTTOM_HAMMER_WICK_MULT * max(body, rng * 0.05)) and (upper_wick < rng * 0.25)
        if is_doji or is_hammer:
            signals.append(low2_date)
    return signals


def _double_bottom_indices(dates, opens, highs, lows, closes, volumes):
    if not opens or not volumes or len(dates) < 10:
        return set()
    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
                       index=pd.to_datetime([str(d) for d in dates]))
    db_dates = detect_double_bottom(df)
    date_to_idx = {str(d)[:10]: i for i, d in enumerate(dates)}
    return {date_to_idx[str(d)[:10]] for d in db_dates if str(d)[:10] in date_to_idx}


def _near_double_bottom(as_of_idx, db_indices, window=DOUBLE_BOTTOM_NEAR_WINDOW):
    return any(0 <= as_of_idx - i <= window for i in db_indices)


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
            opens_raw = df["Open"].tolist()
            highs_raw = df["High"].tolist()
            dates_idx_raw = df.index
        except Exception:
            continue
        keep = [i for i, v in enumerate(volumes_raw) if v and v > 0]
        if len(keep) != len(volumes_raw):
            closes = [closes_raw[i] for i in keep]
            lows = [lows_raw[i] for i in keep]
            volumes = [volumes_raw[i] for i in keep]
            opens = [opens_raw[i] for i in keep]
            highs = [highs_raw[i] for i in keep]
            dates_idx = [dates_idx_raw[i] for i in keep]
        else:
            closes, lows, volumes = closes_raw, lows_raw, volumes_raw
            opens, highs, dates_idx = opens_raw, highs_raw, list(dates_idx_raw)
        if len(closes) < 60:
            skipped_short += 1
            continue

        db_indices = _double_bottom_indices(dates_idx, opens, highs, lows, closes, volumes)

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
            if score < 2:
                continue  # 강한이김(>=2) 모집단만

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]
            db_near = _near_double_bottom(entry_idx, db_indices)

            all_rows.append({"reached": reached, "d5": d5_pct, "date": entry_date, "db_near": db_near})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}", file=sys.stderr)

    lines = ["저울 강한이김(>=2) 모집단에서 '이중바닥 확정 10거래일 이내' 부스터 재검증",
             f"전체 표본 n={len(all_rows)}", ""]

    lines.append("=== 이중바닥 근접 여부(순수분리) ===")
    for label, cond in [("이중바닥 근접(10거래일내)", lambda r: r["db_near"]),
                         ("이중바닥 없음(plain)", lambda r: not r["db_near"])]:
        rows = [r for r in all_rows if cond(r)]
        lines.append(f"{label}: {summarize(rows)}")
    lines.append("")

    dates_sorted = sorted(r["date"] for r in all_rows)
    if dates_sorted:
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 재현성 (전반부 vs 후반부, 기준일 {mid}) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            lines.append(f"-- {period_label} --")
            for label, cond in [("이중바닥 근접", lambda r: r["db_near"]),
                                 ("plain", lambda r: not r["db_near"])]:
                rows = [r for r in all_rows if cond(r) and cond_date(r["date"])]
                lines.append(f"  {label}: {summarize(rows)}")

    with open("double_bottom_boost_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
