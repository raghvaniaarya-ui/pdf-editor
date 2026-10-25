"""Shared pytest fixtures.

The Qt platform is forced to ``offscreen`` so the GUI tests also run on a
headless CI machine (no X server needed).
"""

import os

import pymupdf as fitz
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def sample_pdf(tmp_path):
    """A three page PDF with searchable text."""
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    for number in range(1, 4):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {number} title", fontsize=14)
        page.insert_text((72, 100), "The quick brown fox jumps over the lazy dog", fontsize=10)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def image_file(tmp_path):
    """A small PNG used for image watermarks and image insertion."""
    path = tmp_path / "mark.png"
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
    pix.clear_with(128)
    pix.save(str(path))
    return str(path)
