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