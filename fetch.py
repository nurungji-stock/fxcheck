# -*- coding: utf-8 -*-
"""외국인·기관 매매동향 수집 → data.json  (GitHub Actions에서 자동 실행)"""
import json, re, io, sys, time, datetime, concurrent.futures as cf
import requests, pandas as pd

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept": "application/json, text/html, */*", "Accept-Language": "ko-KR,ko;q=0.9"}
S = requests.Session(); S.headers.update(HDR)
norm = lambda s: re.sub(r"\s+", "", str(s)).lower()

def num(x):
    try: return float(str(x).replace(",", "").replace("+", "").replace("%", "").strip())
    except: return 0.0

# ───────── 종목코드 목록: 1) KRX  2) 네이버  3) 다음 검색 ─────────
def krx_codes():
    codes = {}
    for mt, label in (("stockMkt", "코스피"), ("kosdaqMkt", "코스닥")):
        r = S.get(f"https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType={mt}",
                  headers={"Referer": "https://kind.krx.co.kr/"}, timeout=30)
        r.encoding = "euc-kr"
        df = pd.read_html(io.StringIO(r.text), header=0)[0]
        for n, c in zip(df["회사명"], df["종목코드"]):
            codes[norm(n)] = (str(c).zfill(6), str(n).strip(), label)
    return codes

def naver_codes():
    codes = {}
    for sosok in (0, 1):
        for p in range(1, 80):
            r = S.get(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={p}",
                      headers={"Referer": "https://finance.naver.com/"}, timeout=15)
            r.encoding = "euc-kr"
            found = re.findall(r'item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)<', r.text)
            if not found: break
            for c, n in found: codes[norm(n)] = (c, n.strip(), "코스피" if sosok == 0 else "코스닥")
            time.sleep(0.2)
    return codes

def daum_search(name):
    """다음 금융 검색 API로 종목코드 찾기 (여러 엔드포인트 시도)"""
    h = {"Referer": "https://finance.daum.net/"}
    for url in (f"https://finance.daum.net/api/search/quotes?q={name}&limit=10",
                f"https://finance.daum.net/api/quote/search?q={name}&limit=10"):
        try:
            r = S.get(url, headers=h, timeout=10)
            if r.status_code != 200: continue
            j = r.json()
            hits = []
            def walk(o):
                if isinstance(o, dict):
                    sc = o.get("symbolCode") or o.get("code")
                    nm = o.get("name") or o.get("shortName")
                    if sc and nm and re.fullmatch(r"A?\d{6}", str(sc)):
                        hits.append((str(sc)[-6:], str(nm).strip(), o.get("market", "") or o.get("exchange", "")))
                    for v in o.values(): walk(v)
                elif isinstance(o, list):
                    for v in o: walk(v)
            walk(j)
            if not hits: continue
            for c, n, m in hits:
                if norm(n) == norm(name): return c, n, m
            return hits[0]
        except Exception:
            continue
    return None

# ───────── 종목별 매매동향: 네이버 / 다음 ─────────
def trend_naver(code):
    r = S.get(f"https://finance.naver.com/item/frgn.naver?code={code}", headers={"Referer": "https://finance.naver.com/"}, timeout=15)
    if r.status_code != 200: raise RuntimeError(f"HTTP {r.status_code}")
    r.encoding = "euc-kr"
    if "frgn" not in r.text and "외국인" not in r.text: raise RuntimeError("차단된 페이지")
    for t in pd.read_html(io.StringIO(r.text)):
        if t.shape[1] != 9: continue
        t = t.copy(); t.columns = range(9)
        t = t[t[0].astype(str).str.match(r"\d{4}\.\d{2}\.\d{2}")]
        if len(t) == 0: continue
        return [dict(d=str(a), close=num(b), rate=num(c), inst=num(e), frgn=num(f), hold=num(g))
                for a, b, c, e, f, g in zip(t[0], t[1], t[3], t[5], t[6], t[8])][:10]
    raise RuntimeError("표 없음")

DAUM_DEBUG = [False]
def trend_daum(code):
    h = {"Referer": f"https://finance.daum.net/quotes/A{code}"}
    r = S.get(f"https://finance.daum.net/api/investor/days?symbolCode=A{code}&perPage=10&page=1&pagination=true", headers=h, timeout=15)
    if r.status_code != 200: raise RuntimeError(f"daum HTTP {r.status_code}")
    data = r.json().get("data", [])
    if not data: raise RuntimeError("daum 빈 응답")
    if not DAUM_DEBUG[0]:
        DAUM_DEBUG[0] = True
        print("DAUM SAMPLE KEYS:", json.dumps(data[0], ensure_ascii=False)[:1500])
    out = []
    for it in data:
        keys = {k.lower(): k for k in it}
        def pick(*parts, exclude=()):
            for kl, k in keys.items():
                if all(p in kl for p in parts) and not any(x in kl for x in exclude):
                    return num(it[k])
            return 0.0
        rate = pick("changerate")
        if abs(rate) < 1: rate *= 100          # 0.0123 형태면 %로
        out.append(dict(
            d=str(it.get("date", ""))[:10].replace("-", "."),
            close=pick("tradeprice"),
            rate=rate,
            inst=pick("institution", "straight", "volume") or pick("institution", "volume", exclude=("ratio",)),
            frgn=pick("foreign", "straight", "volume") or pick("foreign", "volume", exclude=("own", "ratio")),
            hold=pick("foreign", "own", "rate") or pick("foreign", "ratio")))
    # 보유율이 0.46 처럼 비율로 오면 %로, 등락률이 비어 있으면 종가로 직접 계산
    for i, r in enumerate(out):
        if 0 < abs(r["hold"]) < 1: r["hold"] *= 100
        if r["rate"] == 0 and i + 1 < len(out) and out[i + 1]["close"]:
            r["rate"] = (r["close"] / out[i + 1]["close"] - 1) * 100
    return out


# ───────── 일봉 + MACD ─────────
def _pick(it, *cands):
    for k in cands:
        if k in it: return num(it[k])
    kl = {k.lower(): k for k in it}
    for k in cands:
        for lk, ok in kl.items():
            if k.lower() in lk: return num(it[ok])
    return 0.0

def candles_daum(code, n=130):
    """일봉 과거→최신: dict(d, o, h, l, c, v)"""
    sym = code if not code.isdigit() else "A" + code
    h = {"Referer": f"https://finance.daum.net/quotes/{sym}"}
    for url in (f"https://finance.daum.net/api/charts/{sym}/days?limit={n}&adjusted=true",
                f"https://finance.daum.net/api/quote/{sym}/days?symbolCode={sym}&page=1&perPage={n}&pagination=true"):
        try:
            r = S.get(url, headers=h, timeout=15)
            if r.status_code != 200: continue
            j = r.json()
            data = j.get("data") if isinstance(j, dict) else j
            if not isinstance(data, list) or not data: continue
            rows = []
            for it in data:
                if not isinstance(it, dict): continue
                d = str(it.get("date") or it.get("candleTime") or it.get("tradeDate") or "")[:10]
                c = _pick(it, "tradePrice", "closePrice", "close")
                if not d or not c: continue
                rows.append(dict(d=d, c=c, o=_pick(it, "openingPrice", "openPrice", "open") or c,
                                 h=_pick(it, "highPrice", "high") or c, l=_pick(it, "lowPrice", "low") or c,
                                 v=_pick(it, "candleAccTradeVolume", "accTradeVolume", "tradeVolume", "volume")))
            if len(rows) < 40: continue
            rows.sort(key=lambda x: x["d"])
            # 장 시작 전엔 오늘 봉이 거래량 0으로 미리 생김 → 버림 (장중엔 거래량이 있으니 그대로 씀)
            while rows and rows[-1]["v"] <= 0: rows.pop()
            if not CANDLE_DEBUG[0]:
                CANDLE_DEBUG[0] = True
                print("CANDLE SAMPLE KEYS:", json.dumps(data[0], ensure_ascii=False)[:600])
                print("CANDLE PARSED:", rows[-1])
            return rows
        except Exception:
            continue
    return None
CANDLE_DEBUG = [False]

def macd_calc(closes, fast=12, slow=26, sig=9):
    def ema(xs, n):
        k = 2 / (n + 1); e = xs[0]; out = [e]
        for x in xs[1:]: e = x * k + e * (1 - k); out.append(e)
        return out
    ef, es = ema(closes, fast), ema(closes, slow)
    m = [a - b for a, b in zip(ef, es)]
    s = ema(m[slow - 1:], sig)
    s = [None] * (slow - 1) + s
    return m, s

def macd_info(rows):
    closes = [r["c"] for r in rows]
    if len(closes) < 40: return None
    m, s = macd_calc(closes)
    last = len(m) - 1
    def cross_up(a, b):      # a가 b를 아래→위로 돌파한 날이 며칠 전인지 (0=오늘), 10일 내 없으면 -1
        for back in range(0, 10):
            i = last - back
            if i < 1 or a[i] is None or a[i-1] is None or b[i] is None or b[i-1] is None: continue
            if a[i-1] <= b[i-1] and a[i] > b[i]: return back
        return -1
    zero = [0.0] * len(m)
    zc = cross_up(m, zero)
    sc = cross_up(m, s)
    scale = closes[-1] or 1
    return [round(m[last] / scale * 100, 2), round((s[last] or 0) / scale * 100, 2), zc, sc, 1 if m[last] > 0 else 0, rows[-1]["d"]]


def rsi14(closes, n=14):
    if len(closes) < n + 2: return None
    g = l = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i-1]
        if d > 0: g += d
        else: l -= d
    ag, al = g / n, l / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i-1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0: return 100.0
    return 100 - 100 / (1 + ag / al)

def bottom_info(cd, inv):
    """바닥 점수 0~4: 이격도≤90 / RSI≤30 / 투매거래량+아래꼬리 / 외국인 매도 소진·전환"""
    closes = [r["c"] for r in cd]
    if len(closes) < 25: return None
    last = cd[-1]
    ma20 = sum(closes[-20:]) / 20
    gap = closes[-1] / ma20 * 100 if ma20 else 100
    rsi = rsi14(closes) or 50
    vols = [r["v"] for r in cd[-21:-1]]
    avgv = sum(vols) / len(vols) if vols and sum(vols) > 0 else 0
    rng = last["h"] - last["l"]
    lower_wick = (min(last["o"], last["c"]) - last["l"]) / rng if rng > 0 else 0
    climax = 1 if (avgv and last["v"] >= 2 * avgv and lower_wick >= 0.4) else 0
    newlow = 1 if cd[-1]["l"] <= min(r["l"] for r in cd[-21:-1]) else 0
    f = [r["frgn"] for r in inv if r.get("frgn") is not None][:4]
    exhaust = 0
    if len(f) >= 3:
        if f[0] > 0 and f[1] < 0: exhaust = 1                                   # 매도 → 매수 전환
        elif f[0] < 0 and f[1] < 0 and f[2] < 0 and abs(f[0]) < abs(f[1]) < abs(f[2]): exhaust = 1   # 매도 규모 3일 연속 감소
    score = (1 if gap <= 90 else 0) + (1 if rsi <= 30 else 0) + climax + exhaust
    # 가치 하락(회사 사건) 걸러내기: 이격도 75 밑 / 60일 고점 대비 −40% / 하루 거래대금
    hi60 = max(r["h"] for r in cd[-60:])
    drop60 = round((1 - last["c"] / hi60) * 100, 1) if hi60 else 0
    tv = sum(r["c"] * r["v"] for r in cd[-20:]) / 20 / 1e8          # 20일 평균 거래대금(억)
    bad = 0
    if gap < 60: bad = 1            # 평균보다 40% 넘게 빠짐 = 감자·거래정지급 사건
    elif drop60 >= 45: bad = 2      # 두 달 새 45% 넘게 빠짐 = 회사 사건
    elif tv < 1: bad = 3            # 거래가 거의 없음
    # 주도주였나: 6개월 전 가격 대비 최근 두 달 고점 상승률, 120일선 위에 있나
    c120 = closes[-120] if len(closes) >= 120 else closes[0]
    rise = round((hi60 / c120 - 1) * 100, 1) if c120 else 0
    ma120 = sum(closes[-120:]) / min(120, len(closes))
    above120 = 1 if last["c"] >= ma120 else 0
    # 종합 점수 100점: 이격도 30 · RSI 25 · 거래량/꼬리 20 · 외국인 10 · 주도주 15
    def lin(x, x0, x1, p):          # x0→0점, x1→p점 (사이는 비례, 밖은 잘라냄)
        r = (x - x0) / (x1 - x0)
        return p * max(0.0, min(1.0, r))
    volx = last["v"] / avgv if avgv else 0
    total = (lin(gap, 100, 80, 30) + lin(rsi, 50, 20, 25) + lin(volx, 1, 3, 15) + (5 if lower_wick >= 0.4 else 0)
             + (10 if exhaust else (5 if len(f) >= 2 and f[0] < 0 and f[1] < 0 and abs(f[0]) < abs(f[1]) else 0))
             + lin(rise, 0, 100, 15))
    return [score, round(rsi, 1), round(gap, 1), climax, exhaust, newlow, bad, drop60, round(tv, 1), rise, above120, round(total)]


def kospi_history(cd):
    """코스피 일봉으로 날짜별 4지표 — 과거 사례 검증용 (history.json)"""
    out = []
    closes = [r["c"] for r in cd]
    for i in range(25, len(cd)):
        sub = cd[:i + 1]; cl = closes[:i + 1]; last = sub[-1]
        ma20 = sum(cl[-20:]) / 20
        vols = [r["v"] for r in sub[-21:-1]]; avgv = sum(vols) / len(vols) if vols and sum(vols) > 0 else 0
        rng = last["h"] - last["l"]
        wick = round((min(last["o"], last["c"]) - last["l"]) / rng, 2) if rng > 0 else 0
        out.append(dict(d=last["d"], close=last["c"], gap=round(last["c"] / ma20 * 100, 1), rsi=round(rsi14(cl) or 50, 1),
                        vol_x=round(last["v"] / avgv, 2) if avgv else None, wick=wick, low=last["l"]))
    return out


def state_info(cd, inv, bot):
    """상태 판정 + 내 종목 진단
    반환 [0 state(1 들고/2 하루더/3 줄임/0 해당없음), 1 바닥일, 2 바닥대비%, 3 바닥점수, 4 바닥일(동일),
          5 사유, 6 반등기대점수(job에서 채움), 7 바닥일 이격도, 8 바닥일 RSI, 9 바닥일 투매꼬리(0/1),
          10 바닥 종가, 11 현재가, 12 외국인 상태(1 매수·2 매도 급감·0 매도 중·-1 집계없음), 13 최근 외국인 수량, 14 직전 외국인 수량,
          15 외국인 연속 매수일]"""
    closes = [r["c"] for r in cd]
    n = len(closes)
    if n < 40 or not bot: return None
    last = closes[-1]
    fl = [r for r in inv if r.get("frgn") is not None]
    f0 = fl[0]["frgn"] if fl else None
    f1 = fl[1]["frgn"] if len(fl) > 1 else None
    fst = 0
    for r in fl:
        if r["frgn"] > 0: fst += 1
        else: break
    if f0 is None: fstate = -1
    elif f0 > 0: fstate = 1
    elif f1 is not None and f1 < 0 and abs(f0) < abs(f1) * 0.5: fstate = 2
    else: fstate = 0
    tail = [round(last), fstate, f0, f1, fst]
    if bot[6]: return [3, "", 0, 0, "", "회사 사건 종목 — 수급이 아니라 회사 문제라 이 진단이 안 맞습니다", 0, 0, 0, 0, 0] + tail
    flows = {r["d"]: r for r in fl}
    best = None   # 바닥 신호가 있던 날 중 종가가 제일 낮은 날
    for back in range(0, 10):
        i = n - 1 - back
        if i < 25: break
        cl = closes[:i + 1]; ma20 = sum(cl[-20:]) / 20
        gap = cl[-1] / ma20 * 100 if ma20 else 100
        rsi = rsi14(cl) or 50
        d = cd[i]["d"].replace("-", ".")
        rng = cd[i]["h"] - cd[i]["l"]
        vols = [r["v"] for r in cd[max(0, i - 20):i]]; avgv = sum(vols) / len(vols) if vols and sum(vols) > 0 else 0
        wick = (min(cd[i]["o"], cd[i]["c"]) - cd[i]["l"]) / rng if rng > 0 else 0
        climax = 1 if (avgv and cd[i]["v"] >= 2 * avgv and wick >= 0.4) else 0
        f = flows.get(d)
        pts = (1 if gap <= 90 else 0) + (1 if rsi <= 30 else 0) + climax + (1 if f and f["frgn"] > 0 else 0)
        if pts >= 2 or gap <= 92 or rsi <= 35:
            if best is None or cd[i]["c"] < best["c"]:
                best = dict(d=d, c=cd[i]["c"], gap=round(gap, 1), rsi=round(rsi, 1), climax=climax, pts=pts)
    if best is None:
        rise5 = last / closes[-6] - 1 if n > 6 else 0
        if rise5 >= 0.15:
            return [3, "", round(rise5 * 100, 1), 0, "", f"바닥 신호 없이 5일 만에 {rise5*100:.0f}% 급등 — 급등 뒤엔 조정이 옵니다", 0, 0, 0, 0, 0] + tail
        return [0, "", 0, 0, "", "최근 열흘 동안 크게 눌린 적이 없는 종목 — 평소대로 대응", 0, 0, 0, 0, 0] + tail
    rebound = (last / best["c"] - 1) * 100
    ma5 = sum(closes[-5:]) / 5
    head = [best["d"], round(rebound, 1), best["pts"], best["d"]]
    mid = [0, best["gap"], best["rsi"], best["climax"], round(best["c"])]
    if last < best["c"] * 0.97:
        return [3] + head + [f"{best['d'][5:]} 바닥을 다시 깼습니다 — 바닥이 아니었습니다"] + mid + tail
    rebounded = rebound >= 3 or (last > ma5 and rebound > 0)
    if rebounded and fstate in (1, 2):
        return [1] + head + ["바닥 확인 · 반등 확인 · 외국인 매도 멈춤"] + mid + tail
    if rebounded:
        return [2] + head + ["바닥 확인 · 반등 확인 · 외국인은 아직 파는 중"] + mid + tail
    return [2] + head + ["바닥 신호는 나왔지만 아직 반등 전"] + mid + tail

def judge(rows):
    rows = [r for r in rows if r.get("frgn") is not None]
    if not rows: return dict(mark="○", txt="수급 집계 전", st=0, ss=0, s3=0, s5=0)
    f = [r["frgn"] for r in rows]
    st = 0
    for v in f:
        if v > 0: st += 1
        else: break
    ss = 0
    for v in f:
        if v < 0: ss += 1
        else: break
    t = rows[0]
    if st >= 3: mark, txt = "●", f"외국인 {st}일 연속 순매수"
    elif st: mark, txt = "◐", f"외국인 {st}일째 순매수"
    elif ss >= 3: mark, txt = "○", f"외국인 {ss}일 연속 순매도"
    else: mark, txt = "○", "외국인 오늘 순매도"
    if st == 0 and t["inst"] > 0: txt += " · 기관은 매수"
    return dict(mark=mark, txt=txt, st=st, ss=ss, s3=sum(f[:3]), s5=sum(f[:5]))

def main():
    stocks = json.load(open("stocks.json", encoding="utf-8"))          # [[이름, 분류], ...]
    try: SEC = json.load(open("sectors.json", encoding="utf-8"))        # {이름: 섹터}
    except Exception: SEC = {}
    SECN = {norm(k): v for k, v in SEC.items()}
    ALIAS = {"에코프로머티": "에코프로머티리얼즈"}

    # 코드 캐시 (이전 실행 결과 재사용)
    try: cache = json.load(open("codes.json", encoding="utf-8"))
    except Exception: cache = {}
    codes = {}
    for name, fn in (("krx", krx_codes), ("naver", naver_codes)):
        try:
            c = fn(); print(f"{name} codes: {len(c)}")
            if len(c) >= 1000: codes = c; break
        except Exception as e: print(f"{name} codes 실패: {e}")
    if not codes: print("전체 목록 실패 → 캐시/다음 검색으로 개별 조회")

    # 데이터 소스 결정
    source = None
    for name, fn in (("naver", trend_naver), ("daum", trend_daum)):
        try:
            rows = fn("005930"); source = fn; print("source:", name, "| 삼성전자 샘플:", rows[0]); break
        except Exception as e: print(name, "불가:", e)
    if source is None: sys.exit("데이터 소스 전부 실패")

    def resolve(name):
        k = norm(ALIAS.get(name, name))
        if k in codes: return codes[k]
        if name in cache: return tuple(cache[name])
        hit = daum_search(ALIAS.get(name, name))
        if hit: return hit
        return None

    cat_of = {norm(n): c for n, c in stocks}
    # 전 종목: KRX 목록 전체 + (목록에 없으면) 분류기 종목
    universe = []
    if codes:
        seen = set()
        for k, (code, disp, mkt) in codes.items():
            universe.append((disp, cat_of.get(k, 0), (code, disp, mkt))); seen.add(k)
        for n, c in stocks:
            if norm(ALIAS.get(n, n)) not in seen: universe.append((n, c, None))
    else:
        universe = [(n, c, None) for n, c in stocks]
    print("universe", len(universe))

    def job(item):
        name, cat, hit = item
        hit = hit or resolve(name)
        if not hit: return dict(name=name, cat=cat, err="코드 없음")
        code, disp, mkt = hit[0], hit[1] or name, hit[2] if len(hit) > 2 else ""
        err = ""
        for i in range(3):
            try:
                rows = source(code)
                mi = bi = si = None
                try:
                    cd = candles_daum(code)
                    if cd:
                        # 투자자별 집계가 아직 안 올라온 날은 종가만 먼저 표에 넣고 수급 칸은 '집계 전'
                        cdd = cd[-1]["d"].replace("-", ".")
                        if rows and cdd > rows[0]["d"]:
                            prev = rows[0]["close"] or cd[-2]["c"]
                            rows.insert(0, dict(d=cdd, close=cd[-1]["c"], rate=(cd[-1]["c"] / prev - 1) * 100 if prev else 0,
                                                inst=None, frgn=None, hold=rows[0]["hold"]))
                        mi = macd_info(cd); bi = bottom_info(cd, rows)
                        si = state_info(cd, rows, bi)
                        if si is not None and bi:
                            # 반등 기대 점수 100: 고점 대비 낙폭 40 · 올해 주도주 25 · 바닥 상태 15 · 지금 눌림 20
                            def _lin(x, x0, x1, p):
                                r = (x - x0) / (x1 - x0); return p * max(0.0, min(1.0, r))
                            pot = _lin(bi[7], 10, 50, 40) + _lin(bi[9], 0, 100, 25) + (15 if si[0] == 1 else 8 if si[0] == 2 else 0) + _lin(100 - bi[2], 0, 25, 20)
                            si[6] = round(pot)
                except Exception as e:
                    print("candle err", code, e)
                j = judge(rows)
                sec = SECN.get(norm(name)) or SECN.get(norm(disp)) or ""
                return dict(name=disp, cat=cat, code=code, mkt=mkt, days=rows[:7], macd=mi, bottom=bi, sec=sec, state=si, **j)
            except Exception as e:
                err = str(e); time.sleep(1 + i)
        return dict(name=name, cat=cat, code=code, err=err)

    with cf.ThreadPoolExecutor(8) as ex: rows = list(ex.map(job, universe))
    ok = [r for r in rows if "err" not in r]; bad = [r for r in rows if "err" in r]
    # 같은 종목코드가 두 이름으로 들어온 경우 하나로 (분류기 이름 우선, 그다음 짧은 이름)
    byc = {}
    for r in ok:
        k = r["code"]
        if k not in byc or (r["cat"] and not byc[k]["cat"]) or (bool(r["cat"]) == bool(byc[k]["cat"]) and len(r["name"]) < len(byc[k]["name"])):
            byc[k] = r
    ok = list(byc.values())
    print("ok", len(ok), "bad", len(bad), [(b["name"], b["err"]) for b in bad][:15])

    for r in ok: cache[r["name"]] = [r["code"], r["name"], r["mkt"]]
    json.dump(cache, open("codes.json", "w", encoding="utf-8"), ensure_ascii=False)

    ok.sort(key=lambda r: (-r["st"], r["ss"], -(r["days"][0]["frgn"] or 0)))
    kst = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    compact = [[r["name"], r["cat"], r["code"], r["mkt"], r["mark"], r["txt"], r["st"], r["ss"], round(r["s3"]), round(r["s5"]),
                [[d["d"], d["close"], round(d["rate"], 2), None if d["inst"] is None else round(d["inst"]), None if d["frgn"] is None else round(d["frgn"]), round(d["hold"], 2)] for d in r["days"]], r.get("macd"), r.get("bottom"), r.get("sec", ""), r.get("state")] for r in ok]
    import collections as _c
    print("STATE", _c.Counter((r.get("state") or [None])[0] for r in ok))
    print("macd ok", sum(1 for r in ok if r.get("macd")), "/", len(ok))
    # 시장 패닉 게이지
    withb = [r for r in ok if r.get("bottom")]
    gauge = None
    if withb:
        n = len(withb)
        kospi_gap = None
        try:
            kc = None
            for sym in ("KOSPI", "069500"):      # 코스피 지수 → 안 되면 KODEX200으로 대신
                kc = candles_daum(sym, 260)
                if kc: print("kospi proxy:", sym); break
            if kc:
                cl = [r["c"] for r in kc]; kospi_gap = round(cl[-1] / (sum(cl[-20:]) / 20) * 100, 1)
                hist = kospi_history(kc)
                json.dump(hist, open("history.json", "w", encoding="utf-8"), ensure_ascii=False)
                for h in hist:
                    if h["d"][5:] in ("06-12", "06.12", "07-08", "07.08", "07-21", "07.21", "07-28", "07.28", "10-13", "10.13", "10-14", "10.14", "10-15", "10.15"):
                        print("KOSPI", h)
        except Exception as e: print("kospi hist err", e)
        gaps = sorted(r["bottom"][2] for r in withb)
        gauge = dict(n=n, rsi30=round(100 * sum(1 for r in withb if r["bottom"][1] <= 30) / n, 1),
                     low20=round(100 * sum(1 for r in withb if r["bottom"][5]) / n, 1),
                     gap90=round(100 * sum(1 for r in withb if r["bottom"][2] <= 90) / n, 1),
                     median_gap=gaps[n // 2], kospi_gap=kospi_gap,
                     score3=sum(1 for r in withb if r["bottom"][0] >= 3 and not r["bottom"][6]))
        print("GAUGE", gauge)
    inv_base = next((d["d"] for d in ok[0]["days"] if d["frgn"] is not None), "") if ok else ""
    px_base = ok[0]["days"][0]["d"] if ok else ""
    data = dict(updated=kst.strftime("%Y-%m-%d %H:%M"), base=px_base + ("" if inv_base == px_base else f" 종가 · 수급은 {inv_base[5:]}까지"), count=len(ok),
                gauge=gauge, rows=compact, missing=[b["name"] for b in bad if b["cat"]])
    json.dump(data, open("data.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    if len(ok) < len(universe) * 0.5: sys.exit("절반 이상 실패 — 차단 의심")

if __name__ == "__main__": main()
