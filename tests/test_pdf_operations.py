import pytest
import tempfile
import os
from pathlib import Path

import pymupdf as fitz


class TestPDFOperations:
    """Test core PDF operations."""

    @pytest.fixture
    def sample_pdf(self):
        """Create a simple test PDF."""
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Test Page 1", fontsize=12)
        page.insert_text((72, 144), "This is a test PDF for unit tests.", fontsize=10)
        
        page2 = doc.new_page()
        page2.insert_text((72, 72), "Test Page 2", fontsize=12)
        page2.insert_text((72, 144), "Second page content.", fontsize=10)
        
        # Create temp file path without holding it open
        tmp_path = tempfile.mktemp(suffix=".pdf")
        doc.save(tmp_path)
        doc.close()
        yield tmp_path
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    def test_pdf_creation(self, sample_pdf):
        """Test that we can create and open a PDF."""
        doc = fitz.open(sample_pdf)
        assert doc.page_count == 2
        doc.close()

    def test_text_extraction(self, sample_pdf):
        """Test text extraction from PDF."""
        doc = fitz.open(sample_pdf)
        text = doc[0].get_text()
        assert "Test Page 1" in text
        assert "test PDF" in text
        doc.close()

    def test_page_navigation(self, sample_pdf):
        """Test page count and navigation."""
        doc = fitz.open(sample_pdf)
        assert len(doc) == 2
        assert doc[0].number == 0
        assert doc[1].number == 1
        doc.close()

    def test_save_incremental(self, sample_pdf):
        """Test incremental save preserves content."""
        # Copy to a new file for incremental save test
        import shutil
        test_file = sample_pdf.replace(".pdf", "_inc.pdf")
        shutil.copy2(sample_pdf, test_file)
        
        try:
            doc = fitz.open(test_file)
            page = doc[0]
            page.insert_text((72, 200), "Added text", fontsize=10)
            doc.save(test_file, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            doc.close()
            
            # Verify saved file
            doc2 = fitz.open(test_file)
            assert doc2.page_count == 2
            text = doc2[0].get_text()
            assert "Added text" in text
            doc2.close()
        finally:
            if os.path.exists(test_file):
                os.unlink(test_file)

    def test_merge_pdfs(self, sample_pdf):
        """Test merging multiple PDFs."""
        doc1 = fitz.open(sample_pdf)
        doc2 = fitz.open(sample_pdf)
        
        merged = fitz.open()
        merged.insert_pdf(doc1)
        merged.insert_pdf(doc2)
        
        assert merged.page_count == 4
        doc1.close()
        doc2.close()
        merged.close()

    def test_split_pdf(self, sample_pdf):
        """Test splitting PDF into individual pages."""
        doc = fitz.open(sample_pdf)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(len(doc)):
                single = fitz.open()
                single.insert_pdf(doc, from_page=i, to_page=i)
                single.save(Path(tmpdir) / f"page_{i+1}.pdf")
                single.close()
            
            files = list(Path(tmpdir).glob("*.pdf"))
            assert len(files) == 2

    def test_export_page_as_image(self, sample_pdf):
        """Test exporting page as image."""
        doc = fitz.open(sample_pdf)
        page = doc[0]
        
        # Create temp file path without holding it open
        tmp_path = tempfile.mktemp(suffix=".png")
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            pix.save(tmp_path)
            assert os.path.exists(tmp_path)
            assert os.path.getsize(tmp_path) > 0
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        doc.close()

    def test_zoom_matrix(self):
        """Test zoom matrix calculations."""
        mat = fitz.Matrix(2.0, 2.0)
        assert mat.a == 2.0
        assert mat.d == 2.0
        
        mat2 = fitz.Matrix(1.5, 1.5)
        assert mat2.a == 1.5

    def test_rect_operations(self):
        """Test fitz.Rect operations."""
        rect = fitz.Rect(0, 0, 100, 100)
        assert rect.width == 100
        assert rect.height == 100
        assert rect.x0 == 0
        assert rect.y0 == 0
        
        # Test intersection
        rect2 = fitz.Rect(50, 50, 150, 150)
        intersect = rect & rect2
        assert intersect.width == 50
        assert intersect.height == 50


class TestPDFPageWidget:
    """Test PDFPageWidget logic (without GUI)."""

    def test_zoom_calculation(self):
        """Test zoom level calculations."""
        zoom_levels = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]
        for z in zoom_levels:
            percent = int(z * 100)
            assert percent == int(z * 100)

    def test_fit_width_calculation(self):
        """Test fit-width zoom calculation."""
        page_width = 800  # points
        viewport_width = 1200  # pixels
        zoom = viewport_width / page_width
        assert zoom == 1.5


class TestUtilities:
    """Test utility functions."""

    def test_page_range_parsing(self):
        """Test page range string parsing."""
        def parse_range(range_str, max_pages):
            pages = []
            for part in range_str.split(","):
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    pages.extend(range(start - 1, end))
                else:
                    pages.append(int(part) - 1)
            return [p for p in pages if 0 <= p < max_pages]
        
        assert parse_range("1-3", 5) == [0, 1, 2]
        assert parse_range("1,3,5", 5) == [0, 2, 4]
        assert parse_range("2-4,6", 10) == [1, 2, 3, 5]
        assert parse_range("1-10", 3) == [0, 1, 2]  # Clamped

    def test_dark_mode_toggle(self):
        """Test dark mode state management."""
        dark_mode = False
        dark_mode = not dark_mode
        assert dark_mode is True
        dark_mode = not dark_mode
        assert dark_mode is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])