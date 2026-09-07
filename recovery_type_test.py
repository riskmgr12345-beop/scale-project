"""2026-09-07 사용자 요청("이어해줘" -- ZZ의 즉시반등형/추가하락형 개념을 저울로 재검증,
이전 두 번 시도 실패: ①판정창=측정창 순환논리 ②무제한 미래 참조로 실전 불가) -- ZZ 원본
정의(jobs/render_holding_zigzags.py._touch_recovery_type)를 확인하니, ZZ는 "첫 7%p 터치
이후 지금(len(dates), 살아있는 현재)까지 더 낮은 저가를 찍었는지"로 무제한 미래를 쓰고
있었다 -- 저울에 그대로 포팅하면 실전에서 못 쓰는 값이 되는 게 당연했다.

이번엔 무제한 미래 대신 **다리가 끝나는 시점(zigzag로 확정되는 반전점)까지**로 범위를
한정한다 -- 이건 "그 다리가 완전히 끝난 뒤에는 언제든 확인 가능"한 값이라 유한하다. 다만
이 값 자체는 진입 시점(entry_idx)엔 알 수 없는 사후적(retrospective) 라벨이라는 점을
명확히 한다 -- ZZ도 실제로 "매수 전 참고용 경고"가 아니라 "매수 후 관찰"용으로 씀. 목적은
①즉시반등형/추가하락형이 실제로 수익률 차이를 보이는지(ZZ와 같은 방식으로 저울 규모
재확인), ②그 차이가 진입 시점에 이미 알려진 기존 신호(고변동/장중반전/거래량비 등)로
예측 가능한지 확인하는 것 -- ②가 된다면 비로소 "매수 전에 쓸 수 있는" 진짜 배지가 된다."""
import pickle
import statistics
import sys

from scale_validation_test import (
    zigzag_swings, find_touch_entries, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH, summarize,
)


def _leg_end_idx(closes, entry_idx):
    """entry_idx가 속한 하락다리가 끝나는(zigzag로 다음 반전이 확정되는) 인덱스. 아직
    안 끝났으면(진행중, 즉 지금이 마지막 다리) None."""
    swings = zigzag_swings(closes)
    for i in range(len(swings) - 1):
        idx0, _ = swings[i]
        idx1, _ = swings[i + 1]
        if idx0 <= entry_idx < idx1:
            return idx1
    return None  # 진행중인 다리(아직 안 끝남) -- 라벨링 불가


if __name__ == "__main__":
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    all_rows = []
    unresolved = 0  # 다리가 아직 안 끝나서 라벨링 불가했던 건수
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
            score = max(-5, min(5, extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)))
            if score < 2:
                continue

            leg_end = _leg_end_idx(closes, entry_idx)
            if leg_end is None:
                unresolved += 1
                continue  # 다리가 아직 진행중(안 끝남) -- 즉시반등형/추가하락형 라벨링 불가

            went_lower = any(lows[j] < day_low for j in range(entry_idx + 1, leg_end + 1))
            recovery_type = "추가하락형" if went_lower else "즉시반등형"

            horizon_closes = closes[entry_idx + 1: entry_idx + 1 + HORIZON]
            if len(horizon_closes) < HORIZON:
                continue
            reached = any((c / entry_price - 1) * 100 >= 0 for c in horizon_closes)
            d5_pct = (horizon_closes[-1] / entry_price - 1) * 100
            entry_date = dates_idx[entry_idx]

            all_rows.append({"reached": reached, "d5": d5_pct, "date": entry_date,
                              "recovery_type": recovery_type})

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, "
          f"다리 진행중이라 라벨링 불가: {unresolved}", file=sys.stderr)

    lines = ["저울 강한이김(>=2) 모집단, ZZ식 '즉시반등형/추가하락형' 재검증",
             "(사후적 라벨 -- 매수 시점엔 알 수 없음, 다리 완결 후에만 분류 가능. 매수전 필터가",
             " 아니라 '진입시점 신호로 이 라벨을 예측할 수 있는지'를 보려는 목적)",
             f"전체 표본 n={len(all_rows)}", ""]

    lines.append("=== 회복 유형별(순수분리, ZZ와 같은 방식) ===")
    for label, cond in [("즉시반등형", lambda r: r["recovery_type"] == "즉시반등형"),
                         ("추가하락형", lambda r: r["recovery_type"] == "추가하락형")]:
        rows = [r for r in all_rows if cond(r)]
        lines.append(f"{label}: {summarize(rows)}")
    lines.append("")

    dates_sorted = sorted(r["date"] for r in all_rows)
    if dates_sorted:
        mid = dates_sorted[len(dates_sorted) // 2]
        lines.append(f"=== 시기분할 재현성 (기준일 {mid}) ===")
        for period_label, cond_date in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            lines.append(f"-- {period_label} --")
            for label, cond in [("즉시반등형", lambda r: r["recovery_type"] == "즉시반등형"),
                                 ("추가하락형", lambda r: r["recovery_type"] == "추가하락형")]:
                rows = [r for r in all_rows if cond(r) and cond_date(r["date"])]
                lines.append(f"  {label}: {summarize(rows)}")

    with open("recovery_type_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
