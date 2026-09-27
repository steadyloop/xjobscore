FROM python:3.12-slim

WORKDIR /app
COPY . .
# OneShot depends on the core xjobscore application, which is a local project
# rather than a package published on PyPI. Install both from the build context.
RUN rm -rf ./xjobscore/oneshot-ui/build \
        ./xjobscore/oneshot-ui/xjobscore_oneshot_ui.egg-info \
    && pip install --no-cache-dir . ./xjobscore/oneshot-ui
ENV PYTHONPATH=/app
# oneshot_ui is pip-installed into site-packages, so the config default walks up
# from there and never finds /app. Name the client explicitly or the container
# silently serves the JSON API with no pages attached.
ENV XJOBSCORE_ONESHOT_STATIC_DIR=/app/frontend
CMD uvicorn oneshot_ui.app:app --host 0.0.0.0 --port ${PORT:-8080}
