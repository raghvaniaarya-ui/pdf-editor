"""GUI level regression tests for :mod:`main`.

They run against Qt's offscreen platform, so no display is required.
"""

import pymupdf as fitz
import pytest
from PyQt6.QtWidgets import QDialog, QFileDialog, QLineEdit, QMessageBox

import main


@pytest.fixture
def dialogs(monkeypatch):
    """Silence message boxes and pretend the user pressed OK."""

    class Recorder:
        def __init__(self):
            self.messages = []

    recorder = Recorder()

    def info(_parent, title, text, *args, **kwargs):
        recorder.messages.append(("info", title, text))

    def warning(_parent, title, text, *args, **kwargs):
        recorder.messages.append(("warning", title, text))

    def critical(_parent, title, text, *args, **kwargs):
        recorder.messages.append(("critical", title, text))

    monkeypatch.setattr(QMessageBox, "information", staticmethod(info))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(warning))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(critical))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(QMessageBox, "about", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QDialog, "exec", lambda self: QDialog.DialogCode.Accepted
    )
    return recorder


@pytest.fixture
def viewer(qtbot, sample_pdf, dialogs):
    """A viewer with ``sample_pdf`` loaded."""
    window = main.PDFViewer()
    qtbot.addWidget(window)
    window.load_document(sample_pdf)
    yield window
    window.dirty = False
    doc, window.doc = window.doc, None
    if doc is not None and not getattr(doc, "is_closed", True):
        doc.close()


class TestViewerStartup:
    def test_view_constructs(self, qtbot, dialogs):
        window = main.PDFViewer()
        qtbot.addWidget(window)
        assert window.doc is None
        assert window.windowTitle() == main.APP_NAME

    def test_load_document(self, viewer, sample_pdf):
        assert viewer.doc is not None
        assert viewer.doc.page_count == 3
        assert sample_pdf.endswith("sample.pdf")
        assert "sample.pdf" in viewer.windowTitle()

    def test_status_and_page_info(self, viewer):
        assert len(viewer.doc) == 3
        viewer.go_to_page(2)
        assert viewer.current_page == 1
        assert "2/3" in viewer.page_label.text()

    def test_zoom_controls(self, viewer):
        viewer.zoom_in()
        assert viewer.zoom > 1.0
        viewer.zoom_out()
        viewer.set_zoom_fit_width()
        assert 0.1 <= viewer.zoom <= 5.0

    def test_view_modes(self, viewer):
        for mode in ("single", "continuous", "two"):
            viewer.set_view_mode(mode)
            assert viewer.view_mode == mode

    def test_dark_mode_toggle_does_not_crash(self, viewer):
        """Regression: ``toggle_dark_mode`` used a missing ``dark_action``."""
        viewer.toggle_dark_mode(True)
        assert viewer.current_theme == "dark"
        assert viewer.dark_action is not None
        assert viewer.dark_action.isChecked()
        viewer.toggle_dark_mode(False)
        assert viewer.current_theme == "light"

    def test_theme_selector_and_shortcuts_dialog(self, viewer):
        """Regression: the shortcuts dialog used a removed Qt enum."""
        viewer.show_theme_selector()
        viewer.show_shortcuts_dialog()


class TestPageOperations:
    def test_delete_page_is_applied_in_memory(self, viewer):
        """Regression: edits were discarded by reloading the file."""
        original = len(viewer.doc)
        viewer.delete_page()
        assert len(viewer.doc) == original - 1
        assert viewer.dirty

    def test_insert_page_is_applied_in_memory(self, viewer):
        original = len(viewer.doc)
        viewer.insert_page()
        assert len(viewer.doc) == original + 1
        assert viewer.dirty

    def test_rotate_page(self, viewer):
        viewer.rotate_right()
        assert viewer.doc[viewer.current_page].rotation == 90
        viewer.rotate_left()
        assert viewer.doc[viewer.current_page].rotation == 0

    def test_reorder_pages_keeps_file_path(self, viewer, sample_pdf):
        """Regression: reordering went through a temp file and rewrote the path."""
        viewer.reorder_pages([2, 0, 1])
        assert viewer.file_path == sample_pdf
        assert viewer.doc.page_count == 3
        assert "Page 3 title" in viewer.doc[0].get_text()

    def test_crop_pages(self, viewer, monkeypatch):
        monkeypatch.setattr(
            main.CropDialog, "get_margins", lambda self: (10, 10, 10, 10)
        )
        viewer.crop_pages()
        crop = viewer.doc[0].cropbox
        assert crop.x0 == pytest.approx(10)
        assert crop.y0 == pytest.approx(10)


class TestAnnotationsAndForms:
    def test_page_widget_adds_annotations(self, viewer):
        widget = viewer.pages_layout.itemAt(0).widget()
        rect = fitz.Rect(60, 60, 300, 90)
        widget.add_highlight(rect)
        widget.add_underline(rect)
        widget.add_strikeout(rect)
        widget.add_stamp(fitz.Point(200, 300))
        types = [annot.type[1] for annot in viewer.doc[0].annots()]
        assert "Highlight" in types
        assert "Underline" in types
        assert "StrikeOut" in types
        assert "Stamp" in types
        assert viewer.dirty

    def test_ink_drawing(self, viewer):
        """Regression: ``add_ink_annot`` was called with the wrong structure."""
        from PyQt6.QtCore import QPointF

        widget = viewer.pages_layout.itemAt(0).widget()
        widget.draw_paths = [
            [QPointF(10, 10), QPointF(40, 40), QPointF(80, 20)]
        ]
        widget.apply_drawings()
        assert any(annot.type[1] == "Ink" for annot in viewer.doc[0].annots())

    def test_form_field_creation_and_filling(self, viewer, monkeypatch):
        monkeypatch.setattr(
            main.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Customer", True))
        )
        viewer.add_text_field()
        widgets = list(viewer.doc[0].widgets())
        assert [widget.field_name for widget in widgets] == ["Customer"]

        # fill_form uses the dialog values on the matching widgets
        monkeypatch.setattr(
            main.FormFieldDialog,
            "get_values",
            lambda self: {"Customer": "Ada Lovelace"},
        )
        viewer.fill_form()
        assert next(iter(viewer.doc[0].widgets())).field_value == "Ada Lovelace"

    def test_fill_form_without_fields(self, viewer, dialogs):
        viewer.fill_form()
        assert any("No Forms" in message[1] for message in dialogs.messages)


class TestDocumentOperations:
    def test_document_properties_apply(self, viewer, tmp_path):
        """Regression: ``doc.pdf_version()`` and custom metadata keys crashed."""
        dialog = main.DocumentPropertiesDialog(viewer.doc, viewer)
        dialog.title_edit.setText("Quarterly report")
        dialog.author_edit.setText("Ada")
        dialog.custom_tree.addTopLevelItem(
            main.QTreeWidgetItem(["CaseId", "ABC-42"])
        )
        dialog.apply_changes()

        assert viewer.doc.metadata["title"] == "Quarterly report"
        assert main.get_custom_metadata(viewer.doc) == {"CaseId": "ABC-42"}
        assert dialog.pdf_version_label.text().startswith("PDF ")

        path = tmp_path / "properties.pdf"
        viewer.doc.save(str(path))
        reopened = fitz.open(str(path))
        try:
            assert reopened.metadata["title"] == "Quarterly report"
            assert main.get_custom_metadata(reopened) == {"CaseId": "ABC-42"}
        finally:
            reopened.close()

    def test_save_file_marks_clean(self, viewer, tmp_path):
        viewer.doc[0].insert_text((72, 200), "saved change", fontsize=10)
        viewer.file_path = str(tmp_path / "out.pdf")
        viewer.save_file()
        assert not viewer.dirty
        reopened = fitz.open(viewer.file_path)
        try:
            assert "saved change" in reopened[0].get_text()
        finally:
            reopened.close()

    def test_save_file_falls_back_to_full_write(self, viewer, tmp_path):
        """Regression: incremental save failed for in-memory documents."""
        viewer.reorder_pages([1, 0, 2])  # rebuilds the document in memory
        viewer.file_path = str(tmp_path / "reordered.pdf")
        viewer.save_file()
        assert not viewer.dirty
        assert (tmp_path / "reordered.pdf").exists()

    def test_split_pdf(self, viewer, monkeypatch, tmp_path):
        """Regression: getExistingDirectory was unpacked like a tuple."""
        output = tmp_path / "split"
        output.mkdir()
        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(output))
        )
        viewer.split_pdf()
        assert len(list(output.glob("sample_*.pdf"))) == 3

    def test_export_images_all_formats(self, viewer, monkeypatch, tmp_path):
        """Regression: TIFF/BMP exports raised inside PyMuPDF."""
        for image_format in ("PNG", "JPEG", "TIFF", "BMP"):
            monkeypatch.setattr(
                main.ExportDialog,
                "get_settings",
                lambda self, f=image_format: {
                    "range": "1-2",
                    "format": f,
                    "dpi": 72,
                    "quality": 90,
                },
            )
            monkeypatch.setattr(
                QFileDialog, "getExistingDirectory",
                staticmethod(lambda *a, **k: str(tmp_path)),
            )
            viewer.export_images()

        suffixes = {path.suffix for path in tmp_path.glob("page_*")}
        assert suffixes == {".png", ".jpg", ".tiff", ".bmp"}

    def test_watermark_text(self, viewer, monkeypatch, tmp_path):
        """Regression: ``insert_text(opacity=..., rotate=45)`` raised."""
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (str(tmp_path / "watermark.pdf"), "")),
        )
        settings = {
            "type": "text",
            "text": "CONFIDENTIAL",
            "fontsize": 40,
            "rotation": 45,
            "color": (1.0, 0.0, 0.0),
            "image_path": "",
            "opacity": 0.3,
        }
        monkeypatch.setattr(main.WatermarkDialog, "get_settings", lambda self: settings)
        viewer.add_watermark()

        out = fitz.open(str(tmp_path / "watermark.pdf"))
        try:
            assert "CONFIDENTIAL" in out[0].get_text()
            # the original document must not be modified
            assert "CONFIDENTIAL" not in viewer.doc[0].get_text()
        finally:
            out.close()

    def test_watermark_image(self, viewer, monkeypatch, tmp_path, image_file):
        settings = {
            "type": "image",
            "text": "",
            "fontsize": 40,
            "rotation": 0,
            "color": (1.0, 0.0, 0.0),
            "image_path": image_file,
            "opacity": 0.4,
        }
        out_doc = viewer._watermarked_copy(settings)
        try:
            assert len(out_doc[0].get_images()) == 1
        finally:
            out_doc.close()

    def test_page_numbers(self, viewer, monkeypatch, tmp_path):
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (str(tmp_path / "numbers.pdf"), "")),
        )
        monkeypatch.setattr(
            main.PageNumberDialog,
            "get_settings",
            lambda self: {
                "position": "bottom_center",
                "prefix": "Page",
                "suffix": "of {total}",
                "fontsize": 10,
                "color": (0.0, 0.0, 0.0),
            },
        )
        viewer.add_page_numbers()
        out = fitz.open(str(tmp_path / "numbers.pdf"))
        try:
            text = out[2].get_text()
            assert "Page 3 of 3" in text
        finally:
            out.close()

    def test_bates_numbering(self, viewer, monkeypatch, tmp_path):
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (str(tmp_path / "bates.pdf"), "")),
        )
        monkeypatch.setattr(
            main.BatesNumberingDialog,
            "get_settings",
            lambda self: {
                "prefix": "DOC-",
                "suffix": "",
                "start_number": 1,
                "number_digits": 4,
                "font_size": 10,
                "color": (0.0, 0.0, 0.0),
                "position": "bottom_right",
                "page_range": "1-2",
            },
        )
        viewer.add_bates_numbering()
        out = fitz.open(str(tmp_path / "bates.pdf"))
        try:
            assert "DOC-0001" in out[0].get_text()
            assert "DOC-0002" in out[1].get_text()
            assert "DOC-" not in out[2].get_text()
        finally:
            out.close()

    def test_flatten_pdf(self, viewer, monkeypatch, tmp_path):
        viewer.doc[0].add_text_annot(fitz.Point(100, 100), "note")
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (str(tmp_path / "flat.pdf"), "")),
        )
        viewer.flatten_pdf()
        out = fitz.open(str(tmp_path / "flat.pdf"))
        try:
            assert not list(out[0].annots())
            assert "Page 1 title" in out[0].get_text()
        finally:
            out.close()

    def test_compress_pdf(self, viewer, monkeypatch, tmp_path):
        """Regression: ``os`` was never imported in compress_pdf_advanced."""
        target = tmp_path / "small.pdf"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
        )
        viewer.compress_pdf_advanced()
        assert target.exists()


class TestSearch:
    def test_find_text_and_next(self, viewer):
        viewer.find_text("lazy dog")
        assert len(viewer._search_hits) == 3
        assert viewer.current_page == 0
        viewer.find_next()
        assert viewer.current_page == 1

    def test_search_and_replace_all(self, viewer):
        old_rects = viewer.doc[0].search_for("lazy")
        assert old_rects
        viewer.search_text = QLineEdit("lazy")
        viewer.replace_text = QLineEdit("sleepy")
        viewer._replace_all()

        page = viewer.doc[0]
        text = page.get_text()
        assert "sleepy" in text
        assert "lazy" not in text
        # the replacement is drawn where the old word was
        new_word = next(w for w in page.get_text("words") if w[4] == "sleepy")
        assert abs(new_word[0] - old_rects[0].x0) < 6
        assert abs(new_word[1] - old_rects[0].y0) < 6


class TestToolWiring:
    """Every button in the tool panel must map to a real viewer method."""

    def test_tools_panel_actions_exist(self, viewer):
        import inspect
        import re

        source = inspect.getsource(main.ToolsPanel)
        actions = {
            match.strip('\"),')
            for match in re.findall(r'"[a-z_]+"\),', source)
        }
        missing = {action for action in actions if not hasattr(viewer, action)}
        assert not missing

    def test_dropdown_radio_and_signature_fields(self, viewer, monkeypatch):
        answers = iter(
            [
                ("Priority", True),
                ("Low, High", True),
                ("Choice1", True),
                ("Signature1", True),
            ]
        )
        monkeypatch.setattr(
            main.QInputDialog, "getText", staticmethod(lambda *a, **k: next(answers))
        )
        viewer.add_dropdown()
        viewer.add_radio_button()
        viewer.add_signature_field()

        fields = {widget.field_name: widget.field_type_string for widget in viewer.doc[0].widgets()}
        assert fields == {
            "Priority": "ComboBox",
            "Choice1": "RadioButton",
            "Signature1": "Signature",
        }


class TestDialogs:
    @pytest.mark.parametrize(
        "dialog_class,args",
        [
            (main.CropDialog, ()),
            (main.PageNumberDialog, ()),
            (main.CompressDialog, ()),
            (main.BatchProcessDialog, ()),
            (main.MergeSplitDialog, ()),
            (main.PasswordDialog, ()),
            (main.ExportDialog, (3,)),
            (main.WatermarkDialog, ()),
            (main.BatesNumberingDialog, (3,)),
        ],
    )
    def test_dialog_constructs(self, qtbot, dialogs, dialog_class, args):
        dialog = dialog_class(*args)
        qtbot.addWidget(dialog)
        assert dialog.windowTitle()

    def test_password_permissions_are_built(self, qtbot, dialogs):
        dialog = main.PasswordDialog()
        qtbot.addWidget(dialog)
        dialog.user_pwd.setText("secret")
        dialog.user_pwd_confirm.setText("secret")
        user, _owner, permissions = dialog.get_passwords()
        assert user == "secret"
        assert permissions > 0

    def test_empty_password_is_rejected_by_protect(self, viewer, dialogs):
        viewer.protect_pdf()
        assert any("password" in message[2].lower() for message in dialogs.messages)

    def test_export_dialog_validates_range(self, qtbot, dialogs):
        dialog = main.ExportDialog(3)
        qtbot.addWidget(dialog)
        dialog.page_range.setText("1-999")
        assert main.parse_page_range(dialog.page_range.text(), 3) == [0, 1, 2]
