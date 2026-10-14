# -*- coding: utf-8 -*-
"""외국인·기관 매매동향 수집 → data.json  (GitHub Actions에서 자동 실행)"""
import json, re, io, sys, time, datetime, concurrent.futures as cf
import requests, pandas as pd

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
       "Accept-Language": "ko-KR,ko;q=0.9", "Referer": "https://finance.naver.com/"}
S = requests.Session(); S.headers.update(HDR)
norm = lambda s: re.sub(r"\s+", "", str(s)).lower()

def num(x):
    try: return float(str(x).replace(",", "").replace("+", "").replace("%", "").strip())
    except: return 0.0

# ── 종목코드 목록 (네이버 시가총액 페이지 크롤) ──
def naver_codes():
    codes = {}
    for sosok in (0, 1):
        for p in range(1, 80):
            r = S.get(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={p}", timeout=15)
            r.encoding = "euc-kr"
            found = re.findall(r'item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)<', r.text)
            if not found: break
            for c, n in found: codes[norm(n)] = (c, n.strip(), "코스피" if sosok == 0 else "코스닥")
            time.sleep(0.2)
    return codes

def krx_codes():
    r = S.get("https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13", timeout=30)
    r.encoding = "euc-kr"
    df = pd.read_html(io.StringIO(r.text), header=0)[0]
    return {norm(n): (str(c).zfill(6), str(n).strip(), "") for n, c in zip(df["회사명"], df["종목코드"])}

# ── 종목별 매매동향 ──
def trend_naver(code):
    r = S.get(f"https://finance.naver.com/item/frgn.naver?code={code}", timeout=15)
    if r.status_code != 200: raise RuntimeError(f"HTTP {r.status_code}")
    r.encoding = "euc-kr"
    for t in pd.read_html(io.StringIO(r.text)):
        if t.shape[1] != 9: continue
        t = t.copy(); t.columns = range(9)
        t = t[t[0].astype(str).str.match(r"\d{4}\.\d{2}\.\d{2}")]
        if len(t) == 0: continue
        return [dict(d=str(a), close=num(b), rate=num(c), inst=num(e), frgn=num(f), hold=num(g))
                for a, b, c, e, f, g in zip(t[0], t[1], t[3], t[5], t[6], t[8])][:10]
    raise RuntimeError("표 없음")

def trend_daum(code):
    h = dict(HDR); h["Referer"] = f"https://finance.daum.net/quotes/A{code}"
    r = S.get(f"https://finance.daum.net/api/investor/days?symbolCode=A{code}&perPage=10&page=1&pagination=true", headers=h, timeout=15)
    if r.status_code != 200: raise RuntimeError(f"daum HTTP {r.status_code}")
    out = []
    for it in r.json().get("data", []):
        def pick(*keys):
            for k in it:
                kl = k.lower()
                if all(x in kl for x in keys): return num(it[k])
            return 0.0
        out.append(dict(d=str(it.get("date", ""))[:10].replace("-", "."), close=pick("tradeprice"), rate=pick("changerate") * 100,
                        inst=pick("institution", "volume"), frgn=pick("foreign", "straight", "volume") or pick("foreign", "volume"),
                        hold=pick("foreignownshare")))
    if not out: raise RuntimeError("daum 빈 응답")
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
    try: codes = naver_codes(); src = "naver"
    except Exception as e:
        print("naver codes 실패:", e); codes = krx_codes(); src = "krx"
    print("codes", len(codes), src)

    # 어떤 소스가 열리는지 삼성전자로 먼저 테스트
    source = None
    for name, fn in (("naver", trend_naver), ("daum", trend_daum)):
        try: fn("005930"); source = fn; print("source:", name); break
        except Exception as e: print(name, "불가:", e)
    if source is None: sys.exit("데이터 소스 전부 실패")

    ALIAS = {"에코프로머티": "에코프로머티리얼즈"}
    def job(item):
        name, cat = item[0], item[1]
        k = norm(ALIAS.get(name, name))
        if k not in codes: return dict(name=name, cat=cat, err="코드 없음")
        code, disp, mkt = codes[k]
        for i in range(3):
            try:
                rows = source(code); j = judge(rows)
                return dict(name=disp, cat=cat, code=code, mkt=mkt, days=rows[:7], **j)
            except Exception as e:
                err = str(e); time.sleep(1 + i)
        return dict(name=name, cat=cat, code=code, err=err)

    with cf.ThreadPoolExecutor(6) as ex: rows = list(ex.map(job, stocks))
    ok = [r for r in rows if "err" not in r]; bad = [r for r in rows if "err" in r]
    print("ok", len(ok), "bad", len(bad), [b["name"] for b in bad][:20])
    kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    data = dict(updated=kst.strftime("%Y-%m-%d %H:%M"), base=ok[0]["days"][0]["d"] if ok else "",
                count=len(ok), rows=sorted(ok, key=lambda r: (-r["st"], r["ss"], -r["days"][0]["frgn"])),
                missing=[b["name"] for b in bad])
    json.dump(data, open("data.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    if len(ok) < len(stocks) * 0.5: sys.exit("절반 이상 실패 — 차단 의심")

if __name__ == "__main__": main()
