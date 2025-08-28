# trader.py

import logging
import os
import pandas as pd
import pyupbit
from dotenv import load_dotenv
import time


# --- 상수 정의 ---
TICKER = "KRW-BTC"
INTERVAL = "minute240"
TAKER_FEE_RATE = 0.001  # 보수적인 수수료율(0.1%) 적용
MIN_ORDER_AMOUNT_KRW = 5000  # KRW 마켓 최소 주문 금액

# --- API 클라이언트 초기화 ---
# .env 파일에서 API 키를 불러옵니다.
load_dotenv()
access_key = os.getenv('access_key')
secret_key = os.getenv('secret_key')

upbit = None
logger = logging.getLogger(__name__)

# access_key와 secret_key가 있는 경우에만 Upbit 객체 생성
if access_key and secret_key:
    upbit = pyupbit.Upbit(access_key, secret_key)
    logger.info("Upbit client initialized successfully.")
else:
    upbit = None
    logger.warning("API keys not found. Running in simulation mode.")


def fetch_latest_candle(ticker: str = TICKER, interval: str = "minute240", count: int = 2) -> pd.DataFrame | None:
    """
    Upbit API를 호출하여 지정된 개수의 최신 4시간봉 캔들을 가져옵니다.
    'Open', 'High', 'Low', 'Close', 'Volume' 5개의 열로 구성된 DataFrame을 반환합니다.
    """
    logger.info(f"Fetching latest candle for {ticker} with interval {interval}...")
    try:
        # pyupbit.get_ohlcv는 가장 최신 봉부터 가져옵니다. count=1은 가장 최근에 완성된 봉 1개를 의미합니다.
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
        
        if df is None or df.empty:
            logger.error("Failed to fetch candle data from Upbit.")
            return None
        
        # 컬럼명을 대문자로 통일합니다.
        df.columns = ['Open', 'High', 'Low', 'Close', 'Volume', 'Value']
        df.drop(columns=['Value'], inplace=True)
        df.index = df.index.tz_localize('Asia/Seoul').tz_convert('UTC')
        logger.info(f"New candle data fetched: \n{df}")
        return df

    except Exception as e:
        logger.error(f"An error occurred while fetching candle data: {e}")
        return None


def fetch_historical_candles_simple(ticker: str = "KRW-BTC", interval: str = "minute240") -> pd.DataFrame | None:
    """
    Upbit API를 두 번 호출하여 총 400개의 4시간봉 캔들을 가져옵니다.
    """
    logger.info("Fetching 400 historical candles in two chunks...")
    try:
        df1 = pyupbit.get_ohlcv(ticker, interval=interval, count=200)
        if df1 is None or df1.empty:
            logger.error("Failed to fetch candles.")
            return None
        time.sleep(0.2)
        oldest_timestamp = df1.index[0] - pd.Timedelta(hours=9)
        df2 = pyupbit.get_ohlcv(ticker, interval=interval, count=200, to=oldest_timestamp)
        if df2 is None or df2.empty:
            logger.error("Failed to fetch candles.")
            return None

        df_total = pd.concat([df2, df1])

        # 컬럼명 정리
        df_total.columns = ['Open', 'High', 'Low', 'Close', 'Volume', 'Value']
        df_total.drop(columns=['Value'], inplace=True)
        
        # 시간대(Timezone)를 UTC로 통일
        df_total.index = df_total.index.tz_localize('Asia/Seoul').tz_convert('UTC')
        
        # 시간순으로 정렬
        df_total.sort_index(inplace=True)
        
        logger.info(f"Successfully fetched a total of {len(df_total)} candles.")
        return df_total
    
    except Exception as e:
        logger.error(f"An error occurred while fetching historical candles: {e}")
        return None

def fetch_portfolio_state() -> dict:
    """현재 포트폴리오 상태를 불러옵니다."""
    if upbit is None:
        logger.error("Failed to fetch portfolio data from Upbit.")
        return None

    try:
        balances = upbit.get_balances()
        logger.info(f"Fetched Portfolio data: {balances}")

        portfolio = {
            "in_position": False,
            "cash": 0.0,
            "position_size": 0.0,
            "entry_price": 0.0,
        }

        target_currency = TICKER.split('-')[1]  # "BTC"

        for data in balances:
            currency = data['currency']
            if currency == 'KRW':
                portfolio['cash'] = float(data['balance'])
            elif currency == target_currency:
                position_size = float(data['balance'])
                if position_size > 0:
                    portfolio['position_size'] = position_size
                    portfolio['in_position'] = True
                    portfolio['entry_price'] = float(data['avg_buy_price'])

        return portfolio
    
    except Exception as e:
        logger.error(f"An error occurred while fetching portfolio data: {e}")
        return None

def _wait_for_order_completion(order_uuid: str, max_wait_sec: int = 60) -> dict | None:
    """
    특정 주문(uuid)이 체결 완료될 때까지 대기합니다.
    'done' 또는 'cancel'이며 executed_volume > 0 이면 체결로 간주.
    """
    start_time = time.time()
    logger.info(f"주문 완료 대기 시작. UUID: {order_uuid}")
    order_status = None
    while time.time() - start_time < max_wait_sec:
        try:
            # 개별 주문 조회를 통해 정확한 상태를 확인
            order_status = upbit.get_order(order_uuid)
            if order_status:
                state = order_status.get('state')
                executed_volume = float(order_status.get('executed_volume', 0))
                if state in ('done', 'cancel') and executed_volume > 0:
                    logger.info(f"Order {order_uuid} 완료 또는 취소(실제 체결 완료), 상태: {state}, 체결량: {executed_volume}")
                    return order_status
                elif state == "cancel" and executed_volume == 0:
                    logger.warning(f"Order {order_uuid} 취소, 미체결 (잔량 체결 없음).")
                    return order_status
        except Exception as e:
            logger.error(f"Error checking order status for {order_uuid}: {e}")
        
        time.sleep(0.5)

    logger.error(f"Order {order_uuid} did not complete within {max_wait_sec} seconds.")
    return order_status


def execute_order(signal: str, probs: list[float], portfolio: dict) -> dict:
    """
    예측 신호에 따라 실제 거래를 실행하고 포트폴리오 상태를 업데이트합니다.
    """
    prob_loss, prob_hold, prob_profit = probs
    trade_log = {
            'timestamp': str(pd.Timestamp.now(tz='UTC')),
            'uuid': '',
            'signal': 'HOLD', 
            'price': 0, 
            'size': 0,
            'fee': 0,
            'prob_loss': prob_loss,
            'prob_hold': prob_hold,
            'prob_profit': prob_profit
        }

    # --- 실시간 현재가 조회 ---
    try:
        current_price = pyupbit.get_current_price(TICKER)
        time.sleep(0.1)
        if current_price is None:
            logger.error("현재가를 조회할 수 없습니다. 주문 실행을 중단합니다.")
            trade_log['signal'] = 'ERROR'
            return trade_log
    except Exception as e:
        logger.error(f"현재가 조회 중 예외 발생: {e}. 주문 실행을 중단합니다.")
        trade_log['signal'] = 'ERROR'
        return trade_log

    trade_log['price'] = current_price

    # --- 매수 로직 ---
    # if signal == 'buy' and not portfolio.get('in_position', False): # 물타기, 불타기 방지 가드
    if signal == 'buy':
        available_cash = portfolio.get('cash', 0)
        cash_for_buy = available_cash / (1 + TAKER_FEE_RATE)
        
        # 최소 주문 금액 확인
        if cash_for_buy < MIN_ORDER_AMOUNT_KRW:
            logger.warning(f"가용 현금({available_cash:,.0f}원)으로 매수 가능한 금액({cash_for_buy:,.0f}원)이 최소 주문 금액({MIN_ORDER_AMOUNT_KRW:,.0f}원)보다 작아 주문을 실행하지 않습니다.")
            trade_log['signal'] = 'HOLD'
            return trade_log

        logger.info(f"매수 신호 확인. 시장가 매수 주문 실행. 주문액: {cash_for_buy:,.0f}원 (가용 현금: {available_cash:,.0f}원)")

        try:
            # 수수료를 고려하여 계산된 주문 금액으로 API 호출
            order_result = upbit.buy_market_order(TICKER, cash_for_buy)
            if not (order_result and 'uuid' in order_result):
                logger.error(f"매수 주문 제출 실패: {order_result}")
                trade_log['signal'] = 'ERROR'
                return trade_log

            order_uuid = order_result['uuid']
            logger.info(f"매수 주문 제출 완료. UUID: {order_uuid}")

            completed_order = _wait_for_order_completion(order_uuid)
            if completed_order:
                # 매수 시에도 'trades' 리스트를 기반으로 정확한 평균 체결가 계산
                executed_volume = float(completed_order['executed_volume'])
                if executed_volume > 0:
                    total_cost = sum(float(trade['price']) * float(trade['volume']) for trade in completed_order['trades'])
                    avg_price = total_cost / executed_volume
                else:
                    avg_price = 0
                
                fee = float(completed_order['paid_fee'])

                trade_log = {
                    'timestamp': str(pd.Timestamp.now(tz='UTC')), 
                    'uuid': str(completed_order.get('uuid', '')),
                    'signal': 'BUY', 
                    'price': avg_price, 
                    'size': executed_volume, 
                    'fee': fee
                }
                logger.info(f"매수 주문 체결 완료: {trade_log}")

        except Exception as e:
            logger.error(f"매수 주문 실행 중 예외 발생: {e}")
            trade_log['signal'] = 'ERROR'
            return trade_log

    # --- 매도 로직 ---
    elif signal == 'sell':
        position_size_to_sell = portfolio.get('position_size', 0)
        if position_size_to_sell > 0:
            logger.info(f"매도 신호 확인. 보유 수량({position_size_to_sell}) 전체에 대해 시장가 매도 주문 실행.")
            try:
                order_result = upbit.sell_market_order(TICKER, position_size_to_sell)
                if not (order_result and 'uuid' in order_result):
                    logger.error(f"매도 주문 제출 실패: {order_result}")
                    trade_log['signal'] = 'ERROR'
                    return trade_log

                order_uuid = order_result['uuid']
                logger.info(f"매도 주문 제출 완료. UUID: {order_uuid}")

                completed_order = _wait_for_order_completion(order_uuid)
                if completed_order:
                    executed_volume = float(completed_order['executed_volume'])
                    if executed_volume > 0:
                        total_value = sum(float(trade['price']) * float(trade['volume']) for trade in completed_order['trades'])
                        avg_price = total_value / executed_volume
                    else:
                        avg_price = 0
                    
                    fee = float(completed_order['paid_fee'])

                    trade_log = {
                        'timestamp': str(pd.Timestamp.now(tz='UTC')), 
                        'uuid': str(completed_order.get('uuid', '')),
                        'signal': 'SELL', 
                        'price': avg_price, 
                        'size': executed_volume,
                        'fee': fee
                    }
                    logger.info(f"매도 주문 체결 완료: {trade_log}")
            except Exception as e:
                logger.error(f"매도 주문 실행 중 예외 발생: {e}")
                trade_log['signal'] = 'ERROR'
                return trade_log
        else:
            logger.info(f"매도 신호를 확인했지만 포지션의 크기가 없습니다: {trade_log}")
            return trade_log
    elif signal == 'hold':
        logger.info(f"보유 신호 확인. 현재 포지션을 유지합니다: {trade_log}")
        return trade_log
    else:
        logger.info(f"신호 '{signal}' 또는 포지션 상태가 주문 조건에 맞지 않아 거래를 실행하지 않습니다.")
        trade_log['signal'] = 'ERROR'
        return trade_log

    return trade_log


def execute_order_dummy(signal: str, probs: list[float], portfolio: dict) -> dict:
    """
    API 키가 없을 때 실제 주문 없이 거래를 시뮬레이션하고 거래 로그를 반환합니다.
    실제 execute_order 함수와 동일한 입력과 출력을 가지도록 설계되었습니다.
    """
    prob_loss, prob_hold, prob_profit = probs
    trade_log = {
            'timestamp': str(pd.Timestamp.now(tz='UTC')), 
            'uuid': '',
            'signal': 'HOLD', 
            'price': 0, 
            'size': 0,
            'fee': 0,
            'prob_loss': prob_loss,
            'prob_hold': prob_hold,
            'prob_profit': prob_profit
        }
    logger.info("--- 시뮬레이션 모드로 주문 실행 ---")

    # --- 시뮬레이션을 위한 실시간 현재가 조회 ---
    try:
        current_price = pyupbit.get_current_price(TICKER)
        if current_price is None:
            logger.error("[시뮬레이션] 현재가를 조회할 수 없어 더미 주문을 중단합니다.")
            trade_log['signal'] = 'ERROR'
            return trade_log
        logger.info(f"[시뮬레이션] 현재가: {current_price:,.0f}원")
    except Exception as e:
        logger.error(f"[시뮬레이션] 현재가 조회 중 예외 발생: {e}. 더미 주문을 중단합니다.")
        trade_log['signal'] = 'ERROR'
        return trade_log
    
    trade_log['price'] = current_price

    # --- 매수 시뮬레이션 로직 ---
    if signal == 'buy':
        available_cash = portfolio.get('cash', 0)
        
        # 전체 현금을 사용하여 매수할 수 있는 최대 주문 금액을 계산
        cash_for_buy = available_cash / (1 + TAKER_FEE_RATE)
        
        if cash_for_buy < MIN_ORDER_AMOUNT_KRW:
            logger.warning(f"[시뮬레이션] 계산된 매수 금액({cash_for_buy:,.0f}원)이 최소 주문 금액({MIN_ORDER_AMOUNT_KRW:,.0f}원)보다 작습니다.")
            trade_log['signal'] = 'ERROR'
            return trade_log

        logger.info(f"[시뮬레이션] 매수 신호 확인. {cash_for_buy:,.0f}원 상당의 가상 매수 실행.")
        
        # 가상 체결 결과 계산
        executed_volume = cash_for_buy / current_price
        fee = cash_for_buy * TAKER_FEE_RATE

        trade_log = {
            'timestamp': str(pd.Timestamp.now(tz='UTC')), 
            'signal': 'BUY', 
            'price': current_price,  # 시뮬레이션에서는 현재가를 평균 체결가로 간주
            'size': executed_volume, 
            'fee': fee
        }
        logger.info(f"[시뮬레이션] 가상 매수 체결 완료: {trade_log}")

    # --- 매도 시뮬레이션 로직 ---
    elif signal == 'sell':
        position_size_to_sell = portfolio.get('position_size', 0)
        if position_size_to_sell > 0:
            logger.info(f"[시뮬레이션] 매도 신호 확인. 보유 수량({position_size_to_sell}) 전체에 대해 가상 매도 실행.")
            
            # 가상 체결 결과 계산
            total_value = position_size_to_sell * current_price
            fee = total_value * TAKER_FEE_RATE

            trade_log = {
                'timestamp': str(pd.Timestamp.now(tz='UTC')), 
                'signal': 'SELL', 
                'price': current_price, # 시뮬레이션에서는 현재가를 평균 체결가로 간주
                'size': position_size_to_sell,
                'fee': fee
            }
            logger.info(f"[시뮬레이션] 가상 매도 체결 완료: {trade_log}")
    elif signal == 'hold':
        trade_log = {
            'timestamp': str(pd.Timestamp.now(tz='UTC')), 
            'signal': 'HOLD', 
            'price': current_price, 
            'size': 0,
            'fee': 0,
        }
        logger.info(f"보유 신호 확인. 현재 포지션을 유지합니다: {trade_log}")
        return trade_log        
    else:
        logger.info(f"[시뮬레이션] 신호 '{signal}' 또는 포지션 상태가 주문 조건에 맞지 않아 거래를 실행하지 않습니다.")
        trade_log['signal'] = 'ERROR'
        return trade_log

    # trade_log['signal'] = 'ERROR'
    return trade_log