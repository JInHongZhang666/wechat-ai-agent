FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WECHAT_AGENT_DATABASE_URL=sqlite:////app/data/wechat_agent.db \
    WECHAT_AGENT_AGENT_ENABLED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt

COPY backend /app/backend
RUN pip install --no-deps /app/backend

RUN mkdir -p /app/data

EXPOSE 8010

CMD ["uvicorn", "wechat_agent.main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]

