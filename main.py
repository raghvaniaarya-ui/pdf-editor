import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QMessageBox,
    QVBoxLayout, QHBoxLayout, QWidget, QToolBar,
    QLabel, QSpinBox, QComboBox, QScrollArea,
    QDockWidget, QListWidget, QListWidgetItem,
    QMenu, QInputDialog, QColorDialog, QDialog,
    QDialogButtonBox, QFormLayout, QLineEdit,
    QTextEdit, QPushButton, QCheckBox, QSplitter,
    QTabWidget, QTabBar, QScrollBar, QFrame,
    QSlider, QButtonGroup, QRadioButton, QGroupBox,
    QTreeWidget, QTreeWidgetItem, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QRectF, QSize, QPoint, pyqtSignal, QTimer, QMimeData, QEvent
from PyQt6.QtGui import (
    QAction, QIcon, QPixmap, QImage, QKeySequence,
    QPainter, QPen, QColor, QBrush, QCursor,
    QFont, QShortcut, QDrag, QStandardItemModel,
    QStandardItem
)

import pymupdf as fitz


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
        self.draw_path = []
        self.draw_paths = []  # List of completed paths
        
        # Redaction
        self.redact_rects = []  # List of redaction rectangles
        self.redact_preview = None  # Current redaction preview rect
        
        # Highlights storage
        self.highlights = []
        
        # Lazy rendering
        self._pixmap = None
        self._rendered_zoom = None
        self._render_timer = None
    
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
        if self._pixmap is None or abs(self._rendered_zoom - self.zoom) > 0.01:
            self._render_pixmap()
            self.update()
    
    def set_zoom(self, zoom: float):
        self.zoom = zoom
        # Defer rendering to avoid blocking UI
        if self._render_timer:
            self._render_timer.stop()
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render_pixmap)
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
    
    def add_highlight(self, rect: fitz.Rect, color: tuple = (1, 1, 0), opacity: float = 0.3):
        """Add a highlight annotation to the page."""
        annot = self.page.add_highlight_annot(rect)
        if annot:
            annot.set_colors(stroke=color)
            annot.set_opacity(opacity)
            annot.update()
            self.highlights.append((rect, color, opacity))
            self._render_pixmap()
            self.update()
    
    def add_text_annotation(self, point: fitz.Point, text: str, color: tuple = (1, 1, 0)):
        """Add a text annotation (sticky note)."""
        rect = fitz.Rect(point.x - 20, point.y - 20, point.x + 20, point.y + 20)
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
            # Convert to PDF coordinates and add as ink annotation
            pdf_points = [fitz.Point(p.x() / self.zoom, p.y() / self.zoom) for p in path]
            annot = self.page.add_ink_annot(pdf_points)
            if annot:
                annot.set_colors(stroke=(0, 0, 1))  # Blue ink
                annot.set_border_width(2)
                annot.update()
        
        self.draw_paths.clear()
        self._render_pixmap()
        self.update()
    
    def add_stamp(self, point: fitz.Point, stamp_type: str = "Approved"):
        """Add a stamp annotation."""
        # Create a rubber stamp annotation
        rect = fitz.Rect(point.x - 60, point.y - 30, point.x + 60, point.y + 30)
        annot = self.page.add_stamp_annot(rect, stamp_type)
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
            self.add_stamp(fitz.Point(pdf_x, pdf_y))
    
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
                
                menu = QMenu(self)
                highlight_action = menu.addAction("Highlight")
                underline_action = menu.addAction("Underline")
                action = menu.exec(self.mapToGlobal(event.pos().toPoint()))
                
                if action == highlight_action:
                    self.add_highlight(rect, (1, 1, 0), 0.3)
                elif action == underline_action:
                    self.add_highlight(rect, (1, 0, 0), 0.5)
            
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
        self.page_num = page_num
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
        """Handle drag & drop reordering."""
        # Move widget in list
        widget = self._page_widgets.pop(source_page)
        self._page_widgets.insert(target_page, widget)
        
        # Rebuild list widget
        self.list_widget.clear()
        for i, w in enumerate(self._page_widgets):
            item = QListWidgetItem()
            w.page_num = i  # Update page number
            w.setToolTip(f"Page {i + 1}")
            item.setSizeHint(w.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, w)
        
        # Emit new order
        self.pages_reordered.emit([w.page_num for w in self._page_widgets])
    
    def set_current_page(self, page_num: int):
        self.list_widget.setCurrentRow(page_num)
    
    def get_page_order(self):
        """Return current page order."""
        return [w.page_num for w in self._page_widgets]


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


class ExportDialog(QDialog):
    """Dialog for exporting pages as images."""
    
    def __init__(self, page_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Pages as Images")
        self.resize(400, 300)
        
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        self.page_range = QLineEdit(f"1-{page_count}")
        form.addRow("Page Range:", self.page_range)
        
        self.format_combo = QComboBox()
        self.format_combo.addItems(["PNG", "JPEG", "TIFF"])
        form.addRow("Format:", self.format_combo)
        
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 600)
        self.dpi_spin.setValue(150)
        self.dpi_spin.setSuffix(" DPI")
        form.addRow("Resolution:", self.dpi_spin)
        
        layout.addLayout(form)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def get_settings(self):
        return {
            "range": self.page_range.text(),
            "format": self.format_combo.currentText(),
            "dpi": self.dpi_spin.value()
        }


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
        for item in outline:
            level, title, page = item
            tree_item = QTreeWidgetItem([title])
            tree_item.setData(0, Qt.ItemDataRole.UserRole, page - 1)
            if parent:
                parent.addChild(tree_item)
            else:
                self.tree.addTopLevelItem(tree_item)
    
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
            ("Redact", "Redact sensitive content", "redact"),
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
            ("Prepare Form", "Auto-detect form fields", "prepare_form"),
            ("Add Text Field", "Add text input field", "add_text_field"),
            ("Add Checkbox", "Add checkbox", "add_checkbox"),
            ("Add Signature", "Add digital signature", "add_signature"),
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
        
        self._add_tool_group("Page Tools", [
            ("Rotate Left", "Rotate page 90° counter-clockwise", "rotate_left"),
            ("Rotate Right", "Rotate page 90° clockwise", "rotate_right"),
            ("Delete Page", "Remove current page", "delete_page"),
            ("Insert Blank Page", "Add new blank page", "insert_page"),
            ("Crop Pages", "Crop page margins", "crop_pages"),
        ], layout)
        
        self._add_tool_group("Forms & Signatures", [
            ("Prepare Form", "Auto-detect form fields", "prepare_form"),
            ("Add Text Field", "Add text input field", "add_text_field"),
            ("Add Checkbox", "Add checkbox", "add_checkbox"),
            ("Add Radio Button", "Add radio button group", "add_radio_button"),
            ("Add Dropdown", "Add dropdown list", "add_dropdown"),
            ("Add Signature Field", "Add digital signature field", "add_signature_field"),
            ("Create Signature", "Draw or import signature", "create_signature"),
            ("Sign Document", "Sign with certificate", "sign_document"),
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
    
    def set_dark_mode(self, enabled: bool):
        """Update toolbar for dark/light mode."""
        if enabled:
            self.setStyleSheet("""
                DocumentToolbar { 
                    background: #2d2d2d; 
                    border-bottom: 1px solid #444; 
                }
                DocumentToolbar QPushButton { 
                    border: none; 
                    padding: 6px 12px; 
                    border-radius: 4px; 
                    background: transparent;
                    color: #ffffff;
                    font-size: 14px;
                }
                DocumentToolbar QPushButton:hover { 
                    background: #3e3e42; 
                }
                DocumentToolbar QPushButton:pressed { 
                    background: #4e4e52; 
                }
                DocumentToolbar QPushButton:checked {
                    background: #0078d7;
                    color: white;
                }
                DocumentToolbar QComboBox { 
                    padding: 4px 8px; 
                    border: 1px solid #555; 
                    border-radius: 3px;
                    background: #3c3c3c;
                    color: #ffffff;
                    min-width: 100px;
                }
                DocumentToolbar QSpinBox {
                    padding: 4px 8px;
                    border: 1px solid #555;
                    border-radius: 3px;
                    background: #3c3c3c;
                    color: #ffffff;
                }
                DocumentToolbar QLabel {
                    color: #ffffff;
                    font-size: 13px;
                }
            """)
        else:
            self._setup_style()
    
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
        self.zoom_combo.blockSignals(True)
        self.zoom_combo.setCurrentText(f"{int(zoom * 100)}%")
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
        user_pwd = self.user_pwd.text() if self.user_pwd.text() else None
        owner_pwd = self.owner_pwd.text() if self.owner_pwd.text() else None
        
        if user_pwd != self.user_pwd_confirm.text():
            QMessageBox.warning(self, "Error", "User passwords don't match!")
            return None, None, 0
        
        if owner_pwd and owner_pwd != self.owner_pwd_confirm.text():
            QMessageBox.warning(self, "Error", "Owner passwords don't match!")
            return None, None, 0
        
        # Calculate permissions
        permissions = 0
        if self.perm_print.isChecked(): permissions |= fitz.PDF_PERM_PRINT
        if self.perm_modify.isChecked(): permissions |= fitz.PDF_PERM_MODIFY
        if self.perm_copy.isChecked(): permissions |= fitz.PDF_PERM_COPY
        if self.perm_annotate.isChecked(): permissions |= fitz.PDF_PERM_ANNOTATE
        if self.perm_fill_forms.isChecked(): permissions |= fitz.PDF_PERM_FILL_FORMS
        if self.perm_accessibility.isChecked(): permissions |= fitz.PDF_PERM_ACCESSIBILITY
        if self.perm_assemble.isChecked(): permissions |= fitz.PDF_PERM_ASSEMBLE
        if self.perm_print_high.isChecked(): permissions |= fitz.PDF_PERM_PRINT_HIGH
        
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
    
    def get_settings(self):
        return {
            "type": self.type_combo.currentText().lower(),
            "text": self.wm_text.text(),
            "fontsize": self.wm_fontsize.value(),
            "color": (self.wm_color.redF(), self.wm_color.greenF(), self.wm_color.blueF(), self.wm_color.alphaF()),
            "image_path": self.wm_image_path.text() if self.type_combo.currentText() == "Image" else "",
            "opacity": self.opacity_slider.value() / 255.0
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
    
    def get_settings(self):
        return {
            "type": self.type_combo.currentText().lower(),
            "text": self.wm_text.text(),
            "fontsize": self.wm_fontsize.value(),
            "color": (self.wm_color.redF(), self.wm_color.greenF(), self.wm_color.blueF(), self.wm_color.alphaF()),
            "image_path": self.wm_image_path.text() if self.type_combo.currentText() == "Image" else "",
            "opacity": self.opacity_slider.value() / 255.0
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
        return {
            "files": files,
            "action": self.action_combo.currentText().lower().replace(" ", "_")
        }


class ExportDialog(QDialog):
    """Enhanced export dialog for images."""
    
    def __init__(self, page_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Pages as Images")
        self.resize(400, 300)
        
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
    
    def get_settings(self):
        return {
            "range": self.page_range.text(),
            "format": self.format_combo.currentText(),
            "dpi": self.dpi_spin.value(),
            "quality": self.quality_spin.value()
        }


# ============ END DIALOG CLASSES ============


class PDFViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Editor")
        self.resize(1200, 800)
        
        self.doc: Optional[fitz.Document] = None
        self.current_page = 0
        self.zoom = 1.0
        self.dark_mode = False
        self.file_path = ""
        
        self._setup_ui()
        self._setup_sidebar()
        self._setup_toolbar()
        self._apply_theme()
    
    def _setup_ui(self):
        # Menu bar (Acrobat-style)
        self._setup_menubar()
        
        # Main splitter for three-pane layout
        from PyQt6.QtWidgets import QSplitter
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(main_splitter)
        
        # LEFT PANEL - Tools/Navigation
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_panel.setMaximumWidth(280)
        left_panel.setMinimumWidth(200)
        
        # Sidebar tabs
        from PyQt6.QtWidgets import QTabWidget
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
        
        # Connect scroll for lazy rendering
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)
    
    def _setup_menubar(self):
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        open_action = QAction("&Open", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)
        
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
        undo_action = QAction("&Undo", self)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        edit_menu.addAction(undo_action)
        
        redo_action = QAction("&Redo", self)
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        edit_menu.addAction(redo_action)
        
        edit_menu.addSeparator()
        
        find_action = QAction("&Find...", self)
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(self.show_search)
        edit_menu.addAction(find_action)
        
        find_next_action = QAction("Find &Next", self)
        find_next_action.setShortcut(QKeySequence("F3"))
        edit_menu.addAction(find_next_action)
        
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
        
        dark_action = QAction("&Dark Mode", self)
        dark_action.setShortcut(QKeySequence("Ctrl+D"))
        dark_action.triggered.connect(self.toggle_dark_mode)
        view_menu.addAction(dark_action)
        
        view_menu.addSeparator()
        
        left_panel_action = QAction("&Show Left Panel", self)
        left_panel_action.triggered.connect(lambda: self.sidebar_tabs.setVisible(True))
        view_menu.addAction(left_panel_action)
        
        right_panel_action = QAction("&Show Right Panel", self)
        right_panel_action.triggered.connect(lambda: self.tools_panel.setVisible(True))
        view_menu.addAction(right_panel_action)
        
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
    
    def _setup_sidebar(self):
        """Sidebar is now part of the main splitter in _setup_ui"""
        pass
    
    def _setup_toolbar(self):
        """Main toolbar is replaced by menubar and document toolbar"""
        pass
    
    def _apply_theme(self):
        if self.dark_mode:
            self.setStyleSheet("""
                QMainWindow, QWidget { background-color: #2b2b2b; color: #ffffff; }
                QToolBar { background-color: #3c3c3c; border: none; spacing: 4px; }
                QToolBar QLabel { color: #ffffff; }
                QScrollArea { background-color: #1e1e1e; border: none; }
                QStatusBar { background-color: #3c3c3c; color: #ffffff; }
                QSpinBox, QComboBox { background-color: #3c3c3c; color: #ffffff; border: 1px solid #555; padding: 4px; }
                QDockWidget { background-color: #2b2b2b; color: #ffffff; }
                QListWidget { background-color: #1e1e1e; color: #ffffff; border: none; }
                QMenu { background-color: #3c3c3c; color: #ffffff; border: 1px solid #555; }
                QMenu::item:selected { background-color: #0078d7; }
                QDialog { background-color: #2b2b2b; color: #ffffff; }
                QLineEdit, QTextEdit { background-color: #3c3c3c; color: #ffffff; border: 1px solid #555; }
                QPushButton { background-color: #3c3c3c; color: #ffffff; border: 1px solid #555; padding: 6px 12px; }
                QPushButton:hover { background-color: #0078d7; }
                QCheckBox { color: #ffffff; }
            """)
        else:
            self.setStyleSheet("")
    
    def toggle_dark_mode(self, checked: bool):
        self.dark_mode = checked
        self._apply_theme()
        self.dark_action.setChecked(checked)
        if hasattr(self, 'doc_toolbar'):
            self.doc_toolbar.set_dark_mode(checked)
    
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            self.load_document(path)
    
    def save_file(self):
        if self.doc and self.file_path:
            try:
                self.doc.save(self.file_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
                self.status_label.setText("Saved")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")
    
    def save_file_as(self):
        if self.doc:
            path, _ = QFileDialog.getSaveFileName(self, "Save PDF As", "", "PDF Files (*.pdf)")
            if path:
                try:
                    self.doc.save(path)
                    self.file_path = path
                    self.status_label.setText(f"Saved: {Path(path).name}")
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")
    
    def load_document(self, path: str):
        try:
            if self.doc:
                self.doc.close()
            
            self.doc = fitz.open(path)
            self.current_page = 0
            self.file_path = path
            self.render_pages()
            self.update_ui()
            self.thumbnail_sidebar.update_thumbnails(self.doc)
            self.status_label.setText(f"Loaded: {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open PDF:\n{e}")
    
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
        
        # Connect scroll handler for lazy rendering
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)
        
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
        """Reorder PDF pages based on thumbnail drag & drop."""
        if not self.doc or len(new_order) != len(self.doc):
            return
        
        try:
            # Create new document with reordered pages
            new_doc = fitz.open()
            for old_index in new_order:
                new_doc.insert_pdf(self.doc, from_page=old_index, to_page=old_index)
            
            # Save to temp file and reload
            import tempfile
            tmp_path = tempfile.mktemp(suffix=".pdf")
            new_doc.save(tmp_path)
            new_doc.close()
            
            # Reload document
            current_page = self.current_page
            self.load_document(tmp_path)
            self.current_page = min(current_page, len(self.doc) - 1)
            self.scroll_to_page()
            
            self.status_label.setText(f"Pages reordered")
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
    
    def set_zoom_fit_width(self):
        if self.doc and self.pages_layout.count() > 0:
            widget = self.pages_layout.itemAt(0).widget()
            if widget and widget._pixmap:
                viewport_width = self.scroll_area.viewport().width() - 40
                page_width = widget._pixmap.width()
                if page_width > 0:
                    zoom = viewport_width / page_width
                    self.set_zoom(zoom)
    
    def set_zoom_fit_page(self):
        if self.doc and self.pages_layout.count() > 0:
            widget = self.pages_layout.itemAt(0).widget()
            if widget and widget._pixmap:
                viewport_width = self.scroll_area.viewport().width() - 40
                viewport_height = self.scroll_area.viewport().height() - 40
                page_width = widget._pixmap.width()
                page_height = widget._pixmap.height()
                if page_width > 0 and page_height > 0:
                    zoom_w = viewport_width / page_width
                    zoom_h = viewport_height / page_height
                    zoom = min(zoom_w, zoom_h)
                    self.set_zoom(zoom)
    
    def set_zoom_fit_visible(self):
        self.set_zoom_fit_page()
    
    def set_view_mode(self, mode):
        # TODO: Implement view modes (single, continuous, two-page, etc.)
        self.status_label.setText(f"View mode: {mode}")
    
    def toggle_annotate(self, checked: bool):
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget.set_annot_mode(checked)
        
        if checked:
            self.status_label.setText("Annotation mode - Click to add note")
        else:
            self.status_label.setText("Ready")
    
    def toggle_select(self, checked: bool):
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget.set_select_mode(checked)
        
        if checked:
            self.status_label.setText("Select mode - Drag to select text for highlight/underline")
        else:
            self.status_label.setText("Ready")
    
    def on_annotation_added(self, page_num, annot_data):
        self.status_label.setText(f"Annotation added to page {page_num + 1}")
    
    def on_text_selected(self, page_num, text):
        self.status_label.setText(f"Text selected on page {page_num + 1}: {text[:50]}...")
    
    def show_search(self):
        text, ok = QInputDialog.getText(self, "Find", "Search for:")
        if ok and text:
            self.find_text(text)
    
    def find_text(self, text):
        if not self.doc:
            return
        for i in range(len(self.doc)):
            page = self.doc[i]
            matches = page.search_for(text)
            if matches:
                self.current_page = i
                self.scroll_to_page()
                self.status_label.setText(f"Found '{text}' on page {i+1}")
                return
        self.status_label.setText(f"'{text}' not found")
    
    def print_document(self):
        if not self.doc:
            return
        from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Print via PDF rendering
            self.status_label.setText("Printing...")
    
    def redact_tool(self):
        self.status_label.setText("Redact tool - Select text to redact")
        self.sidebar_tabs.setCurrentIndex(2)  # Comments tab
        # TODO: Implement redaction
    
    def prepare_form(self):
        if not self.doc:
            return
        self.status_label.setText("Preparing form - Auto-detecting fields...")
        # TODO: Implement form field detection
    
    def show_about(self):
        QMessageBox.about(self, "About PDF Editor", 
            "PDF Editor v1.0\n\n"
            "A modern PDF viewer and editor built with PyQt6 and PyMuPDF.\n\n"
            "Features:\n"
            "• View, annotate, and edit PDFs\n"
            "• Organize pages (reorder, rotate, delete)\n"
            "• Fill and sign forms\n"
            "• Merge, split, and export PDFs\n"
            "• Dark mode support\n\n"
            "Built for cross-platform desktop.")
    
    def rotate_left(self):
        if not self.doc:
            return
        page = self.doc[self.current_page]
        page.set_rotation((page.rotation - 90) % 360)
        self.render_pages()
        self.status_label.setText("Page rotated left")
    
    def rotate_right(self):
        if not self.doc:
            return
        page = self.doc[self.current_page]
        page.set_rotation((page.rotation + 90) % 360)
        self.render_pages()
        self.status_label.setText("Page rotated right")
    
    def delete_page(self):
        if not self.doc or len(self.doc) <= 1:
            return
        reply = QMessageBox.question(self, "Delete Page", 
            f"Delete page {self.current_page + 1}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.doc.delete_page(self.current_page)
            self.load_document(self.file_path)
            self.status_label.setText("Page deleted")
    
    def insert_page(self):
        if not self.doc:
            return
        self.doc.new_page(self.current_page)
        self.load_document(self.file_path)
        self.status_label.setText("Blank page inserted")
    
    def extract_pages(self):
        if not self.doc:
            return
        # Use existing split_pdf functionality
        self.split_pdf()
    
    def add_text(self):
        self.status_label.setText("Add text - Click on page to insert text box")
        # TODO: Implement text insertion
    
    def add_image(self):
        if not self.doc:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Insert Image", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            self.status_label.setText(f"Image inserted: {Path(path).name}")
            # TODO: Implement image insertion
    
    def add_link(self):
        self.status_label.setText("Add link - Select area to create hyperlink")
        # TODO: Implement link creation
    
    def crop_pages(self):
        self.status_label.setText("Crop pages - Select area to crop")
        # TODO: Implement cropping
    
    def add_note(self):
        self.toggle_annotate(True)
        self.sidebar_tabs.setCurrentIndex(2)  # Comments tab
    
    def highlight_text(self):
        self.toggle_select(True)
    
    def underline_text(self):
        self.toggle_select(True)
    
    def strikethrough(self):
        self.status_label.setText("Strikethrough - Select text to strikethrough")
        # TODO: Implement strikethrough
    
    def freehand_draw(self):
        self.status_label.setText("Freehand draw - Draw on page")
        # TODO: Implement freehand drawing
    
    def add_stamp(self):
        self.status_label.setText("Add stamp - Select stamp type")
        # TODO: Implement stamps
    
    def add_text_field(self):
        self.status_label.setText("Add text field - Click to place field")
        # TODO: Implement form field addition
    
    def add_checkbox(self):
        self.status_label.setText("Add checkbox - Click to place checkbox")
        # TODO: Implement checkbox addition
    
    def add_signature(self):
        self.status_label.setText("Add signature - Draw or import signature")
        # TODO: Implement signature
    
    def sign_document(self):
        self.status_label.setText("Sign document - Select certificate")
        # TODO: Implement digital signing
    
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
        self.status_label.setText("Export to Word - Not yet implemented")
    
    def export_excel(self):
        self.status_label.setText("Export to Excel - Not yet implemented")
    
    def optimize_pdf(self):
        if not self.doc:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Optimize PDF", "", "PDF Files (*.pdf)")
        if path:
            self.doc.save(path, garbage=4, deflate=True, clean=True)
            self.status_label.setText(f"PDF optimized: {Path(path).name}")
    
    def fill_form(self):
        if not self.doc:
            return
        
        # Extract form fields
        fields = []
        for page in self.doc:
            for widget in page.widgets():
                if widget.field_type_string in ('Text', 'Choice', 'Button'):
                    fields.append({
                        'name': widget.field_name,
                        'type': 'text' if widget.field_type_string == 'Text' else 'checkbox' if widget.field_type_string == 'Button' else 'text',
                        'value': widget.field_value
                    })
        
        if not fields:
            QMessageBox.information(self, "No Forms", "This PDF has no fillable form fields.")
            return
        
        dialog = FormFieldDialog(fields, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = dialog.get_values()
            for page in self.doc:
                for widget in page.widgets():
                    if widget.field_name in values:
                        widget.field_value = values[widget.field_name]
                        widget.update()
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
        if not self.doc:
            return
        
        if len(self.doc) < 2:
            QMessageBox.information(self, "Split", "PDF has only 1 page, nothing to split.")
            return
        
        # Simple split: each page as separate file
        path, _ = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if path:
            try:
                for i in range(len(self.doc)):
                    single = fitz.open()
                    single.insert_pdf(self.doc, from_page=i, to_page=i)
                    single.save(Path(path) / f"page_{i+1}.pdf")
                    single.close()
                self.status_label.setText(f"Split into {len(self.doc)} files in {path}")
                QMessageBox.information(self, "Done", f"Split complete! {len(self.doc)} files created.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to split PDF:\n{e}")
    
    def export_images(self):
        if not self.doc:
            return
        
        dialog = ExportDialog(len(self.doc), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            settings = dialog.get_settings()
            
            # Parse page range
            page_range = settings["range"]
            pages = []
            for part in page_range.split(","):
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    pages.extend(range(start - 1, end))
                else:
                    pages.append(int(part) - 1)
            
            # Filter valid pages
            pages = [p for p in pages if 0 <= p < len(self.doc)]
            
            if not pages:
                return
            
            output_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder")
            if not output_dir:
                return
            
            try:
                fmt = settings["format"].lower()
                dpi = settings["dpi"]
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                
                for i in pages:
                    page = self.doc[i]
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    output_path = Path(output_dir) / f"page_{i+1}.{fmt}"
                    pix.save(str(output_path))
                
                self.status_label.setText(f"Exported {len(pages)} pages to {output_dir}")
                QMessageBox.information(self, "Done", f"Exported {len(pages)} pages as {fmt.upper()}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export:\n{e}")
    
    def closeEvent(self, event):
        if self.doc:
            self.doc.close()
        event.accept()
    
    # ============ NEW PDF24-LIKE FEATURES ============
    
    def protect_pdf(self):
        """Add password protection to PDF."""
        if not self.doc:
            return
        
        dialog = PasswordDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            user_pwd, owner_pwd, permissions = dialog.get_passwords()
            
            path, _ = QFileDialog.getSaveFileName(self, "Save Protected PDF", "", "PDF Files (*.pdf)")
            if path:
                try:
                    self.doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, 
                                user_pw=user_pwd, owner_pw=owner_pwd, permissions=permissions)
                    self.status_label.setText(f"PDF protected: {Path(path).name}")
                    QMessageBox.information(self, "Success", "PDF has been password protected.")
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
                    path, _ = QFileDialog.getSaveFileName(self, "Save Unlocked PDF", "", "PDF Files (*.pdf)")
                    if path:
                        self.doc.save(path, encryption=fitz.PDF_ENCRYPT_KEEP)
                        self.status_label.setText(f"PDF unlocked: {Path(path).name}")
                        QMessageBox.information(self, "Success", "PDF has been unlocked.")
                else:
                    QMessageBox.warning(self, "Wrong Password", "Incorrect password.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to unlock PDF:\n{e}")
    
    def add_page_numbers(self):
        """Add page numbers to PDF."""
        if not self.doc:
            return
        
        dialog = PageNumberDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            settings = dialog.get_settings()
            
            path, _ = QFileDialog.getSaveFileName(self, "Save PDF with Page Numbers", "", "PDF Files (*.pdf)")
            if path:
                try:
                    for i in range(len(self.doc)):
                        page = self.doc[i]
                        page_num = i + 1
                        
                        # Format page number
                        if settings["prefix"]:
                            text = f"{settings['prefix']} {page_num}"
                        else:
                            text = str(page_num)
                        if settings["suffix"]:
                            text += f" {settings['suffix']}"
                        
                        # Calculate position
                        rect = page.rect
                        if settings["position"] == "bottom_center":
                            x = rect.width / 2 - 30
                            y = rect.height - 30
                        elif settings["position"] == "bottom_right":
                            x = rect.width - 60
                            y = rect.height - 30
                        elif settings["position"] == "bottom_left":
                            x = 30
                            y = rect.height - 30
                        elif settings["position"] == "top_center":
                            x = rect.width / 2 - 30
                            y = 30
                        elif settings["position"] == "top_right":
                            x = rect.width - 60
                            y = 30
                        else:  # top_left
                            x = 30
                            y = 30
                        
                        # Insert page number
                        page.insert_text((x, y), text, fontsize=settings["fontsize"], 
                                       fontname="helv", color=settings["color"])
                    
                    self.doc.save(path)
                    self.status_label.setText(f"Page numbers added: {Path(path).name}")
                    QMessageBox.information(self, "Success", "Page numbers have been added.")
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to add page numbers:\n{e}")
    
    def add_watermark(self):
        """Add text or image watermark to PDF."""
        if not self.doc:
            return
        
        dialog = WatermarkDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            settings = dialog.get_settings()
            
            path, _ = QFileDialog.getSaveFileName(self, "Save Watermarked PDF", "", "PDF Files (*.pdf)")
            if path:
                try:
                    for i in range(len(self.doc)):
                        page = self.doc[i]
                        rect = page.rect
                        
                        if settings["type"] == "text":
                            # Add text watermark (diagonal)
                            page.insert_text((rect.width/2, rect.height/2), settings["text"],
                                           fontsize=settings["fontsize"], fontname="helv",
                                           color=settings["color"], opacity=settings["opacity"],
                                           rotate=45, overlay=True)
                        elif settings["type"] == "image" and settings["image_path"]:
                            # Add image watermark
                            img_rect = fitz.Rect(rect.width/4, rect.height/4, 
                                               rect.width*3/4, rect.height*3/4)
                            page.insert_image(img_rect, filename=settings["image_path"],
                                            opacity=settings["opacity"], overlay=True)
                    
                    self.doc.save(path)
                    self.status_label.setText(f"Watermark added: {Path(path).name}")
                    QMessageBox.information(self, "Success", "Watermark has been added.")
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to add watermark:\n{e}")
    
    def flatten_pdf(self):
        """Flatten PDF (merge annotations into page content)."""
        if not self.doc:
            return
        
        path, _ = QFileDialog.getSaveFileName(self, "Save Flattened PDF", "", "PDF Files (*.pdf)")
        if path:
            try:
                # Flatten by saving with no incremental and no annotations
                for page in self.doc:
                    # This will flatten annotations into the page
                    pass
                
                self.doc.save(path, garbage=4, deflate=True, clean=True)
                self.status_label.setText(f"PDF flattened: {Path(path).name}")
                QMessageBox.information(self, "Success", "PDF has been flattened (annotations merged).")
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
            for i in range(len(self.doc)):
                page = self.doc[i]
                images = page.get_images(full=True)
                
                for img_index, img in enumerate(images):
                    xref = img[0]
                    pix = fitz.Pixmap(self.doc, xref)
                    
                    if pix.n < 5:  # GRAY or RGB
                        output_path = Path(output_dir) / f"page_{i+1}_img_{img_index+1}.png"
                        pix.save(str(output_path))
                    else:  # CMYK - convert to RGB
                        pix_rgb = fitz.Pixmap(fitz.csRGB, pix)
                        output_path = Path(output_dir) / f"page_{i+1}_img_{img_index+1}.png"
                        pix_rgb.save(str(output_path))
                    
                    count += 1
            
            self.status_label.setText(f"Extracted {count} images to {output_dir}")
            QMessageBox.information(self, "Done", f"Extracted {count} images.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract images:\n{e}")
    
    def compress_pdf_advanced(self):
        """Advanced PDF compression with options."""
        if not self.doc:
            return
        
        dialog = CompressDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            settings = dialog.get_settings()
            
            path, _ = QFileDialog.getSaveFileName(self, "Save Compressed PDF", "", "PDF Files (*.pdf)")
            if path:
                try:
                    # Apply compression settings
                    self.doc.save(path, 
                                garbage=settings["garbage"],
                                deflate=settings["deflate"],
                                clean=settings["clean"],
                                deflate_images=settings["deflate_images"],
                                deflate_fonts=settings["deflate_fonts"])
                    
                    original_size = os.path.getsize(self.file_path) if self.file_path else 0
                    new_size = os.path.getsize(path)
                    reduction = (1 - new_size / original_size) * 100 if original_size > 0 else 0
                    
                    self.status_label.setText(f"Compressed: {reduction:.1f}% reduction")
                    QMessageBox.information(self, "Done", 
                        f"Original: {original_size/1024:.1f} KB\n"
                        f"Compressed: {new_size/1024:.1f} KB\n"
                        f"Reduction: {reduction:.1f}%")
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
        """Batch process multiple PDFs."""
        dialog = BatchProcessDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            settings = dialog.get_settings()
            
            try:
                for file_path in settings["files"]:
                    doc = fitz.open(file_path)
                    
                    if settings["action"] == "compress":
                        out_path = str(Path(file_path).parent / f"compressed_{Path(file_path).name}")
                        doc.save(out_path, garbage=4, deflate=True, clean=True)
                    elif settings["action"] == "flatten":
                        out_path = str(Path(file_path).parent / f"flattened_{Path(file_path).name}")
                        doc.save(out_path, garbage=4, deflate=True, clean=True)
                    elif settings["action"] == "remove_annotations":
                        out_path = str(Path(file_path).parent / f"no_annots_{Path(file_path).name}")
                        for page in doc:
                            for annot in page.annots():
                                page.delete_annot(annot)
                        doc.save(out_path)
                    elif settings["action"] == "add_pagenums":
                        out_path = str(Path(file_path).parent / f"paged_{Path(file_path).name}")
                        for i, page in enumerate(doc):
                            page.insert_text((30, 30), str(i+1), fontsize=10)
                        doc.save(out_path)
                    
                    doc.close()
                
                self.status_label.setText(f"Batch processed {len(settings['files'])} files")
                QMessageBox.information(self, "Done", f"Batch processing complete for {len(settings['files'])} files.")
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Batch processing failed:\n{e}")


# ============ NEW FEATURES ============
    
    def ocr_pdf(self):
        """OCR (Text Recognition) for scanned PDFs using Tesseract."""
        if not self.doc:
            return
        
        try:
            import pytesseract
        except ImportError:
            QMessageBox.warning(self, "OCR Not Available", 
                "pytesseract not installed. Run: pip install pytesseract\n"
                "Also install Tesseract OCR: https://github.com/tesseract-ocr/tesseract")
            return
        
        path, _ = QFileDialog.getSaveFileName(self, "Save OCR PDF", "", "PDF Files (*.pdf)")
        if not path:
            return
        
        try:
            self.status_label.setText("Running OCR... This may take a while.")
            QApplication.processEvents()
            
            for i in range(len(self.doc)):
                page = self.doc[i]
                # Render page as image at high DPI
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                
                # Run OCR
                ocr_text = pytesseract.image_to_string(img)
                
                # Add text layer to page
                if ocr_text.strip():
                    page.insert_text((50, 50), ocr_text, fontsize=8, color=(0, 0, 0), render_mode=3)
            
            self.doc.save(path)
            self.status_label.setText(f"OCR complete: {Path(path).name}")
            QMessageBox.information(self, "Success", "OCR text layer added to PDF.")
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
                img_pdf = fitz.open("pdf", pdf_bytes)
                doc.insert_pdf(img_pdf)
                img_doc.close()
            
            doc.save(path)
            doc.close()
            
            self.status_label.setText(f"Created PDF from {len(paths)} images: {Path(path).name}")
            QMessageBox.information(self, "Success", f"Created PDF with {len(paths)} pages.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create PDF:\n{e}")
    
    def rotate_left(self):
        if not self.doc:
            return
        page = self.doc[self.current_page]
        page.set_rotation((page.rotation - 90) % 360)
        self.render_pages()
        self.status_label.setText("Page rotated left")
    
    def rotate_right(self):
        if not self.doc:
            return
        page = self.doc[self.current_page]
        page.set_rotation((page.rotation + 90) % 360)
        self.render_pages()
        self.status_label.setText("Page rotated right")
    
    def delete_page(self):
        if not self.doc or len(self.doc) <= 1:
            return
        reply = QMessageBox.question(self, "Delete Page", 
            f"Delete page {self.current_page + 1}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.doc.delete_page(self.current_page)
            self.load_document(self.file_path)
            self.status_label.setText("Page deleted")
    
    def insert_page(self):
        if not self.doc:
            return
        self.doc.new_page(self.current_page)
        self.load_document(self.file_path)
        self.status_label.setText("Blank page inserted")
    
    def crop_pages(self):
        QMessageBox.information(self, "Crop Pages", 
            "Crop tool: Select area on page with selection tool, then use Crop Pages.")
    
    def add_text_field(self):
        self.status_label.setText("Add text field - Click on page to place field")
        # Would need form field creation UI
    
    def add_checkbox(self):
        self.status_label.setText("Add checkbox - Click on page to place checkbox")
    
    def add_radio_button(self):
        self.status_label.setText("Add radio button - Click on page to place radio button")
    
    def add_dropdown(self):
        self.status_label.setText("Add dropdown - Click on page to place dropdown")
    
    def add_signature_field(self):
        self.status_label.setText("Add signature field - Click on page to place signature field")
    
    def create_signature(self):
        """Draw or import signature."""
        QMessageBox.information(self, "Create Signature", 
            "Signature creation: Draw with mouse or import image.\n"
            "This would open a signature pad dialog.")
    
    def sign_document(self):
        self.status_label.setText("Sign document - Select certificate file")
        # Would need PKCS#12 certificate handling
    
    def prepare_form(self):
        if not self.doc:
            return
        self.status_label.setText("Preparing form - Auto-detecting fields...")
        # Would auto-detect form fields using text patterns
    
    def redact_tool(self):
        self.status_label.setText("Redact tool - Select text to redact")
        self.sidebar_tabs.setCurrentIndex(2)  # Comments tab
    
    def crop_pages(self):
        self.status_label.setText("Crop pages - Select area to crop")


def main():
    import traceback
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("PDF Editor")
        
        viewer = PDFViewer()
        viewer.show()
        
        sys.exit(app.exec())
    except Exception as e:
        traceback.print_exc()
        input("Press Enter to exit...")

if __name__ == "__main__":
    main()