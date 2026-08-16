# frontend

The browser client for OneShot. Plain HTML, CSS, and vanilla JS — no build step,
no framework, no bundler. Every file here is shipped to the browser as-is.

All data comes from the OneShot API (`xjobscore/oneshot-ui`) over `/api/*`.
Nothing here talks to Postgres, and nothing here is rendered on a server.

## Running it

The API server serves this directory during local development, so the normal
flow is unchanged:

```bash
cd xjobscore/oneshot-ui
../../.venv/bin/python -m oneshot_ui     # http://127.0.0.1:8766
```

The API finds this directory by walking up from its own package to the repo
root. Override with
`XJOBSCORE_ONESHOT_STATIC_DIR`, or point it at a path that does not exist to run
the API on its own with no client attached.

## Tests

```bash
cd frontend && node --test
```

## Sample data

`sample-data.js` is a complete in-browser fake of the API, toggled from the
sidebar. With it on, every page works with no server and no database — useful
for design work and for previewing a deploy before a backend exists.

## Not yet standalone

This directory is now separately deployable in *layout* only. Hosting it on its
own still needs three things:

1. **A configurable API base URL.** `api-client.js` and `resume/app.js` both
   hardcode a same-origin `/api` prefix.
2. **CORS on the API.** The FastAPI app sends no CORS headers, so a browser on a
   different origin is refused before the request is even made.
3. **Auth.** The API has none and assumes a single local user. Putting it on the
   public internet without auth exposes read and write access to anyone.

Page links also assume the API's URL space (`/static/application/index.html`,
`/resume/`) and would be rewritten to plain paths under a static host.
