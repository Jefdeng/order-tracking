FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY *.py ./
# Cloud Run supplies $PORT. One worker: a single process owns the run lock.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
