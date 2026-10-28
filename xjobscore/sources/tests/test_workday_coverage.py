"""Coverage policy and eligibility regressions from actual audited postings."""
import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

from xjobscore import sources, undergraduate
from xjobscore.records import Feed, Item
from xjobscore.sources import json_api
from xjobscore.sources.tests.test_workday_search import SearchNet, NOW, KEY


CASES = json.loads((Path(__file__).parents[3] / 'fixtures/workday_undergraduate_audit.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['title'])
def test_reviewed_applicant_eligibility(case):
    config = json_api.CONFIGS['workday']
    payload = {'title': case['title'], 'jobPostingInfo': {'jobDescription': case['description']}}
    context = {'key': case['key']}
    admitted = json_api._value(config['detail']['only'], payload, context) is True
    kind = json_api._value(config['fields']['job_type'], payload, context)
    assert bool(admitted and kind) == case['expected_include'], case['reason']
    if 'expected_kind' in case:
        assert kind == case['expected_kind']


@pytest.mark.parametrize('description', [
    'You will advise currently enrolled undergraduate students.',
    'Support currently enrolled university students with their schedules.',
    'Responsible for advising currently enrolled university students.',
    'Supervise the student assistant while managing the office.',
])
def test_staff_serving_students_is_not_student_eligibility(description):
    assert undergraduate.job_type('Academic Adviser', description, title_type=None) is None


@pytest.mark.parametrize(('description', 'excluded'), [
    ('Must have completed a bachelor degree and be enrolled in a master degree program.', True),
    ('Must be pursuing a bachelor or master degree.', False),
    ('Must be a senior graduate student enrolled in a master degree programme.', True),
    ('Must be a junior, senior, or graduate student enrolled in a master’s or college program.', False),
    ('Enrolled in a baccalaureate or graduate program.', False),
    ('Currently pursuing a degree in engineering. MS Office skills required.', False),
    ('Must be enrolled in a post-secondary institution. Candidates currently enrolled in a Masters or Ph.D. program may also be considered.', False),
    ('Minimum qualifications: Pursuing a master degree. Preferred qualifications: bachelor-level research experience.', True),
    ('Requirements: Pursuing an engineering degree. Preferred qualifications: enrolled in a master degree program.', False),
])
def test_required_degree_and_optional_alternatives(description, excluded):
    assert undergraduate.advanced_required(description) is excluded


@pytest.fixture
def production_config(monkeypatch):
    config = copy.deepcopy(json_api.CONFIGS['workday'])
    config['list']['body']['limit'] = 2
    config['list']['page'].update(size=2, step=2, concurrency=2)
    config['list']['partition']['window'] = 100
    monkeypatch.setitem(json_api.CONFIGS, 'workday', config)
    return config


def current_feed():
    return Feed(id=1, source='workday', key=KEY, list_total=10, last_full_poll_at=NOW)


async def test_equal_count_replacement_beyond_first_page_is_fetched(production_config):
    net = SearchNet(selected=(0, 3, 5))  # A search would omit row 9.
    net.jobs[9]['title'] = 'German Language Tutor'
    record = await sources.reusing('workday', {}, now=NOW + timedelta(hours=1))(current_feed(), net)
    assert len(record.items) == 10
    assert all(not request['searchText'] for request in net.posts)
    assert len(net.posts) == 5
    assert record.full_listing and not record.filtered_listing and not record.count_unchanged
    ordinary = next(item for item in record.items if item.external_id == '/job/9')
    assert 'jobPostingInfo' in ordinary.payload
    assert record.detail_fetched_count == 10


@pytest.mark.parametrize(('age', 'details', 'reused'), [(1, 0, 10), (25, 10, 0)])
async def test_full_listings_preserve_bounded_detail_reuse(production_config, age, details, reused):
    net = SearchNet()
    # Stable locationsText is part of Workday's list revision.
    for row in net.jobs:
        row['locationsText'] = 'Austin, TX'
    prior = {row['externalPath']: Item(row['externalPath'], {
        **row, 'jobPostingInfo': {'jobDescription': 'Previous description'},
    }) for row in net.jobs}
    record = await sources.reusing('workday', prior, now=NOW + timedelta(hours=age))(current_feed(), net)
    assert len(net.posts) == 5
    assert record.full_listing
    assert record.detail_fetched_count == details
    assert record.reused_item_count == reused


def test_student_coop_can_work_on_continuity_planning():
    description = "Continuity of Operations (COOP) planning internship. Must be currently enrolled in an undergraduate program."
    assert undergraduate.job_type('COOP Student', description, title_type='coop') == 'coop'


def test_preferred_experience_does_not_override_required_degree():
    description = "Preferred Experience and Skills:\nPrior lab work.\nRequired Education:\nMaster’s degree in chemistry."
    assert undergraduate.advanced_required(description)


def test_degree_benefit_is_not_a_required_completed_degree():
    description = "Requirements:\nPursuing an undergraduate degree.\nPreferred Education:\nMaster’s degree in engineering."
    assert not undergraduate.advanced_required(description)
