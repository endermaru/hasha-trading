# main.py
# uvicorn main:app --reload
import logging
from fastapi import FastAPI, BackgroundTasks
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from contextlib import asynccontextmanager
import pandas as pd

# --- 모듈 임포트 ---
# 각 기능별로 분리된 모듈을 가져옵니다.
import state_manager
import predictor
import trader

# --- 기본 설정 ---
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone='UTC')

MIN_CANDLE_COUNT = 400

# --- 핵심 트레이딩 로직 ---
def run_trading_logic():
    """
    모듈들을 조립하여 전체 트레이딩 사이클을 실행합니다.
    """
    logger.info("="*50)
    logger.info("Starting trading logic cycle...")

    # 1. 상태 로드 (from state_manager)
    recent_candles_df = state_manager.load_recent_candles()

    # 2. 최신 데이터 가져오기 (from trader)
    portfolio_state = trader.fetch_portfolio_state()
    new_candle = trader.fetch_latest_candle()
    if new_candle is None or portfolio_state is None:
        logger.error("Could not fetch new candle. Skipping this cycle.")
        return

    # 3. 데이터 업데이트 및 관리
    updated_candles = pd.concat([recent_candles_df, new_candle])
    updated_candles = updated_candles[~updated_candles.index.duplicated(keep='last')]
    updated_candles = updated_candles.iloc[-MIN_CANDLE_COUNT:]
    
    # 4. 신호 생성 (from predictor)
    signal = predictor.generate_signal(updated_candles)

    # 5. 주문 실행 (from trader)
    trade_log = trader.execute_order_dummy(signal, portfolio_state)

    # 6. 상태 저장 (from state_manager)
    state_manager.save_recent_candles(updated_candles)

    if trade_log:
        state_manager.record_trade_log(trade_log)

    logger.info("Trading logic cycle finished.")
    logger.info("="*50)

# --- FastAPI 앱 생명주기 관리 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 초기화 로직, 종료 시 정리 로직을 수행합니다."""
    # Startup
    predictor.initialize_predictor()
    
    # 3. CronTrigger를 사용하여 정확한 시간에 작업 예약
    # hour='*/4' -> 4시간 간격으로 (0, 4, 8, 12, 16, 20시)
    # minute='1' -> 정각에 API 트래픽이 몰리는 것을 피해 1분에 실행
    scheduler.add_job(
        run_trading_logic, 
        CronTrigger(hour='*/4', minute='1'), # <--- 이 부분이 변경됩니다.
        id="trading_job"
    )
    
    scheduler.start()
    logger.info("Scheduler started with CronTrigger. Trading logic will run every 4 hours on the clock (at minute 1).")
    yield
    # Shutdown
    scheduler.shutdown()
    logger.info("Scheduler shut down.")

# --- FastAPI 앱 및 API 엔드포인트 ---
app = FastAPI(title="Trading Bot", lifespan=lifespan)

@app.get("/")
def read_root():
    return {"status": "Trading bot is running"}

@app.post("/trade/trigger", tags=["Trading"])
async def trigger_manual_trade(background_tasks: BackgroundTasks):
    """트레이딩 로직을 1회 수동으로 실행합니다."""
    logger.info("Manual trade trigger requested via API.")
    background_tasks.add_task(run_trading_logic)
    return {"status": "success", "message": "Trading logic triggered in the background."}

@app.get("/status/portfolio", tags=["Status"])
def get_portfolio_status():
    """현재 포트폴리오 상태를 조회합니다."""
    logger.info("Fetching portfolio status via API.")
    return trader.fetch_portfolio_state()

@app.get("/status/logs", tags=["Status"])
def get_trade_logs():
    """거래 기록을 조회합니다."""
    log_path = state_manager.LOG_PATH
    if not log_path.exists():
        return {"message": "Log file not found."}
    try:
        logs_df = pd.read_csv(log_path)
        return logs_df.to_dict(orient='records')
    except pd.errors.EmptyDataError:
        return {"message": "Log file is empty."}
    except Exception as e:
        return {"error": str(e)}

@app.get("/status/scheduler", tags=["Status"])
def get_scheduler_status():
    """스케줄러 상태를 조회합니다."""
    job = scheduler.get_job("trading_job")
    if not job:
        return {"is_scheduled": False}
    return {
        "is_scheduled": True,
        "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
        "is_running": scheduler.running
    }
