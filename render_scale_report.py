"""'저울' 프로젝트 -- 코스피+코스닥 전체(_상한가전조연구의 캐시를 읽기전용 재사용) 종목별
"오를 이유(부스터) vs 내릴 이유(위험신호)"를 저울질해서 상위 15개를 뽑아 보고한다.

2026-09-04 사용자 요청 -- "에이전트를 통해 보고받고 판단 결과로 추천, 상위 점수 15개 종목으로"
-- 실시간(장중 KIS) 조회는 2,700종목 규모에서 불가능해서, 콜라 프로젝트처럼 캐시 기반 일일
스냅샷으로 동작한다(scale_validation_test.py로 이미 검증한 것과 같은 공식).

이번 버전에서 포함한 것: 방향(상승/하락다리), 진행기간, 등락%, 저울점수(부스터-위험신호),
52주 가격위치(캐시 히스토리로 직접 계산 가능).
이번 버전에서 제외한 것(추가 데이터소스 필요, 후속 작업): 수급(투자자별매매동향)·PER·관리종목
여부 -- 이 캐시엔 OHLCV만 있고 재무/투자자 데이터가 없어서, V3의 "객관가치" 배지처럼 전부
넣으려면 별도 데이터소스(DART, KRX 관리종목 리스트 등)를 새로 연결해야 한다.
"""
import os
import pickle
import statistics
from datetime import datetime

THRESHOLD = 0.03
MIN_DEPTH = 2.0  # 상위 15개를 뽑는 용도라 문턱을 낮게 잡아 후보군을 넓게 본다(2%p+)
RISK_RECOVERY_MIN = 3.0
TUG_OF_WAR_RISK_PENALTY = 3
# 2026-09-04 -- 로컬 데스크톱(_상한가전조연구, 한글 폴더명)과 클라우드 라우틴(같이 클론되는
# limitup-precursor-research, 저장소 이름 그대로)이 서로 다른 폴더명을 쓰므로 둘 다 후보로
# 시도한다 -- 콜라 캐시를 읽기전용 재사용하는 원칙은 같지만 실행 환경마다 경로만 다름.
CACHE_PATH_CANDIDATES = [
    "../_상한가전조연구/research_cache/limitup_ohlcv_cache.pkl",
    "../limitup-precursor-research/research_cache/limitup_ohlcv_cache.pkl",
]
UNIVERSE_PATH_CANDIDATES = [
    "../_상한가전조연구/research_cache/universe_full.json",
    "../limitup-precursor-research/research_cache/universe_full.json",
]
TOP_N = 20  # 2026-09-04 사용자 요청("추천 종목 20개로 확장") -- 기존 15개에서 확대
SPARKLINE_DAYS = 40  # 종목별 최근 가격 흐름 미니 그래프용


def _resolve_path(candidates, label):
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(
        f"{label}을(를) 찾을 수 없습니다. 시도한 경로: " + ", ".join(candidates)
        + " -- limitup-precursor-research 저장소가 이 저장소와 같은 부모 폴더에 클론돼 있는지 확인하세요."
    )


def _resolve_cache_path():
    return _resolve_path(CACHE_PATH_CANDIDATES, "콜라 캐시(limitup_ohlcv_cache.pkl)")


def _load_name_to_code():
    """2026-09-04 사용자 요청("각종목 코드번호 추가") -- 콜라 유니버스 목록(name/code/market)을
    읽기전용 재사용해서 name->code 매핑을 만든다. 못 찾으면 빈 dict(코드 칸은 "-"로 표시)."""
    import json
    try:
        path = _resolve_path(UNIVERSE_PATH_CANDIDATES, "콜라 유니버스 목록(universe_full.json)")
    except FileNotFoundError:
        return {}
    with open(path, encoding="utf-8") as f:
        universe = json.load(f)
    return {item["name"]: item["code"] for item in universe if item.get("name") and item.get("code")}


def zigzag_swings(closes, threshold=THRESHOLD):
    if len(closes) < 2:
        return []
    swings = [(0, closes[0])]
    direction = None
    extreme_idx, extreme_price = 0, closes[0]
    for i in range(1, len(closes)):
        c = closes[i]
        if direction is None:
            if c >= extreme_price * (1 + threshold):
                direction = "up"
            elif c <= extreme_price * (1 - threshold):
                direction = "down"
            if direction:
                swings.append((i, c))
                extreme_idx, extreme_price = i, c
            elif c > extreme_price:
                extreme_idx, extreme_price = i, c
            elif c < extreme_price:
                extreme_idx, extreme_price = i, c
        elif direction == "up":
            if c > extreme_price:
                extreme_idx, extreme_price = i, c
            elif c <= extreme_price * (1 - threshold):
                swings[-1] = (extreme_idx, extreme_price)
                direction = "down"
                swings.append((i, c))
                extreme_idx, extreme_price = i, c
        else:
            if c < extreme_price:
                extreme_idx, extreme_price = i, c
            elif c >= extreme_price * (1 + threshold):
                swings[-1] = (extreme_idx, extreme_price)
                direction = "up"
                swings.append((i, c))
                extreme_idx, extreme_price = i, c
    return swings


def current_position(dates_idx, closes):
    """지금(캐시 마지막 날) 기준 진행 중인 다리 정보. swings[-2]->swings[-1]이 진행 중인 구간."""
    swings = zigzag_swings(closes)
    if len(swings) < 2:
        return None
    idx_start, price_start = swings[-2]
    idx_end, price_end = swings[-1]
    leg_dir = "up" if price_end >= price_start else "down"
    leg_pct = (price_end / price_start - 1) * 100
    leg_days = idx_end - idx_start
    return {
        "leg_dir": leg_dir, "leg_pct": leg_pct, "leg_days": leg_days,
        "leg_start_date": dates_idx[idx_start], "leg_start_price": price_start,
        "leg_end_price": price_end,
    }


def touch_depth_now(closes, lows, pos):
    """진행 중인 다리에서 극값 대비 최신 종가의 되돌림 깊이(%p). down이면 저점 대비 반등폭,
    up이면 고점 대비 눌림폭 -- DEPTH_BANDS 방향 정의와 동일."""
    if pos["leg_dir"] == "down":
        return max(0.0, (closes[-1] / lows[-1] - 1) * 100) if lows[-1] else None
    return None  # 이번 버전은 하락다리(반등기대)만 다룬다 -- 저울 공식이 이 방향으로만 검증됨


def volume_ratio_at(volumes):
    if len(volumes) < 21:
        return None
    base = volumes[-21:-1]
    base_med = statistics.median(base)
    if not base_med:
        return None
    return volumes[-1] / base_med


def recent_fast_reversal_active(closes, fast_days=5):
    swings = zigzag_swings(closes)
    if len(swings) < 4:
        return False
    idx_m4, _ = swings[-4]
    idx_m3, _ = swings[-3]
    return (idx_m3 - idx_m4) <= fast_days


def zz_extra_score(vr, fast_rev):
    if vr is None:
        score = 0
    elif vr < 1.0:
        score = -1
    elif vr < 2.0:
        score = 0
    elif vr < 4.0:
        score = 2
    else:
        score = 1
    if fast_rev:
        score += 2
    return score


def year_range_position_pct(closes, highs, lows):
    window = min(len(closes), 252)
    yr_high = max(highs[-window:])
    yr_low = min(lows[-window:])
    cur = closes[-1]
    if yr_high <= yr_low:
        return None
    return max(0.0, min(100.0, (cur - yr_low) / (yr_high - yr_low) * 100))


def build_report():
    with open(_resolve_cache_path(), "rb") as f:
        cache = pickle.load(f)
    name_to_code = _load_name_to_code()

    rows = []
    for name, df in cache.items():
        try:
            closes = df["Close"].tolist()
            highs = df["High"].tolist()
            lows = df["Low"].tolist()
            volumes = df["Volume"].tolist()
            dates_idx = df.index
        except Exception:
            continue
        if len(closes) < 60:
            continue

        pos = current_position(dates_idx, closes)
        if not pos or pos["leg_dir"] != "down":
            continue
        depth_pct = touch_depth_now(closes, lows, pos)
        if depth_pct is None or depth_pct < MIN_DEPTH:
            continue

        entry_price = closes[-1]
        day_low = lows[-1]
        same_day_recovery = (entry_price - day_low) / day_low * 100 if day_low else 0.0
        risk_flag = same_day_recovery >= RISK_RECOVERY_MIN

        vr = volume_ratio_at(volumes)
        fast_rev = recent_fast_reversal_active(closes)
        extra = zz_extra_score(vr, fast_rev)
        score = max(-5, min(5, extra - (TUG_OF_WAR_RISK_PENALTY if risk_flag else 0)))

        yr_pos = year_range_position_pct(closes, highs, lows)

        rows.append({
            "name": name, "code": name_to_code.get(name, "-"), "score": score,
            "leg_dir": pos["leg_dir"], "leg_pct": pos["leg_pct"],
            "leg_days": pos["leg_days"], "depth_pct": depth_pct, "cur_price": entry_price,
            "yr_pos": yr_pos, "risk_flag": risk_flag, "vr": vr, "fast_rev": fast_rev,
            "last_date": dates_idx[-1], "recent_closes": closes[-SPARKLINE_DAYS:],
        })

    rows.sort(key=lambda r: (-r["score"], -r["depth_pct"]))
    return rows[:TOP_N], len(rows)


def render_text(top_rows, total_candidates):
    lines = [
        f"저울 -- 코스피+코스닥 전체 상위 {len(top_rows)}개 (하락다리·반등기대 후보, "
        f"전체 후보 {total_candidates}종목 중)",
        f"기준일: {top_rows[0]['last_date'].date() if top_rows else '-'}",
        "",
    ]
    for i, r in enumerate(top_rows, 1):
        tier = "강한이김" if r["score"] >= 2 else ("약한이김" if r["score"] == 1 else
                                                  ("비김" if r["score"] == 0 else "짐"))
        yr_pos_part = f" | 52주위치 {r['yr_pos']:.0f}%" if r["yr_pos"] is not None else ""
        lines.append(
            f"{i}. {r['name']}({r['code']}) | 저울점수 {r['score']:+d}({tier}) | "
            f"하락다리 {r['leg_days']}일째, {r['leg_pct']:+.1f}% | 되돌림깊이 {r['depth_pct']:.1f}%p"
            f"{yr_pos_part}"
        )
    return "\n".join(lines)


TIER_COLOR = {"강한이김": ("#0a8a3c", "#e8f5eb"), "약한이김": ("#7b6b1a", "#fbf3d8"),
              "비김": ("#898781", "#f0efe9"), "짐": ("#c0392b", "#fbe9e7")}


def _tier_of(score):
    if score >= 2:
        return "강한이김"
    if score == 1:
        return "약한이김"
    if score == 0:
        return "비김"
    return "짐"


def _seesaw_svg(score):
    """2026-09-04 사용자 요청("사이트에 맞는 그래프... 저울 시소") -- 점수(-5~+5)를 실제
    시소(받침점+빔) 기울기로 시각화. 오른쪽(양수, 오를 이유)이 무거우면 오른쪽이 내려가고,
    왼쪽(음수, 내릴 이유)이 무거우면 왼쪽이 내려간다 -- 기존 줄다리기 가로게이지(ZZ)와 달리
    "저울" 이름과 직접 맞는 형태로 새로 디자인."""
    clamped = max(-5, min(5, score))
    angle = -clamped * 3.2  # 점수 5당 16도 정도 기울임(양수=오른쪽이 아래로 -> 화면상 시계반대)
    color = "#0a8a3c" if clamped > 0 else ("#c0392b" if clamped < 0 else "#898781")
    left_r = 3 + max(0, -clamped) * 0.9
    right_r = 3 + max(0, clamped) * 0.9
    return f'''<svg width="64" height="40" viewBox="0 0 64 40">
      <polygon points="32,24 27,36 37,36" fill="#c9c6ba"/>
      <g transform="rotate({angle:.1f} 32 22)">
        <line x1="6" y1="22" x2="58" y2="22" stroke="{color}" stroke-width="2.5" stroke-linecap="round"/>
        <circle cx="6" cy="22" r="{left_r:.1f}" fill="{'#c0392b' if clamped < 0 else '#c9c6ba'}"/>
        <circle cx="58" cy="22" r="{right_r:.1f}" fill="{'#0a8a3c' if clamped > 0 else '#c9c6ba'}"/>
      </g>
    </svg>'''


def _sparkline_svg(closes):
    """최근 가격 흐름(최대 SPARKLINE_DAYS일) 미니 선그래프. 별도 라이브러리 없이 순수 SVG
    polyline -- V3/콜라 다른 화면들도 자체완결형 SVG 차트를 쓰는 것과 같은 톤."""
    if len(closes) < 2:
        return ""
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1.0
    w, h, pad = 120, 32, 3
    step = (w - 2 * pad) / (len(closes) - 1)
    points = []
    for i, c in enumerate(closes):
        x = pad + i * step
        y = pad + (1 - (c - lo) / span) * (h - 2 * pad)
        points.append(f"{x:.1f},{y:.1f}")
    color = "#0a8a3c" if closes[-1] >= closes[0] else "#c0392b"
    last_x, last_y = points[-1].split(",")
    return f'''<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">
      <polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="1.5"/>
      <circle cx="{last_x}" cy="{last_y}" r="2" fill="{color}"/>
    </svg>'''


def render_html(top_rows, total_candidates):
    """2026-09-04 사용자 요청("사이트를 만들어서 거기서 볼 수 있게") -- GitHub Pages로 배포할
    정적 HTML. V3/콜라 대시보드 계열과 같은 톤(단순 표+색 배지)으로 통일, 별도 프레임워크 없이
    자체완결형 파일 하나."""
    base_date = top_rows[0]["last_date"].date() if top_rows else "-"
    strong_count = sum(1 for r in top_rows if r["score"] >= 2)
    rows_html = []
    for i, r in enumerate(top_rows, 1):
        tier = _tier_of(r["score"])
        color, bg = TIER_COLOR[tier]
        yr_pos_html = f"{r['yr_pos']:.0f}%" if r["yr_pos"] is not None else "-"
        rows_html.append(f'''<tr>
      <td>{i}</td>
      <td style="font-weight:700;">{r['name']}</td>
      <td style="color:#898781;font-variant-numeric:tabular-nums;">{r['code']}</td>
      <td><span style="color:{color};background:{bg};border-radius:6px;padding:2px 8px;font-weight:700;">
          {r['score']:+d} {tier}</span></td>
      <td>{_seesaw_svg(r['score'])}</td>
      <td>{_sparkline_svg(r.get('recent_closes') or [])}</td>
      <td>하락다리 {r['leg_days']}일째</td>
      <td style="color:{'#0a8a3c' if r['leg_pct'] >= 0 else '#c0392b'};">{r['leg_pct']:+.1f}%</td>
      <td>{r['depth_pct']:.1f}%p</td>
      <td>{yr_pos_html}</td>
      <td style="text-align:right;">{r['cur_price']:,.0f}</td>
    </tr>''')

    return f'''<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>저울 -- 코스피+코스닥 상위 {len(top_rows)}</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; background:#faf9f6; color:#1c1d1f;
         max-width: 1180px; margin: 0 auto; padding: 24px 16px; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  .sub {{ color:#70706a; font-size: 13px; margin-bottom: 20px; }}
  .table-scroll {{ overflow-x: auto; }}
  table {{ width: 100%; min-width: 900px; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align:left; border-bottom: 2px solid #1c1d1f; padding: 8px 6px; color:#5a5650;
       white-space: nowrap; }}
  td {{ border-bottom: 1px solid #e5e3dc; padding: 8px 6px; vertical-align: middle; }}
  .note {{ margin-top: 20px; font-size: 12.5px; color:#898781; line-height:1.6; }}
</style>
</head><body>
  <h1>⚖ 저울 -- 코스피+코스닥 상위 {len(top_rows)}</h1>
  <div class="sub">기준일 {base_date} · 하락다리(반등기대) 후보 {total_candidates}종목 중 상위 {len(top_rows)} ·
    강한이김(≥2점) {strong_count}개</div>
  <div class="table-scroll">
  <table>
    <tr><th>#</th><th>종목명</th><th>코드</th><th>저울점수</th><th>시소</th><th>최근흐름</th>
        <th>기간</th><th>다리등락%</th>
        <th>되돌림깊이</th><th>52주위치</th><th style="text-align:right;">현재가</th></tr>
    {"".join(rows_html)}
  </table>
  </div>
  <div class="note">
    ≥2점(강한이김)만 실측상 신뢰할 수 있는 신호입니다(도달률 72.8%/평균 +0.49%) -- 1점 이하는
    평균이 오히려 마이너스였습니다. 매일 17:10(KST) 자동 갱신됩니다.
    <br>공식 검증 근거: <a href="https://github.com/riskmgr12345-beop/scale-project">scale-project 저장소</a>
  </div>
</body></html>'''


if __name__ == "__main__":
    top_rows, total = build_report()
    text = render_text(top_rows, total)
    with open("scale_top15_report.txt", "w", encoding="utf-8") as f:
        f.write(text)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(render_html(top_rows, total))
    print(text)
