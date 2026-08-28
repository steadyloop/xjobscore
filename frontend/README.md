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

`sample-data.js` is a complete in-browser fake of the API. With it on, a page
works with no server and no database — useful for design work and for previewing
a deploy before a backend exists. `transport()` short-circuits to the fake before
`fetch` is reached, so nothing in sample mode reaches the network.

**The switch is per page, and two pages have one:** Applications and Resume.
Opportunities does not have one — it always talks to the real API. Source
coverage does not either, because it is a read-only report on the harvest itself
and a demo mode there would only hide what the page exists to show. Opportunities
still loads `sample-data.js`, but only for `OneShotVisitorData`, the separate
browser-local guest workspace it shares with Applications and Resume — see
Sign-in below. Source coverage loads neither.

**It lives in the page it applies to**, never in the sidebar. The sidebar moves
you between pages; this changes what one page is showing, so it belongs next to
that page's own controls. It borrows the height and radius of the control beside
it so the header reads as a single row, and turns amber when on, since a page
rendering fixtures needs to say so — the brand lime would only have read as
"active".

**Where exactly depends on the shell.** Applications and Resume hand-write the
button into `.workspace-actions` inside a static `workspace-header`, beside "Add
application" and the Typst badge respectively.

`PAGES` in `sample-data.js` maps a pathname to a storage key
(`oneshot.sample-data.enabled.application`, `…​.resume`), so Applications can run
on fixtures while Resume shows real data, or any other combination. Every caller
is unchanged by this: a page only ever requests its own data, so "is sample mode
on" and "is it on for this page" are the same question at the two places that
ask — `transport()` in `api-client.js` and the private `api()` in
`resume/app.js`.

The fixture *store* stays shared (one `oneshot.sample-data.state.v6` key), since
an application created from a sample job has to be the same record on every page.
It is seeded once, on the first switch turned on, and not re-seeded afterwards —
re-seeding on every flip would discard the other page's edits.

**The switch is guest-only.** It ships hidden (`navigation.css`) and `nav-count.js`
adds `is-guest` to `<body>` once `/api/auth/me` reports nobody is signed in, so a
signed-in user never sees it blink into view. If a session does turn up while a
switch is on — you signed in from another tab — `disableAll()` clears every
switch and the page reloads if it was this page's.

## Sign-in

Jobs and source coverage stay server-backed while signed out. Applications and
the resume studio use `oneshot.visitor-data.state.v1` in `localStorage`, seeded
blank and separate from sample fixtures. Clearing site data removes that
workspace, and it does not synchronize across browsers. Signing in swaps private
CRUD to the account-scoped PostgreSQL API; the browser-local workspace is kept
but is not merged automatically.

`sample-data.js` owns both in-browser transports because they implement the same
request contract, but sample and visitor state use different keys. Sample mode
still shows its rich fixture workspace; switching it off returns to the visitor's
blank/edited local workspace. Browser-local resumes can run content and coverage
checks, but PDF and `.typ` export require sign-in because rendering is server-side.

The 401 → `/login/` handlers cover a signed-in session expiring between the
shared identity probe and a private request. Anonymous private requests are
routed locally and never receive that 401.

`nav-count.js` builds the account chip in the sidebar footer on all four shells:
avatar, name, email, and a `⋯` menu holding the full address and Sign out. Signed
out it renders the same box as a "Guest / Browser-local · Sign in" link, so nothing
shifts when you sign in. Google's `picture_url` layers over initials drawn
underneath, so a blocked or missing photo degrades instead of leaving a grey disc.

The chip is also what holds the foot of the sidebar down, via `margin-top: auto`
in `navigation.css`. That used to be the sample switch's job — the chip merely
sat beneath it — and moved here when the switch left the sidebar, so the sidebar
is now navigation and identity, nothing else.

It is built in JS, not markup, because the four sidebars are hand-copied and not
byte-identical — one code path beats four edits. The identity probe must **not**
redirect on 401: that is the normal browser-local guest state.

`login/` deliberately loads none of the shared scripts, so nothing on it can 401
and bounce it back to itself.

## Not yet standalone

This directory is now separately deployable in *layout* only. Hosting it on its
own still needs:

1. **A configurable API base URL.** `api-client.js` and `resume/app.js` both
   hardcode a same-origin `/api` prefix.
2. **CORS on the API,** with `allow_credentials` and explicit origins — the
   session cookie is same-origin today, and a cross-origin client would need
   `credentials: "include"` on every call plus `SameSite=None` on the cookie.

Page links also assume the API's URL space and would be rewritten to plain paths
under a static host. Every page shell has one canonical URL — `/`,
`/application/`, `/source-coverage/`, `/resume/`, `/login/`, `/discover/` —
served by a route in
`oneshot_ui/app.py`; the `/static` mount answers on the same files, so a shell must
reference its assets by absolute `/static/...` path rather than `./` or `../`,
which resolve differently at the two depths.
