const state = {
  feeds: [], selectedFeed: null, category: 'ATS', selectedPart: null, search: '',
  expandedSources: new Set(), fetchingFeeds: new Set(), groupFetch: null, allFetch: null,
};
const postingState = { source: null, mode: 'exclude_full_time', usOnly: true, search: '', offset: 0, limit: 50, total: 0, rows: [], sources: [], searchTimer: null };
const $ = (id) => document.getElementById(id);

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.detail || 'Request failed');
    error.status = response.status;
    throw error;
  }
  return body;
}

function text(value) { return value === null || value === undefined || value === '' ? '—' : String(value); }
function pretty(value) { return JSON.stringify(value ?? null, null, 2); }
function safeUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch (_) { return null; }
}
function capitalized(value) {
  const textValue = String(value ?? '');
  return textValue ? textValue[0].toUpperCase() + textValue.slice(1) : '';
}
function displaySource(source) { return source; }
function setResultTitle(source, key) {
  const title = $('result-title');
  title.replaceChildren();
  const sourcePart = document.createElement('span');
  sourcePart.className = 'identity-source';
  sourcePart.textContent = capitalized(displaySource(source));
  title.append(sourcePart);
  if (key) {
    const separator = document.createElement('span');
    separator.className = 'identity-separator';
    separator.textContent = ' · ';
    const keyPart = document.createElement('span');
    keyPart.className = 'identity-key';
    keyPart.textContent = capitalized(key);
    title.append(separator, keyPart);
  }
}
function menuLabel(value) {
  const words = String(value || 'Untitled job').trim().split(/\s+/);
  return words.length > 3 ? `${words.slice(0, 3).join(' ')}…` : words.join(' ');
}
function showMessage(message) { $('message').textContent = message; $('message').hidden = !message; }
function bytes(n) { if (!n) return '0 B'; if (n < 1024) return `${n} B`; if (n < 1048576) return `${(n / 1024).toFixed(1)} KiB`; return `${(n / 1048576).toFixed(1)} MiB`; }
function lastRunText(result) {
  if (!result) return 'not fetched';
  return `${result.elapsed_ms}ms · ${result.item_count} ${result.item_count === 1 ? 'item' : 'items'}`;
}

function fullLastRunText(result) {
  if (!result) return 'No inspector result has been fetched.';
  return `${result.status} · HTTP ${text(result.http_status)} · ${result.elapsed_ms} ms · ${result.item_count} items · ${bytes(result.raw_size_bytes)} raw · ${bytes(result.size_bytes)} memory · ${result.force_full ? 'full response' : 'conditional response'}`;
}

function renderViewerSummary(result) {
  const summary = $('viewer-summary');
  summary.replaceChildren();
  if (!result) {
    summary.textContent = 'No inspector run yet';
    return;
  }
  const metrics = [
    ['Status', result.status],
    ['Response', `HTTP ${text(result.http_status)}`],
    ['Latency', `${result.elapsed_ms} ms`],
    ['Items', `${result.item_count} ${result.item_count === 1 ? 'item' : 'items'}`],
    ['Raw size', bytes(result.raw_size_bytes)],
    ['Memory', bytes(result.size_bytes)],
    ['Fetch mode', result.force_full ? 'Full response' : 'Conditional response'],
  ];
  for (const [label, value] of metrics) {
    const metric = document.createElement('span');
    metric.className = 'run-metric';
    const metricLabel = document.createElement('span');
    metricLabel.className = 'run-metric-label';
    metricLabel.textContent = label;
    const metricValue = document.createElement('strong');
    metricValue.textContent = value;
    metric.append(metricLabel, metricValue);
    summary.append(metric);
  }
}

function sourceGroups() {
  const groups = new Map();
  for (const feed of state.feeds) {
    let group = groups.get(feed.source);
    if (!group) {
      group = { source: feed.source, category: feed.category, feeds: [] };
      groups.set(feed.source, group);
    }
    group.feeds.push(feed);
  }
  return [...groups.values()].sort((a, b) => a.source.localeCompare(b.source));
}

function groupMatches(group) {
  const query = state.search.trim().toLocaleLowerCase();
  if (!query) return true;
  return group.source.toLocaleLowerCase().includes(query) || group.feeds.some((feed) => (
    String(feed.company_name || '').toLocaleLowerCase().includes(query)
    || String(feed.key || '').toLocaleLowerCase().includes(query)
  ));
}

function visibleGroups() {
  return sourceGroups().filter((group) => (
    (state.category === 'all' || group.category === state.category) && groupMatches(group)
  ));
}

function groupChildren(group) {
  const query = state.search.trim().toLocaleLowerCase();
  const sourceMatch = group.source.toLocaleLowerCase().includes(query);
  return group.feeds.filter((feed) => feed.configured && (!query || sourceMatch || (
    String(feed.company_name || '').toLocaleLowerCase().includes(query)
    || String(feed.key || '').toLocaleLowerCase().includes(query)
  )));
}

function visibleConfiguredFeeds() {
  return visibleGroups().flatMap((group) => groupChildren(group));
}

function categoryLabel(category) {
  return category === 'all' ? 'all' : category;
}

function fetchAllLabel() {
  return state.category === 'all' ? 'Fetch all sources' : `Fetch all ${categoryLabel(state.category)}`;
}

function batchFetchActive() {
  return Boolean(state.groupFetch || state.allFetch);
}

function isFetching(feed) {
  return state.fetchingFeeds.has(feed.feed_ref);
}

function groupRunText(group) {
  const configured = group.feeds.filter((feed) => feed.configured);
  const results = configured.map((feed) => feed.last_result).filter(Boolean);
  if (!results.length) return 'No inspector run';
  if (configured.length === 1) return lastRunText(results[0]);
  const failed = results.filter((result) => ['error', 'partial'].includes(result.status)).length;
  const suffix = failed ? ` · ${failed} attention` : '';
  return `${results.length}/${configured.length} fetched${suffix}`;
}

function renderedFeedRow(feed) {
  return [...document.querySelectorAll('tr[data-feed-ref]')]
    .find((row) => row.dataset.feedRef === feed.feed_ref);
}

function updateRenderedFeed(feed) {
  const row = renderedFeedRow(feed);
  if (!row) return;
  const lastRun = row.querySelector('.last-run');
  lastRun.textContent = lastRunText(feed.last_result);
  lastRun.title = fullLastRunText(feed.last_result);
  const viewButton = row.querySelector('.view-result');
  viewButton.disabled = !feed.last_result;
  const fetchButton = row.querySelector('.fetch');
  fetchButton.disabled = !feed.source_registered || isFetching(feed) || batchFetchActive();
  fetchButton.textContent = isFetching(feed) ? 'Fetching…' : 'Fetch';
}

function updateRenderedGroup(source) {
  const group = sourceGroups().find((candidate) => candidate.source === source);
  const row = [...document.querySelectorAll('tr[data-source]')]
    .find((candidate) => candidate.dataset.source === source);
  if (!group || !row) return;
  const configured = group.feeds.filter((feed) => feed.configured);
  const registered = configured.filter((feed) => feed.source_registered);
  const progress = state.groupFetch && state.groupFetch.source === source ? state.groupFetch : null;
  const lastRun = row.querySelector('.last-run');
  lastRun.textContent = configured.length ? groupRunText(group) : '—';
  lastRun.title = configured.length ? groupRunText(group) : 'Add a feed to inspect this source';
  const fetchButton = row.querySelector('.group-fetch');
  fetchButton.disabled = !registered.length || Boolean(progress) || Boolean(state.allFetch);
  fetchButton.textContent = progress ? `Fetching ${progress.completed}/${progress.total}…` : 'Fetch';
}

function renderFeeds() {
  const rows = $('feed-rows');
  rows.replaceChildren();
  const groups = visibleGroups();
  for (const group of groups) {
    const children = groupChildren(group);
    const configured = group.feeds.filter((feed) => feed.configured);
    const registered = configured.filter((feed) => feed.source_registered);
    const expanded = state.expandedSources.has(group.source) || Boolean(state.search.trim());
    const groupRow = document.createElement('tr');
    groupRow.className = 'group-row';
    groupRow.dataset.source = group.source;
    groupRow.innerHTML = '<td><button class="source-group-toggle"><span class="group-chevron"></span><strong></strong></button></td><td class="last-run"></td><td class="actions"><button class="group-fetch fetch">Fetch</button></td>';
    groupRow.querySelector('strong').textContent = group.source;
    groupRow.querySelector('.group-chevron').textContent = children.length ? (expanded ? '▾' : '▸') : '·';
    groupRow.querySelector('.last-run').textContent = configured.length ? groupRunText(group) : '—';
    groupRow.querySelector('.last-run').title = configured.length ? groupRunText(group) : 'Add a feed to inspect this source';
    const toggle = groupRow.querySelector('.source-group-toggle');
    toggle.disabled = !children.length;
    toggle.title = children.length ? (expanded ? 'Collapse source' : 'Expand source') : 'Add a feed to inspect this source';
    toggle.addEventListener('click', () => {
      if (state.expandedSources.has(group.source)) state.expandedSources.delete(group.source);
      else state.expandedSources.add(group.source);
      renderFeeds();
    });
    const groupFetch = groupRow.querySelector('.group-fetch');
    const groupProgress = state.groupFetch && state.groupFetch.source === group.source
      ? state.groupFetch
      : null;
    groupFetch.disabled = !registered.length || Boolean(groupProgress) || Boolean(state.allFetch);
    groupFetch.hidden = !registered.length;
    groupFetch.title = registered.length ? '' : 'No registered feed to fetch';
    groupFetch.textContent = groupProgress
      ? `Fetching ${groupProgress.completed}/${groupProgress.total}…`
      : 'Fetch';
    groupFetch.addEventListener('click', () => fetchGroup(group));
    rows.append(groupRow);

    if (!expanded) continue;
    for (const feed of children) {
      const row = document.createElement('tr');
      row.className = 'child-row';
      row.dataset.feedRef = feed.feed_ref;
      row.classList.toggle('selected', state.selectedFeed === feed.feed_ref);
      const label = feed.company_name || feed.key || 'Unnamed feed';
      row.innerHTML = '<td><button class="source-link"><span class="child-marker">↳</span><strong></strong></button></td><td class="last-run"></td><td class="actions"><button class="view-result secondary">View</button><button class="fetch">Fetch</button></td>';
      row.querySelector('strong').textContent = label;
      row.querySelector('.source-link').title = feed.company_name
        ? `Inspect ${feed.company_name} · token ${feed.key}`
        : `Inspect feed · token ${feed.key}`;
      row.querySelector('.last-run').textContent = lastRunText(feed.last_result);
      row.querySelector('.last-run').title = fullLastRunText(feed.last_result);
      row.querySelector('.source-link').addEventListener('click', () => inspectFeed(feed));
      const viewButton = row.querySelector('.view-result');
      viewButton.disabled = !feed.last_result;
      viewButton.addEventListener('click', () => inspectFeed(feed));
      const fetchButton = row.querySelector('.fetch');
      fetchButton.disabled = !feed.source_registered || isFetching(feed) || batchFetchActive();
      fetchButton.textContent = isFetching(feed) ? 'Fetching…' : 'Fetch';
      fetchButton.addEventListener('click', () => fetchFeed(feed));
      rows.append(row);
    }
  }
  const resultCount = $('feed-search-count');
  if (resultCount) resultCount.textContent = state.search.trim() ? `${visibleConfiguredFeeds().length} feeds` : '';
}

function renderCategoryTabs() {
  const labels = { ATS: 'ATS', Inhouse: 'Inhouse', 'Web Board': 'Web Board', Other: 'Other', all: 'All' };
  const counts = Object.fromEntries(Object.keys(labels).map((category) => [
    category,
    category === 'all'
      ? sourceGroups().length
      : category === 'Inhouse'
        ? sourceGroups()
          .filter((group) => group.category === category)
          .reduce((count, group) => count + group.feeds.filter((feed) => feed.configured).length, 0)
      : sourceGroups().filter((group) => group.category === category).length,
  ]));
  const tabs = $('category-tabs');
  tabs.replaceChildren();
  for (const category of Object.keys(labels)) {
    const button = document.createElement('button');
    button.className = `category-tab ${state.category === category ? 'active' : ''}`;
    button.setAttribute('aria-selected', String(state.category === category));
    button.innerHTML = '<span></span><span class="count"></span>';
    button.querySelector('span').textContent = labels[category];
    button.querySelector('.count').textContent = counts[category];
    button.addEventListener('click', () => { state.category = category; renderCategoryTabs(); renderFeeds(); });
    tabs.append(button);
  }
  const fetchAllButton = $('fetch-all');
  const allProgress = state.allFetch;
  fetchAllButton.textContent = allProgress
    ? `Fetching ${allProgress.completed}/${allProgress.total}…`
    : fetchAllLabel();
  fetchAllButton.disabled = !visibleConfiguredFeeds().some((feed) => feed.source_registered)
    || Boolean(allProgress) || Boolean(state.groupFetch);
}

async function loadFeeds() {
  try { state.feeds = (await api('/api/feeds')).feeds; renderCategoryTabs(); renderFeeds(); showMessage(''); }
  catch (error) { showMessage(error.message); }
}

async function fetchFeed(feed) {
  if (isFetching(feed) || batchFetchActive()) return;
  state.fetchingFeeds.add(feed.feed_ref);
  showMessage('');
  updateRenderedFeed(feed);
  try {
    // The response is produced only after the server has saved the run to
    // SQLite, so applying it here makes the row visibly current immediately.
    feed.last_result = await api(`/api/feeds/${feed.feed_ref}/fetch`, {
      method: 'POST', body: JSON.stringify({ force_full: true }),
    });
  } catch (error) {
    showMessage(error.message);
  } finally {
    state.fetchingFeeds.delete(feed.feed_ref);
    updateRenderedFeed(feed);
    updateRenderedGroup(feed.source);
  }
}

async function fetchGroup(group) {
  const feeds = groupChildren(group).filter((feed) => feed.source_registered);
  if (!feeds.length || batchFetchActive()) return;
  state.groupFetch = { source: group.source, total: feeds.length, completed: 0, failed: 0 };
  showMessage(`Fetching ${group.source}…`);
  renderCategoryTabs();
  renderFeeds();
  try {
    for (const feed of feeds) {
      state.fetchingFeeds.add(feed.feed_ref);
      updateRenderedFeed(feed);
      try {
        feed.last_result = await api(`/api/feeds/${feed.feed_ref}/fetch`, {
          method: 'POST', body: JSON.stringify({ force_full: true }),
        });
      } catch (error) {
        state.groupFetch.failed += 1;
      } finally {
        state.fetchingFeeds.delete(feed.feed_ref);
        state.groupFetch.completed += 1;
        updateRenderedFeed(feed);
        updateRenderedGroup(group.source);
      }
    }
  } finally {
    const { completed, failed } = state.groupFetch;
    state.groupFetch = null;
    renderCategoryTabs();
    await loadFeeds();
    showMessage(failed ? `Fetched ${completed - failed}/${completed} ${group.source} feeds.` : `Fetched ${completed} ${group.source} feeds.`);
  }
}

async function fetchAll() {
  const feeds = visibleConfiguredFeeds().filter((feed) => feed.source_registered);
  if (!feeds.length || batchFetchActive()) return;
  const label = categoryLabel(state.category);
  const scope = state.category === 'all' ? 'sources' : `${label} feeds`;
  state.allFetch = { total: feeds.length, completed: 0, failed: 0 };
  showMessage(`Fetching ${scope}…`);
  renderCategoryTabs();
  renderFeeds();
  try {
    // Run one feed at a time so a category-wide check does not burst every
    // upstream at once, and continue if an individual feed fails.
    for (const feed of feeds) {
      state.fetchingFeeds.add(feed.feed_ref);
      updateRenderedFeed(feed);
      try {
        feed.last_result = await api(`/api/feeds/${feed.feed_ref}/fetch`, {
          method: 'POST', body: JSON.stringify({ force_full: true }),
        });
      } catch (error) {
        state.allFetch.failed += 1;
      } finally {
        state.fetchingFeeds.delete(feed.feed_ref);
        state.allFetch.completed += 1;
        renderCategoryTabs();
        updateRenderedFeed(feed);
        updateRenderedGroup(feed.source);
      }
    }
  } finally {
    const { completed, failed } = state.allFetch;
    state.allFetch = null;
    renderCategoryTabs();
    await loadFeeds();
    showMessage(failed ? `Fetched ${completed - failed}/${completed} ${scope}; ${failed} failed.` : `Fetched ${completed} ${scope}.`);
  }
}

async function inspectFeed(feed) {
  state.selectedFeed = feed.feed_ref;
  renderFeeds();
  await loadResult(feed.feed_ref, feed);
}

function showEmptyInspection(feed) {
  setResultTitle(feed.source, feed.key);
  renderViewerSummary(null);
  $('item-list').replaceChildren();
  $('item-count').textContent = '';
  $('detail-title').textContent = 'Content';
  $('item-detail').textContent = 'Fetch this feed, then select an item or envelope.';
}

async function loadResult(feedRef, selectedFeed = null) {
  try {
    const data = await api(`/api/results/${feedRef}?limit=500`);
    state.selectedFeed = feedRef;
    setResultTitle(data.result.source, data.result.key);
    renderViewerSummary(data.result);
    const list = $('item-list'); list.replaceChildren();
    $('item-count').textContent = `(${data.result.item_count})`;
    state.selectedPart = null;
    const envelope = document.createElement('button');
    envelope.className = 'item-row envelope-row';
    envelope.innerHTML = '<span class="item-index">◆</span><span class="item-summary"><strong>Envelope</strong></span>';
    envelope.addEventListener('click', () => {
      state.selectedPart = 'envelope';
      document.querySelectorAll('.item-row').forEach((row) => row.classList.toggle('selected', row === envelope));
      $('detail-title').textContent = 'Content';
      $('item-detail').textContent = pretty({ result: data.result, envelope: data.envelope });
    });
    list.append(envelope);
    for (const item of data.items) {
      const el = document.createElement('button');
      el.className = 'item-row';
      el.title = item.display_name;
      el.innerHTML = '<span class="item-index"></span><span class="item-summary"><strong></strong></span>';
      el.querySelector('.item-index').textContent = item.index + 1;
      el.querySelector('strong').textContent = menuLabel(item.display_name);
      el.addEventListener('click', () => {
        state.selectedPart = item.index;
        document.querySelectorAll('.item-row').forEach((row) => row.classList.toggle('selected', row === el));
        loadItem(feedRef, item.index);
      });
      list.append(el);
    }
    envelope.click();
  } catch (error) {
    const feed = selectedFeed || state.feeds.find((candidate) => candidate.feed_ref === feedRef);
    if (error.status === 404 && feed) {
      showEmptyInspection(feed);
    } else {
      showMessage(error.message);
    }
  }
}

async function loadItem(feedRef, index) {
  try {
    $('detail-title').textContent = 'Content';
    $('item-detail').textContent = pretty(await api(`/api/results/${feedRef}/items/${index}`));
  } catch (error) { showMessage(error.message); }
}

function showPostingsMessage(message) {
  $('postings-message').textContent = message;
  $('postings-message').hidden = !message;
}

function renderPostingFilterDescription() {
  const descriptions = {
    exclude_full_time: 'Hides postings whose Ashby employment type is explicitly FullTime.',
    intern_explicit: 'Ashby employment type is explicitly Intern.',
    intern_implicit: 'Not Intern or FullTime; title or stored description names an internship, co-op, fellowship, or externship.',
    full_time_internship: 'Ashby employment type is FullTime, but title or stored description names an internship, co-op, fellowship, or externship.',
    all: 'No employment-type filtering; unlisted postings remain excluded.',
  };
  const locationRule = postingState.usOnly
    ? ' US-only excludes only postings with an explicit non-US structured address; Remote and unknown locations without a country stay visible.'
    : ' All locations are included.';
  $('postings-filter-description').textContent = `${descriptions[postingState.mode] || ''}${locationRule}`;
}

function setInspectorView(view) {
  const postings = view === 'postings';
  $('inspector-workspace').hidden = postings;
  $('postings-view').hidden = !postings;
  $('tab-inspector').classList.toggle('active', !postings);
  $('tab-postings').classList.toggle('active', postings);
  if (postings && !postingState.sources.length) loadPostings();
}

function renderPostingSources() {
  const select = $('postings-source');
  const selected = postingState.source ?? '';
  select.replaceChildren();
  const all = document.createElement('option');
  all.value = '*';
  all.textContent = 'All stored sources';
  select.append(all);
  for (const source of postingState.sources) {
    const option = document.createElement('option');
    option.value = source;
    option.textContent = source;
    select.append(option);
  }
  select.value = postingState.source ?? '';
  if (select.value !== selected) postingState.source = select.value || null;
}

function renderPostingRows() {
  const body = $('postings-rows');
  body.replaceChildren();
  if (!postingState.rows.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 5;
    cell.className = 'muted';
    cell.textContent = 'No stored postings match this scope and filter.';
    row.append(cell);
    body.append(row);
    return;
  }
  for (const posting of postingState.rows) {
    const row = document.createElement('tr');
    const titleCell = document.createElement('td');
    const titleButton = document.createElement('button');
    titleButton.className = 'posting-row';
    const title = document.createElement('strong');
    title.textContent = text(posting.title);
    const run = document.createElement('small');
    run.textContent = text(posting.finished_at);
    titleButton.append(title, run);
    titleButton.addEventListener('click', () => loadPostingDetail(posting));
    titleCell.append(titleButton);
    const sourceCell = document.createElement('td');
    sourceCell.textContent = text(posting.source);
    const token = document.createElement('span');
    token.className = 'posting-token';
    token.textContent = text(posting.feed_key);
    sourceCell.append(token);
    const typeCell = document.createElement('td');
    typeCell.textContent = text(posting.employment_type);
    const locationCell = document.createElement('td');
    locationCell.textContent = text(posting.location);
    const urlCell = document.createElement('td');
    const url = safeUrl(posting.url);
    if (url) {
      const link = document.createElement('a');
      link.className = 'posting-url';
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = url;
      urlCell.append(link);
    } else urlCell.textContent = '—';
    row.append(titleCell, sourceCell, typeCell, locationCell, urlCell);
    body.append(row);
  }
}

function renderPostingSummary() {
  $('postings-count').textContent = `${postingState.total.toLocaleString()} matching ${postingState.total === 1 ? 'posting' : 'postings'}`;
  const start = postingState.total ? postingState.offset + 1 : 0;
  const end = Math.min(postingState.offset + postingState.rows.length, postingState.total);
  $('postings-page').textContent = `Showing ${start.toLocaleString()}–${end.toLocaleString()}`;
  $('postings-prev').disabled = postingState.offset === 0;
  $('postings-next').disabled = postingState.offset + postingState.rows.length >= postingState.total;
}

async function loadPostings() {
  showPostingsMessage('');
  const params = new URLSearchParams({ mode: postingState.mode, us_only: String(postingState.usOnly), limit: String(postingState.limit), offset: String(postingState.offset) });
  if (postingState.source) params.set('source', postingState.source);
  if (postingState.search.trim()) params.set('search', postingState.search.trim());
  $('postings-count').textContent = 'Loading…';
  try {
    const data = await api(`/api/postings?${params}`);
    postingState.total = data.total;
    postingState.rows = data.rows;
    postingState.sources = data.sources || [];
    if (postingState.source === null && data.source) postingState.source = data.source;
    renderPostingFilterDescription();
    renderPostingSources();
    renderPostingRows();
    renderPostingSummary();
  } catch (error) {
    postingState.total = 0;
    postingState.rows = [];
    renderPostingRows();
    renderPostingSummary();
    showPostingsMessage(error.message);
  }
}

async function loadPostingDetail(posting) {
  $('posting-detail-title').textContent = text(posting.title);
  $('posting-detail').textContent = 'Loading…';
  try {
    const detail = await api(`/api/postings/${encodeURIComponent(posting.result_id)}/items/${posting.item_index}`);
    $('posting-detail').textContent = pretty(detail);
  } catch (error) {
    $('posting-detail').textContent = error.message;
  }
}

$('fetch-all').addEventListener('click', fetchAll);
if ($('feed-search')) {
  $('feed-search').addEventListener('input', (event) => {
    state.search = event.target.value;
    renderFeeds();
  });
}
$('tab-inspector').addEventListener('click', () => setInspectorView('inspector'));
$('tab-postings').addEventListener('click', () => setInspectorView('postings'));
$('postings-source').addEventListener('change', (event) => {
  postingState.source = event.target.value || '*';
  postingState.offset = 0;
  loadPostings();
});
$('postings-mode').addEventListener('change', (event) => {
  postingState.mode = event.target.value;
  postingState.offset = 0;
  renderPostingFilterDescription();
  loadPostings();
});
$('postings-us-only').addEventListener('change', (event) => {
  postingState.usOnly = event.target.checked;
  postingState.offset = 0;
  renderPostingFilterDescription();
  loadPostings();
});
$('postings-search').addEventListener('input', (event) => {
  postingState.search = event.target.value;
  postingState.offset = 0;
  clearTimeout(postingState.searchTimer);
  postingState.searchTimer = setTimeout(loadPostings, 220);
});
$('postings-refresh').addEventListener('click', loadPostings);
$('postings-prev').addEventListener('click', () => {
  postingState.offset = Math.max(0, postingState.offset - postingState.limit);
  loadPostings();
});
$('postings-next').addEventListener('click', () => {
  if (postingState.offset + postingState.rows.length < postingState.total) {
    postingState.offset += postingState.limit;
    loadPostings();
  }
});
loadFeeds();
