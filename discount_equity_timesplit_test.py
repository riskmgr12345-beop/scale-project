"""discount_equity_curve_test.py의 핵심 결과(할인전략이 즉시매수보다 복리수익 훨씬 나쁨)가
반쪽 데이터의 우연인지, 양쪽 기간에서 재현되는 반복적 패턴인지 시기분할로 확인한다."""
import sys
from discount_equity_curve_test import collect_signals_by_discount, DISCOUNTS
from equity_curve_simulation import run_simulation, STARTING_EQUITY

signals_by_discount, n_candidates = collect_signals_by_discount()

# 기존(0%) 신호의 entry_date 기준 중앙값으로 기간을 반으로 나눈다
base_dates = sorted(s["entry_date"] for s in signals_by_discount[0.0])
mid = base_dates[len(base_dates) // 2]

lines = [f"시기분할 기준일: {mid}", ""]
for cap_slots in [10]:
    for discount in DISCOUNTS:
        signals = signals_by_discount[discount]
        for period_label, cond in [("전반부", lambda d: d < mid), ("후반부", lambda d: d >= mid)]:
            sub = [s for s in signals if cond(s["entry_date"])]
            if not sub:
                lines.append(f"[{cap_slots}슬롯][{'기존' if discount==0.0 else f'-{discount*100:.0f}%할인'}][{period_label}] 표본없음")
                continue
            r = run_simulation(sub, cap_slots, cap_slots)
            label = "기존(즉시매수)" if discount == 0.0 else f"-{discount*100:.0f}% 할인지정가"
            lines.append(f"[{cap_slots}슬롯][{label}][{period_label}] 신호{len(sub)}건, 체결{r['taken']}건, "
                         f"총수익{r['total_return_pct']:+.1f}%, CAGR{r['cagr']:+.1f}%" if r['cagr'] is not None
                         else f"[{cap_slots}슬롯][{label}][{period_label}] 신호{len(sub)}건, 체결{r['taken']}건, 총수익{r['total_return_pct']:+.1f}%")
    lines.append("")

with open("discount_equity_timesplit_result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("done")
