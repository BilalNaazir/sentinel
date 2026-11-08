FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY dashboard ./dashboard
COPY config ./config
COPY prompts ./prompts
COPY data ./data

RUN pip install --no-cache-dir .

CMD ["uvicorn", "sentinel.api.main:app", "--host", "0.0.0.0", "--port", "8000"]