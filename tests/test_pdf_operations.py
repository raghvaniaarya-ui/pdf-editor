"""Tests for the PDF helpers in ``main`` that do not need a window."""

import pymupdf as fitz
import pytest

import main


@pytest.fixture
def document():
    doc = fitz.open()
    for _ in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), "Test Page", fontsize=12)
    yield doc
    doc.close()


class TestParsePageRange:
    """`parse_page_range` is shared by export, Bates numbering and the dialogs."""

    def test_single_range(self):
        assert main.parse_page_range("1-3", 5) == [0, 1, 2]

    def test_comma_separated(self):
        assert main.parse_page_range("1,3,5", 5) == [0, 2, 4]

    def test_mixed(self):
        assert main.parse_page_range("2-4,6", 10) == [1, 2, 3, 5]

    def test_clamped_to_page_count(self):
        assert main.parse_page_range("1-10", 3) == [0, 1, 2]

    def test_whitespace_and_duplicates(self):
        assert main.parse_page_range(" 2 - 3 , 2 ", 5) == [1, 2]

    def test_reversed_range(self):
        assert main.parse_page_range("4-2", 5) == [1, 2, 3]

    @pytest.mark.parametrize("spec", ["abc", "1-", "1,x", "3--4"])
    def test_invalid_input(self, spec):
        with pytest.raises(ValueError):
            main.parse_page_range(spec, 10)


class TestStampPosition:
    """Positioning used for page numbers and Bates numbers."""

    def test_center_is_centred(self):
        rect = fitz.Rect(0, 0, 600, 800)
        x, _y = main.stamp_position(rect, "bottom_center", 10, "12345")
        text_width = fitz.get_text_length("12345", fontname="helv", fontsize=10)
        assert x == pytest.approx((600 - text_width) / 2)

    def test_right_is_flush_right(self):
        rect = fitz.Rect(0, 0, 600, 800)
        x, _y = main.stamp_position(rect, "top_right", 10, "12345")
        assert x > 600 / 2

    def test_bottom_and_top_rows(self):
        rect = fitz.Rect(0, 0, 600, 800)
        _, bottom_y = main.stamp_position(rect, "bottom_left", 10, "1")
        _, top_y = main.stamp_position(rect, "top_left", 10, "1")
        assert bottom_y > top_y


class TestMetadata:
    """Custom metadata lives outside ``Document.set_metadata``'s whitelist."""

    def test_custom_metadata_round_trip(self, document):
        main.set_custom_metadata(document, {"CaseId": "ABC-42", "Reviewer": "Ada"})
        assert main.get_custom_metadata(document) == {
            "CaseId": "ABC-42",
            "Reviewer": "Ada",
        }

    def test_custom_metadata_replaces_removed_keys(self, document):
        main.set_custom_metadata(document, {"A": "1", "B": "2"})
        main.set_custom_metadata(document, {"A": "1"})
        assert main.get_custom_metadata(document) == {"A": "1"}

    def test_custom_metadata_keeps_standard_keys(self, document):
        document.set_metadata({"title": "Report"})
        main.set_custom_metadata(document, {"CaseId": "X"})
        assert document.metadata["title"] == "Report"
        assert main.get_custom_metadata(document) == {"CaseId": "X"}

    def test_custom_metadata_survives_save(self, document, tmp_path):
        main.set_custom_metadata(document, {"CaseId": "ABC-42"})
        path = tmp_path / "meta.pdf"
        document.save(str(path))
        reopened = fitz.open(str(path))
        try:
            assert main.get_custom_metadata(reopened) == {"CaseId": "ABC-42"}
        finally:
            reopened.close()

    def test_pdf_version_string(self, document):
        assert main.pdf_version_string(document).startswith("PDF ")

    def test_metadata_without_info_dictionary(self):
        doc = fitz.open()
        doc.new_page()
        assert main.get_custom_metadata(doc) == {}
        doc.close()


class TestPixmapSaving:
    """TIFF and BMP cannot be written by PyMuPDF itself."""

    @pytest.mark.parametrize("suffix", ["png", "jpg", "tiff", "bmp"])
    def test_save_pixmap(self, qapp, document, tmp_path, suffix):
        path = tmp_path / f"page.{suffix}"
        pix = document[0].get_pixmap(alpha=False)
        main.save_pixmap(pix, path, suffix, 85)
        assert path.exists()
        assert path.stat().st_size > 0


class TestCorePdfOperations:
    """The basic operations the editor relies on."""

    def test_pdf_creation_and_text(self, sample_pdf):
        doc = fitz.open(sample_pdf)
        try:
            assert doc.page_count == 3
            assert "Page 1 title" in doc[0].get_text()
            assert "lazy dog" in doc[2].get_text()
        finally:
            doc.close()

    def test_navigation(self, sample_pdf):
        doc = fitz.open(sample_pdf)
        try:
            assert len(doc) == 3
            assert doc[0].number == 0
            assert doc[2].number == 2
        finally:
            doc.close()

    def test_incremental_save(self, sample_pdf, tmp_path):
        path = tmp_path / "incremental.pdf"
        path.write_bytes(open(sample_pdf, "rb").read())
        doc = fitz.open(str(path))
        doc[0].insert_text((72, 200), "Added text", fontsize=10)
        doc.save(str(path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()

        reopened = fitz.open(str(path))
        try:
            assert "Added text" in reopened[0].get_text()
        finally:
            reopened.close()

    def test_merge(self, sample_pdf):
        first = fitz.open(sample_pdf)
        second = fitz.open(sample_pdf)
        merged = fitz.open()
        try:
            merged.insert_pdf(first)
            merged.insert_pdf(second)
            assert merged.page_count == 6
        finally:
            merged.close()
            first.close()
            second.close()

    def test_split(self, sample_pdf, tmp_path):
        doc = fitz.open(sample_pdf)
        try:
            for i in range(doc.page_count):
                single = fitz.open()
                single.insert_pdf(doc, from_page=i, to_page=i)
                single.save(str(tmp_path / f"page_{i + 1}.pdf"))
                single.close()
        finally:
            doc.close()
        assert len(list(tmp_path.glob("page_*.pdf"))) == 3

    def test_export_page_as_image(self, sample_pdf, tmp_path):
        doc = fitz.open(sample_pdf)
        try:
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        finally:
            doc.close()
        target = tmp_path / "page.png"
        pix.save(str(target))
        assert target.stat().st_size > 0

    def test_zoom_matrix(self):
        assert fitz.Matrix(2.0, 2.0).a == 2.0
        assert fitz.Matrix(1.5, 1.5).d == 1.5

    def test_rect_operations(self):
        rect = fitz.Rect(0, 0, 100, 100)
        other = fitz.Rect(50, 50, 150, 150)
        intersection = rect & other
        assert (intersection.width, intersection.height) == (50, 50)


class TestPdfFeatures:
    """Features that used to raise at runtime."""

    def test_stamp_constants_are_ints(self):
        assert all(isinstance(value, int) for value in main.STAMP_TYPES.values())
        assert len(main.STAMP_TYPES) >= 10

    def test_stamp_annotation_can_be_added(self, document):
        annot = document[0].add_stamp_annot(fitz.Rect(10, 10, 100, 50), main.STAMP_TYPES["Approved"])
        assert annot is not None
        assert annot.type[1] == "Stamp"

    def test_watermark_pixmap_gets_alpha(self, image_file):
        pix = main.load_translucent_pixmap(image_file, 0.3)
        assert pix.alpha == 1
        assert pix.n == 4

    def test_clone_document_is_independent(self, document):
        clone = main.clone_document(document)
        try:
            clone[0].insert_text((72, 300), "only in clone")
            assert "only in clone" in clone[0].get_text()
            assert "only in clone" not in document[0].get_text()
        finally:
            clone.close()

    def test_bake_removes_annotations(self, document):
        document[0].add_text_annot(fitz.Point(100, 100), "note")
        assert list(document[0].annots())
        document.bake()
        assert not list(document[0].annots())

    def test_encrypt_and_unlock_round_trip(self, document, tmp_path):
        protected = tmp_path / "protected.pdf"
        document.save(
            str(protected),
            encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw="secret",
            owner_pw="owner",
        )
        doc = fitz.open(str(protected))
        try:
            assert doc.needs_pass
            assert doc.authenticate("secret")
        finally:
            doc.close()
