# Hasha Trading Bot
이 저장소는 `lstm_strategies_prediction.ipynb`에서 학습/검증한 LSTM 기반 전략을 `trading-bot` FastAPI 서버로 운영하는 프로젝트입니다.  
README는 다음 순서로 구성되어 있습니다.

## 1) 학습 과정 소개 (`lstm_strategies_prediction.ipynb`)
노트북 `notebook/lstm_strategies_prediction.ipynb`는 아래 흐름으로 전략을 만듭니다.

1. 데이터 불러오기 및 리샘플링
2. 4시간봉/일봉 기반 피처 생성 (`pandas_ta` 활용)
3. 스케일링 등 데이터 전처리 (`MinMaxScaler`)
4. 삼중장벽 기반 라벨 생성: 손실/유지/이익
5. LSTM 분류 모델 구성 및 훈련
6. 테스트셋 예측
7. 검증셋 기반 threshold 탐색
8. `vectorbt` 기반 테스트/전체 구간 전략 검증 및 벤치마크 비교

트레이딩 봇 서버는 위 노트북 결과물로 생성된 아티팩트를 로드해 사용합니다.
- `trading-bot/lstm/best_mtf_model_ver1.1.h5`
- `trading-bot/lstm/best_mtf_scaler_ver1.1.pkl`

## 2) 트레이딩 봇 기능/구성 (`trading-bot`)
트레이딩 봇의 코드는 `trading-bot` 디렉터리에 있으며, 역할은 아래와 같습니다.

- `main.py`
  - FastAPI 엔드포인트 제공
  - APScheduler로 4시간마다(UTC 기준, 1분) 자동 매매 루프 실행
- `predictor.py`
  - 기술지표 피처 생성 (4H + 1D 멀티타임프레임)
  - LSTM 추론으로 `BUY` / `SELL` / `HOLD` 신호 생성
- `trader.py`
  - 업비트 캔들/포트폴리오 조회
  - 시장가 매수/매도 주문 실행 및 체결 확인
- `state_manager.py`
  - 최근 캔들/거래 로그를 CSV(`trading-bot/data/`)로 저장
- `slack_bot.py`
  - 거래 결과, 포트폴리오, 스케줄 상태를 Slack으로 전송
  - 상태 조회/로그 조회 인터랙션 지원

주요 API 예시:
- `/`: 서버 상태 확인
- `/trade/prediction`: 현재 신호/확률 조회
- `/trade/trigger`: 수동 트레이드 실행
- `/status/scheduler`: 스케줄러 상태 조회
- `/status/portfolio`, `/status/logs`: 포트폴리오/거래 로그 조회

## 3) 서버(`trading-bot`)만 빠르게 실행하기
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

## 4) 전체 저장소 + notebook까지 실행하기
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
