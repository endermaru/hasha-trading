# main.py
# uvicorn main:app --reload --host 0.0.0.0
import hashlib
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe

import numpy as np
import pandas as pd
import pyotp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

# --- 모듈 임포트 ---
# 각 기능별로 분리된 모듈을 가져옵니다.
import predictor
import slack_bot
import state_manager
import trader

# --- 기본 설정 ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")

load_dotenv()
pswd = os.getenv("pswd")

PIN_HASH = os.getenv("PIN_HASH", "")
TOTP_SECRET = os.getenv("TOTP_SECRET", "")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "")
SESSION_TTL_MINUTES = int(os.getenv("SESSION_TTL_MINUTES", "480"))
LOGIN_RATE_LIMIT_PER_MIN = int(os.getenv("LOGIN_RATE_LIMIT_PER_MIN", "5"))
LOGIN_LOCK_THRESHOLD = int(os.getenv("LOGIN_LOCK_THRESHOLD", "5"))
LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true"

SESSION_COOKIE_NAME = "trading_session"
AUTH_SESSIONS: dict[str, datetime] = {}
LOGIN_FAILURES: dict[str, list[datetime]] = {}
LOCKED_UNTIL: dict[str, datetime] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _auth_config_ready() -> bool:
    return bool(PIN_HASH and TOTP_SECRET and SESSION_SECRET_KEY)


def _client_key(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown-client"


def _prune_old_failures(client_key: str, now: datetime) -> None:
    recent_boundary = now - timedelta(minutes=1)
    failures = LOGIN_FAILURES.get(client_key, [])
    LOGIN_FAILURES[client_key] = [ts for ts in failures if ts >= recent_boundary]


def _verify_pin(pin: str) -> bool:
    pin_hash = hashlib.sha256(pin.encode("utf-8")).hexdigest()
    return hmac.compare_digest(pin_hash, PIN_HASH)


def _verify_otp(otp_code: str) -> bool:
    totp = pyotp.TOTP(TOTP_SECRET)
    return bool(totp.verify(otp_code, valid_window=1))


def _record_login_failure(client_key: str) -> None:
    now = _utc_now()
    _prune_old_failures(client_key, now)
    LOGIN_FAILURES.setdefault(client_key, []).append(now)

    if len(LOGIN_FAILURES[client_key]) >= LOGIN_LOCK_THRESHOLD:
        LOCKED_UNTIL[client_key] = now + timedelta(minutes=LOGIN_LOCK_MINUTES)
        logger.warning("Login locked: client=%s until=%s", client_key, LOCKED_UNTIL[client_key].isoformat())


def _is_rate_limited(client_key: str) -> bool:
    now = _utc_now()
    _prune_old_failures(client_key, now)
    return len(LOGIN_FAILURES.get(client_key, [])) >= LOGIN_RATE_LIMIT_PER_MIN


def _is_locked(client_key: str) -> bool:
    now = _utc_now()
    locked_until = LOCKED_UNTIL.get(client_key)
    if not locked_until:
        return False
    if now >= locked_until:
        LOCKED_UNTIL.pop(client_key, None)
        LOGIN_FAILURES.pop(client_key, None)
        return False
    return True


def _create_session() -> tuple[str, datetime]:
    raw_sid = token_urlsafe(32)
    expires_at = _utc_now() + timedelta(minutes=SESSION_TTL_MINUTES)
    AUTH_SESSIONS[raw_sid] = expires_at
    return _sign_session_id(raw_sid), expires_at


def _sign_session_id(raw_sid: str) -> str:
    sig = hmac.new(SESSION_SECRET_KEY.encode("utf-8"), raw_sid.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw_sid}.{sig}"


def _extract_raw_session_id(signed_sid: str) -> str | None:
    if "." not in signed_sid:
        return None
    raw_sid, sig = signed_sid.rsplit(".", 1)
    expected = hmac.new(SESSION_SECRET_KEY.encode("utf-8"), raw_sid.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return raw_sid


def _get_valid_session_id(request: Request) -> str | None:
    signed_sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not signed_sid:
        return None

    raw_sid = _extract_raw_session_id(signed_sid)
    if not raw_sid:
        return None

    expires_at = AUTH_SESSIONS.get(raw_sid)
    if not expires_at:
        return None

    if _utc_now() >= expires_at:
        AUTH_SESSIONS.pop(raw_sid, None)
        return None

    return raw_sid


def _build_scheduler_status() -> dict:
    job = scheduler.get_job("trading_job")
    if not job:
        return {"is_scheduled": False}
    next_run = job.next_run_time
    now = datetime.now(next_run.tzinfo)
    time_until_next_run = next_run - now

    seconds = time_until_next_run.total_seconds()
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    remaining_str = f"{hours}시간 {minutes}분 {secs}초"

    return {
        "is_scheduled": True,
        "is_running": scheduler.running,
        "next_run_time_utc": next_run.astimezone(timezone.utc).isoformat(),
        "time_until_next_run": {
            "str": remaining_str,
            "total_seconds": round(seconds),
        },
    }


# 비밀번호 검증을 위한 의존성 함수(핵심 트레이드 API 호환 유지)
async def verify_password(password: str):
    if password != pswd:
        logger.error("Invalid password provided for API access.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid password.",
        )


def require_authenticated_session(request: Request):
    session_id = _get_valid_session_id(request)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return session_id


MIN_CANDLE_COUNT = 400

# --- 핵심 트레이딩 로직 ---
def run_trading_logic(signal=None):
    """
    모듈들을 조립하여 전체 트레이딩 사이클을 실행합니다.
    """
    logger.info("=" * 50)
    logger.info("Starting trading logic cycle...")

    # 1. 상태 로드 (from state_manager)
    recent_candles_df = state_manager.load_recent_candles()

    # 2. 데이터 업데이트 및 관리
    updated_candles = None
    if recent_candles_df.empty:
        logger.info("No existing data. Fetching initial candle history.")
        updated_candles = trader.fetch_historical_candles_simple()
        if updated_candles is None or updated_candles.empty:
            logger.error("Could not fetch initial candles. Skipping this cycle.")
            return
    else:
        last_timestamp = recent_candles_df.index[-1]
        now_utc = pd.Timestamp.now(tz="UTC")
        time_diff = now_utc - last_timestamp
        four_hours = pd.Timedelta(hours=4)
        num_to_fetch = int(time_diff / four_hours) + 2  # 여유분(buffer) 2개 추가
        logger.info(f"Last candle is {time_diff} old. Fetching {num_to_fetch} new candles.")
        new_candles = trader.fetch_latest_candle(count=num_to_fetch)
        if new_candles is None:
            logger.error("Could not fetch new candles. Skipping this cycle.")
            return
        updated_candles = pd.concat([recent_candles_df, new_candles])
        updated_candles = updated_candles[~updated_candles.index.duplicated(keep="last")]

    updated_candles = updated_candles.sort_index().iloc[-MIN_CANDLE_COUNT:]

    # 3. 최신 데이터 가져오기 (from trader)
    portfolio_state = trader.fetch_portfolio_state()
    if portfolio_state is None:
        logger.error("Could not fetch portfolio state. Skipping this cycle.")
        return

    # 4. 신호 생성 (from predictor)
    probs = [0.0, 0.0, 0.0]
    if signal is None:
        signal, probs = predictor.generate_signal(updated_candles)
    else:
        _, probs = predictor.generate_signal(updated_candles)

    # 5. 주문 실행 (from trader)
    trade_log = trader.execute_order(signal, probs, portfolio_state)

    portfolio_state = trader.fetch_portfolio_state()
    sched = get_scheduler_status()
    result_message = "🤖 봇 실행: 포트폴리오 유지"
    if trade_log["signal"] == "BUY":
        result_message = "🤖 봇 실행: 매수 주문 체결"
    elif trade_log["signal"] == "SELL":
        result_message = "🤖 봇 실행: 매도 주문 체결"
    message = slack_bot.make_slack_messages(result_message, portfolio_state, probs, sched)
    slack_bot.post_message_blocks(message)

    # 6. 상태 저장 (from state_manager)
    state_manager.save_recent_candles(updated_candles)

    if trade_log:
        state_manager.record_trade_log(trade_log)

    logger.info("Trading logic cycle finished.")
    logger.info("=" * 50)


# --- FastAPI 앱 생명주기 관리 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 초기화 로직, 종료 시 정리 로직을 수행합니다."""
    predictor.initialize_predictor()

    scheduler.add_job(
        run_trading_logic,
        CronTrigger(hour="*/4", minute="1", timezone="UTC"),
        id="trading_job",
    )

    scheduler.start()
    logger.info("Scheduler started with CronTrigger. Trading logic will run every 4 hours on the clock (at minute 1).")
    yield
    scheduler.shutdown()
    logger.info("Scheduler shut down.")


# --- FastAPI 앱 및 API 엔드포인트 ---
app = FastAPI(title="Trading Bot", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


@app.get("/")
def read_root():
    return {"status": "Trading bot is running"}


@app.get("/login")
def login_page(request: Request, error: str = ""):
    if _get_valid_session_id(request):
        return RedirectResponse(url="/view", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.get("/view")
def view_page(request: Request):
    if not _get_valid_session_id(request):
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse("view.html", {"request": request})


@app.post("/auth/login")
async def auth_login(request: Request, pin: str = Form(...), otp_code: str = Form(...)):
    if not _auth_config_ready():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth config is not ready")

    client_key = _client_key(request)

    if _is_locked(client_key):
        logger.warning("Blocked login attempt: locked client=%s", client_key)
        return RedirectResponse(url="/login?error=temporarily_locked", status_code=status.HTTP_303_SEE_OTHER)

    if _is_rate_limited(client_key):
        logger.warning("Blocked login attempt: rate limited client=%s", client_key)
        return RedirectResponse(url="/login?error=rate_limited", status_code=status.HTTP_303_SEE_OTHER)

    if not _verify_pin(pin) or not _verify_otp(otp_code):
        _record_login_failure(client_key)
        logger.warning("Login failed: client=%s", client_key)
        return RedirectResponse(url="/login?error=invalid_credentials", status_code=status.HTTP_303_SEE_OTHER)

    LOGIN_FAILURES.pop(client_key, None)
    LOCKED_UNTIL.pop(client_key, None)

    sid, expires_at = _create_session()
    response = RedirectResponse(url="/view", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=sid,
        expires=int(expires_at.timestamp()),
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="strict",
    )
    logger.info("Login success: client=%s", client_key)
    return response


@app.post("/auth/logout")
async def auth_logout(request: Request):
    signed_sid = request.cookies.get(SESSION_COOKIE_NAME)
    if signed_sid:
        raw_sid = _extract_raw_session_id(signed_sid)
        if raw_sid:
            AUTH_SESSIONS.pop(raw_sid, None)

    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return response


@app.get("/auth/me")
def auth_me(request: Request):
    authenticated = _get_valid_session_id(request) is not None
    return {"authenticated": authenticated}


@app.post("/trade/trigger", tags=["Trading"])
async def trigger_manual_trade(background_tasks: BackgroundTasks, password: str, signal=None, _=Depends(verify_password)):
    """트레이딩 로직을 1회 수동으로 실행합니다."""
    logger.info("Manual trade trigger requested via API.")
    background_tasks.add_task(run_trading_logic, signal)
    return {"status": "success", "message": "Trading logic triggered in the background."}


@app.get("/trade/prediction", tags=["Trading"])
def get_trade_prediction(_=Depends(require_authenticated_session)):
    """현재 시장 상황에 대한 모델 예측을 조회합니다."""
    logger.info("Fetching signal prediction using model.")
    recent_candles_df = state_manager.load_recent_candles()
    if recent_candles_df.empty or len(recent_candles_df) < MIN_CANDLE_COUNT:
        candles_df = trader.fetch_historical_candles_simple()
        if candles_df is None or candles_df.empty:
            raise HTTPException(
                status_code=502,
                detail="Upbit API에서 캔들 데이터를 가져오는 데 실패했습니다.",
            )
        state_manager.save_recent_candles(candles_df)
        recent_candles_df = candles_df

    signal, probs = predictor.generate_signal(recent_candles_df)
    if signal is None:
        raise HTTPException(
            status_code=502,
            detail="모델 예측 생성에 실패했습니다.",
        )
    return {"signal": signal, "probabilities": {"loss": probs[0], "hold": probs[1], "profit": probs[2]}}


@app.get("/status/portfolio", tags=["Status"])
def get_portfolio_status(_=Depends(require_authenticated_session)):
    """현재 포트폴리오 상태를 조회합니다."""
    logger.info("Fetching portfolio status via API.")
    return trader.fetch_portfolio_state()


@app.get("/status/logs", tags=["Status"])
def get_trade_logs(_=Depends(require_authenticated_session)):
    """거래 기록을 조회합니다."""
    log_path = state_manager.LOG_PATH
    if not log_path.exists():
        return {"message": "Log file not found."}
    try:
        logs_df = pd.read_csv(log_path)
        logs_df = logs_df.replace({np.nan: None})
        logs_df = logs_df.replace([np.inf, -np.inf], None).replace({np.nan: None})
        return logs_df.to_dict(orient="records")
    except pd.errors.EmptyDataError:
        return {"message": "Log file is empty."}
    except Exception as e:
        return {"error": str(e)}


@app.get("/status/scheduler", tags=["Status"])
def get_scheduler_status(_=Depends(require_authenticated_session)):
    """스케줄러 상태를 조회합니다."""
    return _build_scheduler_status()


@app.post("/slack", tags=["Status"])
async def call_slack_bot(payload: str = Form(...)):
    """슬랙 봇을 호출합니다"""
    interaction_data = json.loads(payload)

    action = interaction_data.get("actions", [{}])[0]
    action_id = action.get("action_id")
    callback_id = interaction_data.get("callback_id")

    sched = _build_scheduler_status()
    port = trader.fetch_portfolio_state()
    log_path = state_manager.LOG_PATH
    logs_recent_50 = pd.read_csv(log_path).tail(50)
    logs_recent_50 = logs_recent_50.replace({np.nan: None})
    logs_recent_50 = logs_recent_50.replace([np.inf, -np.inf], None).replace({np.nan: None})
    probs = logs_recent_50.iloc[-1][["prob_loss", "prob_hold", "prob_profit"]].tolist() if not logs_recent_50.empty else [0.0, 0.0, 0.0]

    if callback_id == "button_get_status" or action_id == "button_get_status":
        message = slack_bot.make_slack_messages("💸 현재 상태 조회", port, probs, sched, None)
        slack_bot.post_message_blocks(message)

    elif callback_id == "button_get_logs" or action_id == "button_get_logs":
        slack_bot.post_graphs("Hasha Bot Log(Recent 50 Candles)", logs_recent_50)

    return


@app.post("/candles/initialize", tags=["Candles"])
async def initialize_candle_data(_=Depends(verify_password)):
    """
    400개의 과거 캔들 데이터를 Upbit에서 가져와 서버에 CSV 파일로 저장합니다.
    (데이터 초기화 용도)
    """
    try:
        candles_df = trader.fetch_historical_candles_simple()

        if candles_df is None or candles_df.empty:
            raise HTTPException(
                status_code=502,
                detail="Upbit API에서 캔들 데이터를 가져오는 데 실패했습니다.",
            )

        state_manager.save_recent_candles(candles_df)

        return {
            "status_code": 200,
            "content": {
                "message": "성공적으로 캔들 데이터를 가져와 저장했습니다.",
                "candle_count": len(candles_df),
                "saved_path": str(state_manager.CANDLE_DATA_PATH),
            },
        }

    except Exception as e:
        logger.error(f"API 처리 중 심각한 오류 발생: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"서버 내부 오류 발생: {str(e)}",
        )
