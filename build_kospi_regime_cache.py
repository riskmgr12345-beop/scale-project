"""2026-09-04 사용자 요청("①번 시장국면 배지부터 화면에 반영") -- render_scale_report.py가
매번 FinanceDataReader로 KOSPI를 직접 조회하면, 클라우드 라우틴(저울 프로젝트 일일 리포트)의
샌드박스는 금융데이터 사이트 아웃바운드가 막혀 있어서(2026-09-03 인프라 문제로 이미 확인된
제약, ZZ/콜라 라우틴과 동일) 실패한다. 그래서 이 스크립트는 네트워크가 열려 있는 곳(로컬 PC,
또는 GitHub Actions 러너 -- 이 저장소 기존 refresh_research_cache.yml류가 매일 증명하듯 GHA는
막혀있지 않음)에서만 실행해 작은 JSON 캐시를 만들고, render_scale_report.py는 그 캐시만 읽는다
(콜라의 OHLCV 캐시를 읽기전용 재사용하는 것과 같은 원칙).

국면 정의는 regime_filter_test.py에서 이미 검증한 것과 동일: 코스피 종가가 자기 60일 이동
평균 위/아래인지. 그 검증(2026-09-04)에서 확인된 실측 성과:
- 상승장(코스피>=60일선): 강한이김(>=2) 5일도달률 69.7%, 평균+-0.26%
- 하락장(코스피<60일선):  강한이김(>=2) 5일도달률 79.3%, 평균+2.06%
(전반부/후반부 시기분할 둘 다 "하락장이 더 낫다" 방향 재현 확인됨, regime_filter_result.txt/
regime_timesplit_result.txt 참고)
"""
import json

import FinanceDataReader as fdr

REGIME_MA_WINDOW = 60
OUT_PATH = "research_cache/kospi_regime_cache.json"

if __name__ == "__main__":
    kospi = fdr.DataReader("KS11", "2026-01-01")
    close = kospi["Close"]
    ma60 = close.rolling(REGIME_MA_WINDOW).mean()
    last_date = close.index[-1]
    last_close = float(close.iloc[-1])
    last_ma = float(ma60.iloc[-1])
    regime = "up" if last_close >= last_ma else "down"

    payload = {
        "date": str(last_date.date()),
        "kospi_close": last_close,
        "kospi_ma60": last_ma,
        "regime": regime,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(payload)
