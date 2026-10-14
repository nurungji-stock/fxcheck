# 외국인 연속매수 체크
네이버 금융 투자자별 매매동향을 평일 장중 10분마다 수집해 `data.json`에 저장하고, `index.html`이 그걸 보여줍니다.
- 종목 목록·분류: `stocks.json` (이름, 분류번호 1~4)
- 수집: `fetch.py` / 스케줄: `.github/workflows/update.yml`
