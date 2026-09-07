"""2026-09-07 사용자 요청("zz, 콜라, 환타, 저울 등 가장 좋은 확률은?" -> "ZZ와 환타도 같은
방식으로 돌려서 공정하게 비교해볼까요?" -> "해봐줘") -- 저울의 equity_curve_simulation.py와
완전히 같은 방법론(강한이김>=2 신호, N슬롯 로테이션, 손절없음, 5거래일보유, 복리)을 ZZ의
49종목에 그대로 적용해서 공정 비교한다. 데이터는 V3의 data.py(load_price, yfinance)를
그대로 재사용 -- 저울과 똑같은 지그재그/점수 공식(scale_validation_test.py, 원래 ZZ에서
포팅된 것)을 쓰므로 로직 차이 없이 "유니버스 크기"만 다른 비교가 된다."""
import sys
import pickle

sys.path.insert(0, r"C:\Users\82102\Desktop\주식자동매매_V3")
import data as v3_data

import scale_validation_test as svt
from scale_validation_test import (
    zigzag_swings, volume_ratio_at, recent_fast_reversal_active,
    zz_extra_score, RISK_RECOVERY_MIN, TUG_OF_WAR_RISK_PENALTY, HORIZON,
)
svt.MIN_DEPTH = 10.0
find_touch_entries = svt.find_touch_entries

from equity_curve_simulation import run_simulation, STARTING_EQUITY

START_DATE = "2018-01-01"
CACHE_FILE = "_zz_49_ohlcv_cache.pkl"


def fetch_zz_cache():
    """49종목 OHLCV를 한 번만 받아서 로컬에 캐시(재실행 시 네트워크 재호출 방지)."""
    try:
        with open(CACHE_FILE, "rb") as f:
            return pickle.load(f)
    except FileNotFoundError:
        pass
    cache = {}
    for name, ticker in v3_data.TICKERS.items():
        try:
            df = v3_data.load_price(ticker, START_DATE, "2026-09-08")
            if len(df) > 60:
                cache[name] = df
                print(f"  {name}: {len(df)}행", file=sys.stderr)
        except Exception as e:
            print(f"  {name} 실패: {e}", file=sys.stderr)
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(cache, f)
    return cache


def collect_zz_signals(cache):
    signals = []
    skipped_short = 0
    for name, df in cache.items():
        try:
            closes = df["Close"].tolist()
            lows = df["Low"].tolist()
            volumes = df["Volume"].tolist()
            dates_idx = list(df.index)
        except Exception:
            continue
        keep = [i for i, v in enumerate(volumes) if v and v > 0]
        if len(keep) != len(volumes):
            closes = [closes[i] for i in keep]
            lows = [lows[i] for i in keep]
            volumes = [volumes[i] for i in keep]
            dates_idx = [dates_idx[i] for i in keep]
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


if __name__ == "__main__":
    print("ZZ 49종목 데이터 수집 중...", file=sys.stderr)
    cache = fetch_zz_cache()
    signals = collect_zz_signals(cache)

    lines = [f"ZZ(49종목, {START_DATE}~) 강한이김(>=2, MIN_DEPTH=10%) N슬롯 로테이션 시뮬레이션",
             f"저울과 완전히 동일한 방법론(손절없음, 5거래일보유, 복리) -- 유니버스 크기만 다름",
             f"진입 신호 총수: {len(signals)}", ""]

    for cap_slots, sizing_slots in [(3, 3), (5, 5), (10, 10), (20, 20)]:
        r = run_simulation(signals, cap_slots, sizing_slots)
        lines.append(f"=== {cap_slots}슬롯 ===")
        lines.append(f"기간: {r['start_date'].date()} ~ {r['end_date'].date()} ({r['years']:.2f}년)")
        lines.append(f"체결: {r['taken']}건, 슬롯부족 누락: {r['missed']}건 "
                      f"({r['missed']/len(signals)*100:.1f}%)" if signals else "")
        lines.append(f"시작자산 {STARTING_EQUITY:,.0f}원 -> 최종자산 {r['final_equity']:,.0f}원 "
                      f"(총 {r['total_return_pct']:+.1f}%)")
        lines.append(f"연환산(CAGR): {r['cagr']:+.1f}%" if r["cagr"] is not None else "")
        lines.append(f"최대낙폭(MDD): {r['mdd']:.1f}%")
        lines.append("")

    with open("zz_equity_curve_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")
