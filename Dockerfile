# 레일웨이 배포용. 런타임 전용 의존성만 설치한다(requirements.txt).
# pandas/scikit-learn/pytest 등은 requirements-dev.txt 로 분리돼 있고 여기서는 설치하지 않는다.
FROM python:3.12-slim

WORKDIR /app

# 의존성만 먼저 복사해 레이어 캐시를 살린다.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY web/ ./web/
COPY domains/ ./domains/

ENV HOST=0.0.0.0

# PORT 는 레일웨이가 기동 시 주입한다(server/__main__.py 가 os.environ["PORT"] 를 읽음).
CMD ["python", "-m", "server"]
