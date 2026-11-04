"""내일 관심 종목 — 매일 장 마감 후 자동 (picks.json)

관심 종목 = 매수 추천이 아님. 조건을 통과한 후보만 보여주고,
이 중 다음 날 실제로 수급이 들어온 종목은 소통방에서 매수 타점과 함께 공유.

조건 (바닥 + 수급 + 추세):
  · 120일 고점보다 25% 이상 빠졌다가 60일 바닥을 찍고 5거래일 이상 지남
  · 종가가 20일선 위, 20일선이 5일 전보다 올라감, 20일선보다 10% 넘게 뜨진 않음
  · 당일 10% 넘게 급등한 종목 제외 (추격 방지)
  · 최근 5거래일 중 3일 이상 외국인+기관 합산 순매수
  · 1,000원 이상 · 20일 평균 거래대금 10억 이상 · 스팩/우선주 제외
  → 5일 순매수량 ÷ 20일 평균 거래량이 큰 순서로 하루 TOPK종목

다음 날 체크: 관심 종목에 그다음 거래일 외국인·기관 순매수가 들어왔는지 (✓/✗) 자동 표시.
외국인·기관 수치는 장 마감 뒤 늦게 확정되므로 18시 이후에 돌림.
"""
import json, os, datetime as dt
from concurrent.futures import ThreadPoolExecutor

TOPK, LIQ, KEEP = 5, 10e8, 30
RULE = dict(dd=-0.25, since=5, flow_days=3, max_ext=0.10, max_up=0.10)
UA = {"User-Agent": "Mozilla/5.0"}


def excl(n):
    return "스팩" in n or n.endswith("우") or n.endswith("우B") or "(전환)" in n or "ETN" in n


def check(c, t, flow):
    """조건 통과하면 근거(dict), 아니면 None. c: [[d,o,h,l,c,v],...] 과거→최신, flow: {YYYYMMDD: [외국인, 기관]}"""
    if t < 125: return None
    cl = c[t][4]
    if cl < 1000: return None
    w20 = c[t - 20:t]
    if sum(r[4] * r[5] for r in w20) / 20 < LIQ: return None
    av = sum(r[5] for r in w20) / 20
    hi120 = max(r[2] for r in c[t - 120:t])
    w60 = c[t - 60:t + 1]
    lo_i = min(range(61), key=lambda i: w60[i][3])
    dd = w60[lo_i][3] / hi120 - 1
    if dd > RULE["dd"] or 60 - lo_i < RULE["since"]: return None
    ma = lambda k: sum(r[4] for r in c[k - 19:k + 1]) / 20
    m0, m5 = ma(t), ma(t - 5)
    if not (cl > m0 and m0 > m5) or cl / m0 - 1 > RULE["max_ext"]: return None
    if cl / c[t - 1][4] - 1 > RULE["max_up"]: return None
    fr = ins = 0; days = 0; both = 0
    for r in c[t - 4:t + 1]:
        f = flow.get(r[0].replace("-", ""))
        if not f: return None
        days += (f[0] + f[1]) > 0; both += f[0] > 0 and f[1] > 0
        fr += f[0]; ins += f[1]
    if days < RULE["flow_days"]: return None
    return dict(sc=(fr + ins) / max(av, 1), dd=round(dd, 3), up=round(cl / w60[lo_i][3] - 1, 3),
                days=days, both=both, fr=fr > 0, ins=ins > 0)


def num(s):
    try: return float(str(s).replace(",", "").replace("+", "") or 0)
    except Exception: return 0.0


def main():
    import fetch
    kst = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=9)
    test = os.environ.get("PDATE")
    today = test or kst.strftime("%Y-%m-%d")
    if not test and kst.hour < 18:
        print("외국인·기관 확정 전 (18시 이후 실행)"); return
    try: P = json.load(open("picks.json", encoding="utf-8"))
    except Exception: P = {}
    days = P.get("days", {})
    codes = json.load(open("codes.json", encoding="utf-8"))
    items = list({v[0]: (v[0], v[1], v[2]) for v in codes.values()
                  if isinstance(v, list) and len(v) >= 3 and len(v[0]) == 6 and not excl(v[1])}.values())

    def cand(it):
        try: cd = fetch.candles_daum(it[0], 140)
        except Exception: return None
        if not cd: return None
        return it, [[r["d"], r["o"], r["h"], r["l"], r["c"], r["v"]] for r in cd if r["d"] <= today]
    with ThreadPoolExecutor(8) as ex:
        got = {g[0][0]: g for g in ex.map(cand, items) if g and len(g[1]) >= 130}
    tdays = sorted({r[0] for g in got.values() for r in g[1]})
    if not tdays or tdays[-1] != today:
        print("오늘 장 안 열림:", tdays[-1] if tdays else None); return

    flows = {}
    def get_flow(code):
        if code in flows: return flows[code]
        try:
            j = fetch.S.get(f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize=10", timeout=15, headers=UA).json()
            f = {r["bizdate"]: [num(r["foreignerPureBuyQuant"]), num(r["organPureBuyQuant"])] for r in j}
        except Exception: f = None
        flows[code] = f; return f

    # 1) 아직 안 만든 최근 거래일 관심 종목 (보통 오늘 하루, 처음엔 어제까지 같이)
    for day in [d for d in tdays[-2:] if d not in days]:
        pre = []
        for code, (it, c) in got.items():
            i = next((k for k in range(len(c) - 1, -1, -1) if c[k][0] == day), None)
            if i is None: continue
            cc = c[:i + 1]
            if check(cc, len(cc) - 1, {r[0].replace("-", ""): [1, 1] for r in cc[-5:]}) is None: continue
            pre.append((it, cc))

        def one(x):
            it, cc = x
            f = get_flow(it[0])
            if not f: return None
            why = check(cc, len(cc) - 1, f)
            return None if why is None else (why, it, cc)
        with ThreadPoolExecutor(6) as ex:
            hits = sorted([h for h in ex.map(one, pre) if h], key=lambda h: -h[0]["sc"])[:TOPK]
        days[day] = [dict(code=it[0], n=it[1], m=it[2], c=cc[-1][4], **{k: v for k, v in why.items() if k != "sc"})
                     for why, it, cc in hits]
        print(day, "가격 조건", len(pre), "→ 관심", [p["n"] for p in days[day]])

    # 2) 다음 거래일 수급 체크 (✓/✗)
    for d, ps in days.items():
        nxt = next((x for x in tdays if x > d), None)
        if not nxt: continue
        for p in ps:
            if "next" in p: continue
            f = get_flow(p["code"])
            v = f.get(nxt.replace("-", "")) if f else None
            if not v: continue
            p["next"] = dict(d=nxt, fr=v[0] > 0, ins=v[1] > 0)

    keep = sorted(days)[-KEEP:]
    P = dict(days={d: days[d] for d in keep}, upd=kst.strftime("%m/%d %H:%M") if not test else today + " 테스트", rule=RULE)
    json.dump(P, open("picks.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    # 3) 텔레그램 (선택)
    tok, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if tok and chat and today in days:
        lines = [f"[{today} 장 마감 · 내일 관심 종목]"] + [
            f"{p['n']} ({p['code']}) · 외국인·기관 5일 중 {p['days']}일 매수 · 바닥 대비 +{round(p['up'] * 100)}%" for p in days[today]]
        prev = [x for x in sorted(days) if x < today]
        if prev:
            ok = [p["n"] for p in days[prev[-1]] if p.get("next") and (p["next"]["fr"] or p["next"]["ins"])]
            lines.append(f"\n[어제 관심 → 오늘 수급 들어옴] " + (", ".join(ok) or "없음"))
        import requests
        try: requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data=dict(chat_id=chat, text="\n".join(lines)), timeout=15)
        except Exception as e: print("텔레그램 실패", e)


if __name__ == "__main__":
    main()
