# Feed overview design preview

Open `index.html` directly in a browser, or serve the repository's mockups
directory from the repository root:

```bash
python3 -m http.server 8767 --bind 127.0.0.1 --directory mockups
```

Then visit `http://127.0.0.1:8767/feed-overview-mockup/index.html`.

The self-contained prototype uses illustrative provider health data and does not call any APIs. It includes responsive provider cards, one dot per board, search, a needs-attention filter, table sorting and pagination, provider details, and light/dark themes. The sample board inventory totals 111,984 across 36 providers, matching the supplied overview.

`preview.png` and `mobile-preview.png` show the desktop and mobile layouts.
