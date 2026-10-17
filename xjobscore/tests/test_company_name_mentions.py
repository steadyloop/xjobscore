"""Novel-name tests distinguish extraction, lookup, and employer selection."""
from dataclasses import asdict
import json
import random

import pytest

from xjobscore.company_name import CompanyNameCatalog, extract_company_name, extract_company_names, plain_text


@pytest.mark.parametrize('description,expected', [
    ('Caliber Collision has an immediate job opening for an apprentice.', 'Caliber Collision'),
    ('Cedar Robotics seeks a Summer Legal Intern.', 'Cedar Robotics'),
    ('Cobalt Orchard Labs offers paid internship opportunities.', 'Cobalt Orchard Labs'),
    ('When you work at Meijer, you are provided with career opportunities.', 'Meijer'),
    ('For us at Cedar Robotics, we value learning.', 'Cedar Robotics'),
    ('CURRENT PENN STATE EMPLOYEE please apply internally.', 'PENN STATE'),
    ('Name and Address of Employer: St. Olaf College, 1520 St. Olaf Ave, Northfield, MN', 'St. Olaf College'),
    ('Gilead Sciences Inc. is committed to providing equal employment opportunities to all employees.', 'Gilead Sciences Inc'),
    ('The City of Baltimore is an inclusive equal opportunity employer.', 'The City of Baltimore'),
    ('Cedar Robotics employees support clients.', 'Cedar Robotics'),
    ('Since 1980, Cedar Robotics has delivered high-quality products.', 'Cedar Robotics'),
    ('Candi Solar provides solar power to local communities.', 'Candi Solar'),
    ('Heidelberg Materials France Ciments recrute pour son activité.', 'Heidelberg Materials France Ciments'),
    ('Cedar Robotics busca estudiantes para prácticas.', 'Cedar Robotics'),
])
def test_generalized_employer_wording(description, expected):
    result = extract_company_name(description)
    assert result.name == expected
    assert result.status == 'matched'


def test_parent_name_is_available_without_replacing_stated_employer():
    result = extract_company_name('Acme Devices is hiring interns. Acme Devices is a subsidiary of Umbrella Holdings.')
    assert result.name == 'Acme Devices'
    assert any(m.name=='Umbrella Holdings' for m in result.mentions)
    assert any(e.relation=='parent' for e in result.evidence)


def test_institution_can_be_recognized_inside_named_unit():
    result = extract_company_name('The Ithaca College Wellness Clinic welcomes students.')
    assert any(m.name=='The Ithaca College' for m in result.mentions)


def test_generic_candidate_extraction_requires_no_known_name():
    result = extract_company_names('Prepare a budget for Cobalt Orchard Labs employees.')
    assert any(m.name=='Cobalt Orchard Labs' for m in result)
    assert all(m.canonical_name is None for m in result)


@pytest.mark.parametrize('description,canonical', [
    ('Buckle’s retail business offers opportunities.', 'Buckle'),
    ('Macy’s retail business offers opportunities.', "Macy's"),
    ('Projects at café étoile are challenging.', 'Café Étoile'),
    ('We work with Holland & Knight employees.', 'Holland and Knight'),
    ('F.N.B. supports banking customers.', 'F.N.B. Corporation'),
    ('Work for Cedar Robotics staff.', 'The Cedar Robotics'),
])
def test_lookup_token_boundaries_punctuation_and_accents(description, canonical):
    catalogue = CompanyNameCatalog([canonical])
    result = extract_company_name(description, known_names=catalogue)
    matches = [m for m in result.mentions if m.canonical_name==canonical]
    assert matches
    body = plain_text(description)
    assert all(body[m.start:m.end]==m.name for m in matches)
    json.dumps(asdict(result))


def test_lookup_does_not_match_substrings_of_other_words():
    result = extract_company_name('SquareRoot is hiring at CobaltOrchard, Inc. MyBrand_42 has employees.', known_names=['Square','Cobalt','MyBrand'])
    assert not any(m.canonical_name for m in result.mentions)


def test_lookup_records_multiple_companies_and_does_not_force_a_winner():
    result = extract_company_name('Internships are offered with Acme employees and Beta Labs employees.', known_names=['Acme','Beta Labs'])
    assert {m.canonical_name for m in result.mentions if m.canonical_name}=={'Acme','Beta Labs'}
    assert result.name is None
    assert result.status=='ambiguous'


def test_customer_and_tool_mentions_are_not_automatically_employers():
    result = extract_company_name('Knowledge of Microsoft and experience with Salesforce are required.', known_names=['Microsoft','Salesforce'])
    assert len([m for m in result.mentions if m.canonical_name])==2
    assert result.name is None


def test_unfamiliar_wording_lookup_is_supporting_not_automatic_fallback():
    result = extract_company_name('Daily tasks support Acme employees.', known_names=['Acme'])
    assert result.name=='Acme'
    assert result.confidence=='supporting'


def test_lookup_cannot_resolve_an_unnamed_client():
    result = extract_company_name('Beacon Staffing is an equal opportunity employer. Our client is a leading manufacturer.', known_names=['Beacon Staffing'])
    assert result.status=='ambiguous'
    assert result.name is None


def test_generated_new_names_do_not_depend_on_training_companies():
    rng=random.Random(842)
    prefixes=['Cobalt','Orchid','Cedar','Lunar','Amber','Silver','Étoile','Zephyr']
    middles=['Meadow','Orchard','Harbor','River','Comet','Quartz']
    endings=['Labs','Industries','Robotics','Systems']
    for _ in range(80):
        name=f'{rng.choice(prefixes)} {rng.choice(middles)} {rng.choice(endings)}'
        description=f'{name} is hiring interns.'
        assert extract_company_name(description).name==name
        catalogue=CompanyNameCatalog([name])
        assert any(m.canonical_name==name for m in catalogue.find(description))


@pytest.mark.parametrize('name,description', [
    ('Bang Zoom! Studios','Bang Zoom! Studios is hiring.'),
    ('THE·TEAM','THE·TEAM provides services.'),
    ('Re:Build Manufacturing','Re:Build Manufacturing is hiring.'),
    ('H2M Architects + Engineers','H2M architects + engineers is hiring.'),
    ('Our Children’s Trust','At Our Children’s Trust, we hire interns.'),
    ('Our National Conversation','OUR NATIONAL CONVERSATION invites students.'),
    ('As You Sow','We have no open internships at As You Sow.'),
    ('Fort Hays Tech | North Central','Fort Hays Tech | North Central is an employer.'),
    ('candi solar','Visit www.candi.solar to learn more.'),
])
def test_unusual_brand_spelling_is_recovered_by_lookup(name,description):
    matches=extract_company_names(description,known_names=[name])
    assert any(m.canonical_name==name for m in matches)


def test_incomplete_entity_at_end_of_plain_text_is_flushed():
    body='Petco is hiring interns. Research & Development &'
    assert plain_text(body)==body
    assert any(m.canonical_name=='Petco' for m in extract_company_names(body,known_names=['Petco']))


def test_lookup_does_not_cross_sentence_punctuation_absent_from_name():
    assert not CompanyNameCatalog(['Alpha Beta']).find('Alpha! Beta is hiring.')


def test_generic_url_hints_do_not_assign_an_employer():
    result=extract_company_name('Visit www.candi.solar and https://www.example.com/about/cedar-robotics.')
    hints=[m for m in result.mentions if m.rule=='website_name_hint']
    assert any(m.name=='candi.solar' for m in hints)
    assert result.name is None


def test_malformed_website_hint_does_not_fail_description_parsing():
    assert extract_company_name('Cobalt Labs is hiring. https://[broken.com').name=='Cobalt Labs'


def test_catalogue_requires_a_collection_of_names():
    with pytest.raises(TypeError,match='iterable'):
        CompanyNameCatalog('Cobalt Labs')


def test_feed_config_lookup_retains_authoritative_names_and_only_suggests():
    from xjobscore.company_name import enrich_posting
    from xjobscore.records import Feed,Posting
    feed=Feed(id=1,source='lever',key='cobalt',config={'company_from_description':'fallback','company_description_known_names':['Cobalt Labs']})
    post=Posting(external_id='1',title='Intern',url_posting='https://example/1',company_raw='cobalt',description_html='Daily work supports Cobalt Labs employees.')
    result=enrich_posting(feed,post)
    assert result.company_raw=='cobalt'
    lookup=result.source_extra['company_name_from_description']
    assert lookup['confidence']=='supporting'
    assert any(m['canonical_name']=='Cobalt Labs' for m in lookup['mentions'])


def test_punctuation_in_brand_does_not_leave_only_industry_noun():
    assert extract_company_name('Bang Zoom! Studios is hiring interns.').name=='Bang Zoom! Studios'


def test_bare_institution_type_is_not_a_company_identity():
    assert extract_company_name('The University is a leading university.').name is None


def test_lookup_does_not_assign_a_vendor_to_an_unnamed_client_without_other_evidence():
    result=extract_company_name('Our client needs experience with Microsoft. Interns work on site.',known_names=['Microsoft'])
    assert result.name is None
    assert result.status=='ambiguous'
