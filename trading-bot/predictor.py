# predictor.py

import logging
import joblib
import numpy as np
import pandas as pd
import pandas_ta as ta  # pandas_ta 라이브러리 사용
from keras.models import load_model

# --- 설정값 ---
LOOKBACK_WINDOW = 30
ENTRY_THRESHOLD = 0.2  # 최적화된 값으로 수정하세요
EXIT_THRESHOLD = 0.42   # 최적화된 값으로 수정하세요

 # 일봉 SMA(50) 계산에 필요한 최소 300개와 약간의 여유분을 고려합니다.
# LOOKBACK_WINDOW(30)보다 훨씬 긴 기간이 필요합니다.
MIN_CANDLE_COUNT = 400 # 50일 * 6개/일 = 300개 + 여유분

# --- 전역 객체 로딩 ---
MODEL = None
SCALER = None
FIT_FEATURE_NAMES = None
logger = logging.getLogger(__name__)

def initialize_predictor():
    """
    봇 시작 시 모델과 스케일러를 메모리에 로드합니다.
    이 함수는 main.py에서 앱 시작 시 한번만 호출됩니다.
    """
    global MODEL, SCALER, FIT_FEATURE_NAMES
    try:
        logger.info("Loading ML model and scaler for the predictor...")
        MODEL = load_model('./lstm/best_mtf_model_ver1.1.h5')
        MODEL.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'])
        
        SCALER = joblib.load('./lstm/best_mtf_scaler_ver1.1.pkl')
        FIT_FEATURE_NAMES = SCALER.feature_names_in_
        logger.info("Predictor initialized successfully.")
        return True
    except Exception as e:
        logger.error(f"Error initializing predictor: {e}")
        return False

def generate_signal(candles_4h: pd.DataFrame) -> str:
    """
    주어진 캔들 데이터로 피처를 생성하고 모델 예측을 통해 신호를 반환합니다.
    """
    if MODEL is None or SCALER is None:
        logger.error("Predictor is not initialized. Cannot generate signal.")
        return 'hold'

    if len(candles_4h) < MIN_CANDLE_COUNT:
        logger.warning(f"Not enough candle data ({len(candles_4h)}) to create features. Need at least {MIN_CANDLE_COUNT}.")
        return 'hold'

    logger.info("Creating features for prediction...")
    
    # 1. 피처 생성
    features_4h = candles_4h.copy()
    features_4h.ta.rsi(length=14, append=True, col_names=('RSI_14_4H',))
    features_4h.ta.macd(fast=12, slow=26, signal=9, append=True, col_names=('MACD_12_26_9_4H', 'MACDh_12_26_9_4H', 'MACDs_12_26_9_4H'))
    features_4h.ta.bbands(length=20, std=2, append=True, col_names=('BBL_20_2.0_4H', 'BBM_20_2.0_4H', 'BBU_20_2.0_4H', 'BBB_20_2.0_4H', 'BBP_20_2.0_4H'))

    daily_resampled = candles_4h.resample('D').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
    })
    features_1d = daily_resampled.copy()
    features_1d.ta.rsi(length=14, append=True, col_names=('RSI_14_1D',))
    features_1d.ta.sma(length=50, append=True, col_names=('SMA_50_1D',))
    features_1d.ta.adx(length=14, append=True, col_names=('ADX_14_1D', 'DMP_14_1D', 'DMN_14_1D'))
    
    daily_indicator_cols = ['RSI_14_1D', 'SMA_50_1D', 'ADX_14_1D', 'DMP_14_1D', 'DMN_14_1D']
    features_1d_to_merge = features_1d[daily_indicator_cols]
    
    final_features = pd.merge(features_4h, features_1d_to_merge, left_index=True, right_index=True, how='left')
    final_features.ffill(inplace=True)
    final_features.dropna(inplace=True)

    if len(final_features) < LOOKBACK_WINDOW:
        logger.warning("Not enough data after feature creation to form a sequence.")
        return 'hold'

    # 2. 예측에 사용할 마지막 시퀀스 준비
    last_sequence_features = final_features[FIT_FEATURE_NAMES].tail(LOOKBACK_WINDOW)
    
    if len(last_sequence_features) < LOOKBACK_WINDOW:
        logger.warning("Sequence is shorter than lookback window. Skipping prediction.")
        return 'hold'

    scaled_features = SCALER.transform(last_sequence_features)
    X = np.array([scaled_features])

    # 3. 예측 수행
    logger.info("Predicting with the model...")
    prediction = MODEL.predict(X)[0]
    prob_loss, prob_hold, prob_profit = prediction[0], prediction[1], prediction[2]
    logger.info(f"Prediction probabilities -> Loss: {prob_loss:.4f}, Hold: {prob_hold:.4f}, Profit: {prob_profit:.4f}")

    # 4. 신호 결정
    if prob_profit > ENTRY_THRESHOLD:
        return 'buy'
    elif prob_loss > EXIT_THRESHOLD:
        return 'sell'
    else:
        return 'hold'
