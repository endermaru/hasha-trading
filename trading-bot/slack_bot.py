import requests
import os
from dotenv import load_dotenv
import logging
from datetime import datetime, timezone

# .env 파일에서 환경 변수 로드
load_dotenv()

# .env 파일에서 웹훅 URL 가져오기
webhook_url = os.getenv("SLACK_WEBHOOK_URL")
logger = logging.getLogger(__name__)

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