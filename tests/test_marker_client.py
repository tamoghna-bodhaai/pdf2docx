"""The boundary between marker's sidecar and this application.

Two things are being defended here, and they pull in opposite directions. A
response arrives over HTTP and decides where bytes get written inside a job
directory, so its image names have to be checked. But the whole reason the
`marker` mode exists is to see marker's own output, so nothing that is merely
*unexpected* may be rewritten or dropped — only what is unsafe is refused, and
it is refused loudly rather than quietly repaired.
"""

from __future__ import annotations

import base64

import pytest

from app.marker_client import (
    MarkerError,
    is_empty,
    parse_document_response,
    prefix_image_refs,
    split_pages,
    write_raw,
)

PNG = base64.b64encode(b"not really a png").decode("ascii")


def response(**overrides):
    payload = {
        "format": "markdown",
        "content": "# Title\n\n![](_page_0_Figure_1.jpeg)\n",
        "images": {"_page_0_Figure_1.jpeg": PNG},
        "metadata": {"table_of_contents": []},
        "config": {"output_format": "markdown"},
    }
    payload.update(overrides)
    return payload


# -- what must be refused ----------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    [
        "../escape.png",
        "sub/dir.png",
        "/etc/passwd.png",
        "..",
        ".",
        "notes.txt",
        "archive.tar.gz",
        "",
    ],
)
def test_image_names_that_are_not_plain_filenames_are_refused(name):
    with pytest.raises(MarkerError):
        parse_document_response(response(images={name: PNG}))


def test_a_sidecar_error_reports_what_marker_said(monkeypatch):
    """A bare status code sends the reader to a log for something already in hand."""
    import httpx

    from app.marker_client import MarkerClient

    failure = httpx.Response(
        500,
        json={"detail": "marker conversion failed: SpawnError: docker run failed: daemon not running"},
        request=httpx.Request("POST", "http://marker/convert-document"),
    )
    monkeypatch.setattr(httpx, "post", lambda *a, **k: failure)
    with pytest.raises(MarkerError) as raised:
        MarkerClient().convert_document(__import__("pathlib").Path(__file__))
    assert "docker run failed" in str(raised.value)
    assert "500" in str(raised.value)


def test_payload_shape_is_checked():
    with pytest.raises(MarkerError):
        parse_document_response(["not", "an", "object"])
    with pytest.raises(MarkerError):
        parse_document_response(response(content=None))
    with pytest.raises(MarkerError):
        parse_document_response(response(format="pdf"))
    with pytest.raises(MarkerError):
        parse_document_response(response(images={"a.png": "not base64!!"}))


def test_oversized_image_payloads_are_refused(monkeypatch):
    monkeypatch.setattr("app.marker_client.MAX_IMAGE_BYTES", 4)
    with pytest.raises(MarkerError):
        parse_document_response(response(images={"a.png": base64.b64encode(b"12345").decode()}))


# -- what must survive untouched ---------------------------------------------- #


def test_content_is_carried_through_character_for_character():
    awkward = "# H\n\n$$\\frac{a}{b}$$\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n<!-- html -->\n\\(x\\)\n"
    document = parse_document_response(response(content=awkward))
    assert document.content == awkward


def test_marker_keeps_its_own_image_filenames():
    document = parse_document_response(response())
    assert set(document.images) == {"_page_0_Figure_1.jpeg"}


def test_write_raw_writes_content_verbatim(tmp_path):
    # Carries a CRLF deliberately: text-mode writing would silently normalise it,
    # and "byte for byte" has to mean bytes.
    document = parse_document_response(response(content="a\r\nb\n\n{0}" + "-" * 48 + "\nc"))
    path = write_raw(document, tmp_path)
    assert path.read_bytes() == document.content.encode("utf-8")
    assert (tmp_path / "marker" / "images" / "_page_0_Figure_1.jpeg").read_bytes() == b"not really a png"


# -- the only edit made to marker's text --------------------------------------- #


def test_only_known_image_references_are_prefixed():
    markdown = "![a](known.png)\n![b](unknown.png)\n![c](https://example.com/x.png)\n"
    result, prefixed, unresolved = prefix_image_refs(markdown, {"known.png"})
    assert "![a](marker/images/known.png)" in result
    # Left exactly as marker wrote them rather than redirected or deleted.
    assert "![b](unknown.png)" in result
    assert "![c](https://example.com/x.png)" in result
    assert (prefixed, unresolved) == (1, 2)


def test_prefixing_changes_nothing_but_image_targets():
    markdown = "# Title\n\ntext ![a](known.png) more\n\n$$x$$\n"
    result, _, _ = prefix_image_refs(markdown, {"known.png"})
    assert result.replace("marker/images/known.png", "known.png") == markdown


# -- pagination ---------------------------------------------------------------- #


def test_pages_split_on_markers_own_separator():
    rule = "-" * 48
    markdown = f"one\n\n{{0}}{rule}\n\ntwo\n\n{{1}}{rule}\n\nthree"
    assert split_pages(markdown) == ["one", "two", "three"]


def test_a_document_without_separators_is_one_page():
    assert split_pages("just text") == ["just text"]


def test_the_preamble_before_the_first_separator_is_not_a_page():
    """marker writes its separator above each page, so real output starts with one."""
    rule = "-" * 48
    markdown = f"\n\n{{0}}{rule}\n\npage one\n\n{{1}}{rule}\n\npage two\n"
    assert split_pages(markdown) == ["page one", "page two"]


def test_a_blank_page_is_kept_so_the_pages_stay_in_step():
    rule = "-" * 48
    markdown = f"\n\n{{0}}{rule}\n\none\n\n{{1}}{rule}\n\n\n\n{{2}}{rule}\n\nthree"
    pages = split_pages(markdown)
    assert len(pages) == 3
    assert [is_empty(page) for page in pages] == [False, True, False]


def test_a_wholly_blank_document_keeps_its_pages_rather_than_collapsing():
    rule = "-" * 48
    markdown = f"\n\n{{0}}{rule}\n\n\n\n{{1}}{rule}\n\n"
    pages = split_pages(markdown)
    assert len(pages) == 2
    assert all(is_empty(page) for page in pages)


# -- recognising that marker returned nothing ---------------------------------- #


@pytest.mark.parametrize("page", ["", "   \n\n  ", "\n\n---\n\n", "|  |  |\n|--|--|"])
def test_pages_with_no_words_and_no_pictures_are_empty(page):
    assert is_empty(page) is True


@pytest.mark.parametrize(
    "page",
    [
        "a",
        "# Heading",
        "![](_page_0_Figure_1.png)",  # a figure alone is content, not emptiness
        "$$x$$",
    ],
)
def test_pages_with_words_or_pictures_are_not_empty(page):
    assert is_empty(page) is False


def test_separator_width_is_not_assumed():
    """Upstream has changed the rule's width before; a page break is a run of dashes."""
    assert len(split_pages("one\n\n5" + "-" * 24 + "\n\ntwo")) == 2
