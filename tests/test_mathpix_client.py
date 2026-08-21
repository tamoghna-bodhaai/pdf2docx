"""The Mathpix boundary: what is accepted from it, and what is refused.

Everything Mathpix sends arrives over HTTP from a service this codebase does not
control, so it is validated rather than trusted. These tests are about that
validation and about the small vocabulary the download state machine needs —
which failures mean "wait", which mean "never coming", and which mean the job is
over.

No network: `httpx` is replaced at every call site.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app import mathpix_client
from app.mathpix_client import (
    ALWAYS,
    BY_EXT,
    FORMATS,
    REQUIRED,
    Applied,
    MathpixClient,
    MathpixError,
    MathpixNotReady,
    MathpixUnsupported,
    conversion_formats,
    is_empty,
    parse_lines_json,
    parse_status_response,
    requested_formats,
    split_pages,
    write_raw,
)


def response(status: int, payload=None, *, content: bytes | None = None, method="GET") -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=payload if content is None else None,
        content=content,
        request=httpx.Request(method, "https://api.mathpix.com/files/v1/x"),
    )


def client(**overrides) -> MathpixClient:
    values = {
        "base_url": "https://api.mathpix.com",
        "app_id": "an-id",
        "app_key": "a-key",
        "connect_timeout": 1.0,
        "request_timeout": 5.0,
        "poll_interval": 0.0,
        "poll_timeout": 5.0,
    }
    values.update(overrides)
    return MathpixClient(**values)


# -- the format table ------------------------------------------------------------ #


def test_every_format_has_a_distinct_extension_and_a_media_type():
    exts = [entry.ext for entry in FORMATS]
    assert len(exts) == len(set(exts))
    assert all(entry.media_type for entry in FORMATS)


def test_the_formats_mathpix_produces_unasked_are_never_requested():
    assert set(ALWAYS) == {"mmd", "lines.json", "lines.mmd.json"}
    assert not any(BY_EXT[ext].requested for ext in ALWAYS)
    assert not set(ALWAYS) & set(conversion_formats(()))


def test_an_empty_selection_means_every_format():
    assert set(requested_formats(())) == {entry.ext for entry in FORMATS if entry.requested}
    assert requested_formats(()) == requested_formats(None)


def test_unknown_format_names_are_dropped_not_raised():
    """A stale entry in an .env file should not stop a conversion."""
    assert requested_formats(("docx", "not-a-format", "html")) == ("docx", "html")


def test_the_deliverable_is_always_requested():
    assert REQUIRED in requested_formats(("html",))
    assert REQUIRED in requested_formats(("not-a-format",))
    assert conversion_formats(("html",))["docx"] is True


def test_the_latex_archive_is_asked_about_under_the_name_mathpix_reports_it_by():
    """Mathpix serves it as `tex.zip` but reports its state as `latex`."""
    state = parse_status_response(
        {"status": "split", "formats": {"latex": "processing"}}, "f1"
    )
    assert state.format_state(BY_EXT["tex.zip"]) == "processing"


# -- status ---------------------------------------------------------------------- #


def test_a_status_response_is_validated():
    state = parse_status_response(
        {"status": "split", "percent_done": 42.5, "num_pages": 8,
         "num_pages_completed": 3, "formats": {"docx": "processing"}},
        "f1",
    )
    assert (state.status, state.percent_done, state.num_pages_completed) == ("split", 42.5, 3)
    assert not state.done and not state.failed


@pytest.mark.parametrize("payload", [
    [],
    "completed",
    {"status": "somehow-new"},
    {"status": ""},
    {"status": "completed", "formats": ["docx"]},
])
def test_a_malformed_status_is_refused(payload):
    with pytest.raises(MathpixError):
        parse_status_response(payload, "f1")


@pytest.mark.parametrize("value,expected", [(-5, 0.0), (500, 100.0), ("nonsense", 0.0), (None, 0.0)])
def test_a_percentage_outside_its_range_is_clamped(value, expected):
    state = parse_status_response({"status": "split", "percent_done": value}, "f1")
    assert state.percent_done == expected


def test_an_errored_document_stops_the_poll_with_what_mathpix_said(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response(
        200, {"status": "error", "error": "the pdf is encrypted"}))
    with pytest.raises(MathpixError, match="encrypted"):
        client().poll("f1")


def test_polling_reports_progress_as_it_goes(monkeypatch):
    states = iter([
        {"status": "split", "percent_done": 25.0, "num_pages": 4, "num_pages_completed": 1},
        {"status": "split", "percent_done": 75.0, "num_pages": 4, "num_pages_completed": 3},
        {"status": "completed", "percent_done": 100.0, "num_pages": 4, "num_pages_completed": 4},
    ])
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response(200, next(states)))
    seen = []
    final = client().poll("f1", lambda state: seen.append(state.num_pages_completed))
    assert seen == [1, 3, 4]
    assert final.status == "completed"


def test_a_document_that_never_finishes_gives_up_rather_than_polling_forever(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response(
        200, {"status": "split", "percent_done": 10.0}))
    with pytest.raises(MathpixError, match="did not finish"):
        client(poll_timeout=0.0).poll("f1")


# -- submission ------------------------------------------------------------------ #


def test_the_upload_sends_the_options_as_one_json_field(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.7 hello")
    sent = {}

    def post(url, **kwargs):
        sent.update(kwargs)
        sent["url"] = url
        return response(200, {"file_id": "f9"}, method="POST")

    monkeypatch.setattr(httpx, "post", post)
    assert client().submit(pdf, {"conversion_formats": {"docx": True}}) == "f9"
    assert sent["url"] == "https://api.mathpix.com/files/v1"
    assert json.loads(sent["data"]["options_json"]) == {"conversion_formats": {"docx": True}}
    assert "file" in sent["files"]
    assert sent["headers"]["app_id"] == "an-id"
    assert sent["headers"]["app_key"] == "a-key"


def test_the_same_document_and_options_retry_under_the_same_idempotency_key(tmp_path, monkeypatch):
    """A retry after a dropped connection must not be billed as a second job."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.7 hello")
    keys = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: (
        keys.append(kw["headers"]["Idempotency-Key"]), response(200, {"file_id": "f9"}, method="POST"))[1])

    client().submit(pdf, {"a": 1})
    client().submit(pdf, {"a": 1})
    assert keys[0] == keys[1]

    client().submit(pdf, {"a": 2})
    assert keys[2] != keys[0]

    pdf.write_bytes(b"%PDF-1.7 different")
    client().submit(pdf, {"a": 1})
    assert keys[3] != keys[0]


def test_an_upload_without_a_file_id_is_refused(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response(200, {"ok": True}, method="POST"))
    with pytest.raises(MathpixError, match="did not return a file_id"):
        client().submit(pdf)


def test_a_rejected_upload_reports_what_mathpix_called_it(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response(
        415, {"error_id": "unsupported_format"}, method="POST"))
    with pytest.raises(MathpixError, match="unsupported_format"):
        client().submit(pdf)


# -- downloading ----------------------------------------------------------------- #


@pytest.mark.parametrize("status,payload", [
    (404, {"error_id": "format_not_ready"}),
    (409, {"error_id": "whatever"}),
])
def test_a_format_still_converting_means_wait(status, payload, monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response(status, payload))
    with pytest.raises(MathpixNotReady):
        client().fetch("f1", "docx")


@pytest.mark.parametrize("status,payload", [
    (415, {"error_id": "unsupported_format"}),
    (400, {"error_id": "unsupported_format"}),
])
def test_a_format_never_requested_means_never_coming(status, payload, monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response(status, payload))
    with pytest.raises(MathpixUnsupported):
        client().fetch("f1", "xlsx")


def test_a_download_returns_the_bytes_exactly(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response(200, content=b"PK\x03\x04\x00binary"))
    assert client().fetch("f1", "docx") == b"PK\x03\x04\x00binary"


def test_fetch_all_waits_for_a_format_that_is_not_ready_yet(monkeypatch):
    attempts = {"docx": 0}

    def get(url, **kwargs):
        if url.endswith(".docx"):
            attempts["docx"] += 1
            if attempts["docx"] < 3:
                return response(404, {"error_id": "format_not_ready"})
            return response(200, content=b"the docx")
        return response(200, content=b"other")

    monkeypatch.setattr(httpx, "get", get)
    got = {}
    missing = client().fetch_all("f1", ["docx", "mmd"], lambda ext, data: got.update({ext: data}))
    assert missing == {}
    assert got["docx"] == b"the docx"
    assert attempts["docx"] == 3


def test_one_missing_format_does_not_cost_the_others(monkeypatch):
    def get(url, **kwargs):
        if url.endswith(".xlsx"):
            return response(415, {"error_id": "unsupported_format"})
        return response(200, content=b"fine")

    monkeypatch.setattr(httpx, "get", get)
    got = {}
    missing = client().fetch_all("f1", ["docx", "xlsx", "mmd"], lambda ext, d: got.update({ext: d}))
    assert set(got) == {"docx", "mmd"}
    assert "xlsx" in missing


def test_an_operational_failure_for_an_optional_export_fails_collection(monkeypatch):
    def get(url, **kwargs):
        if url.endswith(".xlsx"):
            return response(500, {"error_id": "temporary_backend_failure"})
        return response(200, content=b"fine")

    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(MathpixError, match="temporary_backend_failure"):
        client().fetch_all("f1", ["docx", "mmd", "xlsx"], lambda ext, data: None)


def test_missing_preview_markdown_fails_collection(monkeypatch):
    def get(url, **kwargs):
        if url.endswith(".mmd"):
            return response(415, {"error_id": "unsupported_format"})
        return response(200, content=b"fine")

    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(MathpixError, match="no .mmd"):
        client().fetch_all("f1", ["docx", "mmd"], lambda ext, data: None)


def test_a_missing_deliverable_is_the_one_failure_that_stops_the_job(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response(
        415, {"error_id": "unsupported_format"}))
    with pytest.raises(MathpixError, match="no .docx"):
        client().fetch_all("f1", ["docx"], lambda ext, data: None)


def test_formats_still_converting_when_time_runs_out_are_recorded(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **k: (
        response(404, {"error_id": "format_not_ready"}) if url.endswith(".xlsx")
        else response(200, content=b"fine")))
    got = {}
    missing = client(poll_timeout=0.0).fetch_all(
        "f1", ["docx", "xlsx"], lambda ext, d: got.update({ext: d}))
    assert "docx" in got
    assert "gave up waiting" in missing["xlsx"]


# -- images ---------------------------------------------------------------------- #


@pytest.mark.parametrize("target", [
    "https://cdn.mathpix.com/cropped/../../../etc/passwd",
    "https://cdn.mathpix.com/cropped/note.txt",
    "https://cdn.mathpix.com/cropped/",
    "https://cdn.mathpix.com/cropped/archive.zip",
])
def test_an_image_url_that_is_not_a_plain_picture_name_is_refused(target):
    with pytest.raises(MathpixError):
        mathpix_client._safe_image_name(target, 0)


def test_a_crop_url_keeps_its_own_filename_and_drops_the_query():
    name = mathpix_client._safe_image_name(
        "https://cdn.mathpix.com/cropped/2024_abc.jpg?height=200&width=400", 0)
    assert name == "2024_abc.jpg"


def test_images_are_downloaded_and_the_references_repointed(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response(200, content=b"\x89PNG-ish"))
    markdown = "before ![](https://cdn.mathpix.com/cropped/a.png?w=1) after"
    text, applied = client().download_images(markdown, tmp_path)
    assert "](mathpix/images/a.png)" in text
    assert applied.images_downloaded == 1
    assert (tmp_path / "mathpix" / "images" / "a.png").read_bytes() == b"\x89PNG-ish"


def test_an_image_that_will_not_download_is_left_exactly_as_mathpix_wrote_it(tmp_path, monkeypatch):
    """Silently deleting or redirecting it would misrepresent what Mathpix did."""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response(500, {"error": "gone"}))
    markdown = "![](https://cdn.mathpix.com/cropped/a.png?w=1)"
    text, applied = client().download_images(markdown, tmp_path)
    assert text == markdown
    assert applied.images_downloaded == 0
    assert applied.images_failed == 1


def test_a_reference_that_is_already_local_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("nothing to download"))
    markdown = "![](figures/local.png)"
    text, applied = client().download_images(markdown, tmp_path)
    assert text == markdown
    assert applied.images_unresolved == 1


def test_the_same_image_is_downloaded_once_however_often_it_is_referenced(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "get", lambda url, **k: (
        calls.append(url), response(200, content=b"png"))[1])
    markdown = "![](https://cdn.mathpix.com/cropped/a.png) ![](https://cdn.mathpix.com/cropped/a.png)"
    text, applied = client().download_images(markdown, tmp_path)
    assert len(calls) == 1
    assert text.count("mathpix/images/a.png") == 2
    assert applied.images_downloaded == 1


# -- pages ----------------------------------------------------------------------- #


def test_pages_split_on_mathpix_own_break():
    assert split_pages("one\n\n\\pagebreak\n\ntwo") == ["one", "two"]


def test_a_document_without_a_break_is_one_page():
    assert split_pages("just the one") == ["just the one"]


def test_the_text_before_the_first_break_is_a_page_not_a_preamble():
    """Unlike marker, Mathpix writes its separator between pages, not above each."""
    assert split_pages("first\n\n\\pagebreak\n\nsecond\n\n\\pagebreak\n\nthird") == [
        "first", "second", "third"]


def test_a_blank_page_is_kept_so_the_pages_stay_in_step():
    assert split_pages("one\n\n\\pagebreak\n\n\n\n\\pagebreak\n\nthree") == ["one", "", "three"]


@pytest.mark.parametrize("page", ["", "   ", "\n\n", "$$ $$", "---"])
def test_pages_with_no_words_and_no_pictures_are_empty(page):
    assert is_empty(page)


@pytest.mark.parametrize("page", ["a word", "![](mathpix/images/a.png)", "1"])
def test_pages_with_words_or_pictures_are_not_empty(page):
    assert not is_empty(page)


# -- storage --------------------------------------------------------------------- #


def test_write_raw_writes_bytes_verbatim(tmp_path):
    """A file whose whole purpose is to be the unedited copy cannot differ."""
    data = b"line one\r\nline two\r\n\x00\xff"
    path = write_raw("docx", data, tmp_path)
    assert path == tmp_path / "mathpix" / "document.docx"
    assert path.read_bytes() == data


def test_write_raw_keeps_a_compound_extension_whole(tmp_path):
    assert write_raw("tex.zip", b"z", tmp_path).name == "document.tex.zip"
    assert write_raw("lines.json", b"{}", tmp_path).name == "document.lines.json"


def test_lines_json_is_validated_before_it_is_read():
    assert parse_lines_json('{"pages": []}') == {"pages": []}
    with pytest.raises(MathpixError):
        parse_lines_json("not json")
    with pytest.raises(MathpixError):
        parse_lines_json("[1, 2]")


def test_the_applied_record_states_what_was_done():
    assert Applied(images_downloaded=2, pages=3, paginated=True).as_dict() == {
        "images_downloaded": 2,
        "images_failed": 0,
        "images_unresolved": 0,
        "pages": 3,
        "paginated": True,
    }
