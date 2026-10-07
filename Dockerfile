FROM python:3.12-slim

WORKDIR /app
COPY . .
# OneShot depends on the core xjobscore application, which is a local project
# rather than a package published on PyPI. Install both from the build context.
RUN rm -rf ./xjobscore/oneshot-ui/build \
        ./xjobscore/oneshot-ui/xjobscore_oneshot_ui.egg-info \
    && pip install --no-cache-dir . ./xjobscore/oneshot-ui
ENV PYTHONPATH=/app
CMD uvicorn oneshot_ui.app:app --host 0.0.0.0 --port ${PORT:-8080}
