"""2026-09-07 사용자 요청("결국 수익을 내는게 최종이잖아" -> "1번부터 이어서") -- 지금까지의
모든 검증은 "터치 1건당 평균 도달률/수익률"이었지, "이 전략을 실제로 계속 따라했으면 계좌가
얼마나 불어났을지"(복리 누적 수익, 자산곡선)는 안 봤다. 강한이김(>=2) 신호를 N슬롯 로테이션
(동시 보유 종목 수 제한, V3의 RSI 9슬롯 구조와 같은 개념)으로 실제 시뮬레이션한다.

방식: 신호가 뜨면(강한이김>=2) 빈 슬롯이 있을 때만 균등배분(현재 총자산/N)으로 진입, 5거래일
보유 후 청산(손절 없음 -- ⑰에서 이미 검증된 최선), 그 손익이 총자산에 반영되고 다음 진입
규모도 그에 따라 커지거나 작아진다(복리). 같은 날 여러 신호가 뜨는데 슬롯이 부족하면 저울의
기존 정렬 기준(점수 높은 순 -> 되돌림깊이 큰 순)으로 우선순위를 매긴다.

2026-09-07(같은 날, 1차 결과 확인 후) -- 10슬롯으로 처음 돌려보니 신호 15,875건 중 939건만
체결되고 94%가 슬롯부족으로 누락됨(2,700종목 규모라 하루에도 수십 건씩 신호가 쏟아짐) --
초반에 어쩌다 잡힌 소수 종목이 5일씩 슬롯을 계속 묶어버려 이후 더 좋은 신호를 체계적으로
놓치는 구조적 문제 발견. 슬롯 수를 여러 단계로 비교해서 "슬롯 제약 자체가 결과를 얼마나
왜곡하는지"를 같이 보여준다."""
import pickle
import sys

import scale_validation_test as svt
from scale_validation_test import (
    zigzag_swings, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH,
)

# 2026-09-07 수정 -- scale_validation_test.find_touch_entries가 내부적으로 모듈 상수
# MIN_DEPTH(7.0, 원 검증 baseline)를 쓰는데, 저울 실제 운영(render_scale_report.py)은
# 10.0으로 이미 올려둔 상태(2%->10% 통일 커밋 이후). 여기서도 10.0으로 맞춰서 실제 사이트가
# 보여주는 것과 같은 모집단으로 시뮬레이션한다(전에는 무심코 7.0으로 돌려서 population
# 평균수익이 +0.39%(7%기준)로 실제 프로덕션 값 +1.07%(10%기준)보다 훨씬 낮게 나왔었음).
svt.MIN_DEPTH = 10.0
find_touch_entries = svt.find_touch_entries

STARTING_EQUITY = 10_000_000.0  # 1천만원 -- 임의 기준(비율로 해석하면 시작금액 무관)


def collect_signals():
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    signals = []
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
            same_day_recovery = (entry_price - day_low) / day_low * 100
            risk_flag = same_day_recovery >= RISK_RECOVERY_MIN
            vr = volume_ratio_at(volumes, entry_idx)
            fast_rev = recent_fast_reversal_active(closes, entry_idx)
            extra = zz_extra_score(vr, fast_rev)
            score = max(-5, min(5, extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)))
            if score < 2:
                continue

            swings = zigzag_swings(closes[: entry_idx + 1])
            leg_high = None
            for k in range(len(swings) - 1, -1, -1):
                if swings[k][0] <= entry_idx:
                    leg_high = swings[k][1]
                    break
            depth_pct = (leg_high - day_low) / leg_high * 100 if leg_high else 0.0

            exit_price = closes[entry_idx + HORIZON]
            signals.append({
                "entry_date": dates_idx[entry_idx], "exit_date": dates_idx[entry_idx + HORIZON],
                "name": name, "score": score, "depth_pct": depth_pct,
                "entry_price": entry_price, "exit_price": exit_price,
            })

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, 강한이김 신호 총수: {len(signals)}", file=sys.stderr)
    signals.sort(key=lambda s: (s["entry_date"], -s["score"], -s["depth_pct"]))
    return signals


def run_simulation(signals, cap_slots, sizing_slots=None):
    """cap_slots: 동시보유 최대 종목수(이 이상이면 신호를 거른다). None이면 무제한(다 받음).
    sizing_slots: 균등배분 분모(자산/sizing_slots를 한 건당 배팅). None이면 cap_slots와 동일
    (기존 방식) -- cap_slots=None(무제한)일 때는 반드시 별도로 지정해야 한다(무한대로 나눠서
    배팅액이 0에 수렴하는 걸 방지, 2026-09-07 1차 시도에서 발견한 버그)."""
    if sizing_slots is None:
        sizing_slots = cap_slots
    equity = STARTING_EQUITY
    equity_curve = []
    open_positions = []
    missed = 0
    taken = 0

    all_dates = sorted(set(s["entry_date"] for s in signals) | set(s["exit_date"] for s in signals))
    signals_by_date = {}
    for s in signals:
        signals_by_date.setdefault(s["entry_date"], []).append(s)

    for d in all_dates:
        still_open = []
        for pos in open_positions:
            if pos["exit_date"] == d:
                realized = pos["allocated"] * (pos["exit_price"] / pos["entry_price"])
                equity += realized - pos["allocated"]
                equity_curve.append((d, equity))
            else:
                still_open.append(pos)
        open_positions = still_open

        for s in signals_by_date.get(d, []):
            if cap_slots is not None and len(open_positions) >= cap_slots:
                missed += 1
                continue
            allocated = equity / sizing_slots
            open_positions.append({
                "exit_date": s["exit_date"], "allocated": allocated,
                "entry_price": s["entry_price"], "exit_price": s["exit_price"], "name": s["name"],
            })
            taken += 1

    for pos in open_positions:
        realized = pos["allocated"] * (pos["exit_price"] / pos["entry_price"])
        equity += realized - pos["allocated"]

    total_return_pct = (equity / STARTING_EQUITY - 1) * 100
    n_days = (all_dates[-1] - all_dates[0]).days if all_dates else 0
    years = n_days / 365.25 if n_days else 0
    cagr = ((equity / STARTING_EQUITY) ** (1 / years) - 1) * 100 if years > 0 else None

    peak = STARTING_EQUITY
    mdd = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        dd = (eq / peak - 1) * 100
        mdd = min(mdd, dd)

    return {
        "cap_slots": cap_slots, "sizing_slots": sizing_slots, "taken": taken, "missed": missed,
        "final_equity": equity, "total_return_pct": total_return_pct,
        "cagr": cagr, "mdd": mdd, "years": years,
        "start_date": all_dates[0], "end_date": all_dates[-1],
    }


if __name__ == "__main__":
    signals = collect_signals()
    lines = [f"저울 강한이김(>=2, MIN_DEPTH=10%) 신호, 슬롯수별 로테이션 시뮬레이션 비교 "
             f"(손절없음, 5거래일 보유)",
             f"진입 신호 총수: {len(signals)}", ""]

    scenarios = [(10, 10), (20, 20), (50, 50), (100, 100),
                 (None, 20)]  # (cap_slots, sizing_slots) -- 마지막은 "전부 받되 20종목 기준
                              # 균등배분(5%씩)으로 사이징" -- 무한대로 나눠 배팅액이 0에
                              # 수렴하던 버그 수정판

    for cap_slots, sizing_slots in scenarios:
        r = run_simulation(signals, cap_slots, sizing_slots)
        slot_label = (f"무제한체결(사이징기준 {sizing_slots}종목=매건 {100/sizing_slots:.0f}%씩)"
                       if cap_slots is None else f"{cap_slots}슬롯")
        lines.append(f"=== {slot_label} ===")
        lines.append(f"기간: {r['start_date'].date()} ~ {r['end_date'].date()} ({r['years']:.2f}년)")
        lines.append(f"체결: {r['taken']}건, 슬롯부족 누락: {r['missed']}건 "
                      f"({r['missed']/len(signals)*100:.1f}%)")
        lines.append(f"시작자산 {STARTING_EQUITY:,.0f}원 -> 최종자산 {r['final_equity']:,.0f}원 "
                      f"(총 {r['total_return_pct']:+.1f}%)")
        lines.append(f"연환산(CAGR): {r['cagr']:+.1f}%" if r["cagr"] is not None else "")
        lines.append(f"최대낙폭(MDD): {r['mdd']:.1f}%")
        lines.append("")

    with open("equity_curve_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
