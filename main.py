"""PDF Editor -- a PyQt6 + PyMuPDF desktop PDF viewer/editor.

Run with ``python main.py``.  See README.md for features, shortcuts and
development instructions.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pymupdf as fitz
from PyQt6.QtCore import (
    QMimeData,
    QPoint,
    QRectF,
    QSettings,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDrag,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "PDF Editor"
APP_VERSION = "1.1.0"

#: Display name -> PyMuPDF rubber-stamp constant.  The order matches the
#: standard Adobe stamp set.
STAMP_TYPES = {
    "Approved": fitz.STAMP_Approved,
    "As Is": fitz.STAMP_AsIs,
    "Confidential": fitz.STAMP_Confidential,
    "Departmental": fitz.STAMP_Departmental,
    "Draft": fitz.STAMP_Draft,
    "Experimental": fitz.STAMP_Experimental,
    "Expired": fitz.STAMP_Expired,
    "Final": fitz.STAMP_Final,
    "For Comment": fitz.STAMP_ForComment,
    "For Public Release": fitz.STAMP_ForPublicRelease,
    "Not Approved": fitz.STAMP_NotApproved,
    "Not For Public Release": fitz.STAMP_NotForPublicRelease,
    "Sold": fitz.STAMP_Sold,
    "Top Secret": fitz.STAMP_TopSecret,
}

#: Keys that belong to the standard PDF info dictionary.  Everything else in
#: the info dictionary is treated as user defined ("custom") metadata.
INFO_STANDARD_KEYS = {
    "author",
    "creationdate",
    "creator",
    "encryption",
    "format",
    "keywords",
    "moddate",
    "producer",
    "subject",
    "title",
    "trapped",
}


def parse_page_range(spec: str, page_count: int) -> list[int]:
    """Convert a 1-based page range string into 0-based page indices.

    ``"1-3,5"`` becomes ``[0, 1, 2, 4]``.  Out of range values are dropped,
    duplicates are removed and the input order is preserved.  An empty or
    malformed entry raises :class:`ValueError`.
    """
    pages: list[int] = []
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not match:
            raise ValueError(f"Invalid page range entry: {part!r}")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) is not None else start
        if start > end:
            start, end = end, start
        pages.extend(range(start - 1, end))
    result: list[int] = []
    for page in pages:
        if 0 <= page < page_count and page not in result:
            result.append(page)
    return result


def stamp_position(
    page_rect: fitz.Rect,
    position: str,
    fontsize: float,
    text: str,
    margin: float = 30,
) -> tuple[float, float]:
    """Return the (x, y) baseline for text stamped on a page.

    ``position`` is one of ``bottom_center``, ``bottom_right``,
    ``bottom_left``, ``top_center``, ``top_right``, ``top_left``.
    """
    width = fitz.get_text_length(text, fontname="helv", fontsize=fontsize)
    if position.endswith("center"):
        x = max(margin, (page_rect.width - width) / 2)
    elif position.endswith("right"):
        x = max(margin, page_rect.width - width - margin)
    else:
        x = margin
    if position.startswith("bottom"):
        y = page_rect.height - margin
    else:
        y = margin + fontsize
    return x, y


def pdf_version_string(doc: fitz.Document) -> str:
    """Return a human readable PDF version, e.g. ``"PDF 1.7"``."""
    return doc.metadata.get("format") or "Unknown"


def clone_document(doc: fitz.Document) -> fitz.Document:
    """Return an independent in-memory copy of ``doc``.

    Used by operations that must not mutate the document the user is
    currently editing (page numbers, watermarks, flattening, ...).
    """
    return fitz.open("pdf", doc.tobytes())


def _info_xref(doc: fitz.Document, create: bool = False) -> int | None:
    """Return the xref of the document info dictionary."""
    kind, value = doc.xref_get_key(-1, "Info")
    if kind == "xref":
        return int(value.split()[0])
    if not create:
        return None
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.xref_set_key(-1, "Info", f"{xref} 0 R")
    return xref


def get_custom_metadata(doc: fitz.Document) -> dict[str, str]:
    """Return the non-standard keys stored in the document info dictionary."""
    xref = _info_xref(doc)
    if xref is None:
        return {}
    metadata: dict[str, str] = {}
    for key in doc.xref_get_keys(xref):
        if key.lower() in INFO_STANDARD_KEYS:
            continue
        kind, value = doc.xref_get_key(xref, key)
        if kind == "null":
            continue
        if kind in {"string", "name", "text"}:
            value = value.strip()
            if value.startswith("(") and value.endswith(")"):
                value = value[1:-1]
        metadata[key] = value
    return metadata


def set_custom_metadata(doc: fitz.Document, metadata: dict[str, str]) -> None:
    """Replace the custom metadata of ``doc`` with ``metadata``."""
    xref = _info_xref(doc, create=True)
    if xref is None:  # pragma: no cover - defensive
        raise RuntimeError("Could not create the PDF info dictionary")
    existing = {key.lower(): key for key in doc.xref_get_keys(xref)}
    wanted = {key.lower(): (key, value) for key, value in metadata.items()}

    for key in list(doc.xref_get_keys(xref)):
        lower = key.lower()
        if lower in INFO_STANDARD_KEYS or lower in wanted:
            continue
        # PyMuPDF has no dictionary-key removal API; a null value is the
        # documented way of removing an entry from a PDF dictionary.
        doc.xref_set_key(xref, key, "null")

    for lower, (key, value) in wanted.items():
        doc.xref_set_key(xref, existing.get(lower, key), fitz.get_pdf_str(value))


def load_translucent_pixmap(path: str | Path, opacity: float) -> fitz.Pixmap:
    """Load an image and apply a uniform ``opacity`` (0-1) as its alpha channel."""
    source = fitz.Pixmap(str(path))
    rgb = source if source.n == 3 else fitz.Pixmap(fitz.csRGB, source)
    alpha = bytes([max(1, min(255, int(opacity * 255)))]) * (rgb.width * rgb.height)
    return fitz.Pixmap(fitz.csRGB, rgb.width, rgb.height, rgb.samples + alpha, 1)


def save_pixmap(pix: fitz.Pixmap, path: str | Path, fmt: str, quality: int = 90) -> None:
    """Save ``pix`` as ``path``.

    PyMuPDF can only write PNG/JPG itself, so TIFF and BMP are written through
    Qt (which ships the required image format plugins).
    """
    fmt = fmt.lower().lstrip(".")
    if fmt == "png":
        pix.save(str(path))
        return
    image = QImage(
        pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
    ).copy()
    qt_format = {"jpg": "JPEG", "jpeg": "JPEG", "tif": "TIFF", "tiff": "TIFF"}.get(
        fmt, fmt.upper()
    )
    if not image.save(str(path), qt_format, quality):
        raise OSError(f"Could not write image: {path}")



class PDFPageWidget(QWidget):
    """Widget to display a single PDF page with zoom and annotation support."""
    
    annotation_added = pyqtSignal(object, object)  # page_num, annot_data
    text_selected = pyqtSignal(object, object)  # page_num, text
    render_requested = pyqtSignal(int)  # page_num
    
    def __init__(self, page: fitz.Page, page_num: int, zoom: float = 1.0):
        super().__init__()
        self.page = page
        self.page_num = page_num
        self.zoom = zoom
        self.setMinimumSize(100, 100)
        self.setMouseTracking(True)
        
        # Annotation state
        self.annot_mode = False
        self.select_mode = False
        self.redact_mode = False
        self.draw_mode = False
        self.stamp_mode = False
        self.selection_start = None
        self.selection_end = None
        
        # Freehand drawing
        self.draw_path: list = []
        self.draw_paths: list = []  # List of completed paths
        
        # Redaction
        self.redact_rects: list = []  # List of redaction rectangles
        self.redact_preview = None  # Current redaction preview rect
        
        # Highlights storage
        self.highlights: list = []

        # Active markup type ("highlight"/"underline"/"strikeout") or None for
        # the little "what do you want?" menu, plus the selected stamp.
        self.markup_kind: str | None = None
        self.stamp_type = "Approved"
        
        # Lazy rendering - one timer per widget, reused for every zoom change
        self._pixmap = None
        self._rendered_zoom = None
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render_pixmap)
    
    def _render_pixmap(self):
        """Render page to pixmap at current zoom."""
        if not self.page:
            return
        mat = fitz.Matrix(self.zoom, self.zoom)
        pix = self.page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(img)
        self._rendered_zoom = self.zoom
        self.setFixedSize(self._pixmap.size())
    
    def ensure_rendered(self):
        """Render if not already rendered at current zoom."""
        if self._pixmap is None or abs((self._rendered_zoom or 0.0) - self.zoom) > 0.01:
            self._render_pixmap()
            self.update()
    
    def set_zoom(self, zoom: float):
        self.zoom = zoom
        # Defer rendering to avoid blocking the UI
        self._render_timer.start(0)
    
    def set_annot_mode(self, enabled: bool):
        self.annot_mode = enabled
        self.select_mode = False
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
    
    def set_select_mode(self, enabled: bool):
        self.select_mode = enabled
        self.annot_mode = False
        self.redact_mode = False
        self.draw_mode = False
        self.stamp_mode = False
        if not enabled:
            self.markup_kind = None
        self.setCursor(Qt.CursorShape.IBeamCursor if enabled else Qt.CursorShape.ArrowCursor)
    
    def set_redact_mode(self, enabled: bool):
        self.redact_mode = enabled
        self.annot_mode = False
        self.select_mode = False
        self.draw_mode = False
        self.stamp_mode = False
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
    
    def set_draw_mode(self, enabled: bool):
        self.draw_mode = enabled
        self.annot_mode = False
        self.select_mode = False
        self.redact_mode = False
        self.stamp_mode = False
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
    
    def set_stamp_mode(self, enabled: bool):
        self.stamp_mode = enabled
        self.annot_mode = False
        self.select_mode = False
        self.redact_mode = False
        self.draw_mode = False
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
    
    def _add_text_markup(self, rect: fitz.Rect, kind: str, color: tuple, opacity: float):
        """Add a highlight/underline/strikeout annotation and repaint."""
        add = {
            "highlight": self.page.add_highlight_annot,
            # the runtime API exists, it is simply missing from the stubs
            "underline": self.page.add_underline_annot,
            "strikeout": self.page.add_strikeout_annot,
        }[kind]
        annot = add(rect)
        if annot:
            annot.set_colors(stroke=color)
            annot.set_opacity(opacity)
            annot.update()
            self.highlights.append((rect, color, opacity))
            self._render_pixmap()
            self.update()
            self.annotation_added.emit(
                self.page_num,
                {"type": kind, "rect": (rect.x0, rect.y0, rect.x1, rect.y1)},
            )

    def add_highlight(self, rect: fitz.Rect, color: tuple = (1, 1, 0), opacity: float = 0.3):
        """Add a highlight annotation to the page."""
        self._add_text_markup(rect, "highlight", color, opacity)

    def add_underline(self, rect: fitz.Rect, color: tuple = (1, 0, 0), opacity: float = 0.8):
        """Add an underline annotation to the page."""
        self._add_text_markup(rect, "underline", color, opacity)

    def add_strikeout(self, rect: fitz.Rect, color: tuple = (1, 0, 0), opacity: float = 0.8):
        """Add a strike-out annotation to the page."""
        self._add_text_markup(rect, "strikeout", color, opacity)
    
    def add_text_annotation(self, point: fitz.Point, text: str, color: tuple = (1, 1, 0)):
        """Add a text annotation (sticky note)."""
        annot = self.page.add_text_annot(point, text)
        if annot:
            annot.set_colors(stroke=color)
            annot.update()
            self._render_pixmap()
            self.update()
    
    def add_redaction(self, rect: fitz.Rect):
        """Add a redaction annotation - marks area for removal."""
        # Add redaction annot (will be applied on save)
        annot = self.page.add_redact_annot(rect)
        if annot:
            # Visual indicator - fill with black
            annot.set_colors(fill=(0, 0, 0))
            annot.update()
            self.redact_rects.append(rect)
            self._render_pixmap()
            self.update()
            self.annotation_added.emit(self.page_num, {"type": "redact", "rect": (rect.x0, rect.y0, rect.x1, rect.y1)})
    
    def apply_drawings(self):
        """Bake freehand drawings into the page as annotations."""
        if not self.draw_paths:
            return
        
        for path in self.draw_paths:
            if len(path) < 2:
                continue
            # Convert to PDF coordinates and add as ink annotation.
            # PyMuPDF expects a list of strokes, each stroke a list of points.
            stroke = [(p.x() / self.zoom, p.y() / self.zoom) for p in path]
            annot = self.page.add_ink_annot([stroke])
            if annot:
                annot.set_colors(stroke=(0, 0, 1))  # Blue ink
                annot.set_border(width=2)
                annot.update()
        
        self.draw_paths.clear()
        self._render_pixmap()
        self.update()
    
    def add_stamp(self, point: fitz.Point, stamp_type: str = "Approved"):
        """Add a rubber stamp annotation."""
        rect = fitz.Rect(point.x - 60, point.y - 30, point.x + 60, point.y + 30)
        stamp = STAMP_TYPES.get(stamp_type, fitz.STAMP_Approved)
        annot = self.page.add_stamp_annot(rect, stamp)
        if annot:
            annot.update()
            self._render_pixmap()
            self.update()
            self.annotation_added.emit(self.page_num, {"type": "stamp", "point": (point.x, point.y), "stamp": stamp_type})
    
    def mousePressEvent(self, event):
        if self.annot_mode and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            pdf_x = pos.x() / self.zoom
            pdf_y = pos.y() / self.zoom
            
            text, ok = QInputDialog.getText(self, "Add Note", "Enter annotation text:")
            if ok and text:
                point = fitz.Point(pdf_x, pdf_y)
                self.add_text_annotation(point, text)
                self.annotation_added.emit(self.page_num, {"type": "text", "point": (pdf_x, pdf_y), "text": text})
        
        elif self.select_mode and event.button() == Qt.MouseButton.LeftButton:
            self.selection_start = event.position()
            self.selection_end = event.position()
            self.update()
        
        elif self.redact_mode and event.button() == Qt.MouseButton.LeftButton:
            self.selection_start = event.position()
            self.redact_preview = QRectF(self.selection_start, self.selection_start)
            self.update()
        
        elif self.draw_mode and event.button() == Qt.MouseButton.LeftButton:
            self.draw_path = [event.position()]
            self.update()
        
        elif self.stamp_mode and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            pdf_x = pos.x() / self.zoom
            pdf_y = pos.y() / self.zoom
            self.add_stamp(fitz.Point(pdf_x, pdf_y), self.stamp_type)
    
    def mouseMoveEvent(self, event):
        if self.select_mode and self.selection_start and event.buttons() & Qt.MouseButton.LeftButton:
            self.selection_end = event.position()
            self.update()
        
        elif self.redact_mode and self.selection_start and event.buttons() & Qt.MouseButton.LeftButton:
            self.redact_preview = QRectF(self.selection_start, event.position()).normalized()
            self.update()
        
        elif self.draw_mode and self.draw_path and event.buttons() & Qt.MouseButton.LeftButton:
            self.draw_path.append(event.position())
            self.update()
    
    def mouseReleaseEvent(self, event):
        if self.select_mode and self.selection_start and self.selection_end:
            x1 = min(self.selection_start.x(), self.selection_end.x()) / self.zoom
            y1 = min(self.selection_start.y(), self.selection_end.y()) / self.zoom
            x2 = max(self.selection_start.x(), self.selection_end.x()) / self.zoom
            y2 = max(self.selection_start.y(), self.selection_end.y()) / self.zoom
            
            rect = fitz.Rect(x1, y1, x2, y2)
            text = self.page.get_text("text", clip=rect)
            
            if text.strip():
                self.text_selected.emit(self.page_num, text.strip())

                kind = self.markup_kind
                if kind is None:
                    menu = QMenu(self)
                    highlight_action = menu.addAction("Highlight")
                    underline_action = menu.addAction("Underline")
                    strikeout_action = menu.addAction("Strikethrough")
                    action = menu.exec(self.mapToGlobal(event.pos().toPoint()))
                    if action == highlight_action:
                        kind = "highlight"
                    elif action == underline_action:
                        kind = "underline"
                    elif action == strikeout_action:
                        kind = "strikeout"

                if kind == "highlight":
                    self.add_highlight(rect, (1, 1, 0), 0.3)
                elif kind == "underline":
                    self.add_underline(rect)
                elif kind == "strikeout":
                    self.add_strikeout(rect)
            
            self.selection_start = None
            self.selection_end = None
            self.update()
        
        elif self.redact_mode and self.selection_start and self.redact_preview:
            # Convert to PDF coordinates and add redaction
            rect = self.redact_preview
            x1 = rect.left() / self.zoom
            y1 = rect.top() / self.zoom
            x2 = rect.right() / self.zoom
            y2 = rect.bottom() / self.zoom
            
            pdf_rect = fitz.Rect(x1, y1, x2, y2)
            self.add_redaction(pdf_rect)
            
            self.selection_start = None
            self.redact_preview = None
            self.update()
        
        elif self.draw_mode and self.draw_path:
            if len(self.draw_path) > 1:
                self.draw_paths.append(self.draw_path.copy())
                self.apply_drawings()
            self.draw_path = []
            self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        if self._pixmap:
            painter.drawPixmap(0, 0, self._pixmap)
        else:
            # Show placeholder while rendering
            painter.fillRect(self.rect(), QColor(240, 240, 240))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"Page {self.page_num + 1}\n(Rendering...)")
        
        if self.select_mode and self.selection_start and self.selection_end:
            painter.setPen(QPen(QColor(0, 120, 215), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(QColor(0, 120, 215, 50)))
            rect = QRectF(self.selection_start, self.selection_end).normalized()
            painter.drawRect(rect)
        
        # Redaction preview
        if self.redact_mode and self.redact_preview:
            painter.setPen(QPen(QColor(255, 0, 0), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(QColor(255, 0, 0, 80)))
            painter.drawRect(self.redact_preview)
        
        # Current draw path
        if self.draw_mode and self.draw_path and len(self.draw_path) > 1:
            painter.setPen(QPen(QColor(0, 0, 255), 2, Qt.PenStyle.SolidLine))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            for i in range(len(self.draw_path) - 1):
                painter.drawLine(self.draw_path[i], self.draw_path[i + 1])
        
        # Completed draw paths (already rendered to pixmap via apply_drawings)
        # They're baked into the pixmap now


class ThumbnailWidget(QWidget):
    """Thumbnail widget for page navigation with drag & drop reordering."""
    
    page_clicked = pyqtSignal(int)
    drag_started = pyqtSignal(int)  # page_num
    drop_requested = pyqtSignal(int, int)  # source_page, target_page
    
    def __init__(self, page: fitz.Page, page_num: int):
        super().__init__()
        # page_num is the position in the sidebar, page_index the position in
        # the document.  They only differ after a drag & drop reorder.
        self.page_num = page_num
        self.page_index = page_num
        self.setFixedSize(120, 160)
        self.setToolTip(f"Page {page_num + 1}")
        self.setAcceptDrops(True)
        
        # Render thumbnail
        mat = fitz.Matrix(0.3, 0.3)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        self.pixmap = QPixmap.fromImage(img)
        
        self._drag_start_pos = None
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self.page_clicked.emit(self.page_num)
    
    def mouseMoveEvent(self, event):
        if self._drag_start_pos and event.buttons() & Qt.MouseButton.LeftButton:
            distance = (event.pos() - self._drag_start_pos).manhattanLength()
            if distance > QApplication.startDragDistance():
                drag = QDrag(self)
                mime_data = QMimeData()
                mime_data.setData("application/x-pdf-page", str(self.page_num).encode())
                drag.setMimeData(mime_data)
                drag.setPixmap(self.pixmap.scaled(60, 80, Qt.AspectRatioMode.KeepAspectRatio))
                drag.setHotSpot(QPoint(30, 40))
                drag.exec(Qt.DropAction.MoveAction)
                self._drag_start_pos = None
    
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-pdf-page"):
            event.acceptProposedAction()
            self.setStyleSheet("border: 2px solid #0078d7;")
    
    def dragLeaveEvent(self, event):
        self.setStyleSheet("")
    
    def dropEvent(self, event):
        self.setStyleSheet("")
        if event.mimeData().hasFormat("application/x-pdf-page"):
            source_page = int(event.mimeData().data("application/x-pdf-page").data().decode())
            if source_page != self.page_num:
                self.drop_requested.emit(source_page, self.page_num)
            event.acceptProposedAction()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.pixmap)
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.drawText(5, 15, f"Page {self.page_num + 1}")


class ThumbnailSidebar(QDockWidget):
    """Sidebar with page thumbnails."""
    
    page_selected = pyqtSignal(int)
    pages_reordered = pyqtSignal(list)  # new page order list
    
    def __init__(self, parent=None):
        super().__init__("Thumbnails", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setMinimumWidth(140)
        self.setMaximumWidth(160)
        
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setIconSize(QSize(120, 160))
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setSpacing(10)
        self.setWidget(self.list_widget)
        
        self._page_widgets = []  # Store widget references
    
    def update_thumbnails(self, doc: fitz.Document):
        self.list_widget.clear()
        self._page_widgets = []
        for i in range(len(doc)):
            page = doc[i]
            item = QListWidgetItem()
            widget = ThumbnailWidget(page, i)
            widget.page_clicked.connect(self.page_selected.emit)
            widget.drop_requested.connect(self._on_drop_requested)
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
            self._page_widgets.append(widget)
    
    def _on_drop_requested(self, source_page: int, target_page: int):
        """Handle drag & drop reordering of pages."""
        if not (0 <= source_page < len(self._page_widgets)):
            return
        if not (0 <= target_page < len(self._page_widgets)):
            return

        # Move the widget (page_index travels with it)
        widget = self._page_widgets.pop(source_page)
        self._page_widgets.insert(target_page, widget)

        # Rebuild the list and refresh the displayed page numbers
        self.list_widget.clear()
        for i, w in enumerate(self._page_widgets):
            item = QListWidgetItem()
            w.page_num = i
            a = w.page_index + 1
            w.setToolTip(f"Page {a}")
            item.setSizeHint(w.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, w)

        # Emit the new document order (original page indices)
        self.pages_reordered.emit([w.page_index for w in self._page_widgets])
    
    def set_current_page(self, page_num: int):
        self.list_widget.setCurrentRow(page_num)
    
    def get_page_order(self):
        """Return the current document order (0-based page indices)."""
        return [w.page_index for w in self._page_widgets]


class CropDialog(QDialog):
    """Dialog asking for crop margins (in points)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Crop Pages")
        self.resize(320, 240)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.top_spin = QSpinBox()
        self.right_spin = QSpinBox()
        self.bottom_spin = QSpinBox()
        self.left_spin = QSpinBox()
        for spin in (self.top_spin, self.right_spin, self.bottom_spin, self.left_spin):
            spin.setRange(0, 500)
            spin.setSuffix(" pt")
        form.addRow("Top:", self.top_spin)
        form.addRow("Right:", self.right_spin)
        form.addRow("Bottom:", self.bottom_spin)
        form.addRow("Left:", self.left_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_margins(self) -> tuple[int, int, int, int]:
        """Return (top, right, bottom, left) margins in points."""
        return (
            self.top_spin.value(),
            self.right_spin.value(),
            self.bottom_spin.value(),
            self.left_spin.value(),
        )


class MergeSplitDialog(QDialog):
    """Dialog for merging multiple PDFs."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Merge PDFs")
        self.resize(500, 400)
        
        layout = QVBoxLayout(self)
        
        self.file_list = QListWidget()
        self.file_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        layout.addWidget(QLabel("Drag to reorder:"))
        layout.addWidget(self.file_list)
        
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add Files")
        add_btn.clicked.connect(self.add_files)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self.remove_selected)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(remove_btn)
        layout.addLayout(btn_layout)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.files = []
    
    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select PDFs to Merge", "", "PDF Files (*.pdf)")
        for path in paths:
            if path not in self.files:
                self.files.append(path)
                self.file_list.addItem(Path(path).name)
    
    def remove_selected(self):
        for item in self.file_list.selectedItems():
            row = self.file_list.row(item)
            self.files.pop(row)
            self.file_list.takeItem(row)
    
    def get_files(self):
        return self.files


class FormFieldDialog(QDialog):
    """Dialog for filling form fields."""
    
    def __init__(self, fields: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fill Form Fields")
        self.resize(400, 500)
        
        layout = QVBoxLayout(self)
        self.field_widgets = {}
        
        for field in fields:
            label = QLabel(f"{field['name']} ({field['type']})")
            layout.addWidget(label)

            widget: QWidget
            if field['type'] == 'text':
                widget = QLineEdit()
                widget.setText(field.get('value', ''))
            elif field['type'] == 'textarea':
                widget = QTextEdit()
                widget.setPlainText(field.get('value', ''))
            elif field['type'] == 'checkbox':
                widget = QCheckBox()
                widget.setChecked(field.get('value', False))
            else:
                widget = QLineEdit()
                widget.setText(str(field.get('value', '')))
            
            self.field_widgets[field['name']] = widget
            layout.addWidget(widget)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def get_values(self):
        values = {}
        for name, widget in self.field_widgets.items():
            if isinstance(widget, QLineEdit):
                values[name] = widget.text()
            elif isinstance(widget, QTextEdit):
                values[name] = widget.toPlainText()
            elif isinstance(widget, QCheckBox):
                values[name] = widget.isChecked()
        return values


# ============ ADOBE-STYLE UI COMPONENTS ============

class OutlineSidebar(QWidget):
    """Bookmarks/Outline sidebar - Acrobat style."""
    
    page_selected = pyqtSignal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        
        toolbar = QHBoxLayout()
        expand_btn = QPushButton("Expand All")
        expand_btn.clicked.connect(self.expand_all)
        collapse_btn = QPushButton("Collapse All")
        collapse_btn.clicked.connect(self.collapse_all)
        toolbar.addWidget(expand_btn)
        toolbar.addWidget(collapse_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree)
        
        self._bookmarks = []
    
    def set_document(self, doc):
        self.tree.clear()
        self._bookmarks = []
        if not doc:
            return
        
        outline = doc.get_toc()
        if outline:
            self._build_tree(outline)
            self.tree.expandAll()
    
    def _build_tree(self, outline, parent=None):
        """Build the bookmark tree, honouring the outline nesting levels."""
        stack: list[tuple[int, QTreeWidgetItem]] = []
        for level, title, page in outline:
            tree_item = QTreeWidgetItem([title])
            tree_item.setData(0, Qt.ItemDataRole.UserRole, page - 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                stack[-1][1].addChild(tree_item)
            else:
                self.tree.addTopLevelItem(tree_item)
            stack.append((level, tree_item))
    
    def _on_item_clicked(self, item, column):
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if page is not None:
            self.page_selected.emit(page)
    
    def expand_all(self):
        self.tree.expandAll()
    
    def collapse_all(self):
        self.tree.collapseAll()


class CommentsSidebar(QWidget):
    """Annotations/Comments sidebar - Acrobat style."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Comments", "Highlights", "Notes", "Stamps"])
        filter_layout.addWidget(self.filter_combo)
        filter_layout.addStretch()
        layout.addLayout(filter_layout)
        
        self.comments_list = QListWidget()
        self.comments_list.setAlternatingRowColors(True)
        layout.addWidget(self.comments_list)
        
        btn_layout = QHBoxLayout()
        reply_btn = QPushButton("Reply")
        delete_btn = QPushButton("Delete")
        btn_layout.addWidget(reply_btn)
        btn_layout.addWidget(delete_btn)
        layout.addLayout(btn_layout)
    
    def set_document(self, doc):
        self.comments_list.clear()
        if not doc:
            return
        
        for i in range(len(doc)):
            page = doc[i]
            annots = list(page.annots()) if page.annots() else []
            for annot in annots:
                item = QListWidgetItem(f"Page {i+1}: {annot.type[1]} - {annot.info.get('content', '')[:50]}")
                item.setData(Qt.ItemDataRole.UserRole, (i, annot))
                self.comments_list.addItem(item)


class ToolsPanel(QWidget):
    """Right-side tools panel - Acrobat style."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_viewer = parent
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        
        self._add_tool_group("Organize Pages", [
            ("Rotate Left", "Rotate page counter-clockwise", "rotate_left"),
            ("Rotate Right", "Rotate page clockwise", "rotate_right"),
            ("Delete Page", "Delete current page", "delete_page"),
            ("Insert Page", "Insert blank page", "insert_page"),
            ("Extract Pages", "Extract pages to new PDF", "extract_pages"),
        ], layout)
        
        self._add_tool_group("Edit PDF", [
            ("Add Text", "Add text box", "add_text"),
            ("Add Image", "Insert image", "add_image"),
            ("Add Link", "Create hyperlink", "add_link"),
            ("Redact", "Redact sensitive content", "redact_tool"),
            ("Crop Pages", "Crop page margins", "crop_pages"),
        ], layout)
        
        self._add_tool_group("Comment", [
            ("Sticky Note", "Add comment note", "add_note"),
            ("Highlight Text", "Highlight selected text", "highlight_text"),
            ("Underline Text", "Underline selected text", "underline_text"),
            ("Strikethrough", "Strikethrough text", "strikethrough"),
            ("Freehand Draw", "Draw freehand", "freehand_draw"),
            ("Add Stamp", "Add stamp", "add_stamp"),
        ], layout)
        
        self._add_tool_group("Forms & Signatures", [
            ("Prepare Form", "List the existing form fields", "prepare_form"),
            ("Add Text Field", "Add text input field", "add_text_field"),
            ("Add Checkbox", "Add checkbox", "add_checkbox"),
            ("Add Dropdown", "Add dropdown list", "add_dropdown"),
            ("Add Radio Button", "Add radio button", "add_radio_button"),
            ("Add Signature Field", "Add a signature field", "add_signature_field"),
            ("Sign Document", "Sign with certificate", "sign_document"),
        ], layout)
        
        self._add_tool_group("Export & Convert", [
            ("Export to Images", "Save pages as images", "export_images"),
            ("Export to Text", "Extract text", "export_text"),
            ("Export to Word", "Convert to Word", "export_word"),
            ("Export to Excel", "Convert to Excel", "export_excel"),
            ("Optimize PDF", "Reduce file size", "optimize_pdf"),
        ], layout)
        
        self._add_tool_group("Protect & Secure", [
            ("Protect PDF", "Add password protection", "protect_pdf"),
            ("Unlock PDF", "Remove password protection", "unlock_pdf"),
            ("Flatten PDF", "Merge annotations into content", "flatten_pdf"),
            ("Add Page Numbers", "Insert page numbers", "add_page_numbers"),
            ("Add Watermark", "Add text or image watermark", "add_watermark"),
        ], layout)
        
        self._add_tool_group("Advanced", [
            ("Extract Images", "Extract all images from PDF", "extract_images_from_pdf"),
            ("Compare PDFs", "Compare two PDF files", "compare_pdfs"),
            ("Batch Process", "Process multiple PDFs", "batch_process"),
            ("Compress PDF", "Advanced compression options", "compress_pdf_advanced"),
        ], layout)
        
        self._add_tool_group("OCR & Convert", [
            ("OCR (Text Recognition)", "Recognize text in scanned PDFs", "ocr_pdf"),
            ("PDF to Word", "Convert to Word document", "pdf_to_word"),
            ("PDF to Excel", "Convert to Excel spreadsheet", "pdf_to_excel"),
            ("PDF to PowerPoint", "Convert to PowerPoint", "pdf_to_ppt"),
            ("Images to PDF", "Convert images to PDF", "images_to_pdf"),
        ], layout)
        
        layout.addStretch()
    
    def _add_tool_group(self, title, tools, parent_layout):
        group = QGroupBox(title)
        group.setCheckable(True)
        group.setChecked(False)
        group.toggled.connect(lambda checked, g=group: self._toggle_group(g, checked))
        layout = QVBoxLayout(group)
        
        for tool_name, tool_desc, action in tools:
            btn = QPushButton(tool_name)
            btn.setToolTip(tool_desc)
            btn.setStyleSheet("text-align: left; padding: 8px;")
            btn.clicked.connect(lambda _, a=action: self._on_tool_click(a))
            layout.addWidget(btn)
        
        parent_layout.addWidget(group)
    
    def _toggle_group(self, group, checked):
        for i in range(group.layout().count()):
            item = group.layout().itemAt(i)
            if item.widget():
                item.widget().setVisible(checked)
    
    def _on_tool_click(self, action):
        if self.parent_viewer and hasattr(self.parent_viewer, action):
            getattr(self.parent_viewer, action)()


class DocumentToolbar(QWidget):
    """Document toolbar above the viewer - Acrobat style."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_viewer = parent
        self.setFixedHeight(48)
        self._setup_style()
        self._setup_layout()
    
    def _setup_style(self):
        """Setup toolbar style - will be updated by parent theme changes."""
        self.setStyleSheet("""
            DocumentToolbar { 
                background: #f0f0f0; 
                border-bottom: 1px solid #d0d0d0; 
            }
            DocumentToolbar QPushButton { 
                border: none; 
                padding: 6px 12px; 
                border-radius: 4px; 
                background: transparent;
                color: #202020;
                font-size: 14px;
            }
            DocumentToolbar QPushButton:hover { 
                background: #e0e0e0; 
            }
            DocumentToolbar QPushButton:pressed { 
                background: #d0d0d0; 
            }
            DocumentToolbar QPushButton:checked {
                background: #0078d7;
                color: white;
            }
            DocumentToolbar QComboBox { 
                padding: 4px 8px; 
                border: 1px solid #ccc; 
                border-radius: 3px;
                background: white;
                color: #202020;
                min-width: 100px;
            }
            DocumentToolbar QSpinBox {
                padding: 4px 8px;
                border: 1px solid #ccc;
                border-radius: 3px;
                background: white;
                color: #202020;
            }
            DocumentToolbar QLabel {
                color: #202020;
                font-size: 13px;
            }
        """)
    
    def _setup_layout(self):
        """Setup toolbar layout with all controls."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)
        
        nav_group = self._create_button_group([
            ("⏮", "First Page", "first_page"),
            ("◀", "Previous Page", "prev_page"),
            ("▶", "Next Page", "next_page"),
            ("⏭", "Last Page", "last_page"),
        ])
        layout.addWidget(nav_group)
        
        layout.addWidget(self._separator())
        
        self.page_input = QSpinBox()
        self.page_input.setMinimum(1)
        self.page_input.setMaximumWidth(70)
        self.page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_input.valueChanged.connect(self._on_page_changed)
        layout.addWidget(self.page_input)
        self.page_total_label = QLabel("/ 0")
        layout.addWidget(self.page_total_label)
        
        layout.addWidget(self._separator())
        
        view_group = self._create_button_group([
            ("□", "Single Page", "view_single"),
            ("▣", "Continuous", "view_continuous"),
            ("□□", "Two Page", "view_two"),
            ("▣▣", "Two Continuous", "view_two_continuous"),
        ], checkable=True)
        layout.addWidget(view_group)
        
        layout.addWidget(self._separator())
        
        zoom_out_btn = QPushButton("−")
        zoom_out_btn.setFixedWidth(32)
        zoom_out_btn.clicked.connect(lambda: self.parent_viewer.zoom_out())
        layout.addWidget(zoom_out_btn)
        
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(["50%", "75%", "100%", "125%", "150%", "200%", "300%", "Fit Width", "Fit Page", "Fit Visible"])
        self.zoom_combo.setCurrentText("100%")
        self.zoom_combo.setMinimumWidth(100)
        self.zoom_combo.currentTextChanged.connect(self._on_zoom_changed)
        layout.addWidget(self.zoom_combo)
        
        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedWidth(32)
        zoom_in_btn.clicked.connect(lambda: self.parent_viewer.zoom_in())
        layout.addWidget(zoom_in_btn)
        
        layout.addStretch()
        
        tools_group = self._create_button_group([
            ("🔍", "Select Tool", "select_tool"),
            ("✎", "Edit Text", "edit_text"),
            ("💬", "Add Comment", "add_comment"),
            ("📎", "Attach File", "attach_file"),
        ])
        layout.addWidget(tools_group)
        
        layout.addWidget(self._separator())
        
        search_btn = QPushButton("🔍 Search")
        search_btn.clicked.connect(self._show_search)
        layout.addWidget(search_btn)
        
        share_btn = QPushButton("🔗 Share")
        layout.addWidget(share_btn)
        
        print_btn = QPushButton("🖨 Print")
        print_btn.clicked.connect(self._print)
        layout.addWidget(print_btn)
    
    def set_theme(self, theme_name: str):
        """Update toolbar for theme."""
        t = self._get_theme_colors(theme_name)
        self.setStyleSheet(f"""
            DocumentToolbar {{ 
                background: {t['bg']}; 
                border-bottom: 1px solid {t['border']}; 
            }}
            DocumentToolbar QPushButton {{ 
                border: none; 
                padding: 6px 12px; 
                border-radius: 4px; 
                background: transparent;
                color: {t['fg']};
                font-size: 14px;
            }}
            DocumentToolbar QPushButton:hover {{ 
                background: {t['btn_hover']}; 
            }}
            DocumentToolbar QPushButton:pressed {{ 
                background: {t['btn_pressed']}; 
            }}
            DocumentToolbar QPushButton:checked {{
                background: {t['btn_checked']};
                color: {t['btn_checked_fg']};
            }}
            DocumentToolbar QComboBox {{ 
                padding: 4px 8px; 
                border: 1px solid {t['combo_border']}; 
                border-radius: 3px;
                background: {t['combo_bg']};
                color: {t['combo_fg']};
                min-width: 100px;
            }}
            DocumentToolbar QSpinBox {{
                padding: 4px 8px;
                border: 1px solid {t['spin_border']};
                border-radius: 3px;
                background: {t['spin_bg']};
                color: {t['spin_fg']};
            }}
            DocumentToolbar QLabel {{
                color: {t['fg']};
                font-size: 13px;
            }}
        """)
    
    def set_dark_mode(self, enabled: bool):
        """Legacy compatibility - use set_theme instead."""
        self.set_theme("dark" if enabled else "light")
    
    def _get_theme_colors(self, theme_name: str):
        colors = {
            "light": {"bg": "#f0f0f0", "border": "#d0d0d0", "fg": "#202020", 
                     "btn_hover": "#e0e0e0", "btn_pressed": "#d0d0d0", 
                     "btn_checked": "#0078d7", "btn_checked_fg": "#ffffff",
                     "combo_bg": "white", "combo_border": "#ccc", "combo_fg": "#202020",
                     "spin_bg": "white", "spin_border": "#ccc", "spin_fg": "#202020"},
            "dark": {"bg": "#2d2d2d", "border": "#444", "fg": "#ffffff",
                    "btn_hover": "#3e3e42", "btn_pressed": "#4e4e52",
                    "btn_checked": "#0078d7", "btn_checked_fg": "#ffffff",
                    "combo_bg": "#3c3c3c", "combo_border": "#555", "combo_fg": "#ffffff",
                    "spin_bg": "#3c3c3c", "spin_border": "#555", "spin_fg": "#ffffff"},
            "blue": {"bg": "#e1e8f0", "border": "#b0c4de", "fg": "#1a2a4a",
                    "btn_hover": "#d0d8e8", "btn_pressed": "#c0cce0",
                    "btn_checked": "#2b5aa0", "btn_checked_fg": "#ffffff",
                    "combo_bg": "white", "combo_border": "#b0c4de", "combo_fg": "#1a2a4a",
                    "spin_bg": "white", "spin_border": "#b0c4de", "spin_fg": "#1a2a4a"},
            "high_contrast": {"bg": "#000000", "border": "#ffff00", "fg": "#ffff00",
                             "btn_hover": "#000080", "btn_pressed": "#0000ff",
                             "btn_checked": "#ffff00", "btn_checked_fg": "#000000",
                             "combo_bg": "#000000", "combo_border": "#ffff00", "combo_fg": "#ffff00",
                             "spin_bg": "#000000", "spin_border": "#ffff00", "spin_fg": "#ffff00"},
            "sepia": {"bg": "#e8dcc8", "border": "#c9b896", "fg": "#4a3f35",
                     "btn_hover": "#dcd0c0", "btn_pressed": "#c9b896",
                     "btn_checked": "#8b6914", "btn_checked_fg": "#f4ecd8",
                     "combo_bg": "#faf5eb", "combo_border": "#c9b896", "combo_fg": "#4a3f35",
                     "spin_bg": "#faf5eb", "spin_border": "#c9b896", "spin_fg": "#4a3f35"},
        }
        return colors.get(theme_name, colors["light"])
    
    def _create_button_group(self, buttons, checkable=False):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        if checkable:
            btn_group = QButtonGroup(widget)
            btn_group.setExclusive(True)
        
        for text, tooltip, action in buttons:
            btn = QPushButton(text)
            btn.setToolTip(tooltip)
            btn.setFixedSize(36, 36)
            btn.setCheckable(checkable)
            if checkable:
                btn_group.addButton(btn)
            btn.clicked.connect(lambda _, a=action: self._on_action(a))
            layout.addWidget(btn)
        
        return widget
    
    def _separator(self):
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet("color: #ccc;")
        return sep
    
    def _on_action(self, action):
        if self.parent_viewer:
            getattr(self.parent_viewer, action, lambda: None)()
    
    def _on_page_changed(self, page):
        if self.parent_viewer:
            self.parent_viewer.go_to_page(page)
    
    def _on_zoom_changed(self, text):
        if self.parent_viewer:
            if text == "Fit Width":
                self.parent_viewer.set_zoom_fit_width()
            elif text == "Fit Page":
                self.parent_viewer.set_zoom_fit_page()
            elif text == "Fit Visible":
                self.parent_viewer.set_zoom_fit_visible()
            else:
                try:
                    zoom = int(text.replace("%", "")) / 100
                    self.parent_viewer.set_zoom(zoom)
                except ValueError:
                    pass
    
    def _show_search(self):
        if self.parent_viewer:
            self.parent_viewer.show_search()
    
    def _print(self):
        if self.parent_viewer:
            self.parent_viewer.print_document()
    
    def update_page_info(self, current, total):
        self.page_input.blockSignals(True)
        self.page_input.setMaximum(total)
        self.page_input.setValue(current)
        self.page_input.blockSignals(False)
        self.page_total_label.setText(f"/ {total}")
    
    def update_zoom(self, zoom):
        text = f"{int(zoom * 100)}%"
        self.zoom_combo.blockSignals(True)
        if self.zoom_combo.findText(text) == -1:
            self.zoom_combo.addItem(text)
        self.zoom_combo.setCurrentText(text)
        self.zoom_combo.blockSignals(False)


# ============ DIALOG CLASSES FOR NEW FEATURES ============

class PasswordDialog(QDialog):
    """Dialog for setting PDF passwords and permissions."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Protect PDF with Password")
        self.resize(400, 350)
        
        layout = QVBoxLayout(self)
        
        # User password
        user_group = QGroupBox("User Password (required to open)")
        user_layout = QFormLayout(user_group)
        self.user_pwd = QLineEdit()
        self.user_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.user_pwd.setPlaceholderText("Enter password to open PDF")
        user_layout.addRow("Password:", self.user_pwd)
        
        self.user_pwd_confirm = QLineEdit()
        self.user_pwd_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.user_pwd_confirm.setPlaceholderText("Confirm password")
        user_layout.addRow("Confirm:", self.user_pwd_confirm)
        layout.addWidget(user_group)
        
        # Owner password
        owner_group = QGroupBox("Owner Password (required to change permissions)")
        owner_layout = QFormLayout(owner_group)
        self.owner_pwd = QLineEdit()
        self.owner_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.owner_pwd.setPlaceholderText("Optional: different from user password")
        owner_layout.addRow("Password:", self.owner_pwd)
        
        self.owner_pwd_confirm = QLineEdit()
        self.owner_pwd_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        owner_layout.addRow("Confirm:", self.owner_pwd_confirm)
        layout.addWidget(owner_group)
        
        # Permissions
        perm_group = QGroupBox("Permissions (when user password is used)")
        perm_layout = QVBoxLayout(perm_group)
        self.perm_print = QCheckBox("Print document")
        self.perm_print.setChecked(True)
        self.perm_modify = QCheckBox("Modify document")
        self.perm_modify.setChecked(False)
        self.perm_copy = QCheckBox("Copy text/images")
        self.perm_copy.setChecked(True)
        self.perm_annotate = QCheckBox("Add/modify annotations")
        self.perm_annotate.setChecked(False)
        self.perm_fill_forms = QCheckBox("Fill form fields")
        self.perm_fill_forms.setChecked(True)
        self.perm_accessibility = QCheckBox("Accessibility (screen readers)")
        self.perm_accessibility.setChecked(True)
        self.perm_assemble = QCheckBox("Assemble document (insert/rotate/delete pages)")
        self.perm_assemble.setChecked(False)
        self.perm_print_high = QCheckBox("High-quality printing")
        self.perm_print_high.setChecked(True)
        
        perm_layout.addWidget(self.perm_print)
        perm_layout.addWidget(self.perm_modify)
        perm_layout.addWidget(self.perm_copy)
        perm_layout.addWidget(self.perm_annotate)
        perm_layout.addWidget(self.perm_fill_forms)
        perm_layout.addWidget(self.perm_accessibility)
        perm_layout.addWidget(self.perm_assemble)
        perm_layout.addWidget(self.perm_print_high)
        layout.addWidget(perm_group)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def get_passwords(self):
        user_text = self.user_pwd.text()
        owner_text = self.owner_pwd.text()
        user_pwd = user_text or None
        owner_pwd = owner_text or None

        if user_text != self.user_pwd_confirm.text():
            QMessageBox.warning(self, "Error", "User passwords don't match!")
            return None, None, 0

        if owner_text != self.owner_pwd_confirm.text():
            QMessageBox.warning(self, "Error", "Owner passwords don't match!")
            return None, None, 0
        
        # Calculate permissions
        permissions = 0
        if self.perm_print.isChecked():
            permissions |= fitz.PDF_PERM_PRINT  # type: ignore[attr-defined]
        if self.perm_modify.isChecked():
            permissions |= fitz.PDF_PERM_MODIFY  # type: ignore[attr-defined]
        if self.perm_copy.isChecked():
            permissions |= fitz.PDF_PERM_COPY  # type: ignore[attr-defined]
        if self.perm_annotate.isChecked():
            permissions |= fitz.PDF_PERM_ANNOTATE  # type: ignore[attr-defined]
        if self.perm_fill_forms.isChecked():
            permissions |= fitz.PDF_PERM_FORM  # type: ignore[attr-defined]
        if self.perm_accessibility.isChecked():
            permissions |= fitz.PDF_PERM_ACCESSIBILITY  # type: ignore[attr-defined]
        if self.perm_assemble.isChecked():
            permissions |= fitz.PDF_PERM_ASSEMBLE  # type: ignore[attr-defined]
        if self.perm_print_high.isChecked():
            permissions |= fitz.PDF_PERM_PRINT_HQ  # type: ignore[attr-defined]

        return user_pwd, owner_pwd, permissions


class PageNumberDialog(QDialog):
    """Dialog for adding page numbers."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Page Numbers")
        self.resize(350, 300)
        
        layout = QVBoxLayout(self)
        
        # Position
        pos_group = QGroupBox("Position")
        pos_layout = QVBoxLayout(pos_group)
        self.pos_combo = QComboBox()
        self.pos_combo.addItems(["Bottom Center", "Bottom Right", "Bottom Left", 
                                "Top Center", "Top Right", "Top Left"])
        self.pos_combo.setCurrentText("Bottom Center")
        pos_layout.addWidget(self.pos_combo)
        layout.addWidget(pos_group)
        
        # Format
        fmt_group = QGroupBox("Format")
        fmt_layout = QFormLayout(fmt_group)
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText("e.g., Page")
        fmt_layout.addRow("Prefix:", self.prefix)
        
        self.suffix = QLineEdit()
        self.suffix.setPlaceholderText("e.g., of 10")
        fmt_layout.addRow("Suffix:", self.suffix)
        
        self.fontsize = QSpinBox()
        self.fontsize.setRange(6, 72)
        self.fontsize.setValue(10)
        fmt_layout.addRow("Font Size:", self.fontsize)
        
        self.color_btn = QPushButton("Choose Color")
        self.color_btn.clicked.connect(self.choose_color)
        self.color = QColor(0, 0, 0)
        self.color_btn.setStyleSheet("background-color: black; color: white;")
        fmt_layout.addRow("Color:", self.color_btn)
        layout.addWidget(fmt_group)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def choose_color(self):
        color = QColorDialog.getColor(self.color, self)
        if color.isValid():
            self.color = color
            self.color_btn.setStyleSheet(f"background-color: {color.name()}; color: {'white' if color.lightness() < 128 else 'black'};")
    
    def get_settings(self):
        return {
            "position": self.pos_combo.currentText().lower().replace(" ", "_"),
            "prefix": self.prefix.text(),
            "suffix": self.suffix.text(),
            "fontsize": self.fontsize.value(),
            "color": (self.color.redF(), self.color.greenF(), self.color.blueF())
        }


class WatermarkDialog(QDialog):
    """Dialog for adding watermarks."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Watermark")
        self.resize(400, 350)
        
        layout = QVBoxLayout(self)
        
        # Type
        type_group = QGroupBox("Watermark Type")
        type_layout = QVBoxLayout(type_group)
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Text", "Image"])
        self.type_combo.currentTextChanged.connect(self.on_type_changed)
        type_layout.addWidget(self.type_combo)
        layout.addWidget(type_group)
        
        # Text settings
        self.text_group = QGroupBox("Text Watermark")
        text_layout = QFormLayout(self.text_group)
        self.wm_text = QLineEdit()
        self.wm_text.setPlaceholderText("CONFIDENTIAL")
        text_layout.addRow("Text:", self.wm_text)
        
        self.wm_fontsize = QSpinBox()
        self.wm_fontsize.setRange(12, 200)
        self.wm_fontsize.setValue(72)
        text_layout.addRow("Font Size:", self.wm_fontsize)
        
        self.wm_color_btn = QPushButton("Choose Color")
        self.wm_color_btn.clicked.connect(self.choose_wm_color)
        self.wm_color = QColor(255, 0, 0, 128)
        self.wm_color_btn.setStyleSheet("background-color: rgba(255,0,0,128);")
        text_layout.addRow("Color:", self.wm_color_btn)

        self.wm_rotation = QSpinBox()
        self.wm_rotation.setRange(0, 180)
        self.wm_rotation.setValue(45)
        self.wm_rotation.setSuffix("°")
        self.wm_rotation.setToolTip("Rotate the watermark text around its centre")
        text_layout.addRow("Rotation:", self.wm_rotation)
        layout.addWidget(self.text_group)
        
        # Image settings
        self.image_group = QGroupBox("Image Watermark")
        image_layout = QFormLayout(self.image_group)
        self.wm_image_path = QLineEdit()
        self.wm_image_path.setReadOnly(True)
        image_layout.addRow("Image:", self.wm_image_path)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_image)
        image_layout.addRow("", browse_btn)
        layout.addWidget(self.image_group)
        
        # Opacity
        opacity_group = QGroupBox("Opacity")
        opacity_layout = QVBoxLayout(opacity_group)
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(64)
        self.opacity_label = QLabel("Opacity: 25%")
        self.opacity_slider.valueChanged.connect(lambda v: self.opacity_label.setText(f"Opacity: {v*100//255}%"))
        opacity_layout.addWidget(self.opacity_label)
        opacity_layout.addWidget(self.opacity_slider)
        layout.addWidget(opacity_group)
        
        self.on_type_changed(self.type_combo.currentText())
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def on_type_changed(self, text):
        self.text_group.setVisible(text == "Text")
        self.image_group.setVisible(text == "Image")
    
    def choose_wm_color(self):
        color = QColorDialog.getColor(self.wm_color, self)
        if color.isValid():
            self.wm_color = color
            self.wm_color_btn.setStyleSheet(f"background-color: {color.name()};")
    
    def browse_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Watermark Image", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            self.wm_image_path.setText(path)
    
    def accept(self):
        """Validate the dialog before closing."""
        if self.type_combo.currentText() == "Text" and not self.wm_text.text().strip():
            QMessageBox.warning(self, "Watermark", "Please enter the watermark text.")
            return
        if self.type_combo.currentText() == "Image" and not self.wm_image_path.text():
            QMessageBox.warning(self, "Watermark", "Please choose a watermark image.")
            return
        super().accept()

    def get_settings(self):
        return {
            "type": self.type_combo.currentText().lower(),
            "text": self.wm_text.text().strip(),
            "fontsize": self.wm_fontsize.value(),
            "rotation": self.wm_rotation.value(),
            "color": (self.wm_color.redF(), self.wm_color.greenF(), self.wm_color.blueF()),
            "image_path": self.wm_image_path.text() if self.type_combo.currentText() == "Image" else "",
            "opacity": max(0.05, self.opacity_slider.value() / 255.0),
        }


class CompressDialog(QDialog):
    """Dialog for advanced PDF compression."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compress PDF")
        self.resize(350, 300)
        
        layout = QVBoxLayout(self)
        
        # Presets
        preset_group = QGroupBox("Compression Preset")
        preset_layout = QVBoxLayout(preset_group)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["Maximum Quality", "Balanced", "Smallest Size", "Custom"])
        self.preset_combo.currentTextChanged.connect(self.on_preset_changed)
        preset_layout.addWidget(self.preset_combo)
        layout.addWidget(preset_group)
        
        # Custom options
        self.custom_group = QGroupBox("Custom Options")
        custom_layout = QFormLayout(self.custom_group)
        
        self.garbage = QSpinBox()
        self.garbage.setRange(0, 4)
        self.garbage.setValue(4)
        custom_layout.addRow("Garbage Collection (0-4):", self.garbage)
        
        self.deflate = QCheckBox("Compress Streams (deflate)")
        self.deflate.setChecked(True)
        custom_layout.addRow("", self.deflate)
        
        self.clean = QCheckBox("Clean Syntax")
        self.clean.setChecked(True)
        custom_layout.addRow("", self.clean)
        
        self.deflate_images = QCheckBox("Compress Images")
        self.deflate_images.setChecked(True)
        custom_layout.addRow("", self.deflate_images)
        
        self.deflate_fonts = QCheckBox("Compress Fonts")
        self.deflate_fonts.setChecked(True)
        custom_layout.addRow("", self.deflate_fonts)
        
        layout.addWidget(self.custom_group)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.on_preset_changed(self.preset_combo.currentText())
    
    def on_preset_changed(self, text):
        if text == "Maximum Quality":
            self.garbage.setValue(1)
            self.deflate.setChecked(True)
            self.clean.setChecked(True)
            self.deflate_images.setChecked(False)
            self.deflate_fonts.setChecked(True)
        elif text == "Balanced":
            self.garbage.setValue(3)
            self.deflate.setChecked(True)
            self.clean.setChecked(True)
            self.deflate_images.setChecked(True)
            self.deflate_fonts.setChecked(True)
        elif text == "Smallest Size":
            self.garbage.setValue(4)
            self.deflate.setChecked(True)
            self.clean.setChecked(True)
            self.deflate_images.setChecked(True)
            self.deflate_fonts.setChecked(True)
        
        self.custom_group.setVisible(text == "Custom")
    
    def get_settings(self):
        return {
            "garbage": self.garbage.value(),
            "deflate": self.deflate.isChecked(),
            "clean": self.clean.isChecked(),
            "deflate_images": self.deflate_images.isChecked(),
            "deflate_fonts": self.deflate_fonts.isChecked()
        }


class BatchProcessDialog(QDialog):
    """Dialog for batch processing multiple PDFs."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Process PDFs")
        self.resize(500, 400)
        
        layout = QVBoxLayout(self)
        
        # Files
        files_group = QGroupBox("Files to Process")
        files_layout = QVBoxLayout(files_group)
        self.files_list = QListWidget()
        self.files_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        files_layout.addWidget(self.files_list)
        
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add Files")
        add_btn.clicked.connect(self.add_files)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self.remove_selected)
        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self.clear_all)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(remove_btn)
        btn_layout.addWidget(clear_btn)
        files_layout.addLayout(btn_layout)
        layout.addWidget(files_group)
        
        # Action
        action_group = QGroupBox("Action")
        action_layout = QVBoxLayout(action_group)
        self.action_combo = QComboBox()
        self.action_combo.addItems(["Compress", "Flatten", "Remove Annotations", "Add Page Numbers"])
        action_layout.addWidget(self.action_combo)
        layout.addWidget(action_group)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select PDF Files", "", "PDF Files (*.pdf)")
        for path in paths:
            if path not in [self.files_list.item(i).text() for i in range(self.files_list.count())]:
                self.files_list.addItem(path)
    
    def remove_selected(self):
        for item in self.files_list.selectedItems():
            self.files_list.takeItem(self.files_list.row(item))
    
    def clear_all(self):
        self.files_list.clear()
    
    def get_settings(self):
        files = [self.files_list.item(i).text() for i in range(self.files_list.count())]
        actions = {
            "Compress": "compress",
            "Flatten": "flatten",
            "Remove Annotations": "remove_annotations",
            "Add Page Numbers": "add_page_numbers",
        }
        return {
            "files": files,
            "action": actions.get(self.action_combo.currentText(), "compress"),
        }


class ExportDialog(QDialog):
    """Enhanced export dialog for images."""
    
    def __init__(self, page_count: int, parent=None):
        super().__init__(parent)
        self.page_count = page_count
        self.setWindowTitle("Export Pages as Images")
        self.resize(400, 320)
        
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        self.page_range = QLineEdit(f"1-{page_count}")
        form.addRow("Page Range:", self.page_range)
        
        self.format_combo = QComboBox()
        self.format_combo.addItems(["PNG", "JPEG", "TIFF", "BMP"])
        form.addRow("Format:", self.format_combo)
        
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 600)
        self.dpi_spin.setValue(150)
        self.dpi_spin.setSuffix(" DPI")
        form.addRow("Resolution:", self.dpi_spin)
        
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(1, 100)
        self.quality_spin.setValue(90)
        self.quality_spin.setSuffix("%")
        self.quality_spin.setToolTip("JPEG quality (ignored for PNG/TIFF)")
        form.addRow("JPEG Quality:", self.quality_spin)
        
        layout.addLayout(form)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def accept(self):
        """Reject an invalid page range instead of failing later."""
        try:
            parse_page_range(self.page_range.text(), self.page_count)
        except ValueError as exc:
            QMessageBox.warning(self, "Page Range", str(exc))
            return
        super().accept()

    def get_settings(self):
        return {
            "range": self.page_range.text(),
            "format": self.format_combo.currentText(),
            "dpi": self.dpi_spin.value(),
            "quality": self.quality_spin.value(),
        }


class DocumentPropertiesDialog(QDialog):
    """Dialog for viewing and editing PDF metadata/properties."""
    
    def __init__(self, doc: fitz.Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("Document Properties")
        self.resize(500, 500)
        
        layout = QVBoxLayout(self)
        
        # Tabs for different property categories
        tabs = QTabWidget()
        
        # Description tab
        desc_widget = QWidget()
        desc_layout = QFormLayout(desc_widget)
        
        meta = doc.metadata
        self.title_edit = QLineEdit(meta.get("title", ""))
        self.author_edit = QLineEdit(meta.get("author", ""))
        self.subject_edit = QLineEdit(meta.get("subject", ""))
        self.keywords_edit = QLineEdit(meta.get("keywords", ""))
        self.creator_edit = QLineEdit(meta.get("creator", ""))
        self.producer_edit = QLineEdit(meta.get("producer", ""))
        self.creation_date_edit = QLineEdit(meta.get("creationDate", ""))
        self.mod_date_edit = QLineEdit(meta.get("modDate", ""))
        self.creation_date_edit.setReadOnly(True)
        self.mod_date_edit.setReadOnly(True)
        
        desc_layout.addRow("Title:", self.title_edit)
        desc_layout.addRow("Author:", self.author_edit)
        desc_layout.addRow("Subject:", self.subject_edit)
        desc_layout.addRow("Keywords:", self.keywords_edit)
        desc_layout.addRow("Creator:", self.creator_edit)
        desc_layout.addRow("Producer:", self.producer_edit)
        desc_layout.addRow("Created:", self.creation_date_edit)
        desc_layout.addRow("Modified:", self.mod_date_edit)
        
        tabs.addTab(desc_widget, "Description")
        
        # Advanced tab
        adv_widget = QWidget()
        adv_layout = QFormLayout(adv_widget)
        
        self.page_count_label = QLabel(str(doc.page_count))
        self.page_size_label = QLabel(
            f"{doc[0].rect.width:.0f} x {doc[0].rect.height:.0f} pts"
            if doc.page_count > 0
            else "N/A"
        )
        self.encrypted_label = QLabel("Yes" if doc.needs_pass else "No")
        self.pdf_version_label = QLabel(pdf_version_string(doc))
        self.permissions_label = QLabel(self._describe_permissions(doc))
        
        adv_layout.addRow("Pages:", self.page_count_label)
        adv_layout.addRow("Page Size:", self.page_size_label)
        adv_layout.addRow("Encrypted:", self.encrypted_label)
        adv_layout.addRow("PDF Version:", self.pdf_version_label)
        adv_layout.addRow("Permissions:", self.permissions_label)
        
        tabs.addTab(adv_widget, "Advanced")
        
        # Custom metadata tab
        custom_widget = QWidget()
        custom_layout = QVBoxLayout(custom_widget)
        
        self.custom_tree = QTreeWidget()
        self.custom_tree.setHeaderLabels(["Key", "Value"])
        self.custom_tree.setColumnWidth(0, 200)
        
        # Add existing custom metadata
        for key, value in get_custom_metadata(doc).items():
            self.custom_tree.addTopLevelItem(QTreeWidgetItem([key, str(value)]))
        
        custom_layout.addWidget(self.custom_tree)
        
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_custom_metadata)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_custom_metadata)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(remove_btn)
        btn_layout.addStretch()
        custom_layout.addLayout(btn_layout)
        custom_layout.addWidget(self.custom_tree)
        
        tabs.addTab(custom_widget, "Custom Metadata")
        
        layout.addWidget(tabs)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply_button is not None:
            apply_button.clicked.connect(self.apply_changes)
        layout.addWidget(buttons)
    
    @staticmethod
    def _describe_permissions(doc: fitz.Document) -> str:
        """Return a readable summary of the permission bits."""
        bits = [
            (fitz.PDF_PERM_PRINT, "print"),  # type: ignore[attr-defined]
            (fitz.PDF_PERM_MODIFY, "modify"),  # type: ignore[attr-defined]
            (fitz.PDF_PERM_COPY, "copy"),  # type: ignore[attr-defined]
            (fitz.PDF_PERM_ANNOTATE, "annotate"),  # type: ignore[attr-defined]
            (fitz.PDF_PERM_FORM, "fill forms"),  # type: ignore[attr-defined]
            (fitz.PDF_PERM_ACCESSIBILITY, "accessibility"),  # type: ignore[attr-defined]
            (fitz.PDF_PERM_ASSEMBLE, "assemble"),  # type: ignore[attr-defined]
            (fitz.PDF_PERM_PRINT_HQ, "print high quality"),  # type: ignore[attr-defined]
        ]
        allowed = [label for bit, label in bits if doc.permissions & bit]
        return ", ".join(allowed) if allowed else "none"

    def _add_custom_metadata(self):
        key, ok = QInputDialog.getText(self, "Add Metadata", "Key:")
        if ok and key:
            value, ok = QInputDialog.getText(self, "Add Metadata", f"Value for '{key}':")
            if ok:
                item = QTreeWidgetItem([key, value])
                self.custom_tree.addTopLevelItem(item)
    
    def _remove_custom_metadata(self):
        for item in self.custom_tree.selectedItems():
            self.custom_tree.takeTopLevelItem(self.custom_tree.indexOfTopLevelItem(item))
    
    def apply_changes(self):
        """Write the dialog values back into the open document."""
        meta = self.doc.metadata
        meta["title"] = self.title_edit.text()
        meta["author"] = self.author_edit.text()
        meta["subject"] = self.subject_edit.text()
        meta["keywords"] = self.keywords_edit.text()
        meta["creator"] = self.creator_edit.text()
        meta["producer"] = self.producer_edit.text()
        self.doc.set_metadata(meta)

        # Custom keys live outside set_metadata()'s whitelist, so they are
        # written through the info dictionary itself.
        custom = {}
        for i in range(self.custom_tree.topLevelItemCount()):
            item = self.custom_tree.topLevelItem(i)
            key = item.text(0).strip()
            if key:
                custom[key] = item.text(1)
        set_custom_metadata(self.doc, custom)
    
    def accept(self):
        self.apply_changes()
        super().accept()


class BatesNumberingDialog(QDialog):
    """Dialog for adding Bates numbering to PDF pages."""
    
    def __init__(self, page_count: int, parent=None):
        super().__init__(parent)
        self.page_count = page_count
        self.setWindowTitle("Add Bates Numbering")
        self.resize(450, 400)
        
        layout = QVBoxLayout(self)
        
        # Settings
        form = QFormLayout()
        
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText("e.g., DOC-")
        form.addRow("Prefix:", self.prefix)
        
        self.suffix = QLineEdit()
        self.suffix.setPlaceholderText("e.g., -CONF")
        form.addRow("Suffix:", self.suffix)
        
        self.start_number = QSpinBox()
        self.start_number.setRange(1, 999999)
        self.start_number.setValue(1)
        form.addRow("Start Number:", self.start_number)
        
        self.number_digits = QSpinBox()
        self.number_digits.setRange(1, 10)
        self.number_digits.setValue(6)
        form.addRow("Number of Digits:", self.number_digits)
        
        self.font_size = QSpinBox()
        self.font_size.setRange(6, 72)
        self.font_size.setValue(10)
        form.addRow("Font Size:", self.font_size)
        
        self.font_color_btn = QPushButton("Choose Color")
        self.font_color_btn.clicked.connect(self.choose_color)
        self.font_color = QColor(0, 0, 0)
        self.font_color_btn.setStyleSheet("background-color: black; color: white;")
        form.addRow("Font Color:", self.font_color_btn)
        
        self.position_combo = QComboBox()
        self.position_combo.addItems(["Bottom Center", "Bottom Right", "Bottom Left", "Top Center", "Top Right", "Top Left"])
        form.addRow("Position:", self.position_combo)
        
        self.page_range = QLineEdit(f"1-{page_count}")
        form.addRow("Page Range:", self.page_range)
        
        layout.addLayout(form)
        
        # Preview
        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_label = QLabel("DOC-000001-CONF")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("font-size: 14px; padding: 20px; border: 1px dashed #ccc;")
        preview_layout.addWidget(self.preview_label)
        layout.addWidget(preview_group)
        
        self.update_preview()
        
        # Connect signals
        self.prefix.textChanged.connect(self.update_preview)
        self.suffix.textChanged.connect(self.update_preview)
        self.start_number.valueChanged.connect(self.update_preview)
        self.number_digits.valueChanged.connect(self.update_preview)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def choose_color(self):
        color = QColorDialog.getColor(self.font_color, self)
        if color.isValid():
            self.font_color = color
            self.font_color_btn.setStyleSheet(f"background-color: {color.name()}; color: {'white' if color.lightness() < 128 else 'black'};")
            self.update_preview()
    
    def update_preview(self):
        num = str(self.start_number.value()).zfill(self.number_digits.value())
        text = f"{self.prefix.text()}{num}{self.suffix.text()}"
        self.preview_label.setText(text)
        self.preview_label.setStyleSheet(f"font-size: 14px; padding: 20px; border: 1px dashed #ccc; color: {self.font_color.name()};")
    
    def accept(self):
        try:
            parse_page_range(self.page_range.text(), self.page_count)
        except ValueError as exc:
            QMessageBox.warning(self, "Page Range", str(exc))
            return
        super().accept()

    def get_settings(self):
        return {
            "prefix": self.prefix.text(),
            "suffix": self.suffix.text(),
            "start_number": self.start_number.value(),
            "number_digits": self.number_digits.value(),
            "font_size": self.font_size.value(),
            "color": (self.font_color.redF(), self.font_color.greenF(), self.font_color.blueF()),
            "position": self.position_combo.currentText().lower().replace(" ", "_"),
            "page_range": self.page_range.text()
        }


# ============ END DIALOG CLASSES ============


class PDFViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 800)

        self.doc: fitz.Document | None = None
        self.current_page = 0
        self.zoom = 1.0
        self.dark_mode = False
        self.current_theme = "light"
        self.file_path = ""
        self.dirty = False
        self.dark_action: QAction | None = None
        self.recent_files: list[str] = []
        self.max_recent_files = 10
        self.view_mode = "continuous"
        self._search_term = ""
        self._search_hits: list[tuple[int, fitz.Rect]] = []
        self._search_index = -1
        self._load_recent_files()
        
        self._setup_ui()
        self._apply_theme()
    
    def _update_window_title(self):
        """Show the file name and a modified marker in the title bar."""
        name = Path(self.file_path).name if self.file_path else "Untitled"
        marker = "*" if self.dirty else ""
        self.setWindowTitle(f"{marker}{name} - {APP_NAME}")

    def _mark_dirty(self, dirty: bool = True):
        self.dirty = dirty
        self._update_window_title()

    def _setup_ui(self):
        # Menu bar (Acrobat-style)
        self._setup_menubar()
        
        # Main splitter for three-pane layout
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(main_splitter)
        
        # LEFT PANEL - Tools/Navigation
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_panel.setMaximumWidth(280)
        left_panel.setMinimumWidth(200)
        
        # Sidebar tabs
        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setTabPosition(QTabWidget.TabPosition.West)
        
        # Page Thumbnails tab
        self.thumbnail_sidebar = ThumbnailSidebar(self)
        self.thumbnail_sidebar.page_selected.connect(self.go_to_page)
        self.thumbnail_sidebar.pages_reordered.connect(self.reorder_pages)
        self.sidebar_tabs.addTab(self.thumbnail_sidebar, "Pages")
        
        # Bookmarks/Outline tab
        self.outline_sidebar = OutlineSidebar(self)
        self.sidebar_tabs.addTab(self.outline_sidebar, "Bookmarks")
        
        # Annotations/Comments tab
        self.comments_sidebar = CommentsSidebar(self)
        self.sidebar_tabs.addTab(self.comments_sidebar, "Comments")
        
        left_layout.addWidget(self.sidebar_tabs)
        main_splitter.addWidget(left_panel)
        
        # CENTER PANEL - Document View
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        
        # Toolbar above document
        self._setup_document_toolbar(center_layout)
        
        # Document scroll area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.scroll_area.setStyleSheet("QScrollArea { background: #cccccc; border: none; }")
        
        self.pages_container = QWidget()
        self.pages_layout = QVBoxLayout(self.pages_container)
        self.pages_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.pages_layout.setSpacing(12)
        self.pages_container.setStyleSheet("background: #cccccc;")
        
        self.scroll_area.setWidget(self.pages_container)
        center_layout.addWidget(self.scroll_area)
        
        main_splitter.addWidget(center_widget)
        
        # RIGHT PANEL - Properties/Tools
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_panel.setMaximumWidth(320)
        right_panel.setMinimumWidth(240)
        
        self.tools_panel = ToolsPanel(self)
        right_layout.addWidget(self.tools_panel)
        main_splitter.addWidget(right_panel)
        
        # Set splitter proportions (left:center:right = 20:60:20)
        main_splitter.setSizes([240, 720, 240])
        main_splitter.setStretchFactor(1, 1)
        
        # Status bar
        self._setup_statusbar()
        
        # Connect scroll for lazy rendering (once - render_pages() must not
        # reconnect this on every reload, which stacked duplicate slots)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)
    
    def _setup_menubar(self):
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        open_action = QAction("&Open", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)
        
        # Recent files submenu
        self.recent_menu = file_menu.addMenu("Recent Files")
        self.update_recent_menu()
        
        file_menu.addSeparator()
        
        save_action = QAction("&Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_file)
        file_menu.addAction(save_action)
        
        save_as_action = QAction("Save &As...", self)
        save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_action.triggered.connect(self.save_file_as)
        file_menu.addAction(save_as_action)
        
        file_menu.addSeparator()
        
        merge_action = QAction("&Merge PDFs...", self)
        merge_action.setShortcut(QKeySequence("Ctrl+M"))
        merge_action.triggered.connect(self.merge_pdfs)
        file_menu.addAction(merge_action)
        
        split_action = QAction("&Split PDF...", self)
        split_action.triggered.connect(self.split_pdf)
        file_menu.addAction(split_action)
        
        file_menu.addSeparator()
        
        export_action = QAction("E&xport", self)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(self.export_images)
        file_menu.addAction(export_action)
        
        file_menu.addSeparator()
        
        print_action = QAction("&Print...", self)
        print_action.setShortcut(QKeySequence.StandardKey.Print)
        print_action.triggered.connect(self.print_document)
        file_menu.addAction(print_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # Edit menu
        edit_menu = menubar.addMenu("&Edit")

        find_action = QAction("&Find...", self)
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(self.show_search)
        edit_menu.addAction(find_action)

        find_next_action = QAction("Find &Next", self)
        find_next_action.setShortcut(QKeySequence("F3"))
        find_next_action.triggered.connect(self.find_next)
        edit_menu.addAction(find_next_action)

        replace_action = QAction("Search & Replace...", self)
        replace_action.setShortcut(QKeySequence("Ctrl+H"))
        replace_action.triggered.connect(self.search_and_replace)
        edit_menu.addAction(replace_action)

        edit_menu.addSeparator()

        props_action = QAction("Document &Properties...", self)
        props_action.setShortcut(QKeySequence("Ctrl+I"))
        props_action.triggered.connect(self.show_document_properties)
        edit_menu.addAction(props_action)
        
        # View menu
        view_menu = menubar.addMenu("&View")
        single_action = QAction("&Single Page", self)
        single_action.triggered.connect(lambda: self.set_view_mode("single"))
        view_menu.addAction(single_action)
        
        continuous_action = QAction("&Continuous", self)
        continuous_action.triggered.connect(lambda: self.set_view_mode("continuous"))
        view_menu.addAction(continuous_action)
        
        two_action = QAction("&Two Page", self)
        two_action.triggered.connect(lambda: self.set_view_mode("two"))
        view_menu.addAction(two_action)
        
        view_menu.addSeparator()
        
        zoom_in_action = QAction("Zoom &In", self)
        zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        zoom_in_action.triggered.connect(self.zoom_in)
        view_menu.addAction(zoom_in_action)
        
        zoom_out_action = QAction("Zoom &Out", self)
        zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_out_action.triggered.connect(self.zoom_out)
        view_menu.addAction(zoom_out_action)
        
        fit_width_action = QAction("&Fit Width", self)
        fit_width_action.triggered.connect(self.set_zoom_fit_width)
        view_menu.addAction(fit_width_action)
        
        fit_page_action = QAction("&Fit Page", self)
        fit_page_action.triggered.connect(self.set_zoom_fit_page)
        view_menu.addAction(fit_page_action)
        
        fit_visible_action = QAction("Fit &Visible", self)
        fit_visible_action.triggered.connect(self.set_zoom_fit_visible)
        view_menu.addAction(fit_visible_action)
        
        view_menu.addSeparator()
        
        self.dark_action = QAction("&Dark Mode", self)
        self.dark_action.setCheckable(True)
        self.dark_action.setShortcut(QKeySequence("Ctrl+D"))
        self.dark_action.toggled.connect(self.toggle_dark_mode)
        view_menu.addAction(self.dark_action)
        
        theme_action = QAction("&Theme Selector...", self)
        theme_action.setShortcut(QKeySequence("Ctrl+T"))
        theme_action.triggered.connect(self.show_theme_selector)
        view_menu.addAction(theme_action)
        
        view_menu.addSeparator()
        
        left_panel_action = QAction("&Show Left Panel", self)
        left_panel_action.triggered.connect(lambda: self.sidebar_tabs.setVisible(True))
        view_menu.addAction(left_panel_action)
        
        right_panel_action = QAction("&Show Right Panel", self)
        right_panel_action.triggered.connect(lambda: self.tools_panel.setVisible(True))
        view_menu.addAction(right_panel_action)
        
        # Go menu / navigation shortcuts
        go_menu = menubar.addMenu("&Go")
        go_action = QAction("&Go to Page...", self)
        go_action.setShortcut(QKeySequence("Ctrl+G"))
        go_action.triggered.connect(self.go_to_page_dialog)
        go_menu.addAction(go_action)

        nav_actions = [
            ("&Next Page", QKeySequence("PgDown"), self.next_page),
            ("&Previous Page", QKeySequence("PgUp"), self.prev_page),
            ("&First Page", QKeySequence("Home"), self.first_page),
            ("&Last Page", QKeySequence("End"), self.last_page),
        ]
        for label, shortcut, slot in nav_actions:
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.triggered.connect(slot)
            go_menu.addAction(action)

        # Tools menu
        tools_menu = menubar.addMenu("&Tools")
        org_action = QAction("&Organize Pages", self)
        org_action.triggered.connect(lambda: self.tools_panel.setVisible(True))
        tools_menu.addAction(org_action)
        
        edit_pdf_action = QAction("&Edit PDF", self)
        edit_pdf_action.triggered.connect(lambda: self.tools_panel.setVisible(True))
        tools_menu.addAction(edit_pdf_action)
        
        comment_action = QAction("&Comment", self)
        comment_action.triggered.connect(lambda: self.sidebar_tabs.setCurrentIndex(2))
        tools_menu.addAction(comment_action)
        
        fill_sign_action = QAction("&Fill & Sign", self)
        fill_sign_action.setShortcut(QKeySequence("F"))
        fill_sign_action.triggered.connect(self.fill_form)
        tools_menu.addAction(fill_sign_action)
        
        redact_action = QAction("&Redact", self)
        redact_action.triggered.connect(self.redact_tool)
        tools_menu.addAction(redact_action)
        
        prepare_form_action = QAction("&Prepare Form", self)
        prepare_form_action.triggered.connect(self.prepare_form)
        tools_menu.addAction(prepare_form_action)
        
        tools_menu.addSeparator()
        
        protect_action = QAction("&Protect PDF...", self)
        protect_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        protect_action.triggered.connect(self.protect_pdf)
        tools_menu.addAction(protect_action)
        
        unlock_action = QAction("&Unlock PDF...", self)
        unlock_action.triggered.connect(self.unlock_pdf)
        tools_menu.addAction(unlock_action)
        
        pagenums_action = QAction("Add &Page Numbers...", self)
        pagenums_action.triggered.connect(self.add_page_numbers)
        tools_menu.addAction(pagenums_action)
        
        watermark_action = QAction("Add &Watermark...", self)
        watermark_action.triggered.connect(self.add_watermark)
        tools_menu.addAction(watermark_action)
        
        flatten_action = QAction("&Flatten PDF...", self)
        flatten_action.triggered.connect(self.flatten_pdf)
        tools_menu.addAction(flatten_action)
        
        tools_menu.addSeparator()
        
        extract_img_action = QAction("&Extract Images...", self)
        extract_img_action.triggered.connect(self.extract_images_from_pdf)
        tools_menu.addAction(extract_img_action)
        
        compare_action = QAction("&Compare PDFs...", self)
        compare_action.triggered.connect(self.compare_pdfs)
        tools_menu.addAction(compare_action)
        
        compress_adv_action = QAction("Advanced &Compress...", self)
        compress_adv_action.triggered.connect(self.compress_pdf_advanced)
        tools_menu.addAction(compress_adv_action)
        
        batch_action = QAction("&Batch Process...", self)
        batch_action.triggered.connect(self.batch_process)
        tools_menu.addAction(batch_action)
        
        tools_menu.addSeparator()
        
        bates_action = QAction("Add &Bates Numbering...", self)
        bates_action.triggered.connect(self.add_bates_numbering)
        tools_menu.addAction(bates_action)
        
        shortcuts_action = QAction("Keyboard &Shortcuts...", self)
        shortcuts_action.setShortcut(QKeySequence("Ctrl+K"))
        shortcuts_action.triggered.connect(self.show_shortcuts_dialog)
        tools_menu.addAction(shortcuts_action)
        
        # Window menu
        window_menu = menubar.addMenu("&Window")
        minimize_action = QAction("&Minimize", self)
        minimize_action.triggered.connect(self.showMinimized)
        window_menu.addAction(minimize_action)
        
        maximize_action = QAction("&Zoom", self)
        maximize_action.triggered.connect(self.showMaximized)
        window_menu.addAction(maximize_action)
        
        # Help menu
        help_menu = menubar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
    
    def _setup_document_toolbar(self, parent_layout):
        """Setup the Acrobat-style document toolbar."""
        self.doc_toolbar = DocumentToolbar(self)
        parent_layout.addWidget(self.doc_toolbar)
    
    def _setup_statusbar(self):
        """Setup the status bar."""
        self.status_label = QLabel("No document loaded")
        self.statusBar().addWidget(self.status_label)
        
        self.page_label = QLabel("Page: 0/0")
        self.statusBar().addPermanentWidget(self.page_label)
        
        self.zoom_label = QLabel("Zoom: 100%")
        self.statusBar().addPermanentWidget(self.zoom_label)
    
    def _apply_theme(self):
        theme = self._get_theme(self.current_theme)
        self.setStyleSheet(theme)
        if hasattr(self, 'doc_toolbar'):
            self.doc_toolbar.set_theme(self.current_theme)
        if hasattr(self, 'sidebar_tabs'):
            for i in range(self.sidebar_tabs.count()):
                widget = self.sidebar_tabs.widget(i)
                if hasattr(widget, 'set_theme'):
                    widget.set_theme(self.current_theme)
    
    def _theme_colors(self, theme_name: str) -> dict:
        """Return the colour dictionary for a theme."""
        themes = {
            "light": {
                "window_bg": "#ffffff",
                "window_fg": "#202020",
                "toolbar_bg": "#f0f0f0",
                "toolbar_border": "#d0d0d0",
                "scroll_bg": "#cccccc",
                "status_bg": "#f0f0f0",
                "status_fg": "#202020",
                "input_bg": "#ffffff",
                "input_fg": "#202020",
                "input_border": "#ccc",
                "button_bg": "transparent",
                "button_fg": "#202020",
                "button_hover": "#e0e0e0",
                "button_pressed": "#d0d0d0",
                "button_checked": "#0078d7",
                "button_checked_fg": "#ffffff",
                "combo_bg": "#ffffff",
                "combo_border": "#ccc",
                "spin_bg": "#ffffff",
                "spin_border": "#ccc",
                "spin_fg": "#202020",
                "label_fg": "#202020",
                "menu_bg": "#ffffff",
                "menu_fg": "#202020",
                "menu_sel_bg": "#0078d7",
                "menu_sel_fg": "#ffffff",
                "dialog_bg": "#ffffff",
                "dialog_fg": "#202020",
                "dock_bg": "#ffffff",
                "dock_fg": "#202020",
                "list_bg": "#ffffff",
                "list_fg": "#202020",
                "push_btn_bg": "#3c3c3c",
                "push_btn_fg": "#ffffff",
                "push_btn_border": "#555",
                "push_btn_hover": "#0078d7",
                "check_fg": "#202020",
                "separator": "#ccc",
            },
            "dark": {
                "window_bg": "#1e1e1e",
                "window_fg": "#d4d4d4",
                "toolbar_bg": "#252526",
                "toolbar_border": "#3c3c3c",
                "scroll_bg": "#1e1e1e",
                "status_bg": "#007acc",
                "status_fg": "#ffffff",
                "input_bg": "#3c3c3c",
                "input_fg": "#d4d4d4",
                "input_border": "#555",
                "button_bg": "transparent",
                "button_fg": "#d4d4d4",
                "button_hover": "#3e3e42",
                "button_pressed": "#4e4e52",
                "button_checked": "#0078d7",
                "button_checked_fg": "#ffffff",
                "combo_bg": "#3c3c3c",
                "combo_border": "#555",
                "spin_bg": "#3c3c3c",
                "spin_border": "#555",
                "spin_fg": "#d4d4d4",
                "label_fg": "#d4d4d4",
                "menu_bg": "#252526",
                "menu_fg": "#d4d4d4",
                "menu_sel_bg": "#0078d7",
                "menu_sel_fg": "#ffffff",
                "dialog_bg": "#252526",
                "dialog_fg": "#d4d4d4",
                "dock_bg": "#252526",
                "dock_fg": "#d4d4d4",
                "list_bg": "#1e1e1e",
                "list_fg": "#d4d4d4",
                "push_btn_bg": "#0e639c",
                "push_btn_fg": "#ffffff",
                "push_btn_border": "#0078d7",
                "push_btn_hover": "#1177bb",
                "check_fg": "#d4d4d4",
                "separator": "#444",
            },
            "blue": {
                "window_bg": "#f0f4f8",
                "window_fg": "#1a2a4a",
                "toolbar_bg": "#e1e8f0",
                "toolbar_border": "#b0c4de",
                "scroll_bg": "#dce4ec",
                "status_bg": "#2b5aa0",
                "status_fg": "#ffffff",
                "input_bg": "#ffffff",
                "input_fg": "#1a2a4a",
                "input_border": "#b0c4de",
                "button_bg": "transparent",
                "button_fg": "#1a2a4a",
                "button_hover": "#d0d8e8",
                "button_pressed": "#c0cce0",
                "button_checked": "#2b5aa0",
                "button_checked_fg": "#ffffff",
                "combo_bg": "#ffffff",
                "combo_border": "#b0c4de",
                "spin_bg": "#ffffff",
                "spin_border": "#b0c4de",
                "spin_fg": "#1a2a4a",
                "label_fg": "#1a2a4a",
                "menu_bg": "#ffffff",
                "menu_fg": "#1a2a4a",
                "menu_sel_bg": "#2b5aa0",
                "menu_sel_fg": "#ffffff",
                "dialog_bg": "#f0f4f8",
                "dialog_fg": "#1a2a4a",
                "dock_bg": "#e8edf3",
                "dock_fg": "#1a2a4a",
                "list_bg": "#ffffff",
                "list_fg": "#1a2a4a",
                "push_btn_bg": "#2b5aa0",
                "push_btn_fg": "#ffffff",
                "push_btn_border": "#1a3a6e",
                "push_btn_hover": "#366ec8",
                "check_fg": "#1a2a4a",
                "separator": "#b0c4de",
            },
            "high_contrast": {
                "window_bg": "#000000",
                "window_fg": "#ffff00",
                "toolbar_bg": "#000000",
                "toolbar_border": "#ffff00",
                "scroll_bg": "#000000",
                "status_bg": "#0000ff",
                "status_fg": "#ffff00",
                "input_bg": "#000000",
                "input_fg": "#ffff00",
                "input_border": "#ffff00",
                "button_bg": "transparent",
                "button_fg": "#ffff00",
                "button_hover": "#000080",
                "button_pressed": "#0000ff",
                "button_checked": "#ffff00",
                "button_checked_fg": "#000000",
                "combo_bg": "#000000",
                "combo_border": "#ffff00",
                "spin_bg": "#000000",
                "spin_border": "#ffff00",
                "spin_fg": "#ffff00",
                "label_fg": "#ffff00",
                "menu_bg": "#000000",
                "menu_fg": "#ffff00",
                "menu_sel_bg": "#0000ff",
                "menu_sel_fg": "#ffff00",
                "dialog_bg": "#000000",
                "dialog_fg": "#ffff00",
                "dock_bg": "#000000",
                "dock_fg": "#ffff00",
                "list_bg": "#000000",
                "list_fg": "#ffff00",
                "push_btn_bg": "#000080",
                "push_btn_fg": "#ffff00",
                "push_btn_border": "#ffff00",
                "push_btn_hover": "#0000ff",
                "check_fg": "#ffff00",
                "separator": "#ffff00",
            },
            "sepia": {
                "window_bg": "#f4ecd8",
                "window_fg": "#4a3f35",
                "toolbar_bg": "#e8dcc8",
                "toolbar_border": "#c9b896",
                "scroll_bg": "#dcd0c0",
                "status_bg": "#8b6914",
                "status_fg": "#f4ecd8",
                "input_bg": "#faf5eb",
                "input_fg": "#4a3f35",
                "input_border": "#c9b896",
                "button_bg": "transparent",
                "button_fg": "#4a3f35",
                "button_hover": "#dcd0c0",
                "button_pressed": "#c9b896",
                "button_checked": "#8b6914",
                "button_checked_fg": "#f4ecd8",
                "combo_bg": "#faf5eb",
                "combo_border": "#c9b896",
                "spin_bg": "#faf5eb",
                "spin_border": "#c9b896",
                "spin_fg": "#4a3f35",
                "label_fg": "#4a3f35",
                "menu_bg": "#f4ecd8",
                "menu_fg": "#4a3f35",
                "menu_sel_bg": "#8b6914",
                "menu_sel_fg": "#f4ecd8",
                "dialog_bg": "#f4ecd8",
                "dialog_fg": "#4a3f35",
                "dock_bg": "#e8dcc8",
                "dock_fg": "#4a3f35",
                "list_bg": "#faf5eb",
                "list_fg": "#4a3f35",
                "push_btn_bg": "#8b6914",
                "push_btn_fg": "#f4ecd8",
                "push_btn_border": "#6b4f10",
                "push_btn_hover": "#a07818",
                "check_fg": "#4a3f35",
                "separator": "#c9b896",
            },
        }
        
        return themes.get(theme_name, themes["light"])

    def _get_theme(self, theme_name: str) -> str:
        """Return the stylesheet for a theme."""
        return self._build_stylesheet(self._theme_colors(theme_name))
    
    def _build_stylesheet(self, t):
        return f"""
            QMainWindow, QWidget {{ background-color: {t['window_bg']}; color: {t['window_fg']}; }}
            QToolBar {{ background-color: {t['toolbar_bg']}; border: 1px solid {t['toolbar_border']}; spacing: 4px; }}
            QToolBar QLabel {{ color: {t['label_fg']}; }}
            QScrollArea {{ background-color: {t['scroll_bg']}; border: none; }}
            QStatusBar {{ background-color: {t['status_bg']}; color: {t['status_fg']}; }}
            QSpinBox, QComboBox {{ background-color: {t['spin_bg']}; color: {t['spin_fg']}; border: 1px solid {t['spin_border']}; padding: 4px; }}
            QDockWidget {{ background-color: {t['dock_bg']}; color: {t['dock_fg']}; titlebar-close-icon: url(close.png); titlebar-normal-icon: url(undock.png); }}
            QDockWidget::title {{ background: {t['toolbar_bg']}; color: {t['dock_fg']}; padding: 4px; }}
            QListWidget {{ background-color: {t['list_bg']}; color: {t['list_fg']}; border: none; }}
            QListWidget::item:selected {{ background-color: {t['menu_sel_bg']}; color: {t['menu_sel_fg']}; }}
            QMenu {{ background-color: {t['menu_bg']}; color: {t['menu_fg']}; border: 1px solid {t['separator']}; }}
            QMenu::item:selected {{ background-color: {t['menu_sel_bg']}; color: {t['menu_sel_fg']}; }}
            QDialog {{ background-color: {t['dialog_bg']}; color: {t['dialog_fg']}; }}
            QLineEdit, QTextEdit {{ background-color: {t['input_bg']}; color: {t['input_fg']}; border: 1px solid {t['input_border']}; }}
            QPushButton {{ background-color: {t['button_bg']}; color: {t['button_fg']}; border: 1px solid {t['push_btn_border']}; padding: 6px 12px; border-radius: 4px; }}
            QPushButton:hover {{ background-color: {t['button_hover']}; }}
            QPushButton:pressed {{ background-color: {t['button_pressed']}; }}
            QPushButton:checked {{ background-color: {t['button_checked']}; color: {t['button_checked_fg']}; }}
            QCheckBox, QRadioButton {{ color: {t['check_fg']}; }}
            QSplitter::handle {{ background-color: {t['separator']}; }}
            QTabWidget::pane {{ border: 1px solid {t['separator']}; background: {t['window_bg']}; }}
            QTabBar::tab {{ background: {t['toolbar_bg']}; color: {t['label_fg']}; padding: 8px 16px; border: 1px solid {t['separator']}; }}
            QTabBar::tab:selected {{ background: {t['window_bg']}; color: {t['button_checked']}; }}
            QGroupBox {{ border: 1px solid {t['separator']}; margin-top: 12px; color: {t['label_fg']}; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t['label_fg']}; }}
        """
    
    def toggle_dark_mode(self, checked: bool):
        """Switch between the light and dark themes (Ctrl+D)."""
        self.dark_mode = checked
        self.current_theme = "dark" if checked else "light"
        self._apply_theme()
        if self.dark_action is not None and self.dark_action.isChecked() != checked:
            self.dark_action.blockSignals(True)
            self.dark_action.setChecked(checked)
            self.dark_action.blockSignals(False)
    
    def show_theme_selector(self):
        """Show theme selector dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Theme Selector")
        dialog.resize(450, 400)
        
        layout = QVBoxLayout(dialog)
        
        # Theme preview list
        themes = ["light", "dark", "blue", "high_contrast", "sepia"]
        theme_names = {
            "light": "☀️ Light",
            "dark": "🌙 Dark", 
            "blue": "💙 Blue Professional",
            "high_contrast": "⚡ High Contrast",
            "sepia": "📜 Sepia"
        }
        
        theme_list = QListWidget()
        theme_list.setIconSize(QSize(64, 48))
        
        for theme_id in themes:
            item = QListWidgetItem(theme_names[theme_id])
            item.setData(Qt.ItemDataRole.UserRole, theme_id)
            # Paint a small swatch from the theme colours so the entry text
            # stays readable (a child widget would cover it).
            colors = self._theme_colors(theme_id)
            swatch = QPixmap(64, 40)
            swatch.fill(QColor(colors["window_bg"]))
            painter = QPainter(swatch)
            painter.fillRect(6, 6, 52, 12, QColor(colors["menu_sel_bg"]))
            painter.fillRect(6, 22, 40, 6, QColor(colors["toolbar_bg"]))
            painter.setPen(QColor(colors["separator"]))
            painter.drawRect(0, 0, 63, 39)
            painter.end()

            item.setIcon(QIcon(swatch))
            item.setSizeHint(QSize(180, 48))
            theme_list.addItem(item)
        
        theme_list.setCurrentRow(themes.index(self.current_theme))
        layout.addWidget(QLabel("Select a theme:"))
        layout.addWidget(theme_list)
        
        # Buttons
        btn_layout = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(lambda: self._apply_selected_theme(dialog, theme_list))
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(apply_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        dialog.exec()
    
    def _apply_selected_theme(self, dialog, theme_list):
        current_item = theme_list.currentItem()
        if current_item:
            theme_id = current_item.data(Qt.ItemDataRole.UserRole)
            self.current_theme = theme_id
            self.dark_mode = (theme_id == "dark")
            self._apply_theme()
            if self.dark_action is not None:
                self.dark_action.blockSignals(True)
                self.dark_action.setChecked(self.dark_mode)
                self.dark_action.blockSignals(False)
            dialog.accept()
    
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            self.load_document(path)
    
    def _load_recent_files(self):
        settings = QSettings("PDFEditor", "RecentFiles")
        self.recent_files = settings.value("recentFiles", [])
        if not isinstance(self.recent_files, list):
            self.recent_files = []
    
    def _save_recent_files(self):
        settings = QSettings("PDFEditor", "RecentFiles")
        settings.setValue("recentFiles", self.recent_files)
    
    def add_recent_file(self, path: str):
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        self.recent_files = self.recent_files[:self.max_recent_files]
        self._save_recent_files()
        self.update_recent_menu()
    
    def update_recent_menu(self):
        self.recent_menu.clear()
        if not self.recent_files:
            no_recent = QAction("No recent files", self)
            no_recent.setEnabled(False)
            self.recent_menu.addAction(no_recent)
            return
        
        for i, path in enumerate(self.recent_files):
            name = Path(path).name
            action = QAction(f"{i+1}. {name}", self)
            action.setToolTip(path)
            action.setData(path)
            action.triggered.connect(lambda _, p=path: self.load_document(p))
            self.recent_menu.addAction(action)
        
        self.recent_menu.addSeparator()
        clear_action = QAction("Clear Recent Files", self)
        clear_action.triggered.connect(self.clear_recent_files)
        self.recent_menu.addAction(clear_action)
    
    def clear_recent_files(self):
        self.recent_files.clear()
        self._save_recent_files()
        self.update_recent_menu()
    
    def save_file(self):
        """Save the current document, keeping the changes user made."""
        if not self.doc:
            return
        if not self.file_path:
            self.save_file_as()
            return
        try:
            try:
                # Incremental saves keep the original file object intact, but
                # only work while the document is still backed by that file.
                self.doc.save(
                    self.file_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP
                )
            except Exception:
                # Documents that were rebuilt in memory (page reordering) or
                # protected ones need a complete rewrite.
                self.doc.save(
                    self.file_path, garbage=3, deflate=True,
                    encryption=fitz.PDF_ENCRYPT_KEEP,
                )
            self._mark_dirty(False)
            self.status_label.setText("Saved")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    def save_file_as(self):
        if not self.doc:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF As", self.file_path or "", "PDF Files (*.pdf)"
        )
        if not path:
            return
        try:
            self.doc.save(path, garbage=3, deflate=True)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")
            return
        self.file_path = path
        self._mark_dirty(False)
        self.add_recent_file(path)
        self.status_label.setText(f"Saved: {Path(path).name}")

    def _confirm_discard_changes(self) -> bool:
        """Return True when it is safe to replace the current document."""
        if not (self.doc and self.dirty):
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved Changes",
            "The document has unsaved changes. Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def load_document(self, path: str):
        if not self._confirm_discard_changes():
            return
        try:
            doc = fitz.open(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open PDF:\n{e}")
            return

        if doc.needs_pass:
            password, ok = QInputDialog.getText(
                self, "Password Required", "Enter the document password:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not doc.authenticate(password):
                doc.close()
                QMessageBox.warning(self, "Wrong Password", "Could not open the document.")
                return

        if self.doc:
            self.doc.close()
        self.doc = doc
        self.current_page = 0
        self.file_path = path
        self.render_pages()
        self.update_ui()
        self.thumbnail_sidebar.update_thumbnails(self.doc)
        self._mark_dirty(False)
        self.status_label.setText(f"Loaded: {Path(path).name}")
        self.add_recent_file(path)
    
    def render_pages(self):
        # Clear existing pages
        while self.pages_layout.count():
            item = self.pages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if not self.doc:
            return
        
        # Create placeholder widgets for all pages (fast)
        for i in range(len(self.doc)):
            page = self.doc[i]
            page_widget = PDFPageWidget(page, i, self.zoom)
            page_widget.annotation_added.connect(self.on_annotation_added)
            page_widget.text_selected.connect(self.on_text_selected)
            self.pages_layout.addWidget(page_widget)
        
        # Update document toolbar
        if hasattr(self, 'doc_toolbar'):
            self.doc_toolbar.update_page_info(self.current_page + 1, len(self.doc))

        # Initial render of visible pages
        QTimer.singleShot(0, self._render_visible_pages)
    
    def _render_visible_pages(self):
        """Render only pages currently visible in viewport."""
        if not self.doc:
            return
        
        viewport_rect = self.scroll_area.viewport().rect()
        viewport_top = self.scroll_area.verticalScrollBar().value()
        viewport_bottom = viewport_top + viewport_rect.height()
        
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget_rect = self.pages_container.mapTo(self.scroll_area.viewport(), widget.pos())
                widget_bottom = widget_rect.y() + widget.height()
                
                # Check if widget intersects viewport (with small buffer)
                if widget_bottom >= viewport_top - 100 and widget_rect.y() <= viewport_bottom + 100:
                    widget.ensure_rendered()
    
    def update_ui(self):
        if self.doc:
            page_text = f"Page: {self.current_page + 1}/{len(self.doc)}"
            zoom_text = f"Zoom: {int(self.zoom * 100)}%"
            self.page_label.setText(page_text)
            self.zoom_label.setText(zoom_text)
            if hasattr(self, 'doc_toolbar'):
                self.doc_toolbar.update_page_info(self.current_page + 1, len(self.doc))
                self.doc_toolbar.update_zoom(self.zoom)
            if hasattr(self, 'outline_sidebar'):
                self.outline_sidebar.set_document(self.doc)
            if hasattr(self, 'comments_sidebar'):
                self.comments_sidebar.set_document(self.doc)
        else:
            self.page_label.setText("Page: 0/0")
            self.zoom_label.setText("Zoom: 100%")
    
    def prev_page(self):
        if self.doc and self.current_page > 0:
            self.current_page -= 1
            self.scroll_to_page()
    
    def next_page(self):
        if self.doc and self.current_page < len(self.doc) - 1:
            self.current_page += 1
            self.scroll_to_page()
    
    def first_page(self):
        if self.doc:
            self.current_page = 0
            self.scroll_to_page()
    
    def last_page(self):
        if self.doc:
            self.current_page = len(self.doc) - 1
            self.scroll_to_page()
    
    def go_to_page_dialog(self):
        """Ask for a page number and jump there (Ctrl+G)."""
        if not self.doc:
            return
        page, ok = QInputDialog.getInt(
            self, "Go to Page", f"Page (1-{len(self.doc)}):",
            self.current_page + 1, 1, len(self.doc),
        )
        if ok:
            self.go_to_page(page)

    def go_to_page(self, page: int):
        if self.doc and 1 <= page <= len(self.doc):
            self.current_page = page - 1
            self.scroll_to_page()
    
    def scroll_to_page(self):
        self.update_ui()
        if hasattr(self, 'thumbnail_sidebar'):
            self.thumbnail_sidebar.set_current_page(self.current_page)
        
        if self.pages_layout.count() > self.current_page:
            widget = self.pages_layout.itemAt(self.current_page).widget()
            if widget:
                self.scroll_area.ensureWidgetVisible(widget)
    
    def reorder_pages(self, new_order: list):
        """Reorder PDF pages based on thumbnail drag & drop.

        The reorder is applied in memory (never through a temporary file) so
        the original file path and any unsaved edits are preserved.
        """
        if not self.doc or sorted(new_order) != list(range(len(self.doc))):
            return

        try:
            new_doc = fitz.open()
            for old_index in new_order:
                new_doc.insert_pdf(self.doc, from_page=old_index, to_page=old_index)
            self.doc.close()
            self.doc = new_doc
            self.current_page = min(self.current_page, len(self.doc) - 1)
            self.render_pages()
            self.update_ui()
            self.thumbnail_sidebar.update_thumbnails(self.doc)
            self._mark_dirty()
            self.status_label.setText("Pages reordered")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to reorder pages:\n{e}")
    
    def _on_scroll(self, value):
        """Handle scroll events - render newly visible pages."""
        self._render_visible_pages()
    
    def zoom_in(self):
        self.set_zoom(min(self.zoom * 1.2, 5.0))
    
    def zoom_out(self):
        self.set_zoom(max(self.zoom / 1.2, 0.1))
    
    def set_zoom(self, zoom: float):
        self.zoom = zoom
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget.set_zoom(zoom)
        self.update_ui()
        if hasattr(self, 'doc_toolbar'):
            self.doc_toolbar.update_zoom(self.zoom)
    
    def _current_page_rect(self) -> fitz.Rect | None:
        """Rect of the page currently shown, or None."""
        if not self.doc or not len(self.doc):
            return None
        index = min(self.current_page, len(self.doc) - 1)
        # rect is the *rotated* page rectangle, which is what the user sees
        return self.doc[index].rect

    def set_zoom_fit_width(self):
        rect = self._current_page_rect()
        if rect is None or not rect.width:
            return
        viewport_width = max(self.scroll_area.viewport().width() - 40, 100)
        self.set_zoom(max(0.1, min(viewport_width / rect.width, 5.0)))

    def set_zoom_fit_page(self):
        rect = self._current_page_rect()
        if rect is None or not rect.width or not rect.height:
            return
        viewport_width = max(self.scroll_area.viewport().width() - 40, 100)
        viewport_height = max(self.scroll_area.viewport().height() - 40, 100)
        zoom = min(viewport_width / rect.width, viewport_height / rect.height)
        self.set_zoom(max(0.1, min(zoom, 5.0)))
    
    def set_zoom_fit_visible(self):
        self.set_zoom_fit_page()
    
    def set_view_mode(self, mode):
        """Apply a simple page layout: single, continuous or two-page."""
        self.view_mode = mode
        total = self.pages_layout.count()
        if not total:
            self.status_label.setText(f"View mode: {mode}")
            return

        for i in range(total):
            widget = self.pages_layout.itemAt(i).widget()
            if widget is None:
                continue
            if mode == "single":
                widget.setVisible(i == self.current_page)
            elif mode == "two":
                widget.setVisible(i in {self.current_page, self.current_page + 1})
            else:  # continuous (default)
                widget.setVisible(True)

        if mode != "continuous":
            self.scroll_to_page()
        else:
            self._render_visible_pages()
        self.status_label.setText(f"View mode: {mode}")

    # Aliases used by the document toolbar buttons
    def view_single(self):
        self.set_view_mode("single")

    def view_continuous(self):
        self.set_view_mode("continuous")

    def view_two(self):
        self.set_view_mode("two")

    def view_two_continuous(self):
        self.set_view_mode("two")

    def select_tool(self):
        self.toggle_select(True)

    def edit_text(self):
        self.add_text()

    def add_comment(self):
        self.add_note()

    def attach_file(self):
        self.add_link()
    
    def toggle_annotate(self, checked: bool = True):
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget.set_annot_mode(checked)
        
        if checked:
            self.status_label.setText("Annotation mode - Click to add note")
        else:
            self.status_label.setText("Ready")
    
    def toggle_select(self, checked: bool = True):
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget.set_select_mode(checked)
        
        if checked:
            self.status_label.setText("Select mode - Drag to select text for highlight/underline")
        else:
            self.status_label.setText("Ready")
    
    def on_annotation_added(self, page_num, annot_data):
        self._mark_dirty()
        self.status_label.setText(f"Annotation added to page {page_num + 1}")
    
    def on_text_selected(self, page_num, text):
        self.status_label.setText(f"Text selected on page {page_num + 1}: {text[:50]}...")
    

    def show_search(self):
        """Prompt for a search term and jump to the first match."""
        text, ok = QInputDialog.getText(self, "Find", "Search for:", text=self._search_term)
        if ok and text:
            self.find_text(text)

    def find_text(self, text: str, start_at_current: bool = False):
        """Collect every occurrence of *text* and jump to the first one."""
        self._search_term = text
        self._search_hits = []
        if not self.doc or not text:
            self.status_label.setText("Nothing to search")
            return

        case_sensitive = bool(
            getattr(self, "case_sensitive", None) and self.case_sensitive.isChecked()
        )
        for page_index in range(len(self.doc)):
            page = self.doc[page_index]
            for rect in page.search_for(text):
                if case_sensitive and text not in page.get_text("text", clip=rect):
                    continue
                self._search_hits.append((page_index, rect))

        if not self._search_hits:
            self._search_index = -1
            self.status_label.setText(f"'{text}' not found")
            return

        self._search_index = -1
        if start_at_current:
            for i, (page_index, _rect) in enumerate(self._search_hits):
                if page_index >= self.current_page:
                    self._search_index = i - 1
                    break
        self.find_next()

    def find_next(self):
        """Jump to the next occurrence of the last search term (F3)."""
        if not self._search_hits:
            self.show_search()
            return
        self._search_index = (self._search_index + 1) % len(self._search_hits)
        page_index, rect = self._search_hits[self._search_index]
        self.current_page = page_index
        self.scroll_to_page()
        widget = self.pages_layout.itemAt(page_index).widget()
        if isinstance(widget, PDFPageWidget):
            self.scroll_area.ensureVisible(
                int(rect.x0 * self.zoom),
                int(rect.y0 * self.zoom),
                int(rect.width * self.zoom),
                int(rect.height * self.zoom),
            )
        self.status_label.setText(
            f"Match {self._search_index + 1} of {len(self._search_hits)} "
            f"on page {page_index + 1}"
        )

    def print_document(self):
        """Render the document pages onto a printer."""
        if not self.doc:
            return
        from PyQt6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        painter = QPainter(printer)
        try:
            page_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
            for index in range(len(self.doc)):
                if index:
                    printer.newPage()
                page = self.doc[index]
                # Fit the PDF page into the printable area, preserving ratio
                scale = min(
                    page_rect.width() / page.rect.width,
                    page_rect.height() / page.rect.height,
                )
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                image = QImage(
                    pix.samples,
                    pix.width,
                    pix.height,
                    pix.stride,
                    QImage.Format.Format_RGB888,
                ).copy()
                painter.drawImage(
                    int(page_rect.x()),
                    int(page_rect.y()),
                    image,
                )
        except Exception as e:  # pragma: no cover - hardware dependent
            QMessageBox.critical(self, "Print Error", f"Printing failed:\n{e}")
        finally:
            painter.end()
        self.status_label.setText("Document sent to printer")

    def redact_tool(self):
        """Enable the redaction tool on the page widgets."""
        if not self.doc:
            return
        self.redact_mode = True
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget.set_redact_mode(True)
        self.status_label.setText("Redact tool - drag over content, then save and apply")

    def prepare_form(self):
        if not self.doc:
            return
        fields = [
            widget.field_name
            for page in self.doc
            for widget in (page.widgets() or [])
            if widget.field_name
        ]
        if fields:
            QMessageBox.information(
                self,
                "Existing Form Fields",
                f"This document already has {len(fields)} form field(s):\n\n"
                f"{', '.join(fields[:20])}",
            )
            self.status_label.setText(f"{len(fields)} form field(s) detected")
        else:
            QMessageBox.information(
                self, "Prepare Form",
                "No fillable form fields were found in this document.",
            )
            self.status_label.setText("No form fields detected")

    def show_about(self):
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "A modern PDF viewer and editor built with PyQt6 and PyMuPDF.\n\n"
            "Features:\n"
            "- View, annotate, and edit PDFs\n"
            "- Organize pages (reorder, rotate, delete)\n"
            "- Fill and sign forms\n"
            "- Merge, split, and export PDFs\n"
            "- Page numbers, watermarks, Bates numbering\n"
            "- Protect / unlock documents\n"
            "- Light, dark, blue, high-contrast and sepia themes\n\n"
            "Built for cross-platform desktop.",
        )

    def show_document_properties(self):
        if not self.doc:
            return
        dialog = DocumentPropertiesDialog(self.doc, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._mark_dirty()
            self.status_label.setText("Document properties updated")

    def add_bates_numbering(self):
        if not self.doc:
            return
        dialog = BatesNumberingDialog(len(self.doc), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.get_settings()

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Bates Numbered PDF", "", "PDF Files (*.pdf)"
        )
        if not path:
            return

        try:
            pages = parse_page_range(settings["page_range"], len(self.doc))
            if not pages:
                QMessageBox.warning(self, "Bates Numbering", "The page range is empty.")
                return

            out_doc = clone_document(self.doc)
            color = tuple(settings["color"][:3])
            for number, index in enumerate(pages):
                page = out_doc[index]
                page_number = settings["start_number"] + number
                num_str = str(page_number).zfill(settings["number_digits"])
                text = f"{settings['prefix']}{num_str}{settings['suffix']}"
                x, y = stamp_position(
                    page.rect, settings["position"], settings["font_size"], text
                )
                page.insert_text(
                    (x, y), text, fontsize=settings["font_size"], fontname="helv",
                    color=color,
                )
            out_doc.save(path, garbage=3, deflate=True)
            out_doc.close()
            self.status_label.setText(f"Bates numbering applied: {Path(path).name}")
            QMessageBox.information(self, "Success", f"Saved {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to apply Bates numbering:\n{e}")

    def search_and_replace(self):
        """Open the search & replace dialog."""
        if not self.doc:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Search and Replace")
        dialog.resize(420, 220)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        self.search_text = QLineEdit(self._search_term)
        form.addRow("Find:", self.search_text)
        self.replace_text = QLineEdit()
        form.addRow("Replace with:", self.replace_text)
        self.case_sensitive = QCheckBox("Case sensitive")
        form.addRow("", self.case_sensitive)
        self.whole_words = QCheckBox("Whole words only")
        form.addRow("", self.whole_words)
        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        find_btn = QPushButton("Find Next")
        find_btn.clicked.connect(lambda: self._find_next(dialog))
        replace_btn = QPushButton("Replace")
        replace_btn.clicked.connect(lambda: self._replace_current(dialog))
        replace_all_btn = QPushButton("Replace All")
        replace_all_btn.clicked.connect(lambda: self._replace_all(dialog))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.reject)
        for button in (find_btn, replace_btn, replace_all_btn, close_btn):
            btn_layout.addWidget(button)
        layout.addLayout(btn_layout)

        dialog.exec()

    def _search_options(self) -> tuple[str, bool]:
        """Return (needle, case_sensitive) from the search dialog widgets."""
        needle = self.search_text.text() if hasattr(self, "search_text") else ""
        case_sensitive = bool(
            getattr(self, "case_sensitive", None) and self.case_sensitive.isChecked()
        )
        return needle, case_sensitive

    def _find_next(self, dialog=None):
        needle, _case_sensitive = self._search_options()
        if not needle:
            return
        if needle != self._search_term or not self._search_hits:
            self.find_text(needle, start_at_current=True)
        else:
            self.find_next()

    def _replace_in_page(self, page: fitz.Page, needle: str, replacement: str,
                         case_sensitive: bool) -> int:
        """Replace every occurrence of *needle* on one page.

        PDF text is not editable, so the matched area is redacted and the
        replacement is drawn in the same place with a matching font size.
        """
        hits = page.search_for(needle)
        if case_sensitive:
            hits = [
                rect
                for rect in hits
                if needle in page.get_text("text", clip=rect)
            ]
        if not hits:
            return 0
        for rect in hits:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)  # type: ignore[attr-defined]
        for rect in hits:
            fontsize = max(6.0, min(rect.height * 0.8, 48.0))
            page.insert_text(
                (rect.x0, rect.y1 - rect.height * 0.2),
                replacement,
                fontsize=fontsize,
                fontname="helv",
                color=(0, 0, 0),
            )
        return len(hits)

    def _replace_current(self, dialog=None):
        needle, case_sensitive = self._search_options()
        replacement = self.replace_text.text() if hasattr(self, "replace_text") else ""
        if not needle or not self.doc:
            return
        if not self._search_hits:
            self.find_text(needle)
            return
        page_index = self.current_page
        page = self.doc[page_index]
        hits = page.search_for(needle)
        if case_sensitive:
            hits = [rect for rect in hits if needle in page.get_text("text", clip=rect)]
        if not hits:
            self.status_label.setText(f"'{needle}' not found on this page")
            return
        rect = hits[0]
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)  # type: ignore[attr-defined]
        page.insert_text(
            (rect.x0, rect.y1 - rect.height * 0.2),
            replacement,
            fontsize=max(6.0, min(rect.height * 0.8, 48.0)),
            fontname="helv",
        )
        self._search_hits = []
        self._mark_dirty()
        self._refresh_after_page_edit(f"Replaced 1 occurrence on page {page_index + 1}")

    def _replace_all(self, dialog=None):
        needle, case_sensitive = self._search_options()
        replacement = self.replace_text.text() if hasattr(self, "replace_text") else ""
        if not needle or not self.doc:
            return
        total = 0
        for page in self.doc:
            total += self._replace_in_page(page, needle, replacement, case_sensitive)
        self._search_hits = []
        if total:
            self._mark_dirty()
            self._refresh_after_page_edit(f"Replaced {total} occurrence(s)")
        else:
            self.status_label.setText(f"'{needle}' not found")
    def show_shortcuts_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.resize(500, 400)

        layout = QVBoxLayout(dialog)
        shortcuts = [
            ("File", [
                ("Open", "Ctrl+O"),
                ("Save", "Ctrl+S"),
                ("Save As", "Ctrl+Shift+S"),
                ("Print", "Ctrl+P"),
                ("Exit", "Ctrl+Q"),
            ]),
            ("Edit", [
                ("Find", "Ctrl+F"),
                ("Find Next", "F3"),
                ("Search & Replace", "Ctrl+H"),
                ("Document Properties", "Ctrl+I"),
            ]),
            ("View", [
                ("Zoom In", "Ctrl++"),
                ("Zoom Out", "Ctrl+-"),
                ("Fit Width", "Ctrl+1"),
                ("Fit Page", "Ctrl+2"),
                ("Dark Mode", "Ctrl+D"),
                ("Theme Selector", "Ctrl+T"),
            ]),
            ("Navigation", [
                ("Next Page", "PgDn / Space"),
                ("Previous Page", "PgUp / Shift+Space"),
                ("First Page", "Home"),
                ("Last Page", "End"),
                ("Go to Page", "Ctrl+G"),
            ]),
            ("Tools", [
                ("Add Note", "N"),
                ("Select / Highlight", "S"),
                ("Fill & Sign", "F"),
                ("Merge PDFs", "Ctrl+M"),
                ("Export Pages", "Ctrl+E"),
                ("Keyboard Shortcuts", "Ctrl+K"),
            ]),
        ]

        tree = QTreeWidget()
        tree.setHeaderLabels(["Category", "Action", "Shortcut"])
        tree.setColumnWidth(0, 150)
        tree.setColumnWidth(1, 200)

        for category, actions in shortcuts:
            cat_item = QTreeWidgetItem([category, "", ""])
            font = cat_item.font(0)
            font.setBold(True)
            cat_item.setFont(0, font)
            for action, shortcut in actions:
                cat_item.addChild(QTreeWidgetItem(["", action, shortcut]))
            tree.addTopLevelItem(cat_item)

        tree.expandAll()
        layout.addWidget(tree)

        btn_layout = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dialog.exec()
    def _refresh_after_page_edit(self, message: str):
        """Re-render after an in-memory page edit and flag the document."""
        if not self.doc:
            return
        self.current_page = min(self.current_page, max(len(self.doc) - 1, 0))
        self.render_pages()
        self.update_ui()
        self.thumbnail_sidebar.update_thumbnails(self.doc)
        self.comments_sidebar.set_document(self.doc)
        self._mark_dirty()
        self.status_label.setText(message)

    def rotate_left(self):
        if not self.doc:
            return
        page = self.doc[self.current_page]
        page.set_rotation((page.rotation - 90) % 360)
        self._refresh_after_page_edit("Page rotated left")

    def rotate_right(self):
        if not self.doc:
            return
        page = self.doc[self.current_page]
        page.set_rotation((page.rotation + 90) % 360)
        self._refresh_after_page_edit("Page rotated right")

    def delete_page(self):
        if not self.doc or len(self.doc) <= 1:
            QMessageBox.information(self, "Delete Page", "A document needs at least one page.")
            return
        reply = QMessageBox.question(
            self, "Delete Page",
            f"Delete page {self.current_page + 1}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.doc.delete_page(self.current_page)
            self._refresh_after_page_edit("Page deleted")

    def insert_page(self):
        if not self.doc:
            return
        self.doc.new_page(self.current_page + 1)
        self._refresh_after_page_edit("Blank page inserted")

    def extract_pages(self):
        if not self.doc:
            return
        # Splitting writes one PDF per page, which is exactly "extract pages"
        self.split_pdf()
    
    def add_text(self):
        """Insert a line of text on the current page."""
        if not self.doc:
            return
        text, ok = QInputDialog.getText(self, "Add Text", "Text to insert:")
        if not ok or not text.strip():
            return
        page = self.doc[self.current_page]
        page.insert_text((72, 72), text.strip(), fontsize=12, fontname="helv")
        self._refresh_after_page_edit("Text inserted")

    def add_image(self):
        """Insert an image, scaled to fit, in the middle of the current page."""
        if not self.doc:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Insert Image", "", "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)"
        )
        if not path:
            return
        try:
            pix = fitz.Pixmap(path)
            page = self.doc[self.current_page]
            rect = page.rect
            scale = min(rect.width / pix.width, rect.height / pix.height) * 0.8
            width, height = pix.width * scale, pix.height * scale
            centre = fitz.Point(rect.width / 2, rect.height / 2)
            target = fitz.Rect(
                centre.x - width / 2, centre.y - height / 2,
                centre.x + width / 2, centre.y + height / 2,
            )
            page.insert_image(target, filename=path)
            self._refresh_after_page_edit(f"Image inserted: {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not insert the image:\n{e}")

    def add_link(self):
        QMessageBox.information(
            self, "Add Link",
            "Hyperlink editing is not available yet.\n"
            "Use the Select tool to copy text, or add links with a PDF authoring tool.",
        )

    def crop_pages(self):
        """Crop the margins of every page (in points)."""
        if not self.doc:
            return
        dialog = CropDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        top, right, bottom, left = dialog.get_margins()
        if top == right == bottom == left == 0:
            return
        try:
            for page in self.doc:
                # The crop box must stay inside the (unrotated) media box.
                media = page.mediabox
                if (
                    media.width - left - right < 36
                    or media.height - top - bottom < 36
                ):
                    raise ValueError("Margins are too large for this page size.")
                page.set_cropbox(
                    fitz.Rect(
                        media.x0 + left,
                        media.y0 + top,
                        media.x1 - right,
                        media.y1 - bottom,
                    )
                )
            self._refresh_after_page_edit("Pages cropped")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to crop pages:\n{e}")

    # ----- markup / drawing modes -------------------------------------

    def _set_markup_mode(self, kind: str, message: str):
        """Enable text selection with a fixed markup type on every page."""
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget.markup_kind = kind
                widget.set_select_mode(True)
        self.status_label.setText(message)

    def add_note(self):
        self.toggle_annotate(True)
        self.sidebar_tabs.setCurrentIndex(2)  # Comments tab

    def highlight_text(self):
        self._set_markup_mode("highlight", "Select text to highlight")

    def underline_text(self):
        self._set_markup_mode("underline", "Select text to underline")

    def strikethrough(self):
        self._set_markup_mode("strikeout", "Select text to strike through")

    def freehand_draw(self):
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget.set_draw_mode(True)
        self.status_label.setText("Freehand draw - drag on the page")

    def add_stamp(self):
        """Choose a rubber stamp, then click on the page to place it."""
        if not self.doc:
            return
        names = list(STAMP_TYPES)
        stamp, ok = QInputDialog.getItem(
            self, "Add Stamp", "Stamp type:", names, 0, editable=False
        )
        if not ok:
            return
        self._stamp_type = stamp
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget.stamp_type = stamp
                widget.set_stamp_mode(True)
        self.status_label.setText(f"Click on the page to place the '{stamp}' stamp")

    # ----- form field creation ----------------------------------------

    def add_text_field(self):
        self._add_form_widget(fitz.PDF_WIDGET_TYPE_TEXT, "Text Field")  # type: ignore[attr-defined]

    def add_checkbox(self):
        self._add_form_widget(fitz.PDF_WIDGET_TYPE_CHECKBOX, "Checkbox")  # type: ignore[attr-defined]

    def _add_form_widget(self, field_type: int, label: str):
        """Create a form field of ``field_type`` near the top of the page."""
        if not self.doc:
            return
        name, ok = QInputDialog.getText(self, f"Add {label}", "Field name:")
        if not ok or not name.strip():
            return
        try:
            page = self.doc[self.current_page]
            widget = fitz.Widget()
            widget.field_type = field_type
            widget.field_name = name.strip()
            widget.field_label = name.strip()
            if field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:  # type: ignore[attr-defined]
                widget.rect = fitz.Rect(72, 72, 90, 90)
                widget.field_value = False
            else:
                widget.rect = fitz.Rect(72, 72, 300, 96)
                widget.field_value = ""
                widget.text_fontsize = 10
            page.add_widget(widget)
            self._refresh_after_page_edit(f"{label} '{name.strip()}' added")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add the field:\n{e}")

    def add_dropdown(self):
        """Create a combo box (dropdown) form field."""
        if not self.doc:
            return
        name, ok = QInputDialog.getText(self, "Add Dropdown", "Field name:")
        if not ok or not name.strip():
            return
        choices, ok = QInputDialog.getText(
            self, "Add Dropdown", "Choices (comma separated):", text="Yes, No"
        )
        if not ok:
            return
        values = [value.strip() for value in choices.split(",") if value.strip()]
        try:
            page = self.doc[self.current_page]
            widget = fitz.Widget()
            widget.field_type = fitz.PDF_WIDGET_TYPE_COMBOBOX  # type: ignore[attr-defined]
            widget.field_name = name.strip()
            widget.field_label = name.strip()
            widget.rect = fitz.Rect(72, 72, 250, 96)
            widget.choice_values = values
            widget.field_value = values[0] if values else ""
            page.add_widget(widget)
            self._refresh_after_page_edit(f"Dropdown '{name.strip()}' added")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add the dropdown:\n{e}")

    def add_radio_button(self):
        """Create a radio button form field."""
        if not self.doc:
            return
        name, ok = QInputDialog.getText(self, "Add Radio Button", "Group name:")
        if not ok or not name.strip():
            return
        try:
            page = self.doc[self.current_page]
            widget = fitz.Widget()
            widget.field_type = fitz.PDF_WIDGET_TYPE_RADIOBUTTON  # type: ignore[attr-defined]
            widget.field_name = name.strip()
            widget.field_label = name.strip()
            widget.rect = fitz.Rect(72, 72, 90, 90)
            widget.field_flags = fitz.PDF_BTN_FIELD_IS_RADIO  # type: ignore[attr-defined]
            widget.field_value = "On"
            page.add_widget(widget)
            self._refresh_after_page_edit(f"Radio button '{name.strip()}' added")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add the radio button:\n{e}")

    def add_signature_field(self):
        """Create an (unsigned) signature form field."""
        if not self.doc:
            return
        name, ok = QInputDialog.getText(self, "Add Signature Field", "Field name:")
        if not ok or not name.strip():
            return
        try:
            page = self.doc[self.current_page]
            widget = fitz.Widget()
            widget.field_type = fitz.PDF_WIDGET_TYPE_SIGNATURE  # type: ignore[attr-defined]
            widget.field_name = name.strip()
            widget.field_label = name.strip()
            widget.rect = fitz.Rect(72, 72, 260, 120)
            page.add_widget(widget)
            self._refresh_after_page_edit(f"Signature field '{name.strip()}' added")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add the signature field:\n{e}")

    def sign_document(self):
        QMessageBox.information(
            self, "Digital Signature",
            "Certificate based digital signing is not implemented yet.\n"
            "You can add a signature image with the Insert Image tool.",
        )
    
    def export_text(self):
        if not self.doc:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Text", "", "Text Files (*.txt)")
        if path:
            text = ""
            for page in self.doc:
                text += page.get_text() + "\n\n"
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
            self.status_label.setText(f"Text exported to {Path(path).name}")
    
    def export_word(self):
        """Alias of ``pdf_to_word`` used by the Export & Convert tool group."""
        self.pdf_to_word()

    def export_excel(self):
        """Alias of ``pdf_to_excel`` used by the Export & Convert tool group."""
        self.pdf_to_excel()
    
    def optimize_pdf(self):
        """Save a cleaned-up copy (garbage collection + deflate)."""
        if not self.doc:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Optimize PDF", "", "PDF Files (*.pdf)"
        )
        if not path:
            return
        try:
            out_doc = clone_document(self.doc)
            out_doc.save(path, garbage=4, deflate=True, clean=True)
            out_doc.close()
            self.status_label.setText(f"PDF optimized: {Path(path).name}")
            QMessageBox.information(self, "Success", f"Saved {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to optimise the PDF:\n{e}")
    
    def fill_form(self):
        if not self.doc:
            return
        
        # Extract form fields
        fields = []
        for page in self.doc:
            for widget in page.widgets() or []:
                kind = widget.field_type_string
                if kind == "CheckBox":
                    field_type = "checkbox"
                elif kind == "Text":
                    field_type = "text"
                else:
                    field_type = "text"
                fields.append(
                    {
                        "name": widget.field_name,
                        "type": field_type,
                        "value": widget.field_value,
                    }
                )

        if not fields:
            QMessageBox.information(self, "No Forms", "This PDF has no fillable form fields.")
            return

        dialog = FormFieldDialog(fields, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.get_values()
        for page in self.doc:
            for widget in page.widgets() or []:
                if widget.field_name in values:
                    widget.field_value = values[widget.field_name]
                    widget.update()
        self._mark_dirty()
        self.render_pages()
        self.status_label.setText("Form fields updated")
    
    def merge_pdfs(self):
        dialog = MergeSplitDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            files = dialog.get_files()
            if len(files) < 2:
                QMessageBox.warning(self, "Merge", "Need at least 2 PDFs to merge.")
                return
            
            try:
                merged = fitz.open()
                for f in files:
                    src = fitz.open(f)
                    merged.insert_pdf(src)
                    src.close()
                
                save_path, _ = QFileDialog.getSaveFileName(self, "Save Merged PDF", "", "PDF Files (*.pdf)")
                if save_path:
                    merged.save(save_path)
                    merged.close()
                    self.load_document(save_path)
                    self.status_label.setText(f"Merged {len(files)} PDFs")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to merge PDFs:\n{e}")
    

    def split_pdf(self):
        """Write every page of the document to its own PDF file."""
        if not self.doc:
            return

        if len(self.doc) < 2:
            QMessageBox.information(self, "Split", "PDF has only 1 page, nothing to split.")
            return

        # getExistingDirectory returns a single string - unpacking it into
        # (path, filter) was the reason splitting always failed.
        output_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if not output_dir:
            return

        stem = Path(self.file_path).stem if self.file_path else "page"
        try:
            for i in range(len(self.doc)):
                single = fitz.open()
                single.insert_pdf(self.doc, from_page=i, to_page=i)
                single.save(str(Path(output_dir) / f"{stem}_{i + 1}.pdf"))
                single.close()
            self.status_label.setText(f"Split into {len(self.doc)} files in {output_dir}")
            QMessageBox.information(
                self, "Done", f"Split complete! {len(self.doc)} files created."
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to split PDF:\n{e}")

    def export_images(self):
        """Export the selected pages as PNG / JPEG / TIFF / BMP images."""
        if not self.doc:
            return

        dialog = ExportDialog(len(self.doc), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.get_settings()

        try:
            pages = parse_page_range(settings["range"], len(self.doc))
        except ValueError as exc:
            QMessageBox.warning(self, "Page Range", str(exc))
            return
        if not pages:
            QMessageBox.warning(self, "Page Range", "No valid pages selected.")
            return

        output_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if not output_dir:
            return

        fmt = settings["format"].lower()
        extension = {"jpeg": "jpg", "tif": "tiff"}.get(fmt, fmt)
        scale = settings["dpi"] / 72.0
        try:
            for i in pages:
                page = self.doc[i]
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                output_path = Path(output_dir) / f"page_{i + 1}.{extension}"
                save_pixmap(pix, output_path, extension, settings.get("quality", 90))
            self.status_label.setText(f"Exported {len(pages)} pages to {output_dir}")
            QMessageBox.information(
                self, "Done", f"Exported {len(pages)} pages as {settings['format']}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export:\n{e}")
    def closeEvent(self, event):
        """Ask before throwing away unsaved changes."""
        if self.doc and self.dirty:
            answer = QMessageBox.question(
                self,
                "Unsaved Changes",
                "The document has unsaved changes. Save before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                self.save_file()
                if self.dirty:  # save failed or was cancelled
                    event.ignore()
                    return
        if self.doc:
            self.doc.close()
        event.accept()
    
    # ----- protect / secure / convert -------------------------------

    def protect_pdf(self):
        """Add password protection to PDF."""
        if not self.doc:
            return
        
        dialog = PasswordDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            user_pwd, owner_pwd, permissions = dialog.get_passwords()
            if user_pwd is None and owner_pwd is None:
                QMessageBox.warning(
                    self, "Protect PDF",
                    "Enter a user and/or owner password before saving.",
                )
                return
            if user_pwd is None:
                QMessageBox.warning(
                    self, "Protect PDF",
                    "A user password is required so the document can be opened.",
                )
                return

            path, _ = QFileDialog.getSaveFileName(
                self, "Save Protected PDF", "", "PDF Files (*.pdf)"
            )
            if not path:
                return
            try:
                self.doc.save(
                    path,
                    encryption=fitz.PDF_ENCRYPT_AES_256,
                    user_pw=user_pwd,
                    owner_pw=owner_pwd or user_pwd,
                    permissions=permissions,
                )
                self.status_label.setText(f"PDF protected: {Path(path).name}")
                QMessageBox.information(self, "Success", f"Saved {Path(path).name}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to protect PDF:\n{e}")
    
    def unlock_pdf(self):
        """Remove password protection from PDF."""
        if not self.doc:
            return
        
        if not self.doc.needs_pass:
            QMessageBox.information(self, "Not Protected", "This PDF is not password protected.")
            return
        
        password, ok = QInputDialog.getText(self, "Unlock PDF", "Enter password:", QLineEdit.EchoMode.Password)
        if ok and password:
            try:
                if self.doc.authenticate(password):
                    path, _ = QFileDialog.getSaveFileName(
                        self, "Save Unlocked PDF", "", "PDF Files (*.pdf)"
                    )
                    if path:
                        # PDF_ENCRYPT_NONE removes the protection entirely
                        self.doc.save(path, encryption=fitz.PDF_ENCRYPT_NONE)
                        self.status_label.setText(f"PDF unlocked: {Path(path).name}")
                        QMessageBox.information(self, "Success", f"Saved {Path(path).name}")
                else:
                    QMessageBox.warning(self, "Wrong Password", "Incorrect password.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to unlock PDF:\n{e}")
    

    def add_page_numbers(self):
        """Write a numbered copy of the document ("Page 3 of 10" and so on)."""
        if not self.doc:
            return

        dialog = PageNumberDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.get_settings()

        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF with Page Numbers", "", "PDF Files (*.pdf)"
        )
        if not path:
            return

        try:
            out_doc = clone_document(self.doc)
            total = len(out_doc)
            for i in range(total):
                page = out_doc[i]
                suffix = settings["suffix"].replace("{total}", str(total))
                text = " ".join(
                    part for part in (settings["prefix"].strip(), str(i + 1), suffix.strip())
                    if part
                )
                x, y = stamp_position(
                    page.rect, settings["position"], settings["fontsize"], text
                )
                page.insert_text(
                    (x, y), text, fontsize=settings["fontsize"], fontname="helv",
                    color=settings["color"],
                )
            out_doc.save(path, garbage=3, deflate=True)
            out_doc.close()
            self.status_label.setText(f"Page numbers added: {Path(path).name}")
            QMessageBox.information(self, "Success", f"Saved {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add page numbers:\n{e}")

    def _watermarked_copy(self, settings: dict) -> fitz.Document:
        """Return a copy of the document with the requested watermark applied."""
        if not self.doc:
            raise ValueError("No document is open")
        out_doc = clone_document(self.doc)
        opacity = float(settings.get("opacity", 0.25))
        color = tuple(settings.get("color", (1.0, 0.0, 0.0))[:3])
        fontsize = float(settings.get("fontsize", 72))
        text = settings.get("text", "")
        rotation = float(settings.get("rotation", 45))

        for index in range(out_doc.page_count):
            page = out_doc[index]
            rect = page.rect
            if settings.get("type") == "text":
                if not text:
                    continue
                font = fitz.Font("helv")
                writer = fitz.TextWriter(rect, color=color, opacity=opacity)
                width = font.text_length(text, fontsize=fontsize)
                start = fitz.Point(
                    (rect.width - width) / 2, rect.height / 2 + fontsize / 3
                )
                writer.append(start, text, font=font, fontsize=fontsize)
                # insert_text() only accepts multiples of 90 degrees, so the
                # rotation is applied through a morph matrix around the centre.
                writer.write_text(
                    page,
                    morph=(fitz.Point(rect.width / 2, rect.height / 2),
                           fitz.Matrix(-rotation)),
                )
            elif settings.get("type") == "image" and settings.get("image_path"):
                img_rect = fitz.Rect(
                    rect.width / 4, rect.height / 4, rect.width * 3 / 4, rect.height * 3 / 4
                )
                page.insert_image(
                    img_rect,
                    pixmap=load_translucent_pixmap(settings["image_path"], opacity),
                    overlay=True,
                )
        return out_doc

    def add_watermark(self):
        """Add a text or image watermark to a copy of the document."""
        if not self.doc:
            return

        dialog = WatermarkDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.get_settings()

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Watermarked PDF", "", "PDF Files (*.pdf)"
        )
        if not path:
            return

        try:
            out_doc = self._watermarked_copy(settings)
            out_doc.save(path, garbage=3, deflate=True)
            out_doc.close()
            self.status_label.setText(f"Watermark added: {Path(path).name}")
            QMessageBox.information(self, "Success", f"Saved {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add watermark:\n{e}")

    def flatten_pdf(self):
        """Bake annotations, form fields and stamps into the page content."""
        if not self.doc:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Flattened PDF", "", "PDF Files (*.pdf)"
        )
        if not path:
            return

        try:
            out_doc = clone_document(self.doc)
            out_doc.bake()  # merges annotations + widgets into the page content
            out_doc.save(path, garbage=4, deflate=True, clean=True)
            out_doc.close()
            self.status_label.setText(f"PDF flattened: {Path(path).name}")
            QMessageBox.information(self, "Success", f"Saved {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to flatten PDF:\n{e}")
    def extract_images_from_pdf(self):
        """Extract all images from PDF."""
        if not self.doc:
            return
        
        output_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder for Images")
        if not output_dir:
            return
        
        try:
            count = 0
            skipped = 0
            for i in range(len(self.doc)):
                page = self.doc[i]
                images = page.get_images(full=True)

                for img_index, img in enumerate(images):
                    xref = img[0]
                    try:
                        pix = fitz.Pixmap(self.doc, xref)
                        if pix.n >= 5:  # CMYK - convert to RGB
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        elif pix.n == 1:  # alpha mask, nothing useful to export
                            skipped += 1
                            continue
                        output_path = (
                            Path(output_dir) / f"page_{i+1}_img_{img_index+1}.png"
                        )
                        pix.save(str(output_path))
                        count += 1
                    except Exception:
                        skipped += 1
            
            self.status_label.setText(f"Extracted {count} images to {output_dir}")
            detail = f"Extracted {count} images."
            if skipped:
                detail += f"\n{skipped} mask/unsupported image(s) were skipped."
            QMessageBox.information(self, "Done", detail)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract images:\n{e}")
    

    def compress_pdf_advanced(self):
        """Save a compressed copy of the document."""
        if not self.doc:
            return

        dialog = CompressDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.get_settings()

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Compressed PDF", "", "PDF Files (*.pdf)"
        )
        if not path:
            return

        try:
            out_doc = clone_document(self.doc)
            out_doc.save(
                path,
                garbage=settings["garbage"],
                deflate=settings["deflate"],
                clean=settings["clean"],
                deflate_images=settings["deflate_images"],
                deflate_fonts=settings["deflate_fonts"],
            )
            out_doc.close()

            original_size = os.path.getsize(self.file_path) if self.file_path else 0
            new_size = os.path.getsize(path)
            reduction = (1 - new_size / original_size) * 100 if original_size else 0

            self.status_label.setText(f"Compressed: {reduction:.1f}% reduction")
            QMessageBox.information(
                self, "Done",
                f"Original: {original_size / 1024:.1f} KB\n"
                f"Compressed: {new_size / 1024:.1f} KB\n"
                f"Reduction: {reduction:.1f}%",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to compress PDF:\n{e}")
    def compare_pdfs(self):
        """Compare two PDFs visually."""
        if not self.doc:
            return
        
        path2, _ = QFileDialog.getOpenFileName(self, "Select Second PDF to Compare", "", "PDF Files (*.pdf)")
        if not path2:
            return
        
        try:
            doc2 = fitz.open(path2)
            
            # Simple comparison: page count and text
            diff_msg = []
            if len(self.doc) != len(doc2):
                diff_msg.append(f"Page count differs: {len(self.doc)} vs {len(doc2)}")
            
            for i in range(min(len(self.doc), len(doc2))):
                text1 = self.doc[i].get_text()
                text2 = doc2[i].get_text()
                if text1 != text2:
                    diff_msg.append(f"Page {i+1}: Text content differs")
            
            doc2.close()
            
            if diff_msg:
                QMessageBox.information(self, "Comparison Result", "\n".join(diff_msg[:20]))
            else:
                QMessageBox.information(self, "Comparison Result", "PDFs appear identical.")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to compare PDFs:\n{e}")
    

    def batch_process(self):
        """Apply one action to a list of files."""
        dialog = BatchProcessDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.get_settings()
        files = settings["files"]
        if not files:
            QMessageBox.information(self, "Batch", "No files selected.")
            return

        action = settings["action"]
        processed = 0
        errors: list[str] = []
        for file_path in files:
            try:
                doc = fitz.open(file_path)
                source = Path(file_path)
                if action == "compress":
                    out_path = source.with_name(f"compressed_{source.name}")
                    doc.save(str(out_path), garbage=4, deflate=True, clean=True)
                elif action == "flatten":
                    out_path = source.with_name(f"flattened_{source.name}")
                    doc.bake()
                    doc.save(str(out_path), garbage=4, deflate=True, clean=True)
                elif action == "remove_annotations":
                    out_path = source.with_name(f"no_annots_{source.name}")
                    for page in doc:
                        for annot in page.annots() or []:
                            page.delete_annot(annot)
                    doc.save(str(out_path))
                elif action == "add_page_numbers":
                    out_path = source.with_name(f"paged_{source.name}")
                    for i, page in enumerate(doc):
                        x, y = stamp_position(page.rect, "bottom_center", 10, str(i + 1))
                        page.insert_text((x, y), str(i + 1), fontsize=10)
                    doc.save(str(out_path), garbage=3, deflate=True)
                doc.close()
                processed += 1
            except Exception as e:
                errors.append(f"{Path(file_path).name}: {e}")

        self.status_label.setText(f"Batch processed {processed}/{len(files)} files")
        if errors:
            QMessageBox.warning(
                self, "Batch Processing",
                f"Processed {processed} of {len(files)} files.\n\n"
                + "\n".join(errors[:10]),
            )
        else:
            QMessageBox.information(
                self, "Done", f"Batch processing complete for {processed} files."
            )

    def ocr_pdf(self):
        """Add an invisible OCR text layer to scanned pages (needs Tesseract)."""
        if not self.doc:
            return

        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            QMessageBox.warning(
                self, "OCR Not Available",
                "OCR needs two extra packages:\n\n"
                "    pip install pytesseract pillow\n\n"
                "and the Tesseract OCR engine itself:\n"
                "https://github.com/tesseract-ocr/tesseract",
            )
            return

        path, _ = QFileDialog.getSaveFileName(self, "Save OCR PDF", "", "PDF Files (*.pdf)")
        if not path:
            return

        try:
            self.status_label.setText("Running OCR... this may take a while")
            QApplication.processEvents()

            out_doc = clone_document(self.doc)
            for page in out_doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image = Image.frombytes(
                    "RGB", (pix.width, pix.height), pix.samples
                )
                text = pytesseract.image_to_string(image)
                if text.strip():
                    rect = fitz.Rect(
                        page.rect.x0 + 36,
                        page.rect.y0 + 36,
                        page.rect.x1 - 36,
                        page.rect.y1 - 36,
                    )
                    # render_mode=3 draws the text invisibly (text layer only)
                    page.insert_textbox(
                        rect, text, fontsize=8, fontname="helv", render_mode=3
                    )
            out_doc.save(path, garbage=3, deflate=True)
            out_doc.close()
            self.status_label.setText(f"OCR complete: {Path(path).name}")
            QMessageBox.information(self, "Success", f"Saved {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"OCR failed:\n{e}")
    def pdf_to_word(self):
        """Convert PDF to Word document."""
        if not self.doc:
            return
        
        try:
            import pdf2docx
        except ImportError:
            QMessageBox.warning(self, "Not Available", 
                "pdf2docx not installed. Run: pip install pdf2docx")
            return

        if not self.file_path:
            QMessageBox.warning(
                self, "Save Required",
                "Save the document to disk before converting it.",
            )
            return

        path, _ = QFileDialog.getSaveFileName(self, "Save as Word", "", "Word Documents (*.docx)")
        if not path:
            return
        
        try:
            self.status_label.setText("Converting to Word...")
            QApplication.processEvents()
            
            cv = pdf2docx.Converter(self.file_path)
            cv.convert(path)
            cv.close()
            
            self.status_label.setText(f"Converted to Word: {Path(path).name}")
            QMessageBox.information(self, "Success", f"Saved as {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Conversion failed:\n{e}")
    
    def pdf_to_excel(self):
        """Convert PDF tables to Excel."""
        if not self.doc:
            return
        
        try:
            import tabula
        except ImportError:
            QMessageBox.warning(self, "Not Available", 
                "tabula-py not installed. Run: pip install tabula-py\n"
                "Also requires Java runtime.")
            return

        if not self.file_path:
            QMessageBox.warning(
                self, "Save Required",
                "Save the document to disk before converting it.",
            )
            return

        path, _ = QFileDialog.getSaveFileName(self, "Save as Excel", "", "Excel Files (*.xlsx)")
        if not path:
            return
        
        try:
            self.status_label.setText("Extracting tables to Excel...")
            QApplication.processEvents()
            
            tabula.convert_into(self.file_path, path, output_format="xlsx", pages="all")
            
            self.status_label.setText(f"Saved as Excel: {Path(path).name}")
            QMessageBox.information(self, "Success", f"Saved as {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Conversion failed:\n{e}")
    
    def pdf_to_ppt(self):
        """Convert PDF to PowerPoint (placeholder)."""
        QMessageBox.information(self, "Not Implemented", 
            "PDF to PowerPoint conversion requires python-pptx and custom layout logic.\n"
            "Install: pip install python-pptx")
    
    def images_to_pdf(self):
        """Convert images to PDF."""
        paths, _ = QFileDialog.getOpenFileNames(self, "Select Images", "", 
            "Images (*.png *.jpg *.jpeg *.tiff *.bmp)")
        if not paths:
            return
        
        path, _ = QFileDialog.getSaveFileName(self, "Save as PDF", "", "PDF Files (*.pdf)")
        if not path:
            return
        
        try:
            doc = fitz.open()
            for img_path in paths:
                img_doc = fitz.open(img_path)
                pdf_bytes = img_doc.convert_to_pdf()
                img_doc.close()
                img_pdf = fitz.open("pdf", pdf_bytes)
                doc.insert_pdf(img_pdf)
                img_pdf.close()

            doc.save(path)
            doc.close()
            
            self.status_label.setText(f"Created PDF from {len(paths)} images: {Path(path).name}")
            QMessageBox.information(self, "Success", f"Created PDF with {len(paths)} pages.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create PDF:\n{e}")


def main() -> int:
    """Application entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("PDFEditor")

    try:
        viewer = PDFViewer()
        viewer.show()
        return app.exec()
    except Exception as exc:  # pragma: no cover - last resort handler
        import traceback

        traceback.print_exc()
        QMessageBox.critical(
            None, f"{APP_NAME} - Startup Error",
            f"The application could not start:\n\n{exc}",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
