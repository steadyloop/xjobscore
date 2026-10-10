"""Keep transport noise out of Bronze revisions without losing source evidence."""

import asyncio
import os
from copy import deepcopy
from uuid import uuid4

import asyncpg
import pytest

from xjobscore import router, store
from xjobscore.records import Feed, Item, Record


def prepare(source, payload, envelope=None, *, key="job-1"):
    kind = "html" if isinstance(payload, str) else "json"
    feed = Feed(1, source, "example")
    return router.prepare(feed, Record(items=[Item(key, payload, kind)], envelope=envelope or {}))[0]


@pytest.mark.parametrize("source,first,second", [
    ("oracle", {"TotalJobsCount": 10, "company": "Acme"}, {"TotalJobsCount": 11, "company": "Acme"}),
    ("icims", {"totalCount": 10, "company": "Acme"}, {"totalCount": 11, "company": "Acme"}),
    ("greenhouse", {"meta": {"total": 10, "company": "Acme"}}, {"meta": {"total": 11, "company": "Acme"}}),
])
def test_board_counts_do_not_create_revisions(source, first, second):
    payload = {"title": "Intern", "totalCount": 3}
    a, b = prepare(source, payload, first), prepare(source, payload, second)
    assert a.data_hash == b.data_hash
    assert a.data["envelope"] == first and b.data["envelope"] == second
    assert a.data["payload"] == payload
    assert prepare(source, {**payload, "title": "Senior"}, second).data_hash != a.data_hash
    assert prepare(source, {**payload, "totalCount": 4}, second).data_hash != a.data_hash
    meaningful = {"meta": {"total": 11, "company": "Other"}} if source == "greenhouse" else {**second, "company": "Other"}
    assert prepare(source, payload, meaningful).data_hash != a.data_hash
    feed = Feed(1, source, "example")
    assert router.snapshot_hash([a]) == router.snapshot_hash([b])
    assert router.snapshot_hash([a]) != router.snapshot_hash([a, prepare(source, payload, first, key="job-2")])


def adp(clock):
    return {"requisitionTitle": "Intern", "postDate": "2026-10-01", "customFieldGroup": {
        "dateFields": [
            {"nameCode": {"codeValue": "PostingDate"}, "dateValue": "2026-10-01"},
            {"nameCode": {"codeValue": "CurrentServerDateTime"}, "dateValue": clock},
        ]}}


def workday(token):
    return {"title": "Intern", "jobPostingInfo": {"videoInfo": {"params": {
        "cdn_token": token,
        "poster": f"https://media.example/poster.jpg?__token__={token}",
        "sources": [{"url": f"https://media.example/video.mp4?duration=30&__token__={token}&tt=a%20b",
                     "type": "video/mp4"}],
        "tracks": [{"url": f"https://media.example/en.vtt?lang=en&__token__={token}#captions",
                    "label": "English"}],
    }}}}


@pytest.mark.parametrize("source,payload", [("adp", adp), ("workday", workday)])
def test_request_clocks_and_media_tokens_do_not_change_jobs_or_raw(source, payload):
    first, second = payload("first"), payload("second")
    originals = deepcopy((first, second))
    a, b = prepare(source, first), prepare(source, second)
    assert a.data_hash == b.data_hash
    assert (a.data["payload"], b.data["payload"]) == originals
    assert (first, second) == originals
    assert prepare("fixture", first).data_hash != prepare("fixture", second).data_hash


@pytest.mark.parametrize("source,payload,path,value", [
    ("adp", adp, ("requisitionTitle",), "Engineer"),
    ("adp", adp, ("postDate",), "2026-10-02"),
    ("adp", adp, ("customFieldGroup", "dateFields", 0, "dateValue"), "2026-10-02"),
    ("adp", adp, ("customFieldGroup", "dateFields", 1, "nameCode", "codeValue"), "ClosingDate"),
    ("workday", workday, ("title",), "Engineer"),
    ("workday", workday, ("jobPostingInfo", "videoInfo", "params", "poster"),
     "https://media.example/other.jpg?__token__=second"),
    ("workday", workday, ("jobPostingInfo", "videoInfo", "params", "sources", 0, "url"),
     "https://media.example/video.mp4?duration=60&__token__=second&tt=a%20b"),
    ("workday", workday, ("jobPostingInfo", "videoInfo", "params", "sources", 0, "url"),
     "https://other.example/video.mp4?duration=30&__token__=second&tt=a%20b"),
    ("workday", workday, ("jobPostingInfo", "videoInfo", "params", "sources", 0, "type"), "video/webm"),
    ("workday", workday, ("jobPostingInfo", "videoInfo", "params", "tracks", 0, "label"), "French"),
    ("workday", workday, ("jobPostingInfo", "videoInfo", "params", "tracks", 0, "url"),
     "https://media.example/en.vtt?lang=en&__token__=second#other"),
    ("workday", workday, ("cdn_token",), "unrelated field"),
])
def test_real_fields_still_create_revisions(source, payload, path, value):
    original, changed = payload("first"), payload("second")
    parent = changed
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    assert prepare(source, original).data_hash != prepare(source, changed).data_hash


@pytest.mark.parametrize("source,payload", [
    ("adp", {"customFieldGroup": None}),
    ("adp", {"customFieldGroup": {"dateFields": None}}),
    ("adp", {"customFieldGroup": {"dateFields": [None, {"nameCode": None, "dateValue": "first"}]}}),
    ("workday", {"jobPostingInfo": None}),
    ("workday", {"jobPostingInfo": {"videoInfo": None}}),
    ("workday", {"jobPostingInfo": {"videoInfo": {"params": None}}}),
    ("workday", {"jobPostingInfo": {"videoInfo": {"params": {
        "poster": "https://[invalid", "sources": None, "tracks": [None, {"url": None}]}}}}),
])
def test_unfamiliar_optional_shapes_keep_the_full_hash(source, payload):
    item = prepare(source, payload)
    assert item.data_hash == router.encoded_hash(item.data)


def hn_envelope(activity):
    return {"story_id": "42", "story_title": "Who is hiring?", "story": {
        "objectID": "42", "title": "Who is hiring?", "children": [activity],
        "num_comments": activity, "updated_at": str(activity),
    }}


def test_hn_thread_activity_does_not_change_jobs_or_raw():
    payload = {"comment_text": "Acme | Engineer", "updated_at": "first"}
    first, second = hn_envelope(1), hn_envelope(2)
    a, b = prepare("hn", payload, first), prepare("hn", payload, second)
    assert a.data_hash == b.data_hash
    assert a.data["envelope"] == first and b.data["envelope"] == second
    assert a.data["payload"] == b.data["payload"] == payload
    for field, value in (("comment_text", "Acme | Senior Engineer"), ("updated_at", "second")):
        assert prepare("hn", {**payload, field: value}, second).data_hash != a.data_hash
    for field, value in (("story_id", "43"), ("story_title", "New thread")):
        assert prepare("hn", payload, {**second, field: value}).data_hash != a.data_hash
    assert prepare("hn", payload, {**second, "story": {**second["story"], "title": "New title"}}).data_hash != a.data_hash
    assert prepare("fixture", payload, first).data_hash != prepare("fixture", payload, second).data_hash
    feed = Feed(1, "hn", "example")
    assert router.snapshot_hash([a]) == router.snapshot_hash([b])
    assert router.snapshot_hash([a]) != router.snapshot_hash([a, prepare("hn", payload, first, key="job-2")])


def avature(token, title="Intern"):
    return (f'<meta name="csp-nonce-style" content="{token}">'
            f"<meta content='{token}' name='csp-nonce-script'>"
            f'<script nonce="{token}">startup()</script>'
            f'<portal-data data-trackercode="{token}" data-cortex=\'{token}\' '
            f'data-publicsessionname = "{token}" data-job-id="job-1"></portal-data>'
            f'<h1>{title}</h1><p>Python</p>')


@pytest.mark.parametrize("upper", [False, True])
def test_avature_ignores_session_fields_but_preserves_job_content_and_raw(upper):
    def html(token, title="Intern"):
        value = avature(token, title)
        if upper:
            for name in ("<meta", "content=", "<portal-data", "data-trackercode", "data-cortex", "data-publicsessionname"):
                value = value.replace(name, name.upper())
        return value
    original = html("first")
    a = prepare("avature", original)
    assert a.data_hash == prepare("avature", html("second")).data_hash
    assert a.data_hash != prepare("avature", html("second", "Engineer")).data_hash
    assert a.data_hash != prepare("avature", html("second").replace("Python", "Rust")).data_hash
    assert a.data_hash != prepare("avature", html("second").replace("job-1", "job-2")).data_hash
    assert a.data_hash == prepare("avature", html("second").replace('data-trackercode="second"', 'data-trackercode="second\nline"')).data_hash
    assert a.data_hash != prepare("avature", html("second") + "<p>This job is closed</p>").data_hash
    assert router.unwrap(a.data)[0].payload == original
    assert prepare("fixture", original).data_hash != prepare("fixture", avature("second")).data_hash
    for content in ('<p>Use nonce="first"</p>', '<script data-nonce="first"></script>',
                    '<p>data-cortex="first"</p>', '<a data-trackercode="first">Apply</a>',
                    '<portal-data-extra data-cortex="first"></portal-data-extra>'):
        assert prepare("avature", content).data_hash != prepare("avature", content.replace("first", "second")).data_hash


def taleo(token="first", title="Intern", posting_date="10/01/2026", schedule="Full time"):
    return f'''<html><head><link href="style.css?timestamp={token}"></head><body>
        <input type="hidden" name="csrftoken" value="{token}">
        <script>
        descRequisition: {{_size:1, _hles:['f1','f2','f3','f4'],
            _hlid:['title','description','postingdate','jobschedule']}}
        api.fillList('{token}', 'descRequisition', ['{title}','Python','{posting_date}','{schedule}']);
        </script><p>Careers</p></body></html>'''


def test_taleo_ignores_form_metadata_but_hashes_full_job_data():
    original = taleo()
    a = prepare("taleo", original)
    assert a.data_hash == prepare("taleo", taleo("second")).data_hash
    for payload in (taleo(title="Engineer"), taleo(posting_date="10/02/2026"),
                    taleo(schedule="Part time"), taleo().replace("Python", "Rust"),
                    taleo().replace("Careers", "This job is closed")):
        assert a.data_hash != prepare("taleo", payload).data_hash
    assert router.unwrap(a.data)[0].payload == original


@pytest.mark.parametrize("payload", ["<p>Job not found</p>", "<script>descRequisition: broken</script>"])
def test_taleo_unfamiliar_pages_keep_full_hash(payload):
    assert prepare("taleo", payload).data_hash != prepare("taleo", payload + "changed").data_hash


@pytest.fixture
async def bronze_db():
    dsn = os.environ.get("XJOBSCORE_TEST_DSN")
    if not dsn:
        pytest.skip("set XJOBSCORE_TEST_DSN for PostgreSQL tests")
    schema = "bronze_hash_test_" + uuid4().hex
    setup = await asyncpg.connect(dsn)
    pool = None
    try:
        await setup.execute(f"CREATE SCHEMA {schema}; SET search_path TO {schema}")
        await setup.execute('''CREATE TABLE raw_posting(
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, source text NOT NULL,
            source_key text NOT NULL, data jsonb NOT NULL, data_hash text NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT now(), UNIQUE(source,source_key,data_hash));
            CREATE TABLE job_posting(id bigint PRIMARY KEY, raw_id bigint REFERENCES raw_posting(id));''')
        pool = await store.connect(dsn, search_path=schema)
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await setup.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await setup.close()


def legacy_hash(source, data):
    volatile = router.VOLATILE.get(source, frozenset())
    return router.encoded_hash({**data, "payload": router._strip(data["payload"], volatile),
                                "envelope": router._strip(data["envelope"], volatile)})


@pytest.mark.parametrize("source,old,new", [
    ("oracle", prepare("oracle", {"title": "Intern"}, {"TotalJobsCount": 10}),
     prepare("oracle", {"title": "Intern"}, {"TotalJobsCount": 11})),
    ("greenhouse", prepare("greenhouse", {"title": "Intern"}, {"meta": {"total": 10}}),
     prepare("greenhouse", {"title": "Intern"}, {"meta": {"total": 11}})),
    ("icims", prepare("icims", {"title": "Intern"}, {"totalCount": 10}),
     prepare("icims", {"title": "Intern"}, {"totalCount": 11})),
    ("avature", prepare("avature", avature("first")), prepare("avature", avature("second"))),
    ("taleo", prepare("taleo", taleo("first")), prepare("taleo", taleo("second"))),
    ("adp", prepare("adp", adp("first")), prepare("adp", adp("second"))),
    ("workday", prepare("workday", workday("first")), prepare("workday", workday("second"))),
    ("hn", prepare("hn", {"comment_text": "Acme | Engineer"}, hn_envelope(1)),
     prepare("hn", {"comment_text": "Acme | Engineer"}, hn_envelope(2))),
])
async def test_rollout_reuses_old_raw_without_rewriting_it(bronze_db, source, old, new):
    feed = Feed(1, source, "example")
    old_hash = legacy_hash(source, old.data)
    async with bronze_db.acquire() as conn:
        raw_id, _ = await store.save_raw(conn, source=source+":example", source_key="job-1",
                                         data=old.data, data_hash=old_hash)
        await conn.execute("INSERT INTO job_posting VALUES(1,$1)", raw_id)
        for _ in range(2):
            stored = (await router.persist(conn, feed, [new]))[0]
            assert stored.raw_id == raw_id and not stored.created
            assert stored.data == new.data and stored.data_hash == old_hash
        assert await conn.fetchval("SELECT count(*) FROM raw_posting") == 1
        assert await conn.fetchval("SELECT data FROM raw_posting") == old.data
        assert await conn.fetchval("SELECT data_hash FROM raw_posting") == old_hash
        assert await conn.fetchval("SELECT raw_id FROM job_posting") == raw_id


async def test_real_changes_and_reverts_use_exact_hash_first(bronze_db):
    feed = Feed(1, "oracle", "example")
    a = prepare("oracle", {"title": "Intern"}, {"TotalJobsCount": 10})
    b = prepare("oracle", {"title": "Engineer"}, {"TotalJobsCount": 11})
    async with bronze_db.acquire() as conn:
        original = (await router.persist(conn, feed, [a]))[0]
        changed = (await router.persist(conn, feed, [b]))[0]
        assert original.created and changed.created and original.raw_id != changed.raw_id
        reverted = (await router.persist(conn, feed, [a]))[0]
        assert not reverted.created and reverted.raw_id == original.raw_id
        assert await conn.fetchval("SELECT count(*) FROM raw_posting") == 2


async def test_hn_job_edits_create_revisions_and_reverts_reuse_them(bronze_db):
    feed = Feed(1, "hn", "example")
    a = prepare("hn", {"comment_text": "Acme | Engineer"}, hn_envelope(1))
    b = prepare("hn", {"comment_text": "Acme | Senior Engineer"}, hn_envelope(2))
    async with bronze_db.acquire() as conn:
        original = (await router.persist(conn, feed, [a]))[0]
        edited = (await router.persist(conn, feed, [b]))[0]
        assert original.created and edited.created and original.raw_id != edited.raw_id
        reverted = (await router.persist(conn, feed, [prepare("hn", a.data["payload"], hn_envelope(3))]))[0]
        assert not reverted.created and reverted.raw_id == original.raw_id
        assert await conn.fetchval("SELECT count(*) FROM raw_posting") == 2


async def test_same_key_multiple_variants_and_concurrent_writes(bronze_db):
    feed = Feed(1, "oracle", "example")
    a, b = prepare("oracle", {"location": "US"}), prepare("oracle", {"location": "CA"})
    async def persist():
        async with bronze_db.acquire() as conn:
            return await router.persist(conn, feed, [a, b, a])
    batches = await asyncio.gather(persist(), persist())
    assert sum(raw.created for batch in batches for raw in batch) == 2
    assert batches[0][0].raw_id == batches[0][2].raw_id == batches[1][0].raw_id
    assert await bronze_db.fetchval("SELECT count(*) FROM raw_posting") == 2


async def test_malformed_previous_raw_does_not_block_fresh_revision(bronze_db):
    async with bronze_db.acquire() as conn:
        await store.save_raw(conn, source="oracle:example", source_key="job-1",
                             data={"version": 99}, data_hash="legacy")
        stored = await router.persist(conn, Feed(1, "oracle", "example"), [prepare("oracle", {"title": "Intern"})])
        assert stored[0].created


async def test_unaffected_vendor_keeps_existing_persistence_path(bronze_db):
    feed = Feed(1, "lever", "example")
    item = prepare("lever", {"title": "Intern"})
    async with bronze_db.acquire() as conn:
        assert (await router.persist(conn, feed, [item]))[0].created
        assert not (await router.persist(conn, feed, [item]))[0].created
