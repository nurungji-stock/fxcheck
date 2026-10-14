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

def judge(rows):
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
                rows = source(code); j = judge(rows)
                return dict(name=disp, cat=cat, code=code, mkt=mkt, days=rows[:7], **j)
            except Exception as e:
                err = str(e); time.sleep(1 + i)
        return dict(name=name, cat=cat, code=code, err=err)

    with cf.ThreadPoolExecutor(8) as ex: rows = list(ex.map(job, universe))
    ok = [r for r in rows if "err" not in r]; bad = [r for r in rows if "err" in r]
    print("ok", len(ok), "bad", len(bad), [(b["name"], b["err"]) for b in bad][:15])

    for r in ok: cache[r["name"]] = [r["code"], r["name"], r["mkt"]]
    json.dump(cache, open("codes.json", "w", encoding="utf-8"), ensure_ascii=False)

    ok.sort(key=lambda r: (-r["st"], r["ss"], -r["days"][0]["frgn"]))
    kst = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    compact = [[r["name"], r["cat"], r["code"], r["mkt"], r["mark"], r["txt"], r["st"], r["ss"], round(r["s3"]), round(r["s5"]),
                [[d["d"], d["close"], round(d["rate"], 2), round(d["inst"]), round(d["frgn"]), round(d["hold"], 2)] for d in r["days"]]] for r in ok]
    data = dict(updated=kst.strftime("%Y-%m-%d %H:%M"), base=ok[0]["days"][0]["d"] if ok else "", count=len(ok),
                rows=compact, missing=[b["name"] for b in bad if b["cat"]])
    json.dump(data, open("data.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    if len(ok) < len(universe) * 0.5: sys.exit("절반 이상 실패 — 차단 의심")

if __name__ == "__main__": main()
