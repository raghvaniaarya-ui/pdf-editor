# PDF Editor

A full-featured desktop PDF viewer and editor built with Python, PyQt6, and PyMuPDF.

## Features

### Viewer
- Open, view, and navigate PDFs
- Zoom in/out (50%-300%), Fit Width
- Page thumbnails sidebar for quick navigation
- Keyboard shortcuts for all actions
- Dark/Light mode toggle

### Editor
- **Text Annotations** - Click to add sticky notes (Press `N`)
- **Text Selection & Highlighting** - Select text, right-click to highlight/underline (Press `S`)
- **Form Filling** - Detect and fill PDF form fields (Press `F`)
- **Save / Save As** - Incremental saves preserve annotations

### Document Operations
- **Merge PDFs** - Combine multiple PDFs with drag-to-reorder (Ctrl+M)
- **Split PDF** - Extract each page as separate file
- **Export Pages as Images** - PNG/JPEG/TIFF at custom DPI (Ctrl+E)

## Installation

```bash
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

## Build Executable

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "PDFEditor" main.py
```

Output: `dist/PDFEditor.exe`

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+O` | Open PDF |
| `Ctrl+S` | Save |
| `Ctrl+Shift+S` | Save As |
| `PgUp` / `PgDn` | Previous/Next page |
| `Ctrl++` / `Ctrl+-` | Zoom in/out |
| `N` | Toggle annotation mode (add notes) |
| `S` | Toggle select mode (highlight/underline) |
| `F` | Fill form fields |
| `Ctrl+M` | Merge PDFs |
| `Ctrl+E` | Export pages as images |
| `Ctrl+D` | Toggle dark mode |

## Architecture

- `main.py` - Single-file application with all features
- `PDFPageWidget` - Renders individual PDF pages, handles annotations
- `ThumbnailSidebar` - Page thumbnail navigation dock
- Uses **PyMuPDF (fitz)** for PDF rendering, text extraction, annotations, forms
- **PyQt6** for cross-platform GUI (Windows/Linux/macOS)

## Tech Highlights for Resume

- Cross-platform desktop app with native feel (PyQt6)
- PDF manipulation: rendering, text extraction, annotations, form filling
- Document operations: merge, split, export
- Custom widget painting (selection rect, thumbnails)
- Dockable UI components
- Theme system (dark/light mode)
- Packaging with PyInstaller

## Roadmap

- [ ] OCR integration (Tesseract)
- [ ] Digital signatures
- [ ] Page reordering (drag & drop in thumbnails)
- [ ] Bookmarks/outline editor
- [ ] Redaction tool
- [ ] Print support
- [ ] Plugin system