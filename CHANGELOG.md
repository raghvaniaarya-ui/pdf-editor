# Changelog

## 1.1.0

Code review pass: fixes for features that crashed on the PyMuPDF 1.24+/2.x API,
data-loss bugs, repository hygiene and CI.

### Fixed – crashes / broken features

- **Watermark** – `Page.insert_text(opacity=...)` and `rotate=45` do not exist;
  text watermarks now use `fitz.TextWriter` with opacity and a rotation matrix,
  and image watermarks get a real alpha channel.
- **Stamps** – `Page.add_stamp_annot()` expects one of the `fitz.STAMP_*`
  constants, not a string (it raised `FileNotFoundError`).
- **Underline / strikethrough** – use `add_underline_annot` / `add_strikeout_annot`
  instead of drawing a red highlight.
- **Freehand drawing** – `add_ink_annot()` needs a list of strokes; also
  `Annot.set_border_width()` was renamed to `set_border(width=...)`.
- **Export pages as images** – TIFF and BMP cannot be written by PyMuPDF; they
  are now written through Qt, and the JPEG quality setting is honoured.
- **Document properties** – `Document.pdf_version()` does not exist (use
  `metadata["format"]`), and custom metadata keys cannot be passed to
  `set_metadata()`; they are read/written through the PDF info dictionary.
- **Advanced compression** – `os` was never imported.
- **OCR** – `PIL.Image` was referenced without being imported.
- **Ctrl+D dark mode** – crashed because `self.dark_action` was never created.
- **Keyboard shortcuts dialog** – `Qt.ItemFlag.ItemIsTristate` no longer exists.
- **Split PDF** – `getExistingDirectory()` returns a string, not a tuple, so
  splitting always raised.
- **Saving** – incremental saves failed for documents rebuilt in memory; a full
  write is used as fallback.
- **Password protection** – `fitz.PDF_PERM_FILL_FORMS` / `PDF_PERM_PRINT_HIGH`
  do not exist (they are `PDF_PERM_FORM` / `PDF_PERM_PRINT_HQ`).
- **Forms** – checkbox fields were misdetected, and adding a field / dropdown /
  radio button / signature field now works.

### Fixed – data loss / correctness

- Deleting or inserting a page reloaded the file from disk, silently discarding
  the change; page edits are now applied in the modified document.
- Reordering pages went through a temp file and changed `file_path`; it now
  rebuilds the document in memory and keeps the original path.
- Page thumbnails always emitted the identity order after a drag & drop.
- Rotating/cropping used the rotated page rectangle; crop boxes are now derived
  from the media box.
- Cropping is available from the UI (margin dialog) instead of being a stub.
- `render_pages()` reconnected the scroll-bar signal on every reload, stacking
  duplicate slots.
- Flattening was a no-op; it now bakes annotations, stamps and widgets.
- Search & replace is implemented (redact + redraw), with a proper
  case-sensitive option; find next cycles through all matches.
- View modes (single / continuous / two page) and the toolbar buttons now do
  something; printing renders the document to the printer.
- Unsaved changes are tracked (`dirty` flag), shown in the window title, and
  the app asks before closing or opening another file.
- Watermark/page number/Bates/flatten/compress/OCR always operate on a copy so
  the edited document is not modified.
- `main()` shows an error dialog instead of calling `input()` in a windowed app.
- Removed duplicated `WatermarkDialog` / `ExportDialog` classes and a block of
  duplicate `PDFViewer` methods that shadowed the fixed implementations.
- Bookmark trees keep their nesting; theme previews are readable; the tools
  panel no longer contains duplicated groups or buttons without a handler.

### Added

- Rotatable text watermarks, dropdown/radio/signature form fields, crop dialog,
  go-to-page action, navigation shortcuts, `Ctrl+G`/`F3`/`Ctrl+I` actions.
- Page range validation in the export and Bates dialogs.
- `pyproject.toml` with ruff / mypy / pytest configuration, `requirements-dev.txt`.
- 80 tests: PDF helpers, metadata round trips, pixmap formats, and GUI
  regression tests that run on Qt's offscreen platform.
- CI now installs the Qt runtime libraries (the test job used to crash on
  `import PyQt6`), builds with `PDFEditor.spec` on every push, and produces
  release artifacts for `v*` tags.

### Changed

- Runtime requirements: `PyMuPDF>=1.24.0` (the code imports the `pymupdf`
  module name), `PyQt6>=6.6.0`.
- `PDFEditor.spec`: windowed (`console=False`), no UPX, Pillow no longer
  excluded (needed for optional OCR).
- Removed the stale `build/`, `dist/` and `__pycache__/` files from version
  control (they were 128 MB of build output); `.gitignore` now covers the
  tooling caches and virtual environments.
