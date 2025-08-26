# state_manager.py

import logging
import json
import pandas as pd
from pathlib import Path
import pyupbit

# --- 설정 및 경로 정의 ---
DATA_DIR = Path("data")
CANDLE_DATA_PATH = DATA_DIR / "recent_candles.csv" 
LOG_PATH = DATA_DIR / "trade_history.csv"

# 데이터 디렉토리가 없으면 생성
DATA_DIR.mkdir(exist_ok=True)
logger = logging.getLogger(__name__)

def load_recent_candles() -> pd.DataFrame:
    """저장된 최근 캔들 데이터를 불러옵니다."""
    if not CANDLE_DATA_PATH.exists():
        logger.warning("Candle data file not found. Returning empty DataFrame.")
        return pd.DataFrame()
    try:
        df = pd.read_csv(CANDLE_DATA_PATH, encoding="utf-8-sig", index_col=0, parse_dates=True)
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
        # df.index = df.index.tz_localize('UTC') 
        logger.info(f"Loaded {len(df)} recent candles.")
        logger.info(f"Data range from {df.index[0]} to {df.index[-1]}.")
        return df
    except Exception as e:
        logger.error(f"Error loading candle data: {e}")
        return pd.DataFrame()

def save_recent_candles(df: pd.DataFrame):
    """최신 캔들 데이터를 JSON 파일로 저장합니다."""
    # orient='split'은 인덱스, 컬럼, 데이터를 분리하여 저장해 정확한 복원이 가능합니다.
    # df.to_json(CANDLE_DATA_PATH, orient="split", indent=4)
    df.to_csv(CANDLE_DATA_PATH, encoding='utf-8-sig', index=True)
    logger.info(f"Saved {len(df)} recent candles.")

def record_trade_log(log_data: dict):
    """거래 기록을 CSV 파일에 추가합니다."""
    log_df = pd.DataFrame([log_data])
    # 파일이 없으면 헤더와 함께 새로 쓰고, 있으면 헤더 없이 내용만 추가합니다.
    file_exists = LOG_PATH.exists()
    log_df.to_csv(LOG_PATH, mode='a', header=not file_exists, index=False)
    logger.info(f"Trade log recorded: {log_data}")
