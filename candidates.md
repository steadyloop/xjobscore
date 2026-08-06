## Candidates — ROI Order

Priority 1 is the highest expected return after breadth, target relevance, integration effort, and maintenance risk.

| Priority | Source | Endpoint | Reason |
|---:|---|---|---|
| 1 | USAJOBS Search API | https://data.usajobs.gov/api/search | Official federal system of record; API. |
| 15 | Zintellect (ORAU/ORISE catalog) | https://www.zintellect.com/Catalog | Official ORISE opportunity catalog. |
| 16 | YC public jobs page | https://www.ycombinator.com/jobs | Original YC postings; public Inertia JSON. |
| 17 | HN "Who is hiring?" — Algolia API | https://hn.algolia.com/api/v1/search_by_date?tags=comment,story_&lt;id&gt;&hitsPerPage=1000 | Original employer/founder posts; public API. |
| 18 | Google Careers (all jobs) | https://www.google.com/about/careers/applications/jobs/results | First-party jobs; stable server HTML. |
| 19 | NIH Summer Internship Program (SIP) | https://www.training.nih.gov/research-training/pb/sip/ | Official NIH internship application. |
| 20 | SULI (Science Undergraduate Laboratory Internships) | https://science.osti.gov/wdts/suli | Official DOE-wide application program. |
| 21 | NASA OSTEM internships (NASA Gateway) | https://stemgateway.nasa.gov/s/explore-opportunities?opportunitytype=internships | Official NASA project application portal. |
| 22 | Meta Careers (all jobs) | https://www.metacareers.com/jobsearch/ | First-party jobs; embedded page data. |
| 31 | Wayfair | https://www.wayfair.com/careers/jobs/ | First-party in-house job board. |
| 32 | Adobe Research internships | https://research.adobe.com/careers/ | First-party research list; separate corpus. |
| 33 | Google Summer of Code | https://summerofcode.withgoogle.com/ | Official unique open-source program. |
| 34 | CIA Student Programs | https://www.cia.gov/careers/student-programs/ | Official CIA student source; distinct corpus. |
| 35 | IC-wide student programs portal | https://apply.intelligencecareers.gov/student-programs | Official IC-wide application portal. |
| 36 | Naval STEM Interns portal | https://www.navalsteminterns.us/ | Official Naval internship portal. |
| 37 | AEOP (Army Educational Outreach Program) hub | https://usaeop.com/ | Official Army program catalog. |
| 38 | SMART Scholarship-for-Service | https://www.smartscholarship.org/smart | Official scholarship/internship program. |
| 39 | Coding it Forward — Civic Digital Fellowship / Summer Fellowship | https://codingitforward.com/fellowship | Original civic-tech fellowship source. |
| 40 | NSIN (National Security Innovation Network) | https://www.nsin.us/x-force/ | Official NSIN fellowship source. |
| 41 | Forage (virtual work experience) | https://www.theforage.com/sitemap.xml | Official simulation catalog; public sitemap. |
| 42 | Extern | https://www.extern.com/externships | Original externship cohorts. |
| 43 | NOAA Hollings Undergraduate Scholarship | https://www.noaa.gov/office-education/hollings-scholarship | Official NOAA scholarship/internship. |
| 44 | Smithsonian Institution internships (SOLAA) | https://internships.si.edu/ | Official Smithsonian application system. |
| 45 | California — CalCareers | https://calcareers.ca.gov/ | Official California job system. |
| 46 | New York — StateJobsNY | https://statejobs.ny.gov/ | Official New York job system. |
| 47 | NYC Jobs | https://cityjobs.nyc.gov/ | Official NYC job system. |
| 50 | Trac (NHS Jobs) | https://www.jobs.nhs.uk/candidate/search | Official NHS job source; public search. |
| 61 | Unstop (India, formerly Dare2Compete) | https://unstop.com/api/public/opportunity/search-result?opportunity=internships&page=1&per_page=15 | Original opportunity platform; public JSON. |
| 62 | Fly.io | https://fly.io/jobs | First-party long-form job postings. |

## Origin ATS Board

All origin-ATS candidates are consolidated here; priorities remain global. `ats.py` fit distinguishes direct shared-adapter configurations from sources needing another transport/parser.

| Priority | Source | Endpoint | Original Reason | ats.py Fit |
|---:|---|---|---|---|
| 2 | SAP SuccessFactors — sitemap | https://jobs.{company}.com/sitemap.xml | Origin ATS; sitemap enumerates jobs. | No — sitemap/HTML. |
| 3 | Recruitee (Tellent) | https://{company}.recruitee.com/api/offers/ | Origin ATS; public JSON. | Direct JSON config. |
| 4 | Pinpoint | https://{company}.pinpointhq.com/postings.json | Origin ATS; public JSON. | Direct JSON config. |
| 5 | ADP Workforce Now | GET https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions?cid={cid}&ccId={ccId} | Origin ATS; public JSON. | Direct JSON config. |
| 6 | Dayforce — search API | POST https://jobs.dayforcehcm.com/api/geo/{tenant}/jobposting/search | Origin ATS; public JSON. | JSON POST config. |
| 7 | Paylocity | https://recruiting.paylocity.com/recruiting/jobs/All/{uuid}/{company-slug} | Origin ATS; public board. | No — HTML board. |
| 8 | Paycom | https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey={32-hex} | Origin ATS; public board. | No — HTML board. |
| 9 | UKG / UltiPro Recruiting | POST https://recruiting.ultipro.com/{companyCode}/JobBoard/{boardGuid}/JobBoardView/LoadSearchResults | Origin ATS; public JSON. | JSON POST config. |
| 10 | IBM Kenexa BrassRing | https://sjobs.brassring.com/TGnewUI/Search/home/HomeWithPreLoad?partnerid={pid}&siteid={sid} | Origin ATS; public search endpoint. | JSON POST search config. |
| 11 | Avature | https://{company}.avature.net/careers/SearchJobs?jobRecordsPerPage=100&jobOffset=0 | Origin ATS; stable public pagination. | No — HTML board. |
| 12 | Taleo (Oracle, legacy) | POST https://{company}.taleo.net/careersection/rest/jobboard/searchjobs?lang=en&portal={portalId} | Origin ATS; session-backed JSON. | No — session/CSRF flow. |
| 13 | PageUp | POST https://careers.pageuppeople.com/{boardId}/{route}/{locale}/search/ | Origin ATS; paginated JSON. | JSON POST config. |
| 14 | PeopleAdmin (Powered by PeopleAdmin) | https://{institution}.peopleadmin.com/postings/search | Origin higher-ed ATS; public search. | No — HTML search. |
| 23 | Eightfold.ai — SmartApply | https://{sub}.eightfold.ai/api/apply/v2/jobs?domain={company_domain}&hl=en&start=0&num=50 | Origin ATS; public JSON on enabled tenants. | Direct JSON config. |
| 24 | Personio | https://{company}.jobs.personio.de/xml (also .com) | Origin ATS; public XML. | No — XML/RSS parser. |
| 25 | JazzHR (Employ Inc) | https://{company}.applytojob.com/apply/jobs/feed (RSS) | Origin ATS; public RSS. | No — RSS parser. |
| 26 | Bullhorn public job API | https://public-rest{cls}.bullhornstaffing.com/rest-services/{corpToken}/query/JobBoardPost?fields=...&where=isOpen=true | Origin staffing ATS; public API. | Direct JSON config. |
| 27 | Gem ATS job board | POST https://jobs.gem.com/api/public/graphql/batch | Origin ATS; public GraphQL. | JSON POST config plus `batch: true`. |
| 28 | ApplicantPro | https://{company}.applicantpro.com/core/jobs/{domain_id} | Origin ATS; public JSON. | Direct JSON config. |
| 29 | Cornerstone OnDemand (CSOD) | https://{company}.csod.com/ux/ats/careersite/{siteId}/home?c={company} | Origin ATS; public modern portal. | No — JWT/headless flow. |
| 30 | Manatal | https://www.careers-page.com/api/v1.0/c/{client_slug}/jobs/?page=1&page_size=50 | Origin ATS; paginated JSON. | Paginated JSON config. |
| 48 | JobAps | https://{state}.jobaps.com/ | Origin state ATS; public HTML. | No — HTML parser. |
| 49 | Oleeo (ex-WCN) | https://{company}.oleeo.com/ / branded | Origin early-career ATS; public HTML. | No — HTML parser. |
| 51 | HireTouch | https://{institution}.hiretouch.com/ | Origin higher-ed ATS; public boards. | No — HTML parser. |
| 52 | SilkRoad OpenHire | https://{company}.openhire.silkroad.com/ | Origin ATS; public legacy boards. | No — legacy HTML parser. |
| 53 | Recruit CRM | POST https://albatross.recruitcrm.io/v1/external-pages/jobs-by-account/get?account={acct}&batch=true | Origin agency ATS; public JSON. | JSON POST config. |
| 54 | JobDiva | https://{company}.jobdiva.com/portal/ | Origin staffing ATS; public portal. | No — HTML portal. |
| 55 | Ceipal | https://{company}.ceipal.com/jobs | Origin staffing ATS; public portal. | No — HTML portal. |
| 56 | Loxo | https://{company}.loxo.co/jobs | Origin staffing ATS; public portal. | No — HTML portal. |
| 57 | Vincere | https://{company}.vincere.io/careers/ | Origin staffing ATS; public portal. | No — HTML portal. |
| 58 | JobAdder | https://{company}.jobadder.com/careers | Origin staffing ATS; public portal. | No — HTML portal. |
| 59 | TalentReef (Mitratech) | POST https://prod-kong.internal.talentreef.com/apply/proxy-es/search-en-us/posting/_search | Origin ATS; public JSON search. | JSON POST config. |
| 60 | Fountain | https://web.fountain.com/c/{company}/{company}/{country}.json | Origin ATS; public JSON. | Direct JSON config. |
