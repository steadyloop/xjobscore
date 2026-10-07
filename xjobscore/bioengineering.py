"""Focus chips for bioengineering students: MedTech, Regulatory and BioTech.

A posting earns a chip from four kinds of evidence, read in this order:

1. Vetoes on the title.  Business, support and trade functions (sales, finance,
   IT, supply chain, facilities...) and patient-care or licensed clinical roles
   (nurse, therapist, pharmacist, medical technologist...) never earn a chip.
   A few life-science phrases that contain a veto word ("post-market",
   "drug delivery", "adverse event", "regulatory policy") are blanked first.
2. Strong title words that mean the chip anywhere ("Biomedical Engineer",
   "Regulatory Affairs", "Cell Culture", "Clinical Research Coordinator").
3. Weak title words that only count with life-science context ("Quality",
   "Microbiology", "Ultrasound", "Oncology"): a life-science employer, or a
   role-level description term.
4. The employer.  Device and pharma companies post most student roles under
   generic titles -- "Mechanical Engineering Co-op", "Quality Engineering
   Intern" -- so the registered employer segment, combined with the role's
   function, decides the chip.  Description terms stand in for an unregistered
   employer, but only role-level ones (ISO 13485, design controls, cell
   culture, cGMP, IRB) -- never industry words, which recur in boilerplate.

Measured on 2,342 hand-labeled US student postings (2026-10-06; see
registry/filters/bioengineering.md): on high-confidence labels MedTech scores
0.97 precision / 1.00 recall, Regulatory 0.97 / 0.97, BioTech 0.95 / 0.95.

The chips are stored by the Gold ``focus`` step, not computed in SQL: the rules
need ~45 regular expressions, which PostgreSQL evaluates at 10-14 us each per
row and recompiles once more than 32 are live in one query.
"""

from __future__ import annotations

import functools
import hashlib
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .job_domain import PROGRAM_NOISE_PATTERN

__all__ = [
    "BIOENGINEERING_FILTER",
    "BIOTECH_FILTER",
    "EMPLOYERS_PATH",
    "FOCUS_FILTERS",
    "FOCUS_VERSION",
    "MEDTECH_FILTER",
    "REGULATORY_FILTER",
    "SEGMENTS",
    "EmployerRegistryError",
    "FocusEvidence",
    "classify",
    "employer_segments",
    "employers",
    "focus_version",
    "normalize_title",
]

# Chip keys match the client's focus filters and the degree-field names.
MEDTECH_FILTER = "med_device"
REGULATORY_FILTER = "clinical_affairs"
BIOTECH_FILTER = "biotech"
FOCUS_FILTERS = (MEDTECH_FILTER, REGULATORY_FILTER, BIOTECH_FILTER)
# Umbrella API token kept for bookmarked requests: any of the three chips.
BIOENGINEERING_FILTER = "bioengineering"

# Bump when a rule changes.  Registry edits are folded in by `focus_version`.
FOCUS_VERSION = 1

EMPLOYERS_PATH = Path(__file__).parent / "registry" / "filters" / "bioengineering-employers.yaml"
SEGMENTS = frozenset(
    {"dev", "dx", "tools", "pharma", "agbio", "lab", "cro", "consult", "bio_institute", "aec_ls"}
)
_LS_EMPLOYER = frozenset({"dev", "dx", "tools", "pharma", "lab", "cro", "consult", "bio_institute"})
_DEVICE_EMPLOYER = frozenset({"dev", "dx"})
_BIO_EMPLOYER = frozenset({"pharma", "tools", "lab", "bio_institute", "agbio", "dx"})


def _bounded(expression: str) -> str:
    """Word-bounded alternation; [^a-z0-9] boundaries keep "R&D" and "Co-op" intact."""
    return rf"(^|[^a-z0-9])(?:{expression})([^a-z0-9]|$)"


# ---------------------------------------------------------------------------
# Title normalization and program wrappers
# ---------------------------------------------------------------------------

# Program wrappers stripped before any rule reads the title, on top of the
# season/year/program vocabulary `job_domain` already strips.
_EXTRA_NOISE = _bounded(
    r"(?:future|emerging|early) talent(?: program(?:me)?)?"
    r"|talent (?:program(?:me)?|pool|pipeline|community|network)"
    r"|campus hire program|internship program(?:me)?|co[- ]?op program"
    r"|graduate program(?:me)?|apprentice(?:ship)? program"
)
# What a title says when it names no role; a title made only of these (and the
# company's own name) is a program-wide posting.
_PROGRAM_WORDS = _bounded(
    r"intern|interns|internship|internships|co[- ]?op|co[- ]?ops|coop|coops|student|students"
    r"|undergrad|undergraduate|grad|graduate|graduates|program|programme|programs"
    r"|opportunit(?:y|ies)|general|interest|interested|posting|application|apply|pool|pipeline"
    r"|future|stem|summer|fall|winter|spring|full[- ]time|part[- ]time|multiple|positions?|roles?"
    r"|openings?|various|join|our|team|in|an|a|the|for|and|of|at|to|with|on|or|start|jan|january"
    r"|june|august|july|months?|campus|hire|hiring|candidates?|new|early|career|careers|level"
    r"|entry|i|ii|1|2|us|usa|remote|hybrid|onsite|share|your|info|information|here|we|you|now"
    r"|today|submit|resume|cv|profile|express|expression|this|is|be|will|register|registration"
)
# Program-wide postings that still carry a location or a place name.
_PROGRAM_PHRASE = _bounded(
    r"talent (?:pool|community|network|pipeline)|general (?:interest|application)"
    r"|interested in an? (?:internship|co[- ]?op)|future (?:internship )?opportunities"
    r"|co[- ]?op opportunities|internship opportunities|internships? (?:in|at)|summer internships"
    r"|co[- ]?op program|internship program|apprentice(?:ship)? program|register your interest"
)

# ---------------------------------------------------------------------------
# Vetoes
# ---------------------------------------------------------------------------

# Life-science phrases that contain a veto word; blanked before the veto runs.
_HARD_VETO_EXCEPTIONS = _bounded(
    r"post[- ]?market(?:ing)?|drug delivery|insulin delivery|adverse events?"
    r"|commercial (?:manufacturing|production|scale|msat|quality)"
    r"|(?:patient|participant|subject|clinical|trial|study) recruitment"
    r"|regulatory policy|health economics"
)
# Business, support and trade functions: out of scope at any employer.
_HARD_VETO = _bounded(
    r"sales|selling|marketing|brand|branding|advertising|commercial|go[- ]to[- ]market"
    r"|market (?:access|development|research|insights?|intelligence)|pricing|reimbursement|payer"
    r"|account (?:executive|manager|management|representative|specialist)"
    r"|customer (?:success|service|support|experience|care)"
    r"|business (?:development|analyst|analytics|intelligence|operations|strategy|systems"
    r"|technology|technologies|partner|enablement|insights)"
    r"|finance|financial|accounting|accountant|accounts (?:payable|receivable)|tax|taxation"
    r"|treasury|fp ?& ?a|controller|controllership|controllers|cfo|payroll|revenue|investor relations"
    r"|corporate (?:development|strategy)|mergers|alliance management|partnerships?"
    r"|product specialists?|procurement|sourcing"
    r"|purchasing|buyer|buying|supply chain|supply management|supplier management|clinical supply"
    r"|logistics|warehouse|warehousing|distribution|fulfillment|inventory|planning|planner"
    r"|master data|material management|vave|value engineering"
    r"|human resources|hr|people (?:operations|analytics|partner)|talent|recruit[a-z]*"
    r"|learning (?:and|&) development|employer branding|total rewards|compensation"
    r"|organizational (?:effectiveness|development)"
    r"|legal|counsel|paralegal|attorney|law|litigation|intellectual property|patents?|contracts?"
    r"|disputes|economics|economist|legislative|lobby[a-z]*"
    r"|public (?:relations|affairs)|government (?:affairs|relations)"
    r"|corporate (?:affairs|responsibility|communications|giving)|social (?:media|impact)"
    r"|copywrit[a-z]*|editorial|journalis[a-z]*|graphic design[a-z]*|video[a-z]*|photograph[a-z]*"
    r"|multimedia|events?"
    r"|administrative|administration|admin|receptionist|front desk"
    r"|office (?:manager|assistant|coordinator|administrator)|executive assistant|clerk|clerical"
    r"|data entry|secretary|mailroom"
    r"|information technology|it|help ?desk|service desk|desktop|technical support|tech support"
    r"|network (?:engineer|engineering|administrator|technician)|sysadmin|systems? administrat[a-z]*"
    r"|cloud|erp|sap|servicenow|salesforce|rpa|robotic process automation"
    r"|enterprise (?:agent|ai|applications?|platforms?|systems|architecture|data|it|technology"
    r"|technologies|software|risk)"
    r"|technology,? ai (?:and|&) data|business technology"
    r"|facilities|facility|maintenance|property services|real estate|utilities (?:technician|tech|operator)s?"
    r"|custodian|custodial|janitor[a-z]*|housekeep[a-z]*|groundskeep[a-z]*|security (?:officer|guard)"
    r"|driver|chauffeur|courier|delivery|mechanic|welder|welding|electrician|hvac|plumb[a-z]*"
    r"|carpent[a-z]*|pipefitter|fitter|machinist|millwright|painter|construction|landscap[a-z]*"
    r"|(?:mechanical|electrical|maintenance|facilities|utilities) (?:technician|tech)s?"
    r"|cook|chef|dietary|food service|barista|cashier|retail|store|merchandis[a-z]*|hospitality"
    r"|ehs|environmental,? health|health,? safety|industrial hygiene|occupational (?:health|safety)"
    r"|safety (?:and|&) environment|process safety|sustainability|global safety"
    r"|environmental (?:compliance|stewardship|engineer|engineering|science|scientist|services"
    r"|consult[a-z]*|planner|planning|policy|research)"
    r"|mba|teacher|teaching|tutor|tutoring|instructor|instructional|docent|curriculum"
    r"|camp counselor|coach|peer mentors?|mentor|lecturer|professor|faculty|adjunct|lab ta"
    r"|teaching assistant"
    r"|leasing|underwrit[a-z]*|actuar[a-z]*|claims|banking|banker|teller|wealth"
    r"|investment|private equity|venture|ventures|trading|trader"
    r"|ai training|ai trainer|data annotat[a-z]*|annotation|data label[a-z]*|rlhf"
    r"|agronom[a-z]*|seed sales|farm|ranch|veterinary health|veteran|skillbridge|military"
    r"|internal (?:controls|audit)"
)
# Functions a strong chip word or an engineering noun can override.
_SOFT_VETO = _bounded(
    r"audit|auditor|auditing|compliance|cyber ?security|security|risk|digital|communications?"
    r"|project management|program management|product management|pmo|project manager"
    r"|program manager|project coordinator|operational excellence|opex|lean|six sigma"
    r"|continuous improvement|business excellence|operations management|strategy|strategic"
    r"|analytics|analyst|data analytics|insights|training|education|scheduling"
    r"|student placement|placement|crm|technology (?:intern|internship|co[- ]?op)|diversity"
    r"|inclusion|policy|content"
)
_SOFT_RESCUE = _bounded(
    r"quality|regulatory|gmp|cgmp|gxp|glp|gcp|clinical|validation|capa|supplier quality"
    r"|product security|device|devices|medical|laboratory|safety risk|engineer|engineering"
    r"|scientist|scientific|epidemiolog[a-z]*|real[- ]world|rwe|heor|outcomes research"
    r"|biostatistic[a-z]*"
)
# Patient care and licensed clinical roles.
_CARE_VETO = _bounded(
    r"nurse|nurses|nursing|rn|lpn|lvn|cna|pct|bsn|np|patient care|caregiver[s]?|caregiving"
    r"|medical assistant|physician|physicians|physician assistant|pa-c|nurse practitioner|surgeon"
    r"|doctor|resident|residency|fellowship|medical student|med student|medical school"
    r"|pharmacist|pharmacy|pharm ?d|therapist|therapists|physiotherap[a-z]*"
    r"|(?:physical|occupational|speech|respiratory|massage|music|art|recreational|behavior(?:al)?"
    r"|aba|marriage|family|radiation) therap[a-z]*"
    r"|athletic trainer|chiropract[a-z]*|dietitian|dietetic[a-z]*|nutritionist|scribe|emt|ems"
    r"|paramedic|phlebotom[a-z]*|sonograph[a-z]*|echocardiograph[a-z]*|radiographer|radiology"
    r"|radiologic"
    r"|(?:radiolog[a-z]*|radiograph[a-z]*|mri|ct|x[- ]?ray|surgical|nuclear medicine|medical|histo"
    r"|cyto|cardiovascular|cardiac|echo|eeg|ekg|ecg|neurodiagnostic|polysomnograph[a-z]*|vascular"
    r"|sterile processing|ophthalmic|dialysis|anesthesia|monitor|orthopedic|orthopaedic"
    r"|emergency department|emergency room|operating room|cath lab|mammography|ultrasound"
    r"|imaging|pharmacy|clinical) (?:tech|techs|technician|technicians|technologist|technologists"
    r"|assistant|assistants|aide|aides)"
    r"|nuclear medicine (?:student|intern)|surgical tech[a-z]*|surg tech|sterile processing"
    r"|central sterile|histotechn[a-z]*|cytotechn[a-z]*|certified surgical"
    r"|dental (?:assistant|hygien[a-z]*)|dentist|hygienist|veterinar[a-z]*"
    r"|vet (?:tech|technician|assistant|nurse|student|receptionist|lab)|dvm|kennel|groom[a-z]*"
    r"|counselor|counseling|social work[a-z]*|psychotherap[a-z]*|psychiatr[a-z]*|psycholog[a-z]*"
    r"|behavior(?:al)? (?:health|technician|tech)|rbt|bcba|mental health|chaplain|doula"
    r"|midwi[a-z]*|optometr[a-z]*|optician|audiologist"
    r"|medical (?:office|receptionist|biller|billing|coder|coding|records|interpreter|secretary"
    r"|courier|laboratory|lab|technologist)"
    r"|health information|home health|hospice"
    r"|patient (?:access|transport|transporter|services|representative|navigator|experience"
    r"|advocate|sitter|registration)"
    r"|unit secretary|sitter|specimen (?:processor|processing|accessioner)"
    r"|rehab(?:ilitation)? (?:aide|technician|tech|assistant|counselor)"
    r"|clinical (?:rotation|rotations|placement|placements|extern|externship|nurse|therapist"
    r"|dietitian|pharmacist|psycholog[a-z]*|social worker|counselor|educator|education|liaison"
    r"|manager|supervisor|student experience|support student|intern|internship|interns|leader"
    r"|lab|laboratory)"
    r"|genetic counsel[a-z]*|speech[- ]language|language patholog[a-z]*|nurse extern"
    r"|student nurse|extern|externship|practicum|lifeguard|camp|youth|childcare|child care"
    r"|daycare|preschool|prn|stepdown|telemetry|icu|nicu|picu|med[- ]surg"
)

# Context that turns a life-science title word into another industry's.
_NON_LS_REGULATORY = _bounded(
    r"bank[a-z]*|financial|finance|capital|credit|lending|loans?|mortgage|securities|broker[a-z]*"
    r"|trading|funds?|insurance|actuar[a-z]*|tax|energy|power|utilit(?:y|ies)|electric|gas|oil"
    r"|pipelines?|telecom[a-z]*|spectrum|fcc|ferc|finra|sec|aml|kyc|bsa|rates?|tariffs?"
    r"|environmental|epa|air|water|nuclear|nrc|aviation|faa|transportation|rail[a-z]*|crypto"
    r"|blockchain|gaming|cannabis|alcohol|tobacco|chemicals?|sds|tsca|fifra|food|government"
    r"|privacy|data protection|trade|export|customs|legislative|records management"
    r"|document (?:&|and) records|technical risk|security|health services|health plan"
)
_NON_LS_BIO = _bounded(
    r"wildlife|fisheries|fish|marine|aquatic|ecolog[a-z]*|environmental|botan[a-z]*|avian|birds?"
    r"|natural resources?|forest[a-z]*|conservation|parks?|zoo|wetlands?|field biolog[a-z]*"
    r"|herpetolog[a-z]*|entomolog[a-z]*|invasive|habitat|range|watershed|stream|river"
    r"|biological (?:technician|science technician|monitor)|oil|gas|petro[a-z]*|refin[a-z]*"
    r"|brew[a-z]*|beer|wine|winery|distill[a-z]*|spirits|kombucha|dairy|mining|metallurg[a-z]*"
    r"|fire assay|battery|batteries|lithium|solar|fuel cell|cell tower|work cell|food|beverage"
    r"|soil|turf|horticultur[a-z]*|forensic|crime|work[- ]study|peer|anatomy|cadaver|physics"
    r"|stockroom|shop|intramural|athletic|teaching|tutor"
)
_SEMICONDUCTOR = _bounded(
    r"ion|wafers?|fab|semiconductors?|etch|diffusion|dopants?|thin films?|lithograph[a-z]*|cmp"
)

# ---------------------------------------------------------------------------
# Title evidence
# ---------------------------------------------------------------------------

_MED_STRONG = _bounded(
    r"bio[- ]?medical (?:engineer|engineering|engineers|equipment|technician|technicians|tech|techs"
    r"|technology|technologies|device|devices|instrumentation|electronics|systems|imaging|optics"
    r"|signals?)"
    r"|bmet|htm|healthcare technology management|clinical engineer(?:ing|s)?"
    r"|biomed (?:tech|techs|technician|technicians|engineer|engineering|equipment)"
    r"|medical (?:device|devices|equipment|technology|technologies|imaging|instrument|instruments"
    r"|instrumentation|electronics|robotics|robot|robots|product|products|physics|physicist"
    r"|physicists|software|engineering|engineer|optics|sensors?|lasers?)"
    r"|med[- ]?tech|medical[- ]devices?"
    r"|diagnostic imaging|molecular imaging|biomedical imaging|clinical imaging|cardiac imaging"
    r"|neuro[- ]?imaging|ultrasound imaging"
    r"|surgical (?:robot|robots|robotic|robotics|device|devices|instrument|instruments|innovation"
    r"|systems?|engineering|engineer|products?|technolog(?:y|ies)|navigation|visualization"
    r"|platform|stapling|energy)"
    r"|robotic(?:s)? surgery|robot(?:ic)?[- ]assisted surgery|surgery (?:field engineer|robot[a-z]*"
    r"|systems?)|robotics and surgery|surgery & robot"
    r"|neuro[- ]?(?:engineer|engineering|engineers|technology|tech|modulation|stimulation"
    r"|prosthetics?|devices?|interfaces?)"
    r"|neural (?:engineer|engineering|interface|interfaces|implant|implants|devices?)"
    r"|brain[- ](?:computer|machine) interfaces?|bci|brain interfaces"
    r"|prosthe(?:tic|tics|sis|ses)|orthotics?|exoskeletons?"
    r"|rehabilitation (?:engineer|engineering|robotics|technology)|assistive technolog(?:y|ies)"
    r"|orthop(?:a)?edic (?:engineer|engineering|device|devices|implant|implants|products?|r ?& ?d"
    r"|design|instruments?|technolog(?:y|ies)|research)"
    r"|catheters?|stents?|guidewires?|heart valves?|transcatheter|tavr|structural heart"
    r"|defibrillat[a-z]*|pacemakers?|cardiac (?:device|devices|rhythm|mapping|ablation)"
    r"|neurovascular|endovascular|peripheral vascular|vascular (?:access|device|devices|therapies)"
    r"|infusion (?:pump|pumps|system|systems|device|devices)|insulin (?:pump|pumps|delivery)"
    r"|glucose (?:monitor[a-z]*|sensor[a-z]*|sensing)|cgm|diabetes (?:device|devices|technology|tech)"
    r"|arthroplasty|spinal cord stimulation|cochlear implants?"
    r"|medical physic(?:s|ist|ists)|radiotherapy|proton therapy|radiation oncology"
    r"|laparoscop[a-z]*|arthroscop[a-z]*|minimally invasive|intraocular|contact lens(?:es)?"
    r"|vision care|optical (?:and|&) vision science"
    r"|hearing (?:aid|aids|instrument|instruments|technolog(?:y|ies)|devices?)|cochlear"
    r"|dental (?:device|devices|implant|implants|product|products|technolog(?:y|ies)|engineer"
    r"|engineering|r ?& ?d)|clear aligners?"
    r"|sleep apnea|cpap|biocompatib[a-z]*|design assurance|design controls?|design history file"
    r"|sterility assurance"
    r"|in[- ]vitro diagnostics?|ivds?|molecular diagnostics?|clinical diagnostics?|point[- ]of[- ]care"
    r"|companion diagnostics?|diagnostic (?:assay|assays|device|devices|instrument|instruments"
    r"|products?|r ?& ?d)|diagnostics (?:r ?& ?d|development|engineering|engineer|assay|systems?)"
    r"|cancer diagnostics"
    r"|health wearables?|wearable (?:health|biosensors?|medical)|health (?:sensing|sensor|sensors"
    r"|hardware)|biosensors?|bio[- ]?signals?|physiological (?:sensing|signals?|monitoring|sensors?)"
    r"|patient monitoring|remote patient monitoring|samd|software as a medical device"
    r"|bio[- ]?instrumentation|bioelectronic[a-z]*|neurotech[a-z]*|bio[- ]?materials?"
    r"|field clinical(?: [a-z]+)?|clinical field|mapping specialists?"
    r"|associate clinical (?:specialist|experience)|implantables?|drug[- ]device"
    r"|combination products?|combination device"
)
_MED_WEAK = _bounded(
    r"ultrasound|mri|magnetic resonance|imaging|image quality|implants?|surgical|surgery|cardiac"
    r"|cardiovascular|vascular|orthop(?:a)?edic[s]?|electrophysiology|endoscop[a-z]*"
    r"|steriliz[a-z]*|sterility|wearables?|human factors|usability|diagnostics?|infant care"
    r"|refurbishment|biomechanic[a-z]*|bio[- ]?mechanical|respiratory|ventilators?|ophthalmic"
    r"|sensors?|medical (?:engineering|r ?& ?d|design)"
)
_REG_STRONG = _bounded(
    r"regulatory (?:affairs|science|sciences|submissions?|publishing|labell?ing|intelligence|cmc"
    r"|chemistry|operations|strategy|writing|writer|innovation|policy)"
    r"|ra ?/ ?qa|qa ?/ ?ra|qara|raqa|ra[ -]qa|qa[ -]ra"
    r"|pharmacovigilance|drug safety|device vigilance|clinical safety|patient safety"
    r"|safety (?:surveillance|science|scientist|data management|case processing)|adverse events?"
    r"|post[- ]?market(?:ing)?(?: (?:surveillance|quality|regulatory|compliance))?"
    r"|clinical (?:research|trial|trials|operations|ops|data|development|affairs|stud(?:y|ies)"
    r"|project|program|quality|pharmacology|science|sciences|scientist|scientists|evidence"
    r"|monitor|monitoring|documentation|compliance|programmer|programming|evaluation"
    r"|investigations?|outcomes|biomarker|bioanalytical|standards)"
    r"|(?:patient|participant|subject|clinical|trial|study) recruitment"
    r"|study (?:coordinator|start[- ]?up|monitor|management)|site (?:activation|start[- ]?up)"
    r"|irb|institutional review board|human (?:subjects|research protection)"
    r"|gmp|cgmp|gxp|glp|good (?:manufacturing|laboratory|documentation|clinical|distribution) practices?"
    r"|capa|corrective (?:and|&) preventive|cqv|c ?& ?q|commissioning (?:and|&) qualification"
    r"|computer(?:ized)? systems? validation|cleaning validation|process validation"
    r"|method validation|equipment validation|test method validation|sterility assurance"
    r"|medical (?:affairs|information|writing|writer|writers|science liaison|review|excellence)"
    r"|msl|scientific (?:writing|writer|communications?|affairs|content)"
    r"|health (?:registration|authority|authorities|economics)|global labell?ing|labell?ing strategy"
    r"|cdisc|sdtm|iso ?13485|21 ?cfr|510 ?\(?k\)?|fda"
)
_REG_WEAK = _bounded(
    r"regulatory|compliance|quality|qa|qc|validation|document control|change control"
    r"|batch records?|deviations?|audits?|complaints?|vigilance|labell?ing|biostatistic[a-z]*"
    r"|statistical programm[a-z]*|epidemiolog[a-z]*|real[- ]world (?:evidence|data)|rwe|heor"
    r"|outcomes research|research coordinator|research compliance|sqm|supplier quality"
    r"|qualification|clinical|quality systems?|q ?& ?c"
)
_BIO_STRONG = _bounded(
    r"bio[- ]?tech(?:nolog(?:y|ies|ist|ical))?|bio[- ]?pharma(?:ceuticals?)?|pharmaceuticals?"
    r"|pharmaceutics|bio[- ]?process(?:ing|es)?|bio[- ]?manufactur[a-z]*|bio[- ]?production"
    r"|bio[- ]?analytic(?:al|s)?|bio[- ]?informatic[a-z]*|bio[- ]?chem(?:istry|ist|ists)"
    r"|bio[- ]?physic[a-z]*|bio[- ]?molecular|bio[- ]?engineer(?:ing|s)?|bioassays?"
    r"|bioconjugat[a-z]*|bio[- ]?catalys[a-z]*|biofabrication|bio[- ]?printing|biosimilars?"
    r"|biofoundry|bio[- ]?sciences?|bio[- ]?medical (?:sciences?|research|science)"
    r"|bio[- ]?separations?|bio[- ]?reactors?|bio[- ]?therapeutics?|biologics|biologic"
    r"|biotherapeutics"
    r"|(?:cell|gene|cell (?:and|&) gene|car[- ]?t|t[- ]cell|nucleic acid|rna|mrna|genetic) ?therap"
    r"(?:y|ies|eutics?)|cgt|car[- ]?t|immunotherap[a-z]*"
    r"|cell (?:biology|culture|line|lines|sciences?|bank|banking|therapy|medicines?|based"
    r"|and molecular)"
    r"|cellular (?:biology|pharmacology|therapy|engineering|immunology|sciences?|phagocytosis)"
    r"|gene (?:editing|therapy|expression|synthesis|regulation|delivery)|genetic engineering"
    r"|genom(?:e|es|ic|ics)|ngs|crispr|mrna|aav|viral vectors?"
    r"|vector (?:process|core|development|manufacturing)|lnp|lipid nanoparticles?|plasmids?"
    r"|oligonucleotides?|nucleic acids?|antibod(?:y|ies)"
    r"|protein (?:science|sciences|engineering|design|purification|expression|characterization"
    r"|chemistry|therapeutics|biochemistry|production|structural)|protein & structural"
    r"|immunogenicity|neurobiolog[a-z]*|pharmacolog[a-z]*|pharmacokinetic[a-z]*|pk ?/ ?pd|pkpd"
    r"|dmpk|adme|pharmacometric[a-z]*|qsp|toxicolog[a-z]*"
    r"|drug (?:discovery|development|product|products|substance|delivery|metabolism|design)"
    r"|discovery (?:biology|sciences?|research|oncology|immunology|neuroscience|biologics"
    r"|pharmaceutics|platform|technologies|bio[- ]?analytics|sample management)"
    r"|(?:oncology|immunology|neuroscience|biologics) discovery"
    r"|translational (?:medicine|research|sciences?|biology|modeling|oncology)|preclinical"
    r"|pre[- ]clinical|nonclinical|non[- ]clinical|in[- ]vivo|in[- ]vitro|vivarium"
    r"|assay development|immunoassays?|elisa|qpcr|flow cytometry|cytometry|pathobiology"
    r"|proteomic[a-z]*|metabolomic[a-z]*|lipidomic[a-z]*|multi[- ]?omics|single[- ]cell"
    r"|spatial (?:biology|omics|transcriptomics)|transcriptomic[a-z]*|structural biology|cryo[- ]?em"
    r"|synthetic biology|synbio|systems biology"
    r"|computational (?:biology|biologist|biosciences|genomics|toxicology|pharmacology|neuroscience"
    r"|biophysics|immunology|biologics)"
    r"|quantitative (?:biology|biosciences|pharmacology)|organism engineering|strain engineering"
    r"|metabolic engineering"
    r"|upstream (?:process|processing|bioprocess[a-z]*|cell culture|purification)"
    r"|downstream (?:process|processing|purification)|(?:process|r ?& ?d) (?:upstream|downstream)"
    r"|drug formulation|preformulation|oral formulation|parenteral formulation|biologics formulation"
    r"|formulation (?:and|&) (?:process|analytical) development|formulation sciences?"
    r"|sterile product development|drug product|drug substance|fill[- ]?finish|parenterals?"
    r"|lyophiliz[a-z]*|sterile (?:products|manufacturing|drug|injectables?|filling)"
    r"|msat|ms ?& ?t|manufacturing sciences? (?:and|&) technology|manufacturing science|cmc"
    r"|large molecule|small molecule"
    r"|analytical (?:development|sciences|r ?& ?d|biochemistry|characterization)"
    r"|qc (?:micro[a-z]*|bioassay)|high[- ]throughput (?:screening|biology|experimentation)"
    r"|monoclonal|bispecifics?"
    r"|molecular (?:biology|biologist|genetics|diagnostics|engineering|sciences?|medicine|pathology"
    r"|cloning)"
    r"|tissue (?:engineering|culture|biology|regeneration)|regenerative (?:medicine|engineering)"
    r"|stem cells?|organoids?|ipscs?"
    r"|plant (?:biotechnology|transformation|genetics|molecular)|crop (?:genetics|biotechnology)"
    r"|ag[- ]?biotech|agricultural biotechnology|trait (?:development|discovery)|genome editing"
    r"|microbiome|biomarkers?|epigenetic[a-z]*|chemical biology|biomolecular|bovine genetics"
    r"|inbre|biomedical informatics|precision medicine|drug[- ]device|combination products?"
)
_BIO_WEAK = _bounded(
    r"biolog(?:y|ical|ist|ists)|bio|life[- ]sciences?|therapeutics|genetics|sequencing|dna|rna"
    r"|proteins?|peptides?|enzymes?|microbio(?:logy|logist|logists|logical|me|mes)|microbial"
    r"|assays?|chromatograph[a-z]*|mass spectrometry|mass spec|purification|fermentation"
    r"|formulation|aseptic[a-z]*|tech(?:nology|nical)? transfer|lab(?:oratory)? (?:automation|robotics)"
    r"|analytical|chemistry|chemist|pathology|plant (?:biology|breeding|science|sciences|pathology)"
    r"|crop science|seeds? development|pharma|discovery|translational|bio[- ]?statistic[a-z]*"
    r"|imm|innovative medicine|janssen"
    # Battery makers run "Cell Engineering" and "Cell Manufacturing" teams too.
    r"|cell (?:engineering|processing|manufacturing)"
)
# Therapeutic areas: bio evidence only beside a science or engineering role,
# never on "Clinical Operations, Oncology" or "Oncology Marketing".
_THERAPEUTIC_AREA = _bounded(
    r"oncology|immuno[- ]?oncology|immunolog[a-z]*|neuroscience[s]?|infectious diseases?"
    r"|pathogens?|virolog[a-z]*|vaccines?|rare diseases?|hematology|inflammation|cardiometabolic"
    r"|metabolic diseases?"
)

# ---------------------------------------------------------------------------
# Role functions, read with the employer segment
# ---------------------------------------------------------------------------

_F_ENGINEERING = _bounded(
    r"engineer|engineers|engineering|r ?& ?d|research (?:and|&) development|npi|v ?& ?v"
    r"|mechatronics|automation|designer|drafter|cad|design|test|testing|verification"
    r"|reliability|sustaining|life ?cycle|lifecycle|experimental|instrumentation|hardware"
    r"|electronics|mechanical|electrical|optical|optics|photonics|robotics|controls"
    r"|(?:product|process|technical|analytical|device|assay|method) development"
)
_F_SCIENCE = _bounded(
    r"scien[a-z]*|reserch|research(?:er|ers)?|lab|labs|laborator(?:y|ies)|chemist[s]?|chemistry"
    r"|formulation|analytical|process development|data scien[a-z]*|machine learning|ml|ai"
    r"|computational|statistic[a-z]*|statistician[s]?|bioinformatic[a-z]*|discovery|translational"
    r"|preclinical|modeling|modelling|models?|predictive|informatics"
)
# Science nouns that keep a quality or clinical title on the BioTech side.
_F_SCIENCE_CORE = _bounded(
    r"scien[a-z]*|lab|labs|laborator(?:y|ies)|chemist[s]?|chemistry|formulation|analytical"
    r"|bioinformatic[a-z]*|biostatistic[a-z]*|computational|machine learning|discovery|preclinical"
    r"|assays?|bench"
)
_F_LAB = _bounded(
    r"scien[a-z]*|research associate|lab|labs|laborator(?:y|ies)|assays?|biolog[a-z]*|molecular"
    r"|chemistry|chemist[s]?|bench|wet[- ]lab|r ?& ?d|qc|quality control|method validation"
    r"|analytical"
)
_F_SOFTWARE = _bounded(
    r"software|firmware|embedded|sdet|full[- ]?stack|developer|programmer|algorithms?"
    r"|data engineer[a-z]*"
)
_F_DESIGN = _bounded(
    r"human factors|hfe|usability|industrial design|interaction design|user experience|ux"
)
_F_MANUFACTURING = _bounded(
    r"manufacturing|production|operations|operator|technicians?|technologists?|packaging|process"
    r"|technical (?:operations|services|development)|tech ops|service engineer|field service"
    r"|refurbishment|molding"
)
_F_QUALITY = _bounded(
    r"quality|qa|qc|validation|document control|capa|complaints?|compliance|regulatory|sterility"
    r"|steriliz[a-z]*|audit[a-z]*|cqv|csv|gmp|gxp|sqm|supplier quality|qualification|vigilance"
    r"|post[- ]?market|q ?& ?c"
)
_F_CLINICAL = _bounded(
    r"clinical|stud(?:y|ies) (?:coordinator|manager|management|start[- ]?up)|trials?"
    r"|research coordinator|research program|data (?:coordinator|manager|management)"
    r"|biostatistic[a-z]*|statistical programm[a-z]*|medical affairs|medical information"
    r"|pharmacovigilance|patient safety|drug safety|epidemiolog[a-z]*|real[- ]world|rwe|heor"
    r"|outcomes research|irb"
)
_F_RESEARCH_ASSISTANT = _bounded(
    r"research (?:assistant|aide|technician|associate|intern|student|trainee|lab)|student research"
    r"|lab(?:oratory)? (?:assistant|aide|technician|intern|student)"
    r"|student (?:technician|employee|worker|assistant|researcher)|science intern|[a-z]+ lab"
)
_RESEARCH_TITLE = _bounded(r"research|investigat[a-z]*|scien[a-z]*|[a-z]+ lab")
# Medical-school departments that make a research assistant title a biomedical lab.
_MEDICAL_DEPARTMENT = _bounded(
    r"medicine|som|school of medicine|college of medicine|medical school|medical center|urology"
    r"|oncology|hematology|cardiology|neurology|pathology|pharmacology|neuroscience|biomedical"
    r"|translational|cancer|immunology|microbiology|physiology|biochemistry|genetics|surgery"
    r"|pediatrics"
)
_WRITING = _bounded(
    r"scientific (?:writing|writer|communications?|affairs|content)|medical writ[a-z]*"
    r"|disease area communications|scientific (?:and )?value content"
)
_CHEMISTRY_ONLY = _bounded(
    r"(?:discovery|medicinal|process|synthetic|organic|exploratory) chemistry|chemical synthesis"
    r"|process research|process chemist[a-z]*"
)
# Engineering a device-and-pharma employer runs on both sides of the house.
_SHARED_ENGINEERING = _bounded(
    r"process|automation|project|manufacturing execution|mes|systems automation|business excellence"
)
_IMPLANT = _bounded(r"implants?|implantables?")
_RADIATION_ONCOLOGY = _bounded(r"radiation oncology")
_REGULATORY_WORD = _bounded(r"regulatory")
_SOFTWARE_QA_RESCUE = _bounded(r"regulatory|gmp|gxp|validation|design (?:quality|assurance)")
_FIELD_SERVICE = _bounded(r"field service|service engineer[a-z]*")
# Field clinical support at device makers is MedTech, not regulatory or clinical research.
_CLINICAL_FIELD = _bounded(
    r"clinical field|field clinical|clinical specialists?|mapping specialists?"
    r"|associate clinical (?:specialist|experience)|clinical account specialists?"
    r"|clinical (?:applications?|education) specialists?"
)
_CRO_ROLE = _bounded(r"data|project|associate|coordinator")
_VALIDATION_ROLE = _bounded(r"cqv|qualification|commissioning|validation")
_AEC_PROCESS = _bounded(
    r"process|bioprocess|pharma[a-z]*|biotech[a-z]*|cqv|qualification|commissioning"
)
_AGBIO_ROLE = _bounded(
    r"breed[a-z]*|genetic[a-z]*|biotech[a-z]*|r ?& ?d|research|development|scien[a-z]*"
)
_SEED_OPERATIONS = _bounded(r"seed (?:production|conditioning)|parent seed|conditioning")
_RESEARCH_WORD = _bounded(r"research")

# ---------------------------------------------------------------------------
# Description evidence: role-level terms only, never industry words
# ---------------------------------------------------------------------------

_DESC_DEVICE = (
    r"iso[ -]?13485|21 ?cfr ?(?:part ?)?820|(^|[^a-z])qmsr([^a-z]|$)|design controls?"
    r"|design history file|device master record|iec[ -]?60601|iso[ -]?14971|iec[ -]?62304"
    r"|iec[ -]?62366|iso[ -]?10993|iso[ -]?1113[57]|510 ?\(k\)|premarket (?:approval|notification)"
    r"|(^|[^a-z])eu ?mdr([^a-z]|$)|class (?:ii|iii) medical devices?|fda[- ]cleared"
    r"|software as a medical device"
    r"|medical device (?:design|development|r ?& ?d|engineering|product development|industry experience)"
)
_DESC_BIO = (
    r"cell culture|bioreactors?|aseptic technique|biosafety cabinet|transfection|plasmids?"
    r"|(^|[^a-z])crispr([^a-z]|$)|(^|[^a-z])q?pcr([^a-z]|$)|(^|[^a-z])elisa([^a-z]|$)"
    r"|flow cytometry|western blots?|(^|[^a-z])akta([^a-z]|$)|tangential flow|ultrafiltration"
    r"|monoclonal antibod|mammalian cells?|tissue culture|mouse models?|rodent models?"
    r"|animal models?|next[- ]generation sequencing|ngs library|single[- ]cell rna"
    r"|protein (?:purification|expression|characterization)|cell lines?|stem cells?|organoids?"
    r"|molecular cloning|gel electrophoresis|cell[- ]based assays?|immunohistochemistry"
)
# Food, beverage and water-treatment work reuses the same bench vocabulary.
_DESC_NOT_BIO = (
    r"food safety|food science|food (?:and|&) beverage|dairy|cheese|beverage|snack|confection"
    r"|bakery|meat|poultry|haccp|(^|[^a-z])sqf([^a-z]|$)|brewing|brewery|winery|pet food"
    r"|flavors?|fragrance|wastewater|water treatment|drinking water|membrane bioreactor"
    r"|(^|[^a-z])mbr([^a-z]|$)|sewage|municipal water"
)
_DESC_REGULATED = (
    r"21 ?cfr|(^|[^a-z])cgmp([^a-z]|$)|(^|[^a-z])gxp([^a-z]|$)|ich[- ]?gcp|good clinical practice"
    r"|ich e6|good documentation practice|batch records?|(^|[^a-z])irb([^a-z]|$)|informed consent"
    r"|clinical trials?|clinical stud(?:y|ies)|clinical research|protocol deviations?"
    r"|adverse events?|pharmacovigilance|regulatory submissions?|(^|[^a-z])ectd([^a-z]|$)"
    r"|iso[ -]?13485|computer system validation|gamp ?5|(?:fda|ema) (?:regulations|guidance|submissions?)"
)
# Life-science regulators, for a bare "Regulatory" title at an unregistered employer.
_DESC_LS_REGULATOR = (
    r"(^|[^a-z])fda([^a-z]|$)|510 ?\(k\)|premarket|investigational new drug"
    r"|(^|[^a-z])(?:ind|nda|bla|anda)([^a-z]|$)|(^|[^a-z])ema([^a-z]|$)|health canada"
    r"|notified body|medical devices?|pharmaceutical|biologics?|clinical (?:trials?|stud(?:y|ies))"
)

_RX = {
    name: re.compile(globals()[name], re.IGNORECASE)
    for name in (
        "_EXTRA_NOISE", "_PROGRAM_WORDS", "_PROGRAM_PHRASE",
        "_HARD_VETO_EXCEPTIONS", "_HARD_VETO", "_SOFT_VETO", "_SOFT_RESCUE", "_CARE_VETO",
        "_NON_LS_REGULATORY", "_NON_LS_BIO", "_SEMICONDUCTOR",
        "_MED_STRONG", "_MED_WEAK", "_REG_STRONG", "_REG_WEAK", "_BIO_STRONG", "_BIO_WEAK",
        "_THERAPEUTIC_AREA",
        "_F_ENGINEERING", "_F_SCIENCE", "_F_SCIENCE_CORE", "_F_LAB", "_F_SOFTWARE", "_F_DESIGN",
        "_F_MANUFACTURING", "_F_QUALITY", "_F_CLINICAL", "_F_RESEARCH_ASSISTANT",
        "_RESEARCH_TITLE", "_MEDICAL_DEPARTMENT", "_WRITING", "_CHEMISTRY_ONLY",
        "_SHARED_ENGINEERING", "_IMPLANT", "_RADIATION_ONCOLOGY", "_REGULATORY_WORD",
        "_SOFTWARE_QA_RESCUE", "_FIELD_SERVICE", "_CLINICAL_FIELD", "_CRO_ROLE",
        "_VALIDATION_ROLE", "_AEC_PROCESS", "_AGBIO_ROLE", "_SEED_OPERATIONS", "_RESEARCH_WORD",
        "_DESC_DEVICE", "_DESC_BIO", "_DESC_NOT_BIO", "_DESC_REGULATED", "_DESC_LS_REGULATOR",
    )
}
_PROGRAM_NOISE = re.compile(PROGRAM_NOISE_PATTERN, re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def _rx(name: str) -> re.Pattern[str]:
    return _RX[name]


def normalize_title(title: str) -> str:
    """Strip program, season and wrapper vocabulary so rules read role nouns."""
    value = _rx("_EXTRA_NOISE").sub(" ", title)
    value = _PROGRAM_NOISE.sub(" ", value)
    return _WHITESPACE.sub(" ", value).strip()


def _program_only(normalized: str, company: str) -> bool:
    value = normalized.lower()
    if company:
        value = value.replace(company.lower(), " ")
    words = _rx("_PROGRAM_WORDS")
    # Twice: bounded words consume their separators, so adjacent ones need a second pass.
    rest = words.sub(" ", words.sub(" ", value))
    return re.search(r"[a-z]{2,}", rest) is None


# ---------------------------------------------------------------------------
# Employer registry
# ---------------------------------------------------------------------------


class EmployerRegistryError(ValueError):
    """The life-science employer registry is malformed."""


@functools.cache
def employers(path: Path = EMPLOYERS_PATH) -> Mapping[str, frozenset[str]]:
    """Map each registered company name, case-folded, to its segments."""
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    segments = document.get("segments")
    if not isinstance(segments, dict):
        raise EmployerRegistryError(f"{path}: expected a top-level 'segments' mapping")
    table: dict[str, set[str]] = {}
    for segment, names in segments.items():
        if segment not in SEGMENTS:
            raise EmployerRegistryError(f"{path}: unknown segment {segment!r}")
        if not isinstance(names, list):
            raise EmployerRegistryError(f"{path}: segment {segment!r} must be a list")
        for name in names:
            if not isinstance(name, str) or not name.strip():
                raise EmployerRegistryError(f"{path}: {segment!r} has a blank company name")
            table.setdefault(name.strip().casefold(), set()).add(segment)
    return {name: frozenset(values) for name, values in table.items()}


def employer_segments(company: str | None) -> frozenset[str]:
    """Return the registered segments for a company name, or none."""
    if not company:
        return frozenset()
    return employers().get(company.strip().casefold(), frozenset())


@functools.cache
def _registry_hash(path: Path = EMPLOYERS_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def focus_version() -> str:
    """Rule revision plus a registry fingerprint, so either change re-enriches."""
    return f"{FOCUS_VERSION}.{_registry_hash()[:12]}"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FocusEvidence:
    """Chips earned by one posting, with the evidence kind behind each."""

    chips: tuple[str, ...] = ()
    reasons: Mapping[str, str] = field(default_factory=dict)
    veto: str | None = None


def classify(
    title: str,
    description: str | None = None,
    *,
    company: str = "",
    segments: Collection[str] | None = None,
) -> FocusEvidence:
    """Return the focus chips a bioengineering student should see on a posting.

    ``segments`` defaults to the registry entry for ``company``.
    """
    seg = frozenset(employer_segments(company) if segments is None else segments)
    text = normalize_title(title)
    body = description or ""

    def has(name: str) -> bool:
        return _rx(name).search(text) is not None

    def says(name: str) -> bool:
        return _rx(name).search(body) is not None

    if has("_CARE_VETO"):
        return FocusEvidence(veto="care")
    if _rx("_HARD_VETO").search(_rx("_HARD_VETO_EXCEPTIONS").sub(" ", text)):
        return FocusEvidence(veto="business")

    engineering, science, science_core = has("_F_ENGINEERING"), has("_F_SCIENCE"), has("_F_SCIENCE_CORE")
    software, design, manufacturing = has("_F_SOFTWARE"), has("_F_DESIGN"), has("_F_MANUFACTURING")
    quality, clinical, lab = has("_F_QUALITY"), has("_F_CLINICAL"), has("_F_LAB")
    assistant = has("_F_RESEARCH_ASSISTANT")
    non_ls_bio = has("_NON_LS_BIO")

    med_strong = has("_MED_STRONG") and not (has("_IMPLANT") and has("_SEMICONDUCTOR"))
    reg_strong = has("_REG_STRONG") and not has("_NON_LS_REGULATORY")
    area = has("_THERAPEUTIC_AREA") and not has("_RADIATION_ONCOLOGY") and not non_ls_bio
    area_role = area and (science or engineering or manufacturing or assistant)
    bio_title = has("_BIO_STRONG") and not non_ls_bio
    bio_strong = bio_title or area_role
    if has("_SOFT_VETO") and not (has("_SOFT_RESCUE") or med_strong or reg_strong or bio_strong):
        return FocusEvidence(veto="soft")

    device_text = says("_DESC_DEVICE")
    bio_text = says("_DESC_BIO") and not says("_DESC_NOT_BIO")
    regulated_text = says("_DESC_REGULATED")

    # A quality, clinical or regulatory function with no science or engineering noun.
    r_only = (
        (quality or clinical or reg_strong)
        and not (science_core or engineering or bio_title or lab)
    ) or has("_REGULATORY_WORD") or has("_WRITING")
    chemistry_only = has("_CHEMISTRY_ONLY") and not bio_strong
    software_qa = software and quality and not has("_SOFTWARE_QA_RESCUE")
    program = _program_only(text, company) or _rx("_PROGRAM_PHRASE").search(title) is not None
    technical = engineering or science or software or design or manufacturing or quality or program
    med_weak = has("_MED_WEAK") and not has("_SEMICONDUCTOR")
    reg_weak = has("_REG_WEAK") and not has("_NON_LS_REGULATORY")
    bio_weak = has("_BIO_WEAK") and not non_ls_bio
    bio_cue = bio_strong or bio_weak
    ls_employer = bool(seg & _LS_EMPLOYER)
    dev, dx, pharma = "dev" in seg, "dx" in seg, "pharma" in seg
    # Utilities, banks and chemical makers all run regulatory desks too.
    unknown_regulatory = (
        not ls_employer
        and has("_REGULATORY_WORD")
        and not (regulated_text or device_text or says("_DESC_LS_REGULATOR"))
    )
    no_regulatory = software_qa or has("_CLINICAL_FIELD") or unknown_regulatory

    reasons: dict[str, str] = {}

    def earn(chip: str, reason: str) -> None:
        reasons.setdefault(chip, reason)

    # MedTech ---------------------------------------------------------------
    if med_strong:
        earn(MEDTECH_FILTER, "title")
    if med_weak and (seg & _DEVICE_EMPLOYER or device_text):
        earn(MEDTECH_FILTER, "weak_title")
    if dev and technical and not (pharma and bio_cue and not med_strong):
        earn(MEDTECH_FILTER, "employer")
    if dx and (engineering or software or design or manufacturing or quality or program or science):
        earn(MEDTECH_FILTER, "employer")
    if not ls_employer and device_text and (engineering or quality or software or design):
        earn(MEDTECH_FILTER, "description")

    # Regulatory --------------------------------------------------------------
    if not no_regulatory:
        if reg_strong:
            earn(REGULATORY_FILTER, "title")
        if reg_weak and (ls_employer or regulated_text):
            earn(REGULATORY_FILTER, "weak_title")
        if (dev or dx or pharma or "lab" in seg) and (quality or clinical):
            earn(REGULATORY_FILTER, "employer")
        if "tools" in seg and quality:
            earn(REGULATORY_FILTER, "employer")
        if "cro" in seg and (clinical or quality or program or has("_CRO_ROLE")):
            earn(REGULATORY_FILTER, "employer")
        if "consult" in seg and (engineering or quality or program or science):
            earn(REGULATORY_FILTER, "employer")
        if "aec_ls" in seg and has("_VALIDATION_ROLE"):
            earn(REGULATORY_FILTER, "employer")
        if "bio_institute" in seg and clinical:
            earn(REGULATORY_FILTER, "employer")
        if not ls_employer and regulated_text and (quality or clinical):
            earn(REGULATORY_FILTER, "description")

    # BioTech ---------------------------------------------------------------
    if bio_strong and not (r_only and not lab) and not chemistry_only:
        earn(BIOTECH_FILTER, "title")
    if bio_weak and not r_only and not chemistry_only and (seg & _BIO_EMPLOYER or bio_text):
        earn(BIOTECH_FILTER, "weak_title")
    if area and not r_only and not chemistry_only and seg & _BIO_EMPLOYER:
        earn(BIOTECH_FILTER, "weak_title")
    if dev and pharma and engineering and has("_SHARED_ENGINEERING") and not med_strong:
        earn(BIOTECH_FILTER, "employer")
    if dx and (lab or bio_cue):
        earn(BIOTECH_FILTER, "employer")
    if (
        pharma
        and (science or engineering or manufacturing or program or bio_cue or lab or (software and science))
        and not r_only
        and not has("_FIELD_SERVICE")
        and not chemistry_only
        and not (dev and not bio_cue)
    ):
        earn(BIOTECH_FILTER, "employer")
    if "tools" in seg and (science or engineering or software or manufacturing or program or lab) and not r_only:
        earn(BIOTECH_FILTER, "employer")
    if "agbio" in seg and (lab or has("_AGBIO_ROLE")) and (not has("_SEED_OPERATIONS") or has("_RESEARCH_WORD")):
        earn(BIOTECH_FILTER, "employer")
    if "lab" in seg and (science or manufacturing or program or engineering or lab):
        earn(BIOTECH_FILTER, "employer")
    if "cro" in seg and lab and not clinical:
        earn(BIOTECH_FILTER, "employer")
    if "consult" in seg and (engineering or has("_VALIDATION_ROLE")):
        earn(BIOTECH_FILTER, "employer")
    if "aec_ls" in seg and has("_AEC_PROCESS"):
        earn(BIOTECH_FILTER, "employer")
    if "bio_institute" in seg and (not (quality or clinical) or bio_cue):
        earn(BIOTECH_FILTER, "employer")
    if (
        (assistant or science)
        and has("_RESEARCH_TITLE")
        and has("_MEDICAL_DEPARTMENT")
        and not clinical
        and not r_only
    ):
        earn(BIOTECH_FILTER, "research_title")
    if not ls_employer and bio_text and (science or engineering or assistant) and not r_only:
        earn(BIOTECH_FILTER, "description")

    chips = tuple(chip for chip in FOCUS_FILTERS if chip in reasons)
    return FocusEvidence(chips=chips, reasons={chip: reasons[chip] for chip in chips})
