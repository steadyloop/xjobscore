FROM python:3.12-slim

# Typst renders the resume PDF. It is not in Debian's repos, so the static musl
# binary comes from GitHub releases. Pinned deliberately: an unpinned "latest"
# would let typesetting shift between deploys, and this template is tuned to fit
# one page. Without this stage the app still runs — /api/resume/resumes/{id}/pdf
# answers 503, and the studio disables "Open PDF" and shows a warning badge
# (frontend/resume/app.js:95,289).
ARG TYPST_VERSION=0.15.1
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl xz-utils; \
    # ca-certificates ships with the base image; mark it manual so the purge
    # below cannot autoremove it as a curl dependency.
    apt-mark manual ca-certificates; \
    case "$(dpkg --print-architecture)" in \
        amd64) target=x86_64-unknown-linux-musl ;; \
        arm64) target=aarch64-unknown-linux-musl ;; \
        *) echo "no Typst build for $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-${target}.tar.xz" \
        | tar -xJ -C /tmp; \
    install -m 0755 "/tmp/typst-${target}/typst" /usr/local/bin/typst; \
    rm -rf "/tmp/typst-${target}"; \
    apt-get purge -y --auto-remove curl xz-utils; \
    rm -rf /var/lib/apt/lists/*; \
    # Fail the build here rather than at the first PDF request.
    typst --version

WORKDIR /app
COPY . .
RUN rm -rf ./xjobscore/oneshot-ui/build \
        ./xjobscore/oneshot-ui/xjobscore_oneshot_ui.egg-info \
    && pip install --no-cache-dir ./xjobscore/oneshot-ui
ENV PYTHONPATH=/app
# oneshot_ui is pip-installed into site-packages, so the config default walks up
# from there and never finds /app. Name the client explicitly or the container
# silently serves the JSON API with no pages attached.
ENV XJOBSCORE_ONESHOT_STATIC_DIR=/app/frontend
CMD uvicorn oneshot_ui.app:app --host 0.0.0.0 --port ${PORT:-8080}
