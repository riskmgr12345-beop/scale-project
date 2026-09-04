"""'저울' 검증추적 -- 2026-09-04 사용자 요청("오늘 추천종목 6일 후 다음주 금요일 79.3% 결과
보고해주고, 다리넘은 종목 계속 결과 추적?") -- ZZ(V3)의 zz_score_tracker.json과 같은 개념을
저울에도 도입한다: 강한이김(>=2점)으로 뽑힌 종목을 진입 시점 가격과 함께 기록해두고, 이후
매일(개별 종목 실시간 가격으로) 5거래일 이내에 (a) 진입가 이상 회복했는지("본전회복",
scale_validation_test.py의 reached와 같은 정의), (b) 진입 시점 다리 저점 대비 3%+ 반등해서
다리가 전환됐는지를 추적해 완료 처리한다.

콜라 캐시(research_cache/limitup_ohlcv_cache.pkl)는 주간 갱신이라 매일 정확한 추적이 안 되므로,
이 트래커는 종목별로 FinanceDataReader를 직접 호출해 최신 일봉을 받는다(로컬 실행 또는 GHA --
둘 다 네트워크 열려있음 확인됨, build_kospi_regime_cache.py와 같은 원칙). 클라우드 라우틴
샌드박스에서는 네트워크가 막혀 있으므로 이 트래커 갱신은 절대 그쪽에서 시도하지 않는다(별도
GHA 워크플로 refresh_verification_tracker.yml이 전담).

HORIZON=5(scale_validation_test.py와 동일)가 지나면 완료 처리하고, 그 전에 본전회복이나
다리전환 중 하나라도 먼저 확정되면 그 시점 기준으로도 기록은 남기되(reached_date/leg_flip_date),
완료(active->completed) 자체는 5거래일 경과 시점에 한다(exit_reason은 그 안에서 다리전환이
있었는지/본전만 회복했는지/둘 다 못했는지로 구분)."""
import json
import os
import statistics

TRACKER_PATH = "verification_tracker.json"
HORIZON = 5


def _load_tracker():
    if not os.path.exists(TRACKER_PATH):
        return {"active": {}, "completed": {}}
    with open(TRACKER_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_tracker(tracker):
    with open(TRACKER_PATH, "w", encoding="utf-8") as f:
        json.dump(tracker, f, ensure_ascii=False, indent=2, default=str)


def seed_from_top_rows(tracker, top_rows):
    """오늘 상위 20개(강한이김) 중 아직 추적 중이 아닌 종목을 새 active 항목으로 추가한다.
    같은 종목이 완료된 지 얼마 안 돼 다시 뽑혀도(다른 다리에서 재신호) 별개 항목으로 추가 --
    ZZ의 completed 딕셔너리처럼 name 단일 키가 아니라 name+entry_date 조합으로 구분한다."""
    added = []
    for r in top_rows:
        if r["score"] < 2:
            continue
        name = r["name"]
        entry_date = str(r["last_date"].date())
        if name in tracker["active"]:
            continue  # 이미 추적 중이면 중복 추가 안 함
        completed_key = f"{name}__{entry_date}"
        if completed_key in tracker["completed"]:
            continue
        tracker["active"][name] = {
            "code": r["code"],
            "entry_date": entry_date,
            "entry_price": r["cur_price"],
            "score": r["score"],
            "depth_pct_at_entry": r["depth_pct"],
            "running_low": r["cur_price"],
            "reached_breakeven": False,
            "reached_date": None,
            "leg_flipped": False,
            "leg_flip_date": None,
            "history": [],
        }
        added.append(name)
    return added


def _trading_days_between(start_str, dates):
    start = None
    for i, d in enumerate(dates):
        if str(d.date()) == start_str:
            start = i
            break
    if start is None:
        return None
    return len(dates) - 1 - start  # entry일 자체는 0일차


def update_active(tracker):
    """active 항목들을 FinanceDataReader로 최신 일봉을 받아 갱신하고, 5거래일 지난 건
    completed로 옮긴다. 네트워크 실패한 개별 종목은 건너뛰고 다음에 다시 시도(에러로 전체
    갱신을 막지 않음). import를 함수 안에 둔 이유: render_scale_report.py는 seed_from_top_rows
    (네트워크 불필요)만 쓰므로, FDR(네트워크 필요) 의존을 이 함수를 실제 호출할 때로 미룬다."""
    import FinanceDataReader as fdr

    completed_now = []
    for name, entry in list(tracker["active"].items()):
        code = entry["code"]
        if not code or code == "-":
            continue
        try:
            df = fdr.DataReader(code, entry["entry_date"])
        except Exception as e:
            entry.setdefault("fetch_errors", []).append(str(e))
            continue
        if df is None or df.empty:
            continue
        closes = df["Close"].tolist()
        dates = list(df.index)
        n_days = _trading_days_between(entry["entry_date"], dates)
        if n_days is None:
            continue

        entry_price = entry["entry_price"]
        # 2026-09-04 발견(look-ahead bias 재발 -- 이 세션 초반 "멀티임계값 캐스케이드" 사고와
        # 같은 종류) -- 처음엔 running_low를 fetch된 전체 구간의 min(closes)으로 미리 계산해서
        # 썼다가, 그러면 "진입일" 판정에 아직 오지도 않은 미래 날짜(예: 다음날 더 빠진 저가)가
        # 섞여 들어가 진입일 자체가 "3%+ 반등"으로 잘못 찍히는 버그가 났다(아이씨에이치·태웅
        # 사례로 발견). 반드시 날짜 순서대로 하루씩 진행하며 그 시점까지의 정보만 쓴다.
        # 진입일(dates[0]) 자체는 정의상 reached/flip 판정에서 제외(entry_price==그날 종가라
        # 항상 참이 되는 동어반복 방지).
        running_low = entry["running_low"]
        for d, c in zip(dates[1:], closes[1:]):
            if not entry["reached_breakeven"] and c >= entry_price:
                entry["reached_breakeven"] = True
                entry["reached_date"] = str(d.date())
            if not entry["leg_flipped"] and running_low and (c / running_low - 1) * 100 >= 3.0:
                entry["leg_flipped"] = True
                entry["leg_flip_date"] = str(d.date())
            if c < running_low:
                running_low = c
        entry["running_low"] = running_low

        entry["history"] = [{"date": str(d.date()), "close": c} for d, c in zip(dates, closes)]
        entry["days_elapsed"] = n_days

        if n_days >= HORIZON:
            final_close = closes[-1]
            final_return_pct = (final_close / entry_price - 1) * 100
            if entry["leg_flipped"]:
                exit_reason = "다리전환"
            elif entry["reached_breakeven"]:
                exit_reason = "본전회복"
            else:
                exit_reason = "미도달"
            completed_key = f"{name}__{entry['entry_date']}"
            tracker["completed"][completed_key] = {
                **entry, "name": name, "exit_date": str(dates[-1].date()),
                "exit_reason": exit_reason, "final_price": final_close,
                "final_return_pct": final_return_pct,
            }
            del tracker["active"][name]
            completed_now.append((name, exit_reason, final_return_pct))
    return completed_now


def summary_stats(tracker):
    """완료된 항목 전체의 실측 도달률/평균수익률 -- 화면에 "저울 자체 실측"으로 보여줄 용도.
    표본이 적을 땐(15건 미만) 그 사실을 같이 표시해야 함(호출부 책임)."""
    completed = list(tracker["completed"].values())
    n = len(completed)
    if not n:
        return {"n": 0}
    reached = sum(1 for c in completed if c["reached_breakeven"]) / n * 100
    flipped = sum(1 for c in completed if c["leg_flipped"]) / n * 100
    avg_return = statistics.mean(c["final_return_pct"] for c in completed)
    return {"n": n, "reached_pct": reached, "flipped_pct": flipped, "avg_return": avg_return}


if __name__ == "__main__":
    tracker = _load_tracker()
    completed_now = update_active(tracker)
    _save_tracker(tracker)
    stats = summary_stats(tracker)
    print(f"active={len(tracker['active'])}, completed={len(tracker['completed'])}")
    print(f"이번 실행에서 완료된 것: {completed_now}")
    print(f"누적 통계: {stats}")
