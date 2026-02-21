# 서버(`trading-bot`)만 빠르게 실행하기
실제 트레이딩 봇이 돌아가는 FastAPI 서버만 실행하려면 아래 순서로 진행하세요.

### 빠른 실행
```bash
git clone https://github.com/endermaru/hasha-trading.git
cd hasha-trading
python -m venv venv
source venv/bin/activate
pip install -r trading-bot/requirements.txt
cd trading-bot
uvicorn main:app --reload --host 0.0.0.0
```

### 동작 확인
- 브라우저에서 `/` 접속
- 예측 확인: `/trade/prediction`
- 스케줄러 상태 확인: `/status/scheduler`

참고: 실제 매매/알림 연동을 위해 `.env`에 API 키 및 Slack 관련 값이 필요합니다.

## 전체 저장소 + notebook까지 실행하기
테스트/학습 코드가 담긴 notebook 파일을 확인하려면 전체 저장소와 데이터 파일이 필요합니다.

### 1. 저장소 받기
```bash
git clone https://github.com/endermaru/hasha-trading.git
cd hasha-trading
```

### 2. 데이터 다운로드 후 배치
- Google Drive: https://drive.google.com/file/d/1k7G4g6WlBFgmk7y6VjvGll9CuG1tWfBP/view?usp=sharing
- 다운로드한 `data.zip`을 풀고, 내부 파일을 `notebook/data`에 넣어주세요.

주요 파일:
- `BTC_1m_data.csv`: 업비트 전체 데이터(2017-09-25 00:00 ~ 2025-08-17 23:59:00, 약 354MB)
- `merged_market_data.csv`: 90일치 비트코인/이더리움/리플/달러인덱스/금 선물/S&P500/Nasdaq 지수(약 55MB)

### 3. notebook 실행
노트북에서 사용하는 파이썬 환경을 준비한 뒤 `notebook/` 아래 파일을 실행하세요.
```bash
python -m venv venv
source venv/bin/activate
pip install -r trading-bot/requirements.txt
```

그다음 Jupyter 환경에서 `notebook/`의 `.ipynb` 파일을 열어 순서대로 실행하세요.
