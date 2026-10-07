# Link checks mockup

Open `index.html` directly, or use the existing Inspector static server:

`http://127.0.0.1:8765/static/link-checks-mockup/index.html`

The standalone preview server for this review is at `http://127.0.0.1:8877/`.
To start it again from the repository root:

```sh
.venv/bin/python -m http.server 8877 --bind 127.0.0.1 --directory xjobscore/inspector/static/link-checks-mockup
```

This standalone preview uses sample data and makes no API requests. Pause,
Settings, Details, and Save operate only in browser memory. Existing System UI
files and backend controls are not modified.

The preview toolbar switches between running, idle, paused, offline, worker
error, loading, failed refresh, failed initial load, and failed settings save.
It also switches between light and dark themes.

Optional query parameters:

- `state=paused` (or another state from the toolbar)
- `theme=dark`
- `settings=1` and `details=1` to open either section
- `presentation=1` to hide preview controls
