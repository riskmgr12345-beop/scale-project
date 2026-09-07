"""2026-09-07(다음날 이어서) -- limit_order_discount_test.py는 "체결된 것만" 단건 평균으로
5% 할인 지정가가 75.5%/+2.62%(체결율 19.2%)로 기존(72.9%/+0.47%) 대비 확실히 개선된다는 걸
보였지만, 체결율이 낮아지는 만큼 "실제로 계좌를 이 전략대로 운영했으면 최종 자산이 얼마나
됐을지"(자본회전율까지 반영한 복리 총수익)는 별도 질문이라 미반영 상태였다(㉖ 참고).

이 스크립트는 equity_curve_simulation.py의 N슬롯 로테이션 프레임을 그대로 재사용해서 이
질문에 직접 답한다. 핵심 아이디어: 할인 지정가 전략에서는 신호일(D) 당일엔 자본이 전혀
묶이지 않고, D+1에 체결됐을 때만 슬롯을 실제로 차지한다 -- 즉 미체결 신호는 애초에
슬롯경쟁에 참여하지 않으므로(신호 자체가 사라짐), "느슨해진 슬롯을 다른 신호가 대신
차지하는" 효과가 시뮬레이션에 자연히 반영된다(run_simulation 로직 변경 없이 입력 신호
리스트만 두 버전으로 나눠서 그대로 재사용).

기존(discount=0%) 방식: entry_date=D(신호일 종가매수), exit_date=D+HORIZON.
할인 방식(discount>0): entry_date=D+1(체결일), entry_price=지정가, exit_date=D+1+HORIZON
  -- D+1 저가가 지정가 밑으로 안 내려오면(미체결) 이 신호는 통째로 버려진다(슬롯 소비 없음).

같은 기간/같은 우선순위 정렬(점수→깊이) 하에서 cap_slots를 저울 실제 운영 규모(10/20)로
맞춰 비교한다."""
import pickle
import sys

import scale_validation_test as svt
from scale_validation_test import (
    zigzag_swings, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON, CACHE_PATH,
)

svt.MIN_DEPTH = 10.0
find_touch_entries = svt.find_touch_entries

from equity_curve_simulation import run_simulation, STARTING_EQUITY

DISCOUNTS = [0.0, 0.02, 0.03, 0.05]  # 0.0 = 기존(대조군)


def collect_signals_by_discount():
    with open(CACHE_PATH, "rb") as f:
        cache = pickle.load(f)

    signals_by_discount = {d: [] for d in DISCOUNTS}
    n_candidates = 0
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
            signal_close = closes[entry_idx]
            day_low = lows[entry_idx]
            if not day_low or not signal_close:
                continue
            same_day_recovery = (signal_close - day_low) / day_low * 100
            risk_flag = same_day_recovery >= RISK_RECOVERY_MIN
            vr = volume_ratio_at(volumes, entry_idx)
            fast_rev = recent_fast_reversal_active(closes, entry_idx)
            extra = zz_extra_score(vr, fast_rev)
            score = max(-5, min(5, extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)))
            if score < 2:
                continue
            n_candidates += 1

            swings = zigzag_swings(closes[: entry_idx + 1])
            leg_high = None
            for k in range(len(swings) - 1, -1, -1):
                if swings[k][0] <= entry_idx:
                    leg_high = swings[k][1]
                    break
            depth_pct = (leg_high - day_low) / leg_high * 100 if leg_high else 0.0

            # 기존(즉시매수) 버전 -- entry_idx 기준
            if entry_idx + HORIZON < len(closes):
                signals_by_discount[0.0].append({
                    "entry_date": dates_idx[entry_idx], "exit_date": dates_idx[entry_idx + HORIZON],
                    "name": name, "score": score, "depth_pct": depth_pct,
                    "entry_price": signal_close, "exit_price": closes[entry_idx + HORIZON],
                })

            # 할인 지정가 버전들 -- D+1 체결 여부 확인
            if entry_idx + 1 >= len(closes):
                continue
            next_day_low = lows[entry_idx + 1]
            exit_idx = entry_idx + 1 + HORIZON
            if exit_idx >= len(closes):
                continue
            for discount in DISCOUNTS:
                if discount == 0.0:
                    continue
                limit_price = signal_close * (1 - discount)
                filled = next_day_low is not None and next_day_low <= limit_price
                if not filled:
                    continue
                signals_by_discount[discount].append({
                    "entry_date": dates_idx[entry_idx + 1], "exit_date": dates_idx[exit_idx],
                    "name": name, "score": score, "depth_pct": depth_pct,
                    "entry_price": limit_price, "exit_price": closes[exit_idx],
                })

    print(f"스캔 종목수: {len(cache)}, 60일미만 제외: {skipped_short}, 강한이김 신호후보 총수: {n_candidates}",
          file=sys.stderr)
    for d in DISCOUNTS:
        signals_by_discount[d].sort(key=lambda s: (s["entry_date"], -s["score"], -s["depth_pct"]))
    return signals_by_discount, n_candidates


if __name__ == "__main__":
    signals_by_discount, n_candidates = collect_signals_by_discount()

    lines = ["할인 지정가 매수 전략의 N슬롯 로테이션 복리 시뮬레이션 (손절없음, 5거래일 보유)",
             "기존(즉시매수)은 신호일 종가 매수, 할인전략은 D+1에 지정가 체결시에만 슬롯 소비",
             f"강한이김(>=2) 신호후보 총수: {n_candidates}", ""]

    for cap_slots in [10, 20]:
        lines.append(f"########## {cap_slots}슬롯 ##########")
        for discount in DISCOUNTS:
            signals = signals_by_discount[discount]
            if not signals:
                continue
            r = run_simulation(signals, cap_slots, cap_slots)
            label = "기존(즉시매수)" if discount == 0.0 else f"-{discount*100:.0f}% 할인지정가"
            lines.append(f"--- {label} ---")
            lines.append(f"체결가능 신호수: {len(signals)}건 (체결율 기준모수 대비 "
                          f"{len(signals)/n_candidates*100:.1f}%)")
            lines.append(f"기간: {r['start_date'].date()} ~ {r['end_date'].date()} ({r['years']:.2f}년)")
            lines.append(f"실제체결: {r['taken']}건, 슬롯부족 누락: {r['missed']}건")
            lines.append(f"시작자산 {STARTING_EQUITY:,.0f}원 -> 최종자산 {r['final_equity']:,.0f}원 "
                          f"(총 {r['total_return_pct']:+.1f}%)")
            lines.append(f"연환산(CAGR): {r['cagr']:+.1f}%" if r["cagr"] is not None else "")
            lines.append(f"최대낙폭(MDD): {r['mdd']:.1f}%")
            lines.append("")

    with open("discount_equity_curve_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
