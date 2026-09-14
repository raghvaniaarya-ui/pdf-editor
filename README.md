# PDF Editor

A desktop PDF viewer and editor built with Python, PyQt6, and PyMuPDF.

## Features
- Open, view, and navigate PDFs
- Zoom in/out, fit to width
- Save and Save As
- Annotation mode (click to add notes)
- Text selection mode
- Keyboard shortcuts

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
- `Ctrl+O` - Open PDF
- `Ctrl+S` - Save
- `Ctrl+Shift+S` - Save As
- `PgUp` / `PgDn` - Previous/Next page
- `Ctrl++` / `Ctrl+-` - Zoom in/out
- `A` - Toggle annotation mode
- `S` - Toggle text selection mode

## Architecture
- `main.py` - Main application with PDFViewer class
- `PDFPageWidget` - Renders individual PDF pages
- Uses PyMuPDF (fitz) for PDF rendering and manipulation
- PyQt6 for cross-platform GUI

## Roadmap
- [ ] Text highlighting and annotations
- [ ] Form filling
- [ ] Page reordering (drag & drop)
- [ ] Merge/split PDFs
- [ ] Digital signatures
- [ ] OCR integration
- [ ] Dark mode