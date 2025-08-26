# Dockerfile

# 1. 베이스 이미지 설정 (Python 3.11)
FROM python:3.11-slim

# 2. 작업 디렉토리 생성 및 설정
WORKDIR /app

# 3. 라이브러리 설치 파일을 먼저 복사하여 Docker 빌드 캐시를 활용
COPY requirements.txt .

# 4. 파이썬 라이브러리 설치
#    --no-cache-dir 옵션으로 불필요한 캐시를 남기지 않아 이미지 용량을 줄입니다.
RUN pip install --no-cache-dir -r requirements.txt

# 5. 필요한 모든 소스 코드와 디렉토리 복사
#    - .py 로 끝나는 모든 파이썬 파일 복사
#    - lstm/ 디렉토리와 그 안의 모델/스케일러 파일 복사
#    - data/ 디렉토리 복사
COPY *.py ./
COPY lstm/ ./lstm/
COPY data/ ./data/

# 6. FastAPI 애플리케이션이 사용할 포트(8000번)를 외부에 노출
EXPOSE 8000

# 7. 컨테이너가 시작될 때 실행할 명령어
#    0.0.0.0 호스트를 사용해야 외부에서 컨테이너로 접근할 수 있습니다.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]