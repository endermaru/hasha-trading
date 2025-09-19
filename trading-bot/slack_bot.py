import requests
import os, io
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta, timezone
import pandas as pd
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import seaborn as sns
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.style as style 


# .env 파일에서 환경 변수 로드
load_dotenv()

# .env 파일에서 웹훅 URL 가져오기
webhook_url = os.getenv("SLACK_WEBHOOK_URL")
bot_token = os.getenv("SLACK_OAUTH_TOKEN")
channel_id = os.getenv("SLACK_CHANNEL_ID")
logger = logging.getLogger(__name__)

slack_menus = [
    {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "현재 상태 조회 📊"
                },
                "style": "primary", # 버튼 색상 (primary, danger)
                "action_id": "button_get_status" # 어떤 버튼인지 식별하기 위한 고유 ID
            },
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "최근 거래 로그 조회 📝"
                },
                "style": "primary", # 버튼 색상 (primary, danger)
                "action_id": "button_get_logs" # 어떤 버튼인지 식별하기 위한 고유 ID
            }
        ]
    },
]

def make_slack_messages(result_message: str, portfolio_state: dict, probs: list, sched: dict, logs: pd.DataFrame | None = None) -> list:
    if logs is None:
        return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{result_message}",
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*현금💵:*\n{portfolio_state['cash']:,.2f}원"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*BTC ₿:*\n{portfolio_state['position_value']:,.2f}원 ({portfolio_state['position_size']:,.7f} BTC)"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*총 자산💰:*\n{portfolio_state['total_value']:,.2f}원"
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*평균 구매가:*\n{portfolio_state['avg_buy_price']:,.2f}원"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*현재 비트코인 가격:*\n{portfolio_state['current_price']:,.2f}원 ({(portfolio_state['current_price'] - portfolio_state['avg_buy_price']) / portfolio_state['avg_buy_price'] * 100 if portfolio_state['avg_buy_price'] > 0 else 0:.2f}%)"
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"확률🎲: `손실 {probs[0]*100:.2f}%` `유지 {probs[1]*100:.2f}%` `이익 {probs[2]*100:.2f}%`"
                },
                {
                    "type": "mrkdwn",
                    "text": f"다음 거래: {sched['next_run_time_utc']}({sched['time_until_next_run']['str']})"
                }
            ]
        }
    ] + slack_menus
    else:
        table_rows = []
        # --- 테이블 헤더 ---
        header = f"{'Time(UTC)':<12}{'SIGNAL':>6}{'PRICE':>15}{'SIZE':>15}{'FEE':>10}{'LOSS(%)':>10}{'HOLD(%)':>10}{'PRFT(%)':>10}"
        table_rows.append(header)
        table_rows.append("-" * len(header)) # 구분선

        # --- 테이블 본문 ---
        for _, row in logs.iterrows():
            # 데이터 포맷팅
            ts_utc = datetime.fromisoformat(row['timestamp']).astimezone(timezone.utc)
            time_str = ts_utc.strftime('%m-%d %H:%M')

            signal_map = {
                'BUY': 'BUY',
                'SELL': 'SELL',
                'HOLD': 'HOLD',
                'ERROR': 'ERR'
            }
            signal_str = signal_map.get(row.get('signal', 'HOLD').upper(), 'HOLD ⏸️')
            # prob_loss
            prob_loss_value = row.get('prob_loss')
            prob_loss = f"{prob_loss_value * 100:.2f}%" if prob_loss_value is not None else "0.00%"

            # prob_hold
            prob_hold_value = row.get('prob_hold')
            prob_hold = f"{prob_hold_value * 100:.2f}%" if prob_hold_value is not None else "0.00%"

            # prob_profit
            prob_profit_value = row.get('prob_profit')
            prob_profit = f"{prob_profit_value * 100:.2f}%" if prob_profit_value is not None else "0.00%"
            fee = row.get('fee', 0)
            
            price_str = f"₩ {row['price']:,.0f}"
            size_str = f"₿ {row['size']:.6f}" if row['size'] > 0 else "-"
            fee_str = f"₩ {fee:,.1f}" if fee > 0 else "-"

            # f-string의 정렬 기능을 이용해 각 열의 너비를 맞춥니다.
            # :<15 : 왼쪽 정렬, 15칸
            # :>15 : 오른쪽 정렬, 15칸
            row_str = f"{time_str:<12}{signal_str:>6}{price_str:>15}{size_str:>15}{fee_str:>10}{prob_loss:>10}{prob_hold:>10}{prob_profit:>10}"
            table_rows.append(row_str)
            
        # 모든 행을 합쳐서 하나의 문자열로 만듭니다.
        table_content = "\n".join(table_rows)
        
        # 최종 Block Kit 구조
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "📄 최근 거래 로그 요약"}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    # ``` 로 감싸 고정폭 폰트를 사용합니다.
                    "text": f"```{table_content}```"
                }
            }
        ]
        return blocks + slack_menus

def post_message(message: str):
    """
    슬랙 채널에 메시지를 보내는 함수
    """

    if webhook_url is None:
        logger.error("SLACK_WEBHOOK_URL is not set in environment variables.")
        return
    
    now_utc = datetime.now(timezone.utc)
    timestamp = now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')
    full_message = f"_{timestamp}_ {message}"
    
    payload = {
        "text": full_message
    }
    
    response = requests.post(
        webhook_url, json=payload,
        headers={'Content-Type': 'application/json'}
    )
    
    if response.status_code != 200:
        logger.error(f"Request to Slack returned an error {response.status_code}, the response is:\n{response.text}")
        return
    
def post_message_blocks(blocks: list):
    """
    슬랙 채널에 Block Kit 기반의 메시지를 보내는 함수
    """
    if webhook_url is None:
        logger.error("SLACK_WEBHOOK_URL is not set in environment variables.")
        return
    
    now_utc = datetime.now(timezone.utc)
    timestamp = now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')
    blocks += [
        {
            "type": "divider"
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "plain_text",
                    "text": f"처리 시간: {timestamp}"
                }
            ]
        },
    ]

    try:
        response = requests.post(
            webhook_url,
            json={"blocks": blocks},
            headers={'Content-Type': 'application/json'}
        )
        response.raise_for_status()  # 2xx 응답 코드가 아니면 예외 발생
    except requests.exceptions.RequestException as e:
        logger.error(f"Request to Slack failed: {e}")
        if e.response is not None:
            logger.error(f"Response text: {e.response.text}")

def post_graphs(title:str, logs: pd.DataFrame):
    style.use('seaborn-v0_8-dark-palette')
    # 1. 데이터 준비 (이전과 동일)
    df = logs.copy()
    if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
        df['timestamp'] = pd.to_datetime(df['timestamp'])

    # 2. Matplotlib 그래프 생성 (figure 크기 설정)
    fig, ax = plt.subplots(figsize=(12, 6))

    # --- 꺾은선 그래프 그리기 (Seaborn 함수 사용) ---
    sns.lineplot(data=df, x='timestamp', y='price', ax=ax, linewidth=1.5, label='Price')
    # sns.lineplot(data=df, x='timestamp', y='price', ax=ax, linewidth=1.5, label='Price', color='white')

    # --- BUY/SELL 신호 마커 표시 ---
    buy_signals = df[df['signal'] == 'BUY']
    sell_signals = df[df['signal'] == 'SELL']
    ax.scatter(buy_signals['timestamp'], buy_signals['price'],
               marker='^', color='lime', s=150, label='BUY', zorder=5, ec='black') # 색상을 더 밝게
    ax.scatter(sell_signals['timestamp'], sell_signals['price'],
               marker='v', color='red', s=150, label='SELL', zorder=5, ec='black') # 테두리 추가

    # 3. 그래프 디자인 및 축 서식 설정
    price_min = df['price'].min()
    price_max = df['price'].max()
    padding = (price_max - price_min) * 0.1
    ax.set_ylim(price_min - padding, price_max + padding)
    
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d\n%H:00'))
    plt.xticks(ha='center')

    ax.set_title(title, fontsize=16, weight='bold')
    ax.set_ylabel("BTC(₩)", fontsize=12)
    ax.grid(True, axis='y', which='both', linestyle='--', linewidth=0.5)
    ax.set_xlabel("")

    ax.legend()
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x / 1_000_000:.1f}M'))
    fig.tight_layout()
    fig.show()

    # 슬랙 업로드
    buf = io.BytesIO()
    fig.savefig(buf,format='jpg', dpi=80, facecolor=fig.get_facecolor())
    buf.seek(0)
    try:
        client = WebClient(token=bot_token)
        result = client.files_upload_v2(
            channel=channel_id,
            content=buf.getvalue(),
            filename=f"trading_log_{df['timestamp'].iloc[-1].strftime('%Y%m%d_%H%M')}.png",
            initial_comment=f"📄 {df['timestamp'].iloc[-1].strftime('%Y-%m-%d %H:%M')}(UTC) 기준 거래 로그 그래프입니다.",
            title=f"trading_log_{df['timestamp'].iloc[-1].strftime('%Y%m%d_%H%M')}.png"
        )
        logger.info("✅ 그래프를 성공적으로 업로드했습니다!")

    except SlackApiError as e:
        logger.error(f"❌ 그래프 업로드 실패: {e.response['error']}")
    finally:
        plt.close(fig)
