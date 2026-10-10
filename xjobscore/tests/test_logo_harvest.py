import asyncio
from io import BytesIO

import httpx
from PIL import Image

from xjobscore.logo_harvest import (
    MAX_EDGE,
    MIN_EDGE,
    _fetch_logo,
    _icon_candidates,
    _is_image,
    normalize,
    too_small,
)


def _png(size: tuple[int, int], *, color=(200, 30, 30, 255)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_normalize_downscales_only_what_is_too_big_for_the_card():
    big = _png((512, 512))
    shrunk = normalize(big)
    assert max(Image.open(BytesIO(shrunk)).size) == MAX_EDGE
    assert len(shrunk) < len(big)

    # Already small enough: returned byte-identical rather than re-encoded, so a
    # rerun cannot slowly degrade an image by repeatedly recompressing it.
    small = _png((64, 64))
    assert normalize(small) is small
    assert normalize(shrunk) is shrunk


def test_normalize_preserves_aspect_ratio_and_transparency():
    wide = normalize(_png((600, 300), color=(0, 0, 0, 0)))
    image = Image.open(BytesIO(wide))
    assert image.size == (MAX_EDGE, MAX_EDGE // 2)
    # Flattening the alpha onto a guessed background is the one edit here that
    # would actually show, since these marks are mostly transparent.
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0


def test_normalize_keeps_anything_it_cannot_decode():
    # SVG is resolution-independent and Pillow cannot open it; both reasons to
    # pass it through untouched rather than lose it.
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="9" height="9"/></svg>'
    assert normalize(svg) is svg
    junk = b"\x89PNG\r\n\x1a\n" + b"not really a png"
    assert normalize(junk) is junk


def test_is_image_rejects_the_html_error_page_a_404_serves_as_a_favicon():
    assert _is_image(_png((16, 16)))
    assert _is_image(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    assert not _is_image(b"<!DOCTYPE html><html><body>404</body></html>")


def test_icon_candidates_prefer_apple_touch_then_largest_then_favicon():
    html = """
      <link rel="icon" sizes="16x16" href="/small.png">
      <link rel="icon" sizes="192x192" href="/big.png">
      <link rel="apple-touch-icon" href="/apple.png">
    """
    found = _icon_candidates(html, "https://example.test/")
    assert found[0] == "https://example.test/apple.png"
    assert found[1] == "https://example.test/big.png"
    assert found[2] == "https://example.test/small.png"
    # Always tried last, because a server answers it whether or not it is declared.
    assert found[-1] == "https://example.test/favicon.ico"

    # A site that declares nothing still yields the conventional path.
    assert _icon_candidates("", "https://example.test/") == ["https://example.test/favicon.ico"]


def test_too_small_rejects_the_favicons_that_render_as_smudges():
    # A 16px mark drawn into a 30px box is upscaled almost 2x; that is the blur
    # the card is better off replacing with its initials.
    assert too_small(_png((16, 16)))
    assert not too_small(_png((MIN_EDGE, MIN_EDGE)))
    assert not too_small(_png((180, 180)))

    # Judged on the long edge, so a wide wordmark is not rejected for being short.
    assert not too_small(_png((128, 20)))

    # Never rejected: resolution-independent, and undecodable bytes are the
    # browser's call rather than ours.
    assert not too_small(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    assert not too_small(b"\x89PNG\r\n\x1a\n not a real png")


def _fetch(routes: dict[str, httpx.Response | Exception], domain: str) -> tuple[bytes | None, list[str]]:
    """Run `_fetch_logo` against canned responses; unlisted URLs 404."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        asked.append(url)
        answer = routes.get(url, httpx.Response(404))
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def run() -> bytes | None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _fetch_logo(client, domain)

    return asyncio.run(run()), asked


GOOGLE = "https://www.google.com/s2/favicons?domain={}&sz=" + str(MAX_EDGE)


def test_fetch_retries_www_when_the_apex_never_answers():
    # Prudential: the apex times out while www serves.
    logo = _png((64, 64))
    found, asked = _fetch(
        {
            "https://acme.test/": httpx.ConnectTimeout("apex down"),
            "https://acme.test/favicon.ico": httpx.ConnectTimeout("apex down"),
            "https://www.acme.test/": httpx.Response(200, text="<html></html>"),
            "https://www.acme.test/favicon.ico": httpx.Response(200, content=logo),
        },
        "acme.test",
    )
    assert found == logo
    assert GOOGLE.format("acme.test") not in asked


def test_fetch_skips_www_when_the_apex_answered():
    # A host that served a page served its icons too; www would only repeat them.
    _, asked = _fetch(
        {"https://acme.test/": httpx.Response(200, text="<html></html>")},
        "acme.test",
    )
    assert not any("www.acme.test" in url for url in asked)


def test_fetch_falls_back_to_google_when_the_site_is_walled():
    # Blue Origin: every request of ours draws a 429 bot checkpoint.
    logo = _png((128, 128))
    found, _ = _fetch(
        {
            "https://acme.test/": httpx.Response(429, text="checkpoint"),
            "https://acme.test/favicon.ico": httpx.Response(429, text="checkpoint"),
            GOOGLE.format("acme.test"): httpx.Response(200, content=logo),
        },
        "acme.test",
    )
    assert found == logo


def test_fetch_ignores_googles_placeholder_globe():
    # Google answers "no icon" with a 404 that still carries a globe PNG.
    found, _ = _fetch(
        {GOOGLE.format("acme.test"): httpx.Response(404, content=_png((16, 16)))},
        "acme.test",
    )
    assert found is None


def test_fetch_keeps_a_small_mark_only_when_nothing_larger_exists():
    tiny, small = _png((16, 16)), _png((24, 24))
    # Boeing: 16px on the site, 16px from Google -- stored rather than an empty box.
    found, _ = _fetch(
        {
            "https://acme.test/": httpx.Response(200, text="<html></html>"),
            "https://acme.test/favicon.ico": httpx.Response(200, content=tiny),
            GOOGLE.format("acme.test"): httpx.Response(200, content=small),
        },
        "acme.test",
    )
    assert found == small

    # Any drawable mark from any source beats the held small one.
    big = _png((64, 64))
    found, _ = _fetch(
        {
            "https://acme.test/": httpx.Response(200, text="<html></html>"),
            "https://acme.test/favicon.ico": httpx.Response(200, content=tiny),
            GOOGLE.format("acme.test"): httpx.Response(200, content=big),
        },
        "acme.test",
    )
    assert found == big
