FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ./xjobscore/oneshot-ui
ENV PYTHONPATH=/app
CMD uvicorn oneshot_ui.app:app --host 0.0.0.0 --port ${PORT:-8080}
