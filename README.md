# PDF Editor

A full-featured desktop PDF viewer and editor built with Python, PyQt6 and PyMuPDF.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Features

### Viewer

- Open, view and navigate PDFs (continuous, single page and two-page layouts)
- Zoom in/out, Fit Width, Fit Page and Fit Visible
- Page thumbnails sidebar with drag & drop page reordering
- Bookmarks/outline panel with nested entries
- Comments panel listing every annotation in the document
- Password prompt for protected PDFs
- Five themes: light, dark, blue, high contrast and sepia

### Editing

- **Text markup** – select text and highlight, underline or strike through it
- **Sticky notes** – click to place a comment (`N`)
- **Freehand drawing** – ink annotations baked into the page
- **Stamps** – the standard Adobe rubber-stamp set (Approved, Draft, …)
- **Redaction** – mark areas and apply redactions
- **Insert text, images** and **crop pages**
- **Form filling** – detect and fill existing form fields (`F`)
- **Form creation** – add text fields and checkboxes
- **Search & replace** across the whole document (case sensitive option)

### Document operations

- Merge PDFs (drag to reorder before merging, `Ctrl+M`)
- Split a PDF into one file per page
- Extract pages / export pages as PNG, JPEG, TIFF or BMP (`Ctrl+E`)
- Rotate, delete and insert pages
- Page numbers and Bates numbering
- Text and image watermarks (rotatable, translucent)
- Flatten annotations and form fields into the page content
- Compress / optimise, batch processing
- Protect with a password (AES-256 + permissions) and remove protection
- Document properties: standard metadata **and** custom info-dictionary keys
- Extract images, export text, compare two PDFs

### Workflow

- Recent files menu (persisted with `QSettings`)
- Unsaved-changes warning on close
- Real printing (pages rendered to the selected printer)
- Keyboard shortcuts dialog (`Ctrl+K`)

## Installation

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Optional extras:

| Feature | Packages |
|---------|----------|
| OCR (text recognition) | `pip install pytesseract pillow` + [Tesseract](https://github.com/tesseract-ocr/tesseract) |
| PDF → Word | `pip install pdf2docx` |
| PDF → Excel | `pip install tabula-py` (needs a Java runtime) |

## Run

```bash
python main.py
```

## Tests and linting

```bash
pip install -r requirements-dev.txt

pytest tests/ -v            # unit + GUI smoke tests (Qt offscreen)
ruff check .                # style / correctness
mypy main.py                # static typing
```

The GUI tests run on Qt's `offscreen` platform, so no display is required. On a
bare Linux box install the usual Qt runtime libraries first
(`libgl1 libegl1 libxkbcommon-x11-0 libdbus-1-3 libxcb-*`).

## Build an executable

```bash
pip install pyinstaller
pyinstaller PDFEditor.spec
```

Output: `dist/PDFEditor.exe` (Windows), `dist/PDFEditor` (Linux/macOS).
The CI workflow builds all three platforms and attaches them to a GitHub
release for every `v*` tag.

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+O` | Open PDF |
| `Ctrl+S` / `Ctrl+Shift+S` | Save / Save As |
| `Ctrl+P` | Print |
| `Ctrl+F` / `F3` | Find / Find next |
| `Ctrl+H` | Search & replace |
| `Ctrl+I` | Document properties |
| `Ctrl+G` | Go to page |
| `PgUp` / `PgDn` | Previous / next page |
| `Home` / `End` | First / last page |
| `Ctrl++` / `Ctrl+-` | Zoom in / out |
| `Ctrl+1` / `Ctrl+2` | Fit width / fit page |
| `N` | Add note (annotation mode) |
| `S` | Select tool (markup) |
| `F` | Fill & sign |
| `Ctrl+M` | Merge PDFs |
| `Ctrl+E` | Export pages as images |
| `Ctrl+D` | Toggle dark mode |
| `Ctrl+T` | Theme selector |
| `Ctrl+K` | Keyboard shortcuts dialog |

## Architecture

`main.py` is intentionally a single-file application (simple distribution and
PyInstaller packaging):

- Module-level helpers – `parse_page_range`, `stamp_position`,
  `pdf_version_string`, `clone_document`, `load_translucent_pixmap`,
  `save_pixmap`, `get_custom_metadata` / `set_custom_metadata`
- `PDFPageWidget` – renders one page, handles selection, markup, drawing,
  redaction and stamps
- `ThumbnailSidebar` / `ThumbnailWidget` – page navigation and reordering
- `OutlineSidebar`, `CommentsSidebar`, `ToolsPanel`, `DocumentToolbar` – UI shell
- Dialogs – export, watermark, page numbers, Bates numbering, compress, batch,
  password, crop, form fields, document properties
- `PDFViewer` – the main window: menus, actions, document state
- Uses **PyMuPDF (fitz)** for rendering, text extraction, annotations, forms,
  encryption and page manipulation, and **PyQt6** for the cross-platform GUI

Operations that produce a new file (page numbers, watermarks, flatten,
compress, Bates numbering, OCR) work on an in-memory copy of the document, so
the file you are editing is never modified behind your back.

## Known limitations

- Digital signatures / certificate signing are not implemented (a signature
  image can be inserted with *Insert Image*).
- PDF → Word / Excel depend on optional third-party tools.
- Link annotations cannot be created in the editor yet.

## License

MIT
