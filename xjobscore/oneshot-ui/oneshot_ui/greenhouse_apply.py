"""Submit a tracked application through a Greenhouse-hosted apply form.

Scope, deliberately narrow: only postings whose apply URL is a Greenhouse
hosted board (job-boards.greenhouse.io and its EU and legacy hosts). Forms
embedded on company sites wrap the same widget in an iframe or a custom shell
and are not attempted.

The flow is two calls. ``build_plan`` reads the posting's questions from the
public job board API and pairs each one with a value from the profile, the
account's saved answers, or nothing. The client shows that plan for review; the
confirmed answers come back and ``submit_hosted`` drives the form in headless
Chromium. Greenhouse's apply POST needs the company's own API key, which is why
the browser is involved at all.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

BOARDS_API = "https://boards-api.greenhouse.io/v1/boards"

# One API host serves every region; the EU hosted board still answers on the
# default API (verified against a live EU posting, the .eu API host does not
# resolve).
HOSTED_HOSTS = frozenset(
    {"job-boards.greenhouse.io", "job-boards.eu.greenhouse.io", "boards.greenhouse.io"}
)
_HOSTED_PATH = re.compile(r"^/(?P<board>[^/]+)/jobs/(?P<job>\d+)/?$")

# Fields Greenhouse puts on every form. Anything else is a per-posting custom
# question keyed by its label in the answer bank.
BUILTIN_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "preferred_name",
        "email",
        "phone",
        "resume",
        "resume_text",
        "cover_letter",
        "cover_letter_text",
    }
)
FIELD_TYPES = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "multiselect",
}


@dataclass(frozen=True)
class HostedPosting:
    board: str
    job_id: int
    apply_url: str

    @property
    def questions_url(self) -> str:
        return f"{BOARDS_API}/{self.board}/jobs/{self.job_id}?questions=true"


@dataclass
class PlanField:
    name: str
    label: str
    type: str
    required: bool
    options: list[str] = field(default_factory=list)
    value: Any = None
    # profile | saved | answer | resume | missing | skipped
    source: str = "skipped"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "options": list(self.options),
            "value": self.value,
            "source": self.source,
        }


@dataclass
class SubmissionResult:
    ok: bool
    message: str
    final_url: str = ""
    screenshot: str = ""


def parse_hosted_url(url: str | None) -> HostedPosting | None:
    """The board token and job id of a Greenhouse hosted apply URL, else None."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or parts.hostname not in HOSTED_HOSTS:
        return None
    match = _HOSTED_PATH.match(parts.path)
    if not match:
        return None
    return HostedPosting(
        board=match.group("board"),
        job_id=int(match.group("job")),
        apply_url=f"https://{parts.hostname}/{match.group('board')}/jobs/{match.group('job')}",
    )


def answer_key(label: str) -> str:
    """A label normalised for the answer bank: case, punctuation, and spacing folded."""
    return re.sub(r"[^a-z0-9]+", " ", str(label or "").casefold()).strip()


async def fetch_job(posting: HostedPosting, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """The posting with its application questions, from the public board API."""
    owned = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        response = await client.get(posting.questions_url)
        response.raise_for_status()
        return response.json()
    finally:
        if owned:
            await client.aclose()


def _split_name(full_name: str) -> tuple[str, str]:
    parts = str(full_name or "").split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def _link(profile: dict[str, Any], *needles: str) -> str:
    for link in profile.get("links") or []:
        url = link.get("url", "") if isinstance(link, dict) else str(link)
        text = f"{url} {link.get('label', '') if isinstance(link, dict) else ''}".casefold()
        if any(needle in text for needle in needles):
            return url
    return ""


def _match_option(value: Any, options: list[str]) -> str | None:
    wanted = str(value or "").strip().casefold()
    for option in options:
        if option.casefold() == wanted:
            return option
    return None


def _profile_value(name: str, label: str, profile: dict[str, Any]) -> str:
    first, last = _split_name(profile.get("full_name", ""))
    key = answer_key(label)
    if name == "first_name":
        return first
    if name == "last_name":
        return last
    if name == "preferred_name" or "preferred first name" in key:
        return first
    if name == "email":
        return str(profile.get("email") or "")
    if name == "phone":
        return str(profile.get("phone") or "")
    if "linkedin" in key:
        return _link(profile, "linkedin")
    if "github" in key:
        return _link(profile, "github")
    if "portfolio" in key or "website" in key:
        return _link(profile, "portfolio", "site", "github")
    return ""


def build_plan(
    job: dict[str, Any],
    profile: dict[str, Any],
    saved_answers: dict[str, Any] | None = None,
    answers: dict[str, Any] | None = None,
) -> list[PlanField]:
    """Pair every question on the form with the value the submitter will type.

    Precedence is explicit answers, then the profile, then the saved bank.
    A required question that ends with no value is reported as ``missing`` so
    the client can ask for it rather than the submitter failing on the form.
    """
    saved = saved_answers or {}
    given = answers or {}
    plan: list[PlanField] = []
    for question in job.get("questions") or []:
        label = str(question.get("label") or "")
        required = bool(question.get("required"))
        fields = question.get("fields") or []
        if not fields:
            continue
        # Resume and cover letter offer a file and a text alternative; the
        # resume is always sent as the rendered PDF, the cover letter as text.
        raw = fields[0]
        name = str(raw.get("name") or "")
        if name == "cover_letter" and len(fields) > 1:
            raw = fields[1]
            name = str(raw.get("name") or "")
        kind = FIELD_TYPES.get(str(raw.get("type") or ""), "text")
        options = [str(item.get("label")) for item in raw.get("values") or [] if item.get("label") is not None]
        item = PlanField(name=name, label=label, type=kind, required=required, options=options)

        if name == "resume":
            item.source = "resume"
            plan.append(item)
            continue

        if name in given:
            item.value, item.source = given[name], "answer"
        else:
            from_profile = _profile_value(name, label, profile)
            if from_profile:
                item.value, item.source = from_profile, "profile"
            elif answer_key(label) in saved:
                item.value, item.source = saved[answer_key(label)], "saved"

        if kind == "select":
            matched = _match_option(item.value, options) if item.value not in (None, "") else None
            if item.value not in (None, "") and matched is None:
                item.value, item.source = None, "skipped"
            else:
                item.value = matched
        elif kind == "multiselect":
            chosen = item.value if isinstance(item.value, list) else ([item.value] if item.value else [])
            kept = [option for option in (_match_option(choice, options) for choice in chosen) if option]
            item.value = kept
            if chosen and not kept:
                item.source = "skipped"
        elif item.value is not None:
            item.value = str(item.value).strip()

        empty = item.value in (None, "", [])
        if empty:
            item.value = None
            item.source = "missing" if required else "skipped"
        plan.append(item)
    return plan


def missing_required(plan: list[PlanField]) -> list[PlanField]:
    return [item for item in plan if item.required and item.source == "missing"]


def bankable_answers(plan: list[PlanField]) -> dict[str, Any]:
    """Custom-question answers worth remembering for the next form."""
    remembered: dict[str, Any] = {}
    for item in plan:
        if item.name in BUILTIN_FIELDS or item.source != "answer" or item.value in (None, "", []):
            continue
        remembered[answer_key(item.label)] = item.value
    return remembered


# -- browser ----------------------------------------------------------------

_SUCCESS_TEXT = re.compile(
    r"thank you for applying|application has been submitted|application received|we have received your application",
    re.I,
)


def _css_id(value: str) -> str:
    return "#" + re.sub(r"([^\w-])", r"\\\1", value)


async def submit_hosted(
    posting: HostedPosting,
    plan: list[PlanField],
    resume_pdf: bytes,
    resume_filename: str,
    *,
    headless: bool = True,
    debug_dir: Path | None = None,
    timeout_s: float = 90,
    dry_run: bool = False,
) -> SubmissionResult:
    """Fill and submit the hosted form. Never raises for a form-level failure.

    ``dry_run`` fills everything and screenshots the page instead of pressing
    submit, which is how the selectors are checked against a live board.
    """
    from playwright.async_api import async_playwright

    shot = ""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 1600},
            locale="en-US",
        )
        page = await context.new_page()
        try:
            await page.goto(posting.apply_url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_selector("#application-form", timeout=30_000)
            # The form is server-rendered before React attaches its handlers;
            # a click on the Attach button in that window is silently lost.
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass

            # The resume goes first: Greenhouse parses the upload and resets
            # the name and email inputs from it, wiping anything typed before.
            ordered = sorted(plan, key=lambda item: item.source != "resume")
            for item in ordered:
                await _fill_field(page, item, resume_pdf, resume_filename)
            await _ensure_phone_country(page)

            if dry_run:
                outcome: str | None = "Dry run: form filled, not submitted."
            else:
                await page.click("#application-form button[type=submit]")
                outcome = await _await_outcome(page, timeout_s)
            if debug_dir is not None:
                debug_dir.mkdir(parents=True, exist_ok=True)
                shot = str(debug_dir / f"greenhouse-{posting.board}-{posting.job_id}.png")
                await page.screenshot(path=shot, full_page=True)
            return SubmissionResult(
                ok=outcome is None or dry_run,
                message=outcome or "Application submitted.",
                final_url=page.url,
                screenshot=shot,
            )
        except Exception as exc:  # Playwright raises on timeouts and detached nodes
            if debug_dir is not None:
                debug_dir.mkdir(parents=True, exist_ok=True)
                shot = str(debug_dir / f"greenhouse-{posting.board}-{posting.job_id}-error.png")
                try:
                    await page.screenshot(path=shot, full_page=True)
                except Exception:
                    shot = ""
            return SubmissionResult(ok=False, message=f"{type(exc).__name__}: {exc}", final_url=page.url, screenshot=shot)
        finally:
            await context.close()
            await browser.close()


async def _fill_field(page: Any, item: PlanField, resume_pdf: bytes, resume_filename: str) -> None:
    if item.source == "resume":
        # Setting files on the hidden input directly never reaches the React
        # handler; the Attach button's file chooser does, and the widget then
        # swaps its buttons for a filename chip.
        attach = page.locator("#resume").locator("xpath=..").locator("button", has_text="Attach")
        await attach.first.scroll_into_view_if_needed()
        chooser = None
        for attempt in range(4):
            try:
                async with page.expect_file_chooser(timeout=5_000) as pending:
                    await attach.first.click()
                chooser = await pending.value
                break
            except Exception:
                if attempt == 3:
                    raise
                await asyncio.sleep(1.5)
        assert chooser is not None
        await chooser.set_files(
            {"name": resume_filename, "mimeType": "application/pdf", "buffer": resume_pdf}
        )
        chip = page.locator(".file-upload__filename", has_text=resume_filename)
        await chip.first.wait_for(timeout=30_000)
        return
    if item.value in (None, "", []):
        return
    selector = _css_id(item.name)
    if item.type in ("text", "textarea"):
        if item.name == "cover_letter_text":
            await page.click('[data-testid="cover_letter-text"]')
        await page.fill(selector, str(item.value))
        return
    if item.type == "select":
        await _pick_option(page, selector, str(item.value))
        return
    if item.type == "multiselect":
        # Each choice is a checkbox sharing the question's name; the option
        # text lives in the <label for=...> beside it.
        wanted = {option.casefold() for option in item.value}
        boxes = page.locator(f'input[type=checkbox][name="{item.name}"]')
        for index in range(await boxes.count()):
            box = boxes.nth(index)
            box_id = await box.get_attribute("id")
            text = (await page.locator(f'label[for="{box_id}"]').inner_text()).strip()
            if text.casefold() in wanted:
                await box.check()


async def _pick_option(page: Any, selector: str, label: str) -> None:
    """Choose one entry in a react-select combobox by its visible label."""
    control = page.locator(selector)
    await control.click()
    await control.fill(label)
    exact = page.locator('[role="option"]', has_text=re.compile(rf"^\s*{re.escape(label)}\s*$"))
    if await exact.count():
        await exact.first.click()
    else:
        await control.press("Enter")


async def _ensure_phone_country(page: Any) -> None:
    """The phone widget carries a required country combobox that starts empty."""
    country = page.locator("#country")
    if not await country.count():
        return
    picked = country.locator(
        "xpath=ancestor::div[contains(@class,'select__value-container')]"
        "//div[contains(@class,'select__single-value')]"
    )
    if await picked.count() and (await picked.first.inner_text()).strip():
        return
    await _pick_option(page, "#country", "United States")


async def _await_outcome(page: Any, timeout_s: float) -> str | None:
    """None on a confirmed submission, else the reason the form refused it."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        body = await page.locator("body").inner_text()
        if "/confirmation" in page.url or _SUCCESS_TEXT.search(body):
            return None
        errors = page.locator('[id$="-error"]:not(:empty), [role="alert"]:not(:empty)')
        count = await errors.count()
        messages = []
        for index in range(count):
            text = (await errors.nth(index).inner_text()).strip()
            if text:
                messages.append(text)
        if messages:
            return "Greenhouse rejected the form: " + "; ".join(dict.fromkeys(messages))
        await asyncio.sleep(1)
    return "Timed out waiting for Greenhouse to confirm the submission."
