from io import BytesIO

from PIL import Image

from xjobscore.logo_harvest import (
    MAX_EDGE,
    MIN_EDGE,
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
