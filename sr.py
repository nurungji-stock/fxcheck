"""지지·저항 자동 탐지 — 여러 기법이 겹치는 가격대를 찾아 점수를 매김.

입력: 일봉 리스트 [{d,o,h,l,c,v}, ...] (오래된 → 최신)
출력: [{p, score, up, why:[근거...]}, ...]

쓰는 기법
  1. 전고점·전저점 (스윙 고점/저점, 같은 자리에 여러 번 닿을수록 강함)
  2. 매물대 (가격대별 거래량, 거래가 몰린 자리)
  3. 피보나치 되돌림 (최근 큰 파동의 23.6 / 38.2 / 50 / 61.8 / 78.6%)
  4. 이동평균선 (20 / 60 / 120일)
  5. 갭 (아직 메워지지 않은 갭의 경계)
  6. 장대봉 (장대양봉·장대음봉의 시가와 절반값)
  7. 라운드 넘버 (심리적 가격대, 다른 근거와 겹칠 때만 의미)
"""


def _atr(cd, n=14):
    tr = []
    for i in range(1, len(cd)):
        h, l, pc = cd[i]["h"], cd[i]["l"], cd[i - 1]["c"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    tr = tr[-n:]
    return sum(tr) / len(tr) if tr else 0


def _ma(cl, k):
    return sum(cl[-k:]) / k if len(cl) >= k else None


def tick(p):
    t = (1000 if p >= 500000 else 500 if p >= 200000 else 100 if p >= 50000 else
         50 if p >= 20000 else 10 if p >= 5000 else 5 if p >= 2000 else 1)
    return int(round(p / t) * t)


def _round_step(p):
    for s in (100000, 50000, 10000, 5000, 1000, 500, 100, 50, 10):
        if p / s >= 8:
            return s
    return 1


ORDER = ["공모가", "상장", "석 달", "매물대", "피", "20", "60", "120", "상승", "하락", "장대", "라운드"]

def _pick(C, cd, px, atr, max_each=3, span=0.30, min_score=2.5, tol_cap=None, max_dist=0.2):
    n = len(cd)
    hi = [x["h"] for x in cd]
    lo = [x["l"] for x in cd]
    # 범위 밖 제거
    C = [c for c in C if abs(c[0] / px - 1) <= span and abs(c[0] / px - 1) >= 0.004]
    C.sort(key=lambda x: x[0])

    # 가까운 후보끼리 묶기 (하루 평균 움직임의 0.5배, 최소 1%)
    tol = max(atr * 0.5, px * 0.01) if tol_cap is None else min(max(atr * 0.5, px * 0.01), px * tol_cap)
    groups = []
    for c in C:
        if groups:
            g = groups[-1]
            cen = sum(x[0] * x[1] for x in g) / sum(x[1] for x in g)
            if abs(c[0] - cen) <= tol:
                g.append(c)
                continue
        groups.append([c])

    out = []
    for g in groups:
        kinds = {x[3] for x in g}
        if kinds == {"round"}:
            continue
        wsum = sum(x[1] for x in g)
        # 같은 기법이 여러 번 겹치면 가산을 줄임 (전고점 3번 = 2 + 1.2 + 1.2 ...)
        score = 0.0
        seen = {}
        for x in sorted(g, key=lambda z: -z[1]):
            cnt = seen.get(x[3], 0)
            score += x[1] * (1.0 if cnt == 0 else 0.6)
            seen[x[3]] = cnt + 1
        # 대표 가격: 라운드 넘버 제외 가중평균
        core = [x for x in g if x[3] != "round"] or g
        p = sum(x[0] * x[1] for x in core) / sum(x[1] for x in core)
        # 근거 이름 정리
        why = []
        sw_hi = sum(1 for x in g if x[2] == "전고점")
        sw_lo = sum(1 for x in g if x[2] == "전저점")
        if sw_hi: why.append("전고점" + (f" {sw_hi}번" if sw_hi > 1 else ""))
        if sw_lo: why.append("전저점" + (f" {sw_lo}번" if sw_lo > 1 else ""))
        rnd_close = any(x[3] == "round" and abs(x[0] / p - 1) <= 0.005 for x in g)
        for name in sorted({x[2] for x in g if x[3] not in ("swing",) and (x[3] != "round" or rnd_close)},
                           key=lambda s: ORDER.index(
                               next(t for t in ORDER if s.startswith(t)))):
            why.append(name)
        out.append(dict(p=tick(p), score=round(score, 1), up=p > px, why=why,
                        touches=sum(1 for i in range(max(0, n - 120), n) if lo[i] <= p * 1.005 and hi[i] >= p * 0.995)))

    # 위·아래 각각: 가장 가까운 의미 있는 선 1개 + 점수 높은 선, 서로 3% 이상 떨어지게
    res = []
    for up in (True, False):
        side = [o for o in out if o["up"] == up and o["score"] >= min_score]
        if not side:
            continue
        pick = [min(side, key=lambda o: abs(o["p"] / px - 1))]
        for o in sorted(side, key=lambda o: -o["score"]):
            if len(pick) >= max_each:
                break
            if o["score"] >= min_score + 1.5 and abs(o["p"] / px - 1) <= max_dist and all(abs(o["p"] / q["p"] - 1) >= 0.03 for q in pick):
                pick.append(o)
        res += pick
    for o in res:
        o["grade"] = "강" if o["score"] >= 7 else "중" if o["score"] >= 4.5 else "약"
    return sorted(res, key=lambda o: -o["p"])

def find_levels(cd, max_each=3, span=0.30, ipo=None):
    n = len(cd)
    if n < 60:
        return ipo_levels(cd, ipo, max_each, span)
    px = cd[-1]["c"]
    atr = _atr(cd)
    hi = [x["h"] for x in cd]
    lo = [x["l"] for x in cd]
    op = [x["o"] for x in cd]
    cl = [x["c"] for x in cd]
    vo = [x.get("v") or 0 for x in cd]
    C = []  # (price, weight, reason, kind)

    # 1. 스윙 고점·저점 — 앞뒤 5봉 안에서 제일 높은/낮은 봉
    k = 5
    for i in range(k, n - k):
        rec = 1.0 + 0.5 * (i > n - 60)  # 최근 석 달 안의 고·저점은 가중
        if hi[i] == max(hi[i - k:i + k + 1]):
            C.append((hi[i], 2.0 * rec, "전고점", "swing"))
        if lo[i] == min(lo[i - k:i + k + 1]):
            C.append((lo[i], 2.0 * rec, "전저점", "swing"))

    # 1-2. 최근 석 달 바닥·꼭대기 — 사람들이 제일 기억하는 가격
    C.append((min(lo[-60:]), 2.5, "석 달 바닥", "swing2"))
    C.append((max(hi[-60:]), 2.5, "석 달 꼭대기", "swing2"))

    # 2. 매물대 — 최근 120봉 거래량을 가격 구간에 나눠 담기
    seg = cd[-120:]
    pmin, pmax = min(x["l"] for x in seg), max(x["h"] for x in seg)
    nb = 40
    if pmax > pmin:
        w = (pmax - pmin) / nb
        bins = [0.0] * nb
        for x in seg:
            a = int((x["l"] - pmin) / w)
            b = min(nb - 1, int((x["h"] - pmin) / w))
            a = min(a, nb - 1)
            share = (x.get("v") or 0) / (b - a + 1)
            for j in range(a, b + 1):
                bins[j] += share
        avg = sum(bins) / nb
        for j in range(nb):
            left = bins[j - 1] if j > 0 else 0
            right = bins[j + 1] if j < nb - 1 else 0
            if bins[j] >= left and bins[j] >= right and bins[j] > avg * 1.25:
                C.append((pmin + w * (j + 0.5), 1.5 + min(2.0, bins[j] / avg - 1), "매물대", "vp"))

    # 3. 피보나치 — 최근 120봉 최고점·최저점 사이 되돌림
    H = max(hi[-120:])
    L = min(lo[-120:])
    ih = n - 120 + hi[-120:].index(H)
    il = n - 120 + lo[-120:].index(L)
    if H > L:
        for r, wgt in ((0.236, 1.0), (0.382, 2.0), (0.5, 2.0), (0.618, 2.5), (0.786, 1.5)):
            p = H - r * (H - L) if ih > il else L + r * (H - L)
            C.append((p, wgt, f"피보나치 {r * 100:.1f}%".replace(".0%", "%"), "fib"))

    # 4. 이동평균선
    for kk, wgt in ((20, 1.5), (60, 2.0), (120, 2.0)):
        m = _ma(cl, kk)
        if m:
            C.append((m, wgt, f"{kk}일선", "ma"))

    # 5. 갭 — 최근 120봉 중 아직 안 메워진 갭
    for i in range(max(1, n - 120), n):
        if lo[i] > hi[i - 1] and min(lo[i:]) > hi[i - 1]:
            C.append((hi[i - 1], 2.0, "상승갭 하단", "gap"))
        if hi[i] < lo[i - 1] and max(hi[i:]) < lo[i - 1]:
            C.append((lo[i - 1], 2.0, "하락갭 상단", "gap"))

    # 6. 장대봉 — 몸통이 하루 평균 움직임의 1.8배 이상인 봉 (최근 80봉)
    for i in range(max(1, n - 80), n):
        body = cl[i] - op[i]
        if abs(body) >= 1.8 * atr and atr:
            mid = (op[i] + cl[i]) / 2
            if body > 0:
                C.append((op[i], 1.5, "장대양봉 시가", "candle"))
                C.append((mid, 1.5, "장대양봉 절반", "candle"))
            else:
                C.append((op[i], 1.5, "장대음봉 시가", "candle"))
                C.append((mid, 1.5, "장대음봉 절반", "candle"))

    # 8. 공모가 — 상장 1년 안 된 종목
    if ipo:
        C.append((ipo, 3.0, "공모가", "ipo"))

    # 7. 라운드 넘버
    st = _round_step(px)
    base = int(px // st) * st
    for j in range(-6, 8):
        p = base + j * st
        if p > 0:
            C.append((p, 0.7, "라운드 넘버", "round"))

    return _pick(C, cd, px, atr, max_each, span)



def ipo_levels(cd, ipo=None, max_each=3, span=0.30):
    """상장 60일이 안 된 종목: 공모가 · 상장 첫날 시가/종가 · 상장 후 최고/최저 · 짧은 고점/저점"""
    n = len(cd)
    if n < 1:
        return []
    px = cd[-1]["c"]
    hi = [x["h"] for x in cd]
    lo = [x["l"] for x in cd]
    atr = _atr(cd) if n >= 3 else px * 0.05
    C = []
    if ipo:
        C.append((ipo, 5.0, "공모가", "ipo"))
    C.append((cd[0]["o"], 3.0, "상장 첫날 시가", "ipo1"))
    if n >= 2:
        C.append((cd[0]["c"], 2.0, "상장 첫날 종가", "ipo1c"))
        C.append((max(hi), 3.5, "상장 후 최고가", "ipohi"))
        C.append((min(lo), 3.5, "상장 후 최저가", "ipolo"))
    k = 2
    for i in range(k, n - k):
        if hi[i] == max(hi[i - k:i + k + 1]):
            C.append((hi[i], 2.0, "전고점", "swing"))
        if lo[i] == min(lo[i - k:i + k + 1]):
            C.append((lo[i], 2.0, "전저점", "swing"))
    st = _round_step(px)
    base = int(px // st) * st
    for j in range(-6, 8):
        if base + j * st > 0:
            C.append((base + j * st, 0.7, "라운드 넘버", "round"))
    return _pick(C, cd, px, atr, max_each, max(span, 0.5), min_score=2.0, tol_cap=0.03, max_dist=0.5)


CODE = {"공모가": "IP", "상장 첫날 시가": "I1", "상장 첫날 종가": "IC", "상장 후 최고가": "IH", "상장 후 최저가": "IL",
        "석 달 바닥": "B", "석 달 꼭대기": "T", "매물대": "V", "20일선": "M20", "60일선": "M60", "120일선": "M120",
        "상승갭 하단": "GU", "하락갭 상단": "GD", "장대양봉 시가": "CUo", "장대양봉 절반": "CUh",
        "장대음봉 시가": "CDo", "장대음봉 절반": "CDh", "라운드 넘버": "R"}


def encode(levels):
    """data.json용 압축: [가격, 위(1)/아래(0), 강2·중1·약0, [근거코드...]]"""
    out = []
    for o in levels:
        w = []
        for x in o["why"]:
            if x.startswith("전고점") or x.startswith("전저점"):
                k = x.split(" ")
                w.append(("H" if x.startswith("전고점") else "L") + (k[1].replace("번", "") if len(k) > 1 else "1"))
            elif x.startswith("피보나치"):
                w.append("F" + x.split(" ")[1].replace("%", ""))
            else:
                w.append(CODE.get(x, x))
        out.append([o["p"], 1 if o["up"] else 0, {"강": 2, "중": 1, "약": 0}[o["grade"]], w, o.get("rec")])
    return out


def track(cd, levels):
    """각 가격대의 최근 기록: 닿은 횟수, 반응 횟수(닿고 5거래일 안에 하루 평균 움직임만큼 되돌림)
    지지는 반등, 저항은 막힘. 마지막 5거래일은 결과가 안 나와서 뺌."""
    if not cd or not levels: return
    atr = _atr(cd); px = cd[-1]["c"]; tol = max(atr * 0.5, px * 0.01); end = len(cd) - 5
    for o in levels:
        p, up = o["p"], o["up"]; n = k = 0; j = 0
        while j < end:
            x = cd[j]
            if (not up and x["l"] <= p + tol and x["h"] > p - tol * 3) or (up and x["h"] >= p - tol and x["l"] < p + tol * 3):
                res = None; t = j
                for t in range(j, min(j + 6, len(cd))):
                    c = cd[t]["c"]
                    if (not up and c < p - tol) or (up and c > p + tol): res = "b"; break
                    if (not up and c >= p + atr) or (up and c <= p - atr): res = "o"; break
                n += 1; k += res == "o"; j = t + 1
                while j < end and ((not up and cd[j]["l"] <= p + tol) or (up and cd[j]["h"] >= p - tol)): j += 1
            else:
                j += 1
        o["rec"] = [n, k]
