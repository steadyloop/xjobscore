export const meta = {
  name: 'exhaustive-job-source-map',
  description: 'Build an exhaustive, machine-actionable catalog of every online source of CS/Applied-Math internships, co-ops, part-time and startup roles',
  phases: [
    { title: 'Domain sweep', detail: '14 parallel researchers, one per source family' },
    { title: 'Completeness critic', detail: 'read all sections, name what is missing' },
    { title: 'Gap fill', detail: 'agents cover the critic-identified holes' },
  ],
}

// Durable output dir (was the session scratchpad; scratchpad is ephemeral so this now lives in-repo).
const DIR = '/Users/mr.po/Documents/GitHub/xjobscore/xjobscore/sources/_opus-wip'

// Sections already written to DIR. Workflow scripts have no filesystem access, so completion
// cannot be auto-detected — update this list by hand from `ls _opus-wip/` before re-running.
const DONE = new Set([
  '01-general-aggregators',
  '02-ats-platforms',
  '03-student-intern-platforms',
  '04-startup-vc-ecosystem',
  '05-bigtech-structured-programs',
  '07-nontech-fortune-a',
  '09-government-labs',
])

const PREAMBLE = `
You are one of 14 parallel researchers building the PLANNING-STAGE source catalog for a student who wants to
capture EVERY online internship / co-op / part-time / startup role available to a CS + Applied Math undergraduate,
INCLUDING technical roles at non-tech companies. The catalog feeds a scraping/ingestion pipeline (project "xjobscore"),
so it must be machine-actionable, not a blog post.

GOAL: maximize VALID, DISTINCT, ACTIONABLE sources in your assigned domain. Breadth first, but no invented URLs.

RULES:
1. NEVER invent a URL. If you are not confident a URL is correct/live, either (a) use WebSearch/WebFetch to verify it,
   or (b) include it but mark confidence "low" and say how to find the real one. Prefer verifying anything you would
   rank Tier 1 or Tier 2.
2. Use WebSearch liberally to discover sources you do not already know, and to confirm 2025-2026 status
   (many boards died: Stack Overflow Jobs, GitHub Jobs, Otta->Welcome to the Jungle, AngelList->Wellfound, etc.).
   Flag anything dead/renamed explicitly rather than silently dropping it.
3. Be EXHAUSTIVE. Long tail matters more than the obvious. If you can name 120 sources, name 120. Do not stop at 20.
4. No duplication of another researcher's turf, but if a source spans domains, include it and note the overlap.

OUTPUT FORMAT — write a markdown file. Structure it as:

## <Section Number>. <Section Title>

<2-4 sentence orientation: why this family matters for this student, what the yield/effort profile is.>

### <Sub-group name>

| Source | URL | Access | Auth | Coverage | Tier | Notes |
|---|---|---|---|---|---|---|

Column meanings:
- Access: one of \`API\`, \`RSS\`, \`JSON\` (undocumented but stable JSON endpoint), \`HTML\` (scrape),
  \`HEADLESS\` (needs JS/browser automation), \`MANUAL\` (human check / no programmatic path),
  \`EMAIL\` (newsletter/alert), \`SOCIAL\` (Discord/Slack/X/Reddit feed).
- Auth: \`none\`, \`key\` (free API key), \`paid\`, \`account\` (login required), \`edu\` (school SSO), \`invite\`.
- Coverage: what it actually yields — role types, geography, volume estimate, freshness.
- Tier: \`T1\` (must-have, high yield / low effort), \`T2\` (worth building), \`T3\` (long tail / opportunistic),
  \`T0\` (dead or trap — documented so it is not re-investigated).
- Notes: the ingestion hint. Endpoint patterns, pagination quirks, rate limits, anti-bot, seasonality,
  deadline windows, or "must apply through X".

After the tables in your section, add:

**Ingestion notes** — a short prose block: concrete endpoint patterns / URL templates worth hardcoding,
known rate limits, anti-bot posture, and the seasonal calendar for this family (when postings open/close).

**Traps & dead ends** — what looks promising in this domain but is not, and why.

Write ONLY your own section. No preamble, no "here is my section", no closing summary. Start with the \`## N.\` heading.
`

const SECTIONS = [
  {
    n: 1,
    slug: '01-general-aggregators',
    title: 'General Job Aggregators, Meta-Search & Their APIs',
    brief: `Every general-purpose job board and aggregator, plus the programmatic layer under them.
Cover: LinkedIn (+ its internship/experience-level filters and the realities of scraping it), Indeed (+ the
publisher-API shutdown and what replaced it), Glassdoor, ZipRecruiter, SimplyHired, Monster, CareerBuilder, Dice,
Snagajob, Talent.com, Adzuna, Jooble, Careerjet, Jobrapido, Neuvoo, JobisJob, Trovit, Whatjobs, Lensa, Getwork,
LinkUp, Nexxt, Recruit.net, Jora, GlobalJobs, Ladders, Hired, Vettery, Otta/Welcome to the Jungle, Built In
(and its per-city subdomains), The Muse, Handshake's public surface, Google Jobs / the schema.org JobPosting
structured-data layer and how Google's job graph is reachable (SerpAPI, ValueSERP, Bright Data SERP).
Then the API/data-vendor tier: Adzuna API, USAJOBS API, Reed API, The Muse API, Jooble API, Careerjet API,
Findwork.dev, Arbeitnow, Remotive API, RemoteOK API, Himalayas API, JSearch/RapidAPI, Apify job actors,
Coresignal, People Data Labs, Bright Data, Oxylabs, Zyte, SerpAPI Google Jobs, LinkUp data, Common Crawl,
Greenhouse/Lever meta-aggregators. Also the new AI-native aggregators: HiringCafe, Simplify, Jobright,
Careerflow, Teal, Sonara, LazyApply, Massive/Talentprise — noting which ones are themselves scrapable.
For each API give the base endpoint, free-tier limits, and whether it exposes internships as a filter.`,
  },
  {
    n: 2,
    slug: '02-ats-platforms',
    title: 'ATS Platforms — The Direct-From-Employer Firehose',
    brief: `This is the single highest-leverage family: scrape the ATS, not the aggregator. Enumerate EVERY applicant
tracking system a US/global employer might use, with its public job-board URL pattern AND its public JSON endpoint
pattern where one exists. Cover at minimum: Greenhouse (job board API v1, embed vs hosted, boards.greenhouse.io),
Lever (api.lever.co/v0/postings), Ashby (jobs.ashbyhq.com + the posting-api GraphQL), Workday (myworkdayjobs.com
CXS /wday/cxs/ POST endpoint, tenant+site discovery), iCIMS, SmartRecruiters (api.smartrecruiters.com/v1/companies),
Taleo / Oracle Cloud Recruiting (ORC), SAP SuccessFactors, Jobvite, BambooHR, Breezy HR, Workable, Recruitee,
Personio, Teamtailor, JazzHR, Rippling, Gem, Dover, Pinpoint, Comeet, Eightfold.ai, Phenom People, Avature,
Paylocity, Paycom, ADP, UKG/Kronos, Ceridian Dayforce, ClearCompany, Hirebridge, ApplicantPro, Jobsoid,
Zoho Recruit, Freshteam, Manatal, Polymer, Trakstar/Hire, CATS, Bullhorn, Symplicity, Handshake-as-ATS,
Oleeo, Cornerstone OnDemand, Radancy/TalentBrew, Jibe, Talemetry, Beamery, SeekOut, Sense, Fountain, HiBob.
For each: the canonical public URL shape, the JSON endpoint if any, auth posture, pagination, rate limits,
Cloudflare/anti-bot status, and how to DISCOVER which companies use it (e.g. Google/Bing site: operators,
BuiltWith, PublicWWW, Common Crawl host queries, Wappalyzer, DNS/CNAME fingerprints).
Also cover open-source ATS-crawler projects and company-list repos that already map company -> ATS slug.`,
  },
  {
    n: 3,
    slug: '03-student-intern-platforms',
    title: 'Student- & Intern-Specific Platforms + Curated GitHub Lists',
    brief: `Everything built specifically for students. Cover: Handshake (incl. how the student API/GraphQL surface
behaves, school SSO, employer-direct postings that appear nowhere else), Symplicity/CSM, 12Twenty, Simplify.jobs,
RippleMatch, WayUp, Chegg Internships/Internships.com, LinkedIn Students, Forage (virtual experience -> pipeline),
Parker Dewey (micro-internships), Riipen, Extern, Praxis, Bright Network, Jumpstart/Untapped, Prepathon,
CareerShift, Vault/Firsthand, Ripplematch, Untapped.io, Steppingblocks, Yello, Brazen (virtual fairs),
CareerEco, Hire-A-Longhorn-style school portals in general.
Then GitHub curated lists — this is a major family. Cover the whole ecosystem including SimplifyJobs
Summer20XX-Internships and New-Grad-Positions, vanshb03/Summer20XX-Internships, ReaVNaiL/New-Grad,
Ouckah/Summer20XX-Internships, coderQuad, speedyapply, arunike, jobright-ai lists, cvrve/, pittcsc legacy,
CSCareers.dev, Levels.fyi internships, plus "awesome-*" lists and the GitHub topic/search queries that surface
new ones every season. Explain how to ingest these programmatically (raw.githubusercontent README parsing,
the .json listings some repos ship, git commit polling, GitHub API watch/stars, RSS via commit atom feeds).
Note the seasonality (repos rebuild each August/September) and how to auto-discover next season's repo.`,
  },
  {
    n: 4,
    slug: '04-startup-vc-ecosystem',
    title: 'Startup & VC Ecosystem — Talent Networks, Accelerators, Portfolio Boards',
    brief: `Startups rarely post to aggregators; you reach them through VC portfolio job boards and accelerator networks.
Cover: Y Combinator Work at a Startup (+ its API/JSON surface + the YC company directory), Wellfound (ex-AngelList
Talent), Wellfound recently-funded filters, Hacker News "Who Is Hiring" monthly threads (+ hnhiring.com,
the Algolia HN API for programmatic ingestion), Key Values, Startup.jobs, AngelMatch, Founders' networks.
Then VC talent/job boards — be exhaustive: a16z, Sequoia, Accel, Index, Greylock, Bessemer, Lightspeed,
General Catalyst, Founders Fund, Khosla, Craft, First Round, NEA, Insight Partners, Battery, Benchmark, 8VC,
Initialized, SignalFire, GV, Kleiner Perkins, Menlo, Redpoint, Scale Venture, Susa, Uncork, Village Global,
Bain Capital Ventures, Costanoa, Amplify, Boldstart, Felicis, FirstMark, Foundation Capital, Freestyle,
Homebrew, IVP, Madrona, Matrix, Mayfield, Norwest, Pear VC, Point Nine, Ribbit, Sapphire, Spark, Thrive,
Tiger Global, Union Square Ventures, Upfront, Wing, Emergence, Coatue, Dragoneer, ICONIQ, Altimeter.
Note which use Getro / Consider.vc / Jobs.gem / Teamtailor-style shared infrastructure — that shared
infrastructure is the real scraping win, so identify the common platforms (Getro/getro.com networks,
Consider, Jobs by VC, Breakout List, Cord).
Then accelerators/studios: Techstars, 500 Global, Antler, Entrepreneur First, AI Grant, Neo, South Park Commons,
Pioneer, On Deck, HF0, Founders Inc, Alchemist, MassChallenge, Plug and Play, SOSV/HAX/IndieBio, Betaworks,
Atomic, Sutter Hill, Hangar, Pace, Forum Ventures.
Also startup-discovery signals: Crunchbase, PitchBook, Tracxn, Dealroom, TechCrunch funding feed, Product Hunt,
Wellfound funding filters, layoffs.fyi (inverse signal), SEC Form D filings, Delaware incorporations.`,
  },
  {
    n: 5,
    slug: '05-bigtech-structured-programs',
    title: 'Big Tech & Named Structured / Early-Identity Programs',
    brief: `Named programs with their own application portals and calendars that never surface on aggregators, plus
the direct careers portals of every major tech employer. Cover early-identity / sophomore / first-year programs
exhaustively: Google STEP + Google SWE Intern + CSSI + Google Research Internships + Google Summer of Code,
Meta University (Engineering/Analytics/Product Design) + Meta SWE Intern + Above & Beyond, Microsoft Explore +
Microsoft Discovery + New Technologists + MSR internships, Amazon Propel + SDE Intern + Amazon Future Engineer,
Apple internships + Apple Scholars, Nvidia, Uber STAR + Uber Engineering, Pinterest Apprenticeship + Engage,
LinkedIn CORE/LEAP, Salesforce Futureforce, Adobe Digital Academy + Adobe Research, IBM Accelerate + Extreme Blue,
Intel, AMD, Qualcomm, Broadcom, Texas Instruments, Micron, Applied Materials, Lam Research, ASML, Analog Devices,
Marvell, Arm, Synopsys, Cadence, Palantir Path/Winter Fellowship, Stripe, Databricks, Snowflake, Datadog,
Cloudflare, Twilio, Atlassian, Airbnb, DoorDash, Instacart, Lyft, Coinbase, Robinhood, Plaid, Ramp, Brex,
Figma, Notion, Canva, Asana, Slack, Zoom, Dropbox, Box, Roblox, Unity, EA, Riot, Epic Games, Activision,
Take-Two, Netflix, Spotify, Reddit, Discord, Snap, TikTok/ByteDance, Shopify (Dev Degree!), Squarespace, Wix,
Yelp, Zillow, Redfin, Compass, Opendoor, Expedia, Booking, Tripadvisor, eBay, Etsy, Wayfair, Chewy, Shein,
Temu, Grammarly, Duolingo, Coursera, Udemy, Chegg, Khan Academy, Anthropic, OpenAI, DeepMind, Mistral,
Cohere, Scale AI, Hugging Face, Perplexity, Character.AI, Runway, Midjourney, Cursor/Anysphere, Sierra,
Harvey, Glean, Together, Fireworks, Groq, Cerebras, SambaNova, Tenstorrent, Modular, Weights & Biases,
LangChain, LlamaIndex, Pinecone, Weaviate, Chroma, Vercel, Netlify, Supabase, PlanetScale, Neon, Railway,
Render, Fly.io, Linear, Retool, Airtable, Zapier, Make, HashiCorp, GitLab, JetBrains, Docker, Grafana,
Elastic, Confluent, Redis, MongoDB, Neo4j, Couchbase, Cockroach, Timescale, ClickHouse, dbt, Fivetran,
Airbyte, Sigma, Looker, Tableau, Alteryx, Palantir, C3.ai, Samsara, Verkada, Rivian-style. For each employer
family give the careers-portal URL pattern and which ATS backs it (so section 2's endpoints apply).
Emphasize application-window calendars (many open July-September for the following summer, some in May).`,
  },
  {
    n: 6,
    slug: '06-quant-finance-trading',
    title: 'Quant Finance, Trading, Banking & Insurance-Tech — The Applied Math Lane',
    brief: `This is the highest-value lane for an Applied Math student and it runs on its OWN calendar (often
12-18 months ahead) and its OWN boards. Cover exhaustively:
Prop trading / market making: Jane Street (incl. INSIGHT, AMP, Immersion, FTTP), Citadel + Citadel Securities
(Discover, Datathon, Invest in Her, Associate programs), Two Sigma, Hudson River Trading, Jump Trading,
Optiver, IMC, DRW, SIG (Discovery Day), Akuna, Old Mission, Belvedere, Five Rings, Radix, Quantlab, XTX Markets,
G-Research, Man Group/AHL, Squarepoint, Tower Research, Headlands, Wolverine, PEAK6, CTC (Chicago Trading),
Group One, Transmarket, Vatic, Virtu, Flow Traders, Maven, Qube, Marshall Wace, Millennium, Point72 (Academy,
Proving Ground), Balyasny, Schonfeld, ExodusPoint, Verition, Walleye, Bridgewater, AQR, DE Shaw, Renaissance,
Voleon, Cubist, WorldQuant (+ WorldQuant Brain/IQC), Arrowstreet, Acadian, Numerai, QuantConnect.
Banks & their tech divisions: Goldman Sachs (Engineering, Possibilities/Insight/Undergrad Camp), JPMorgan Chase
(SEP, Code for Good, Advancing Black Pathways, Winning Women), Morgan Stanley (Technology Analyst, Freshman
Enhancement), Citi, BofA/Merrill, Wells Fargo, Barclays, UBS, Credit Suisse legacy, Deutsche Bank, HSBC, BNP,
SocGen, RBC, TD, BMO, Scotiabank, CIBC, Nomura, Mizuho, MUFG, Jefferies, Evercore, Lazard, Houlihan.
Asset managers / exchanges / market infra: BlackRock, Vanguard, Fidelity, State Street, BNY, Northern Trust,
T. Rowe, Invesco, PIMCO, Wellington, Capital Group, Schwab, Nasdaq, NYSE/ICE, CME, Cboe, DTCC, MSCI, S&P Global,
Moody's, FactSet, Bloomberg (+ Bloomberg internships), Refinitiv/LSEG, Morningstar, SIFMA.
Fintech/crypto: Stripe, Plaid, Block/Square, PayPal, Adyen, Marqeta, Affirm, Chime, SoFi, Robinhood, Coinbase,
Kraken, Gemini, Circle, Ripple, Anchorage, Fireblocks, Jane-adjacent crypto MMs (GSR, Wintermute, Amber, B2C2).
Insurance/actuarial-tech: Progressive, GEICO, State Farm, Allstate, Liberty Mutual, Travelers, Nationwide,
USAA, MetLife, Prudential, AIG, Chubb, Munich Re, Swiss Re, Verisk, Guidewire, Lemonade, Root, Hippo.
Also the domain-specific boards: eFinancialCareers, Wall Street Oasis, QuantNet, Quantitative-finance job
boards, Firsthand/Vault, Selby Jennings, Oxbridge-style recruiters, and the "quant internship tracker"
GitHub repos and Discord servers. Flag the extremely early deadlines and the OA-first funnels.`,
  },
  {
    n: 7,
    slug: '07-nontech-fortune-a',
    title: 'Tech Roles at Non-Tech Companies — Part A: Retail, CPG, Healthcare, Pharma, Hospitality, Food, Agriculture',
    brief: `Non-tech companies hire enormous numbers of SWE/data/analytics/ML interns and face far less applicant
competition. Enumerate companies AND their specific tech-internship program names and careers portals.
Retail/e-commerce: Walmart (Global Tech, plus Walmart Labs), Target (Technology Internship), Costco, Kroger
(84.51 — a major data-science shop), Albertsons, Publix, Ahold Delhaize, Home Depot, Lowe's, Best Buy, Dollar
General, Dollar Tree, TJX, Ross, Nordstrom, Macy's, Kohl's, Gap, Lululemon, Nike, Adidas, Under Armour, VF,
Levi's, Ulta, Sephora/LVMH, Estee Lauder, Bath & Body Works, Victoria's Secret, Dick's, Academy, Tractor Supply,
AutoZone, O'Reilly, Advance Auto, CarMax, Carvana, Wayfair, Chewy, Overstock, QVC/Qurate, Williams-Sonoma, IKEA.
CPG/food/bev: P&G, Unilever, Nestle, PepsiCo, Coca-Cola, Keurig Dr Pepper, General Mills, Kellanova, Post,
Mondelez, Kraft Heinz, Conagra, Campbell's, Hormel, Tyson, JBS, Smithfield, Land O'Lakes, Dairy Farmers,
Colgate, Kimberly-Clark, Clorox, Church & Dwight, SC Johnson, Reckitt, Henkel, L'Oreal, Coty, Hershey, Mars,
Ferrero, Danone, AB InBev, Molson Coors, Constellation Brands, Diageo, Brown-Forman, Bacardi.
Restaurants/hospitality/travel: McDonald's, Starbucks, Chipotle, Yum Brands, Restaurant Brands, Wendy's,
Domino's, Papa John's, Darden, Bloomin', Texas Roadhouse, Cracker Barrel, Panera/JAB, Subway, Chick-fil-A,
Marriott, Hilton, Hyatt, IHG, Wyndham, Choice, MGM, Caesars, Wynn, Las Vegas Sands, Disney (Parks tech +
Disney Technology Internships + Disney College Program), Universal/Comcast, Royal Caribbean, Carnival,
Norwegian, Booking, Expedia, Airbnb, Vrbo, Sabre, Amadeus, Travelport.
Healthcare systems/payers/providers: UnitedHealth/Optum, Elevance, Cigna/Evernorth, CVS/Aetna, Humana, Centene,
Molina, Kaiser Permanente, HCA, Tenet, Ascension, CommonSpirit, Providence, Advocate Health, Mayo Clinic,
Cleveland Clinic, Johns Hopkins Medicine, Mass General Brigham, NewYork-Presbyterian, Northwell, Intermountain,
Geisinger, Sutter, Baylor Scott & White, UPMC. Health IT: Epic Systems (huge intern class), Oracle Health/Cerner,
Athenahealth, Veeva, IQVIA, Change/Optum Insight, Waystar, R1, Doximity, Teladoc, Included Health, Komodo,
Tempus, Flatiron Health, Verily, Color, 23andMe, Invitae, Natera, Guardant, Illumina, PacBio, 10x Genomics.
Pharma/biotech: Pfizer, Merck, BMS, Lilly, AbbVie, Amgen, Gilead, Biogen, Regeneron, Vertex, Moderna, BioNTech,
Novartis, Roche/Genentech, AstraZeneca, GSK, Sanofi, Bayer, Takeda, Novo Nordisk, Boehringer, Teva, Viatris,
Zoetis, Thermo Fisher, Danaher, Agilent, Waters, Bruker, Bio-Rad, Charles River, Labcorp, Quest.
Agriculture: John Deere, AGCO, CNH, Cargill, ADM, Bunge, Corteva, FMC, Nutrien, Mosaic, Scotts, Syngenta.
For each cluster note the ATS in use and the program name to search for.`,
  },
  {
    n: 8,
    slug: '08-nontech-fortune-b',
    title: 'Tech Roles at Non-Tech Companies — Part B: Aerospace, Defense, Industrial, Auto, Energy, Telecom, Media, Logistics, Consulting, Real Estate',
    brief: `Second half of the non-tech Fortune-1000 sweep. Same treatment: company, tech-internship program name,
careers portal, ATS, and any clearance/citizenship constraint.
Aerospace/defense primes & mid-tier: Boeing, Lockheed Martin, Northrop Grumman, RTX (Raytheon, Collins, P&W),
General Dynamics (+ GDIT, GDMS), L3Harris, BAE Systems, Leidos, SAIC, Booz Allen, CACI, ManTech, Peraton,
Parsons, Jacobs, Amentum, KBR, Huntington Ingalls, Textron, Spirit Aero, Honeywell Aerospace, GE Aerospace,
Safran, Airbus, Rolls-Royce, MITRE, Aerospace Corporation, Draper, JHU APL, IDA, RAND, Anduril, Palantir,
SpaceX, Blue Origin, Rocket Lab, Firefly, Relativity, Astra, Sierra Space, Axiom, Vast, Varda, Shield AI,
Skydio, Applied Intuition, Saronic, Castelion, Hadrian, Epirus, Anduril-adjacent defense tech.
Automotive/mobility: GM (+ Cruise), Ford, Stellantis, Tesla, Rivian, Lucid, Toyota (+ TRI), Honda, Nissan,
Hyundai/Kia, Subaru, Mazda, BMW, Mercedes (MBRDNA), VW/Audi/Porsche (+ CARIAD), Volvo, Polestar, Bosch, Denso,
Continental, ZF, Aptiv, Magna, Lear, BorgWarner, Valeo, Mobileye, Waymo, Zoox, Nuro, Aurora, Kodiak, Motional,
Luminar, Ouster, Innoviz, Harman, Garmin, Cummins, Paccar, Navistar, Caterpillar, Deere-mobility.
Industrial/manufacturing/materials: GE (Vernova/Aerospace/Healthcare + Edison Engineering), Siemens,
ABB, Schneider, Emerson, Eaton, Rockwell Automation, Honeywell, 3M, Illinois Tool Works, Parker Hannifin,
Dover, Fortive, Ametek, Roper, Xylem, Trane, Carrier, Johnson Controls, Otis, Whirlpool, Stanley Black & Decker,
Corning, DuPont, Dow, LyondellBasell, PPG, Sherwin-Williams, Ecolab, Air Products, Linde, Alcoa, Nucor,
Steel Dynamics, Freeport, Newmont, Vulcan, Martin Marietta.
Energy/utilities: ExxonMobil, Chevron, ConocoPhillips, Shell, BP, TotalEnergies, Equinor, Oxy, EOG, Devon,
Pioneer, Hess, Marathon, Phillips 66, Valero, SLB, Halliburton, Baker Hughes, NOV, NextEra, Duke, Southern,
Dominion, Exelon, Constellation, AEP, PG&E, Edison International, Xcel, WEC, DTE, Entergy, FirstEnergy, PPL,
Sempra, National Grid, Vistra, NRG, TVA, Berkshire Hathaway Energy, First Solar, Enphase, SolarEdge, Sunrun,
Form Energy, Commonwealth Fusion, Helion, TerraPower, X-energy, Oklo, NuScale.
Telecom/media/entertainment: AT&T, Verizon, T-Mobile, Comcast/NBCU, Charter, Cox, Altice, Dish/EchoStar,
Lumen, Frontier, Crown Castle, American Tower, Disney, Warner Bros Discovery, Paramount, Sony (+ PlayStation),
Fox, AMC Networks, Live Nation, Endeavor, Nielsen, Gannett, NYT, News Corp, Bloomberg Media, Thomson Reuters,
RELX, Wiley, Pearson, McGraw Hill, Cengage, Scholastic.
Logistics/transport: UPS, FedEx, XPO, GXO, RXO, CH Robinson, JB Hunt, Schneider National, Werner, Knight-Swift,
Old Dominion, Saia, Union Pacific, CSX, Norfolk Southern, BNSF, Canadian National, CP, Delta, United, American,
Southwest, Alaska, JetBlue, Spirit, Frontier, Maersk, MSC, Flexport, Convoy-legacy, Uber Freight, Ryder, Penske.
Consulting/prof services/staffing: McKinsey (+ QuantumBlack), BCG (+ BCG X/Gamma), Bain (+ Vector), Deloitte,
PwC, EY, KPMG, Accenture (+ Federal, Song), Capgemini, Infosys, TCS, Wipro, Cognizant, HCLTech, Tech Mahindra,
LTIMindtree, Genpact, Slalom, ThoughtWorks, EPAM, Globant, Grid Dynamics, Publicis Sapient, ZS Associates,
Analysis Group, Cornerstone Research, Charles River Associates, NERA, Exponent, Gartner, Forrester, IDC,
Robert Half, Insight Global, TEKsystems, Aerotek, Kforce, Randstad, Adecco, ManpowerGroup, Apex Systems.
Real estate/construction/proptech: CBRE, JLL, Cushman, Colliers, Prologis, Simon, Brookfield, Blackstone,
Bechtel, Fluor, AECOM, Turner, Skanska, Kiewit, Whiting-Turner, Procore, Autodesk, Trimble, Bentley Systems,
Zillow, Redfin, CoStar, VTS, Matterport.`,
  },
  {
    n: 9,
    slug: '09-government-labs',
    title: 'Government, National Labs, Public Sector & Cleared Defense Research',
    brief: `Massive, underapplied-to, and on completely separate portals with early deadlines. Cover:
Federal hiring infrastructure: USAJOBS (+ its public API), Pathways Internship Program, Presidential Management
Fellows (grad but note it), Virtual Student Federal Service (VSFS), Federal Work Study, Direct Hire Authority
for STEM, Schedule A, Workforce Recruitment Program (WRP) for students with disabilities.
Intelligence/defense: NSA (Stokes/CSSP, Graduate/Undergraduate Training, Co-op), CIA (undergrad internships,
Directorate of Digital Innovation), FBI (Honors Internship, Collegiate Hiring Initiative), DIA, NRO, NGA,
ODNI, DoD SMART Scholarship, SEAP/CQL/NREIP (Navy), Army Educational Outreach Program (AEOP), AFRL Scholars,
ARL, NRL, AFRL, DEVCOM, NSWC/NUWC, SPAWAR/NIWC, MDA, DTRA, DARPA (+ its student programs), IARPA, Cyber Corps
Scholarship for Service (SFS), DoD Cyber Scholarship, CyberCorps, National Security Innovation Network (NSIN),
Defense Innovation Unit (DIU).
DOE national labs (each with its own portal + SULI/CCI/Omni Technology Alliance/MSIPP): Sandia, Los Alamos,
Lawrence Livermore, Lawrence Berkeley, Oak Ridge, Argonne, Brookhaven, Pacific Northwest, Idaho, Fermilab,
SLAC, Jefferson Lab, Ames, NREL, Princeton Plasma Physics, Savannah River, NETL, plus DOE SULI/CCI/VFP and
the Omni/WDTS umbrella application.
Civilian agencies: NASA (OSTEM/Pathways/Gateway, JPL, Ames, Goddard, JSC, Langley, Glenn, Marshall, KSC),
NIST (SURF), NIH (SIP/Summer Internship Program), CDC, FDA, NOAA (Hollings, EPP), EPA, USGS, USDA, Census
Bureau, BLS, BEA, GAO, CBO, CRS, Library of Congress, Smithsonian, NSF, DOT/FAA/FHWA, DHS (+ CISA, HS-STEM
Summer Internship), TSA, Secret Service, IRS, Treasury, SEC, CFTC, FDIC, OCC, CFPB, FTC, FCC, Federal Reserve
Board + all 12 regional Reserve Banks (their quantitative/RA internships are a top Applied Math target),
State Department (+ Foreign Service Internship), USAID, Peace Corps.
FFRDCs/nonprofit research: MITRE, Aerospace Corp, IDA, RAND, CNA, SEI/CMU, Lincoln Laboratory (MIT LL),
APL (JHU), Draper, Riverside Research, Noblis, Battelle.
State/local/civic tech: state government job portals (note the pattern, name the big ones: CA, NY, TX, WA, MA,
IL, VA, MD, GA, NC, FL, PA, OH, MI, NJ, CO, AZ), NYC Tech Talent Pipeline, Code for America Fellowship,
US Digital Response, Coding it Forward Civic Digital Fellowship (a flagship for CS students), TechCongress,
Aspen Tech Policy Hub, Beeck Center, Ash Center, National Governors Association, city CTO offices.
Explicitly flag US citizenship / clearance / green-card requirements per program, and the very early deadlines
(many close October-January).`,
  },
  {
    n: 10,
    slug: '10-research-academic-fellowships',
    title: 'Research Programs, REUs, Math-Specific Programs, AI Labs & Fellowships',
    brief: `The Applied Math half of the user's profile makes this family unusually valuable, and these are almost
never on job boards. Cover:
NSF REU: the official NSF REU site search, how to enumerate REU sites programmatically, the CS/Math/Statistics
directorates, the DMR/DMS/CISE distinctions, plus the aggregator sites (mathprograms.org, AMS listings,
Pathways to Science / IBP, ETAP - NSF's new Education & Training Application system).
Math-specific summer programs: SMALL (Williams), SPUR (MIT), Cornell SPUR, DIMACS REU, ICERM, IPAM RIPS (a
prime applied-math/industry hybrid), MSRI-UP / SLMath, PCMI, Budapest Semesters in Mathematics + BSM Applied,
Math in Moscow, EDGE, MathILy, Canada/USA Mathcamp (counselor roles), Ross, PROMYS (counselor), SUMaC,
Duluth REU, Emory REU, Indiana REU, Michigan REU, Chicago REU, Berkeley MSRI, SAMSI, SIAM Gateway,
AMS Mathematical Research Communities, INFORMS, MAA, Project NExT.
Physics/eng adjacent with heavy computation: LIGO SURF, CERN Summer Student Program (+ openlab), DESY,
Fermilab SIST/IF, SLAC, Perimeter Institute PSI Start, Institute for Advanced Study, Santa Fe Institute REU,
Simons Foundation / Flatiron Institute summer internships (CCA/CCB/CCM/CCN/CCQ — very strong applied math fit).
University-run: Caltech SURF, MIT UROP + MSRP, Stanford CURIS + SURF, Berkeley SUPERB, CMU RISS (robotics),
CMU REUSE, Georgia Tech, UT Austin, UW CSE, Michigan SURE, Illinois, Wisconsin, Harvard PRISE/BLISS,
Princeton PICSciE, Yale, Columbia, Cornell, Penn SUNFEST, JHU, Duke, Northwestern, Rice, UCLA, UCSD,
plus DAAD RISE (Germany), Mitacs Globalink (Canada), Amgen Scholars (global), Nakatani RIES (Japan),
EPFL Summer Research, ETH Zurich SRF, IST Austria ISTernship, Max Planck, INRIA, Weizmann, Technion,
NUS/NTU, Tsinghua, KAIST, University of Tokyo Amgen.
Industry research labs & AI residencies: Microsoft Research, Google Research + DeepMind (student researcher,
Student Researcher Program, AI Residency), Meta AI/FAIR (AI Residency, research intern), Anthropic (Fellows
Program, internships), OpenAI (Residency, internships, Scholars), NVIDIA Research, Adobe Research, IBM Research,
Amazon Science, Apple ML Research, Netflix Research, Spotify Research, Bell Labs (Nokia), Toyota Research
Institute, Honda Research, Bosch Research, Siemens Research, Mitsubishi MERL, NEC Labs, Samsung Research,
Huawei Noah's Ark, Salesforce Research, Snap Research, ByteDance/TikTok, Allen Institute for AI (AI2),
Vector Institute, Mila, Amii, Alan Turing Institute, EPFL/ETH labs, Redwood Research, MATS (ML Alignment
Theory Scholars), ARENA, SERI MATS, Constellation, FAR AI, Apollo Research, Astera, Arc Institute, Chan
Zuckerberg Biohub, Broad Institute, Whitehead.
Fellowships / talent programs (money + network + often a job): Kleiner Perkins Fellows, Neo Scholars,
Contrary Talent, Z Fellows, Thiel Fellowship, Emergent Ventures, Interact Fellowship, Rise (Schmidt Futures),
Schmidt Science Fellows, Knight-Hennessy (grad), Marshall/Rhodes/Gates (note), Goldwater Scholarship,
Hertz, NSF GRFP (grad), DOE CSGF, Churchill, Fulbright, Boren, Critical Language, Coca-Cola Scholars,
Jack Kent Cooke, Davidson, Regeneron STS/ISEF pipelines, 8VC Fellows, General Catalyst Fellows,
Sequoia Rising, a16z Talent x Opportunity, Soma Fellows, On Deck, Pioneer, 1517 Fund Medici,
Kernel/Anthropic-style research fellowships, Google Summer of Code, MLH Fellowship, Outreachy,
Linux Foundation Mentorship, Season of Docs, Open Summer of Code, Julia Season of Contributions.`,
  },
  {
    n: 11,
    slug: '11-affinity-communities-social',
    title: 'Affinity & Diversity Pipelines, Communities, Discord/Slack/Reddit & Newsletters',
    brief: `Hidden-market channels: postings shared in communities that never hit a board, plus affinity programs
that are effectively a parallel (and less competitive) recruiting funnel.
Affinity/diversity pipelines — be exhaustive and note eligibility: ColorStack, Rewriting the Code (RTC),
CodePath, Management Leadership for Tomorrow (MLT), SEO Career/SEO Tech Developer, INROADS, Sponsors for
Educational Opportunity, Jopwell, Blavity/AfroTech, Blacks in Technology, NSBE (+ NSBE convention career fair),
SHPE, Latinas in Tech, Techqueria, SACNAS, AISES, Out in Tech, Lesbians Who Tech, oSTEM, Women in Tech,
AnitaB/Grace Hopper Celebration, Society of Women Engineers (SWE), Girls Who Code (+ College Loop), Break
Through Tech (NY/Chicago/DC/LA), Hidden Genius Project, /dev/color, Code2040, Genesys Works, Year Up,
Per Scholas, NPower, Emma Bowen Foundation, HACU, Thurgood Marshall College Fund, UNCF, APIA Scholars,
Bottom Line, America Needs You, Braven, Basta, COOP Careers, Ascend, Prospanica, ALPFA, NABA, NAAAP,
National Society of Black Physicists, Mathematically Gifted & Black, Lathisms, Spectra (LGBTQ+ mathematicians),
Association for Women in Mathematics (AWM), plus Tapia Conference, Richard Tapia, CMD-IT, ACM SIGACCESS.
Also veterans/disability/first-gen/rural pipelines: VetsinTech, Hiring Our Heroes, Lime Connect, Disability:IN
NextGen, Neurodiversity hiring programs (SAP Autism at Work, Microsoft Neurodiversity, JPMC Autism at Work),
First Gen Forward, Rural CS.
Communities & real-time feeds: r/csMajors, r/cscareerquestions, r/internships, r/EngineeringStudents,
r/datascience, r/MachineLearning, r/quant, r/financialcareers, r/forhire, r/jobbit, r/remotejs, r/hiring;
Discord servers (CS Careers, ColorStack, Rewriting the Code, Blind-adjacent, quant Discords, Ouckah/Simplify
notification servers, Jobright, university CS servers) — and crucially HOW to ingest them (Discord bot with
a self-created webhook relay, RSS-to-Discord bridges, public server invite discovery, the legal/ToS posture);
Slack communities (Rands Leadership, Locally Optimistic, Data Angels, MLOps.community, DataTalks.Club,
Techqueria Slack, Out in Tech Slack, Elpha, Hampton, Lunchclub); Telegram/WhatsApp job channels;
X/Twitter (accounts and search queries that surface postings, "who's hiring" threads, the API cost reality);
LinkedIn (hashtags, "hiring" post filters, alumni search, recruiter posts); Blind; Levels.fyi; Hacker News.
Newsletters & RSS: TLDR (+ TLDR jobs), Hacker Newsletter, Pointer, Bytes, JavaScript Weekly-family,
Data Elixir, The Batch (DeepLearning.AI), Import AI, The Sequence, Ben's Bites, TLDR AI, Lenny's Newsletter
job board, Sourcegraph/Console, Refactoring, The Pragmatic Engineer job board (strong), StaffEng,
80,000 Hours job board (+ their newsletter), EA Forum, Future Forum, Interview Query, Big Tech Interviews,
Levels.fyi newsletter, Layoffs.fyi, Techmeme, Product Hunt daily, Indie Hackers.
For each community, give the practical ingestion path and the ToS/rate-limit reality.`,
  },
  {
    n: 12,
    slug: '12-parttime-freelance-remote',
    title: 'Part-Time, Freelance, Contract, Gig, AI-Data Work & Remote-First Boards',
    brief: `The user explicitly wants part-time roles, so treat this as a first-class family, not an afterthought.
Remote-first boards: RemoteOK (+ its free JSON API), We Work Remotely, Remote.co, Remotive (+ API), Himalayas
(+ API), Working Nomads, JustRemote, Jobspresso, NoDesk, Remote Leaf, DailyRemote, Pangian, Remote Woman,
FlexJobs (paid), Virtual Vocations, SkipTheDrive, EuropeRemotely, Remote Index, Wellfound remote filter,
4dayweek.io, Otta/WTTJ remote, Dynamite Jobs, RemoteHub, Remote Rocketship, Remoote.
Freelance/contract marketplaces: Upwork, Fiverr, Freelancer.com, PeoplePerHour, Guru, Toptal, Gun.io, Arc.dev,
Braintrust, Contra, A.Team, Turing, Andela, Lemon.io, CloudDevs, X-Team, Codementor, Codeable, YouTeam,
Gigster, Crossover, DevProjects, Bounties (Gitcoin, Algora, Replit Bounties, Bountysource legacy),
OpenAI/Anthropic-adjacent contractor pools.
AI data / RLHF / expert-annotation work — currently the single best-paying flexible part-time lane for a
strong CS/math student: Scale AI + Outlier + Remotasks, Surge AI, Mercor, Micro1, DataAnnotation.tech,
Handshake AI, Invisible Technologies, Turing (expert AI training), Snorkel, Labelbox, Appen, TELUS
International AI, RWS/TrainAI, Prolific, Alignerr, Sigma AI, CloudFactory, iMerit, Toloka, Clickworker,
Lionbridge legacy, Pareto.ai, Soul AI, Ontology/Perceptive-style math-expert programs, and the specific
"math/competition-level reasoning" contractor tracks that pay premium rates for Applied Math skill.
Tutoring/teaching: Wyzant, Chegg Tutors legacy, Varsity Tutors, Preply, Cambly, Course Hero/Studypool,
Numerade, Photomath/Google expert tracks, Art of Problem Solving (AoPS graders/instructors), Mathnasium,
Kumon, Juni Learning, CodeWizardsHQ, Create & Learn, iD Tech, campus tutoring centers, TA/grader/LA positions,
Khan Academy content contractors, Brilliant, Outschool.
Campus & flexible: on-campus research assistantships, IT help desk, library systems, campus data/analytics
offices, department web dev, campus makerspace, student consulting groups, Fed Reserve RA part-time,
Riipen/Parker Dewey micro-projects, Handshake part-time filter, university-affiliated startup incubators.
Open source as a paid path: GSoC, MLH Fellowship, Outreachy, LFX Mentorship, Julia/NumFOCUS Small Development
Grants, Django/Python Software Foundation grants, Open Collective bounties, GitHub Sponsors, Algora bounties.
Include per-source: pay range reality, hours flexibility, whether it counts as "experience" on a resume,
tax/1099 posture, and whether it's compatible with F-1/international status (flag this as a caveat to verify).`,
  },
  {
    n: 13,
    slug: '13-international-and-university-systems',
    title: 'International Markets, Global Programs & University Co-op / Career-Services Systems',
    brief: `Two related families: geography-specific boards, and the school-mediated channels that are invisible externally.
Canada: WaterlooWorks (the co-op gold standard), Magnet, TalentEgg, Job Bank Canada, Indeed.ca, Riipen,
Mitacs (Globalink + Accelerate), NSERC USRA, Canadian federal FSWEP + Co-op, Shopify Dev Degree, RBC Amplify,
TD Invent, CIBC, Manulife, Sun Life, OMERS, CPPIB, Ontario Teachers', Wealthsimple, Coveo, Kinaxis, OpenText,
Telus, Bell, Rogers, Hydro-Quebec, Ubisoft Montreal, Element AI legacy, Cohere, Mila-affiliated startups.
UK/Ireland: Bright Network, Milkround, Gradcracker, RateMyPlacement, Prospects, TargetJobs, Save the Student,
GradTouch, Inside Careers, e4s, Student Ladder, Talentpool, Debut/Amazing, plus the UK spring-week /
insight-week / placement-year system (a genuine 12-month industrial placement channel), STEP, Nuffield,
The Year in Industry, UKESF, Turing Institute, GCHQ/MI5/MI6 student schemes, Dyson, ARM, DeepMind London,
Jane Street/Optiver/IMC/Citadel London desks, Bloomberg London, and the Oxbridge/Imperial/UCL careers portals.
EU: EURES, Graduateland, JobTeaser, Welcome to the Jungle (FR), StepStone, Xing, Indeed regional, Honeypot,
Landing.jobs, Talent.io, Jobbatical, IAESTE, AIESEC, Erasmus+ traineeships, ESA (Young Graduate Trainee +
student internships), CERN, DAAD RISE, Google Zurich/Munich, Amazon Berlin, Booking Amsterdam, Adyen, Spotify
Stockholm, Klarna, Revolut, N26, Zalando, Delivery Hero, SAP, Siemens, Bosch, Zeiss, Trivago, Personio,
Celonis, DeepL, Aleph Alpha, Mistral (Paris), Hugging Face, Criteo, Datadog Paris, Doctolib, Qonto.
Asia-Pacific: Wantedly + Japan Dev + TokyoDev + Rakuten/Mercari/LINE/PayPay (Japan), Saramin/JobKorea/Wanted
(Korea), 51job/BOSS Zhipin/Lagou/Nowcoder/Niuke (China), NodeFlair + Glints + JobStreet/JobsDB + Tech in Asia
(SEA), Naukri/Internshala/Unstop/HackerEarth/Cutshort/AngelList India/LinkedIn India (India), SEEK +
GradConnection + Prosple + GradAustralia (AU/NZ), Grab/Sea/GoTo/Shopee regional programs, Singapore's
SGUnited/MyCareersFuture, Hong Kong JobsDB.
LatAm/other: Get on Board, Torre, Computrabajo, Bumeran, Nubank/Rappi/Mercado Libre/dLocal/Globant programs,
Terminal, Ubiminds; Africa: Andela, Gebeya, Jobberman, Talent.com regional; Middle East: Bayt, GulfTalent,
Wuzzuf, Careem/Talabat/Noon.
Visa/eligibility reality layer: what "internship" means for F-1 CPT/OPT, J-1 for international programs,
IAESTE/AIESEC as the visa-solving path, UK Student Visa work rights, Canadian co-op work permit, Schengen
internship agreements, and which US programs bar non-citizens (cross-reference the government section).
University-mediated channels: Handshake/Symplicity/12Twenty/Simplicity per-school portals, co-op programs with
their own employer networks (Northeastern NUworks, Waterloo, Drexel, Cincinnati, Purdue, Georgia Tech, RIT,
Kettering, Michigan State, Northeastern), department mailing lists and Slack/Discord, faculty research pages
and direct-professor cold email, campus career fairs (fall + spring calendars), industry-affiliate programs
(Stanford SAIL affiliates, MIT ILP, Berkeley RISE/Sky, CMU corporate partners), alumni databases,
student org corporate sponsors (ACM, IEEE, SIAM chapters, hackathon sponsor lists), and honors/scholar programs.
Hackathon-to-job pipelines: MLH, Devpost, Major hackathons (HackMIT, PennApps, TreeHacks, HackTX, Hack the North,
CalHacks, LA Hacks, HackNY), Kaggle competitions, ICPC, Putnam, MCM/COMAP, HackerRank/Codeforces/CodeChef/
AtCoder/TopCoder contests used as recruiting funnels, Meta Hacker Cup, CTFs (picoCTF, DEFCON quals),
Citadel/Jane Street/Two Sigma datathons, Bloomberg/Optiver trading competitions.`,
  },
  {
    n: 14,
    slug: '14-signals-outreach-infrastructure',
    title: 'Alternative Discovery Signals, Cold-Outreach Infrastructure & Meta-Sources',
    brief: `Sources that are not job boards but reliably reveal hiring before or instead of a posting, plus the
infrastructure to act on them, plus meta-sources that generate MORE sources over time.
Funding/growth signals: Crunchbase (+ API), PitchBook, Tracxn, Dealroom, CB Insights, TechCrunch/Axios Pro
Rata/Term Sheet/StrictlyVC funding feeds, Wellfound "recently funded", SEC EDGAR Form D full-text search,
state incorporation records, YC batch announcements, Product Hunt launches, GitHub org star velocity,
Hacker News Show HN, App Store/Play Store new releases, job-count deltas on a company's own board
(a rising Greenhouse posting count is a hiring-surge signal).
Employment-data signals: H-1B LCA disclosure data (DOL OFLC, h1bdata.info, myvisajobs) — reveals which
employers sponsor and at what salary; PERM data; OPT/CPT employer lists; SEVIS-certified employer patterns;
Levels.fyi salary data; Blind company boards; Glassdoor/Indeed company review pages; layoffs.fyi as an
inverse/timing signal; WARN Act notices; LinkedIn headcount growth; SimilarWeb/Sensor Tower growth.
Tech-stack targeting (find companies using what you know): BuiltWith, Wappalyzer, PublicWWW (source-code
search across the web — extremely good for finding every site embedding a given ATS), Shodan, Censys,
SecurityTrails, Common Crawl index queries, GitHub code search, StackShare, TheirStack, Datanyze, HG Insights.
Contact discovery & outreach infra: LinkedIn (alumni filter, 2nd-degree, Sales Navigator), Hunter.io,
RocketReach, Apollo.io, Clay, Lusha, ContactOut, Snov.io, Findymail, email-pattern inference, university
alumni directories, professor/lab pages, GitHub commit emails (note ToS/ethics), conference attendee lists,
Twitter/X DMs, warm-intro paths. Include the practical outreach stack: sequencing tool, tracking, follow-up
cadence, and the ethical/CAN-SPAM/ToS boundaries — be honest about what is and is not acceptable.
Meta-sources (sources that spawn sources): GitHub "awesome-*" list ecosystem + GitHub topic and code search
queries that surface new internship repos each season; Reddit wikis (r/csMajors, r/cscareerquestions,
r/internships wikis); Notion/Airtable public trackers students publish each cycle; Google Sheets crowd-sourced
trackers; university career-center resource pages (they publish curated source lists — mine several);
"list of job boards" directories (JobBoardSearch.com, Job Board Finder, Recruiterly, Niche Job Boards lists);
Wikipedia lists of largest employers by state/industry (a systematic way to enumerate non-tech employers);
Fortune 500 / Forbes / Inc 5000 / Deloitte Fast 500 / Built In Best Places lists; Glassdoor Best Places;
LinkedIn Top Startups; industry association member directories (SIAM, INFORMS, ACM, IEEE, AIAA, ASME,
BIO, PhRMA, AHIP, ABA) — each publishes a member list that becomes a company-target list.
Also: how to build a self-expanding source registry — dedupe keys, canonical company IDs, ATS-slug resolution,
crawl scheduling by source volatility, and a "new source detected" review queue. Give concrete design advice.`,
  },
]

const SUMMARY_SCHEMA = {
  type: 'object',
  properties: {
    section: { type: 'string' },
    source_count: { type: 'integer', description: 'Approximate number of distinct sources documented' },
    t1_highlights: {
      type: 'array', items: { type: 'string' },
      description: 'Up to 8 Tier-1 sources you documented, name only',
    },
    gaps_noticed: {
      type: 'array', items: { type: 'string' },
      description: 'Source families you saw hints of but that fall outside your assigned scope',
    },
  },
  required: ['section', 'source_count', 't1_highlights', 'gaps_noticed'],
}

phase('Domain sweep')

const TODO = SECTIONS.filter(s => !DONE.has(s.slug))
log(`Resuming: ${DONE.size} sections already on disk, dispatching ${TODO.length} researchers`)

const results = await parallel(TODO.map(s => () =>
  agent(
    `${PREAMBLE}

YOUR ASSIGNED SECTION: **${s.n}. ${s.title}**

Scope brief (a floor, not a ceiling — add everything else you know that belongs here):
${s.brief}

Write your finished section to the file: ${DIR}/${s.slug}.md
Use the Write tool. The file must contain ONLY the markdown section (starting with "## ${s.n}. ${s.title}").
Aim for genuine exhaustiveness — a section with 80-150+ distinct rows is expected for most of these domains.
Then return the structured summary.`,
    { label: `research:${s.slug}`, phase: 'Domain sweep', schema: SUMMARY_SCHEMA }
  )
))

const ok = results.filter(Boolean)
log(`Sweep complete: ${ok.length}/${TODO.length} new sections (${DONE.size + ok.length}/${SECTIONS.length} total), ~${ok.reduce((a, r) => a + (r.source_count || 0), 0)} sources documented this run`)

phase('Completeness critic')

const CRITIC_SCHEMA = {
  type: 'object',
  properties: {
    missing_families: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          why_it_matters: { type: 'string' },
          examples: { type: 'array', items: { type: 'string' } },
        },
        required: ['name', 'why_it_matters', 'examples'],
      },
      description: 'Whole source families absent from all 14 sections',
    },
    thin_sections: { type: 'array', items: { type: 'string' }, description: 'Sections that under-delivered, with what is missing' },
    suspect_entries: { type: 'array', items: { type: 'string' }, description: 'Entries likely wrong, dead, or hallucinated that need verification' },
  },
  required: ['missing_families', 'thin_sections', 'suspect_entries'],
}

const critique = await agent(
  `You are the completeness critic for an exhaustive job-source catalog built for a CS + Applied Math undergraduate
who wants to capture EVERY online internship / co-op / part-time / startup role, including tech roles at non-tech companies.

Read EVERY .md file in ${DIR}/ (list the directory first, then read each file in full).

Then answer, adversarially and specifically:
1. What ENTIRE source families are missing from all 14 sections? Think hard about blind spots:
   nonprofits/NGOs, education/edtech, sports analytics, gaming/esports, music/audio tech, legal tech, gov-adjacent
   contractors, trade/vocational, franchising, small-and-medium local businesses, family offices, hedge-fund
   seeding, university spinouts, patent/IP, standards bodies, open-source foundations, religious/community orgs,
   co-ops and worker collectives, unions, political campaigns, think tanks, journalism/data-journalism,
   space/ocean/polar research, climate/energy nonprofits, effective-altruism orgs, biosecurity, deeptech,
   semiconductors fabs, hardware startups, robotics, agtech, foodtech, proptech, insurtech, regtech, adtech,
   martech, HR tech, dev tools, cybersecurity, and anything else you can think of.
   Also consider NON-COMPANY sources: professional society job boards, conference recruiting, journal/preprint
   author outreach, competition sponsors, alumni newsletters, and pure-technique sources (Google dorks / search
   operator recipes that surface postings directly, RSS discovery, sitemap crawling, structured-data harvesting).
2. Which of the 14 sections is thin relative to its mandate, and exactly what is missing from it?
3. Which specific documented entries look wrong, dead, renamed, or hallucinated and need verification?

Be harsh. The user asked for EXHAUSTIVE. Do not congratulate. Return only the structured critique.`,
  { label: 'critic:completeness', phase: 'Completeness critic', schema: CRITIC_SCHEMA, effort: 'high' }
)

log(`Critic found ${critique?.missing_families?.length || 0} missing families, ${critique?.thin_sections?.length || 0} thin sections`)

phase('Gap fill')

const gapList = (critique?.missing_families || []).map(f => `- **${f.name}** — ${f.why_it_matters}. Seeds: ${(f.examples || []).join(', ')}`).join('\n')
const thinList = (critique?.thin_sections || []).join('\n- ')
const suspectList = (critique?.suspect_entries || []).join('\n- ')

const gapAgents = [
  {
    slug: '15-gap-industry-verticals',
    title: 'Vertical & Niche Industry Source Families',
    task: `Write section 15: every INDUSTRY VERTICAL / niche job-source family the critic flagged as missing, plus any
you can think of yourself. Focus on verticals with their own dedicated job boards and their own hiring norms:
cybersecurity, gaming/esports, robotics, semiconductors/hardware, climate/energy, space/ocean/polar, biotech/
biosecurity, agtech/foodtech, legal tech, regtech/compliance, insurtech, proptech, adtech/martech, HR tech,
dev tools/infra, sports analytics, music/audio/creative tech, edtech, journalism & data journalism, nonprofits/
NGOs/international development, think tanks & policy, political campaigns & civic tech, effective altruism,
open-source foundations & standards bodies, professional societies, deeptech, and any others.
For EACH vertical name its dedicated boards (e.g. Hitmarker and Work with Indies for games; ClearanceJobs and
CyberSeek for security; ClimateBase, Terra.do, Work on Climate for climate; 80,000 Hours and EA Jobs for EA;
Devex, ReliefWeb, Idealist, UN Careers for development; BioSpace, Nature Careers, Science Careers for bio;
MathJobs.org, AMS EIMS, INFORMS, SIAM, ACM, IEEE, USENIX for academic/professional societies) plus the anchor
employers and any conference-recruiting channel. Be exhaustive per vertical.`,
  },
  {
    slug: '16-gap-techniques-and-dorks',
    title: 'Direct-Discovery Techniques — Search Operators, Structured Data, Sitemaps & RSS',
    task: `Write section 16: PURE TECHNIQUE sources — ways to surface postings without any board at all.
Cover in concrete, copy-pasteable detail:
(a) Google/Bing/DuckDuckGo search-operator recipes ("dorks") that reliably surface internship postings —
    site: operators for every major ATS domain (boards.greenhouse.io, jobs.lever.co, jobs.ashbyhq.com,
    *.myworkdayjobs.com, apply.workable.com, jobs.smartrecruiters.com, *.icims.com, etc.) combined with
    intern/co-op/2026 keywords, inurl:/intitle: variants, date-restricted queries, and how to automate them
    (SerpAPI, Bing Web Search API, Brave Search API, DuckDuckGo HTML, Searx instances) plus the CAPTCHA reality.
(b) schema.org JobPosting structured-data harvesting: how Google for Jobs indexing works, what JSON-LD looks
    like on a careers page, how to extract it, and why it makes ANY compliant careers page machine-readable.
(c) sitemap.xml and robots.txt mining for careers URLs; /jobs.xml, /careers/sitemap.xml patterns; ATS sitemaps.
(d) RSS/Atom discovery: which boards still ship feeds, feed-autodiscovery link tags, RSS-Bridge, and building
    feeds from HTML with tools like Feed43/RSSHub/Huginn.
(e) Common Crawl and the CC index API for enumerating every host with a given ATS path; PublicWWW source search.
(f) Email-alert harvesting: turning LinkedIn/Indeed/Glassdoor/company job alerts into a parseable inbox pipeline
    (dedicated address, IMAP, per-sender parsers) — often the ONLY reliable path for anti-bot sites.
(g) Browser-automation posture: when Playwright/Puppeteer is needed, stealth realities, session reuse,
    residential proxies, and the legal/ToS line (hiQ v. LinkedIn context, CFAA posture) — be accurate and
    honest about what is defensible and what is not.
(h) Change-detection tooling on career pages: changedetection.io, Distill, Visualping, and diffing your own
    stored snapshots.
Give real URL templates and query strings, not vague advice.`,
  },
  {
    slug: '17-gap-remainder-and-corrections',
    title: 'Remaining Gaps, Thin-Section Supplements & Verification Notes',
    task: `Write section 17, which has three parts:
PART A — Cover every remaining missing family from the critic's list that sections 15 and 16 do not cover.
PART B — Supplement the thin sections. For each section the critic called thin, add a "Supplement to Section N"
subsection with the specific missing sources filled in (same table format).
PART C — A "Verification queue" table listing every entry the critic flagged as suspect/dead/renamed, with:
Entry | Concern | Verified status (use WebSearch to actually check the important ones) | Correct URL or replacement.
Do the verification work — do not just restate the concern.`,
  },
]

const gapResults = await parallel(gapAgents.map((g, i) => () =>
  agent(
    `${PREAMBLE}

YOUR ASSIGNED SECTION: **${15 + i}. ${g.title}**

${g.task}

CONTEXT — the completeness critic reviewed the 14 sections already written in ${DIR}/ and reported:

MISSING FAMILIES:
${gapList || '(none reported)'}

THIN SECTIONS:
- ${thinList || '(none reported)'}

SUSPECT ENTRIES NEEDING VERIFICATION:
- ${suspectList || '(none reported)'}

You may read the existing files in ${DIR}/ to avoid duplicating what is already covered.

Write your finished section to: ${DIR}/${g.slug}.md
Use the Write tool. The file must contain ONLY the markdown section (starting with "## ${15 + i}. ${g.title}").
Then return the structured summary.`,
    { label: `gapfill:${g.slug}`, phase: 'Gap fill', schema: SUMMARY_SCHEMA }
  )
))

const allOk = [...ok, ...gapResults.filter(Boolean)]
log(`Gap fill complete. Total sections: ${allOk.length}`)

return {
  sections: allOk.map(r => ({ section: r.section, count: r.source_count, t1: r.t1_highlights })),
  total_sources: allOk.reduce((a, r) => a + (r.source_count || 0), 0),
  critique,
  dir: DIR,
}
