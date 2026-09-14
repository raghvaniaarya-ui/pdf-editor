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
    QTextEdit, QPushButton, QCheckBox
)
from PyQt6.QtCore import Qt, QRectF, QSize, QPoint, pyqtSignal, QTimer
from PyQt6.QtGui import (
    QAction, QIcon, QPixmap, QImage, QKeySequence,
    QPainter, QPen, QColor, QBrush, QCursor,
    QFont, QShortcut
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
        self.selection_start = None
        self.selection_end = None
        
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
        self.setCursor(Qt.CursorShape.IBeamCursor if enabled else Qt.CursorShape.ArrowCursor)
    
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
    
    def mouseMoveEvent(self, event):
        if self.select_mode and self.selection_start and event.buttons() & Qt.MouseButton.LeftButton:
            self.selection_end = event.position()
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


class ThumbnailWidget(QWidget):
    """Thumbnail widget for page navigation."""
    
    page_clicked = pyqtSignal(int)
    
    def __init__(self, page: fitz.Page, page_num: int):
        super().__init__()
        self.page_num = page_num
        self.setFixedSize(120, 160)
        self.setToolTip(f"Page {page_num + 1}")
        
        # Render thumbnail
        mat = fitz.Matrix(0.3, 0.3)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        self.pixmap = QPixmap.fromImage(img)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.page_clicked.emit(self.page_num)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.pixmap)
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.drawText(5, 15, f"Page {self.page_num + 1}")


class ThumbnailSidebar(QDockWidget):
    """Sidebar with page thumbnails."""
    
    page_selected = pyqtSignal(int)
    
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
    
    def update_thumbnails(self, doc: fitz.Document):
        self.list_widget.clear()
        for i in range(len(doc)):
            page = doc[i]
            item = QListWidgetItem()
            widget = ThumbnailWidget(page, i)
            widget.page_clicked.connect(self.page_selected.emit)
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
    
    def set_current_page(self, page_num: int):
        self.list_widget.setCurrentRow(page_num)


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
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        
        self.pages_container = QWidget()
        self.pages_layout = QVBoxLayout(self.pages_container)
        self.pages_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.pages_layout.setSpacing(20)
        
        self.scroll_area.setWidget(self.pages_container)
        layout.addWidget(self.scroll_area)
        
        # Status bar
        self.status_label = QLabel("No document loaded")
        self.statusBar().addWidget(self.status_label)
        
        self.page_label = QLabel("Page: 0/0")
        self.statusBar().addPermanentWidget(self.page_label)
        
        self.zoom_label = QLabel("Zoom: 100%")
        self.statusBar().addPermanentWidget(self.zoom_label)
    
    def _setup_sidebar(self):
        self.thumbnail_sidebar = ThumbnailSidebar(self)
        self.thumbnail_sidebar.page_selected.connect(self.go_to_page)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.thumbnail_sidebar)
    
    def _setup_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setIconSize(QSize(24, 24))
        self.addToolBar(toolbar)
        
        # File actions
        open_action = QAction("Open", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file)
        toolbar.addAction(open_action)
        
        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_file)
        toolbar.addAction(save_action)
        
        save_as_action = QAction("Save As", self)
        save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_action.triggered.connect(self.save_file_as)
        toolbar.addAction(save_as_action)
        
        toolbar.addSeparator()
        
        # Merge/Split
        merge_action = QAction("Merge PDFs", self)
        merge_action.setShortcut("Ctrl+M")
        merge_action.triggered.connect(self.merge_pdfs)
        toolbar.addAction(merge_action)
        
        split_action = QAction("Split PDF", self)
        split_action.triggered.connect(self.split_pdf)
        toolbar.addAction(split_action)
        
        toolbar.addSeparator()
        
        # Navigation
        prev_action = QAction("Previous", self)
        prev_action.setShortcut(QKeySequence.StandardKey.MoveToPreviousPage)
        prev_action.triggered.connect(self.prev_page)
        toolbar.addAction(prev_action)
        
        next_action = QAction("Next", self)
        next_action.setShortcut(QKeySequence.StandardKey.MoveToNextPage)
        next_action.triggered.connect(self.next_page)
        toolbar.addAction(next_action)
        
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.valueChanged.connect(self.go_to_page)
        toolbar.addWidget(QLabel("Page:"))
        toolbar.addWidget(self.page_spin)
        
        toolbar.addSeparator()
        
        # Zoom
        zoom_out_action = QAction("Zoom Out", self)
        zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_out_action.triggered.connect(self.zoom_out)
        toolbar.addAction(zoom_out_action)
        
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(["50%", "75%", "100%", "125%", "150%", "200%", "300%", "Fit Width"])
        self.zoom_combo.setCurrentText("100%")
        self.zoom_combo.currentTextChanged.connect(self.set_zoom_from_combo)
        toolbar.addWidget(self.zoom_combo)
        
        zoom_in_action = QAction("Zoom In", self)
        zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        zoom_in_action.triggered.connect(self.zoom_in)
        toolbar.addAction(zoom_in_action)
        
        toolbar.addSeparator()
        
        # Edit actions
        self.annotate_action = QAction("Add Note", self)
        self.annotate_action.setCheckable(True)
        self.annotate_action.setShortcut("N")
        self.annotate_action.triggered.connect(self.toggle_annotate)
        toolbar.addAction(self.annotate_action)
        
        self.select_action = QAction("Select Text", self)
        self.select_action.setCheckable(True)
        self.select_action.setShortcut("S")
        self.select_action.triggered.connect(self.toggle_select)
        toolbar.addAction(self.select_action)
        
        # Form filling
        form_action = QAction("Fill Form", self)
        form_action.setShortcut("F")
        form_action.triggered.connect(self.fill_form)
        toolbar.addAction(form_action)
        
        toolbar.addSeparator()
        
        # Export
        export_action = QAction("Export Images", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export_images)
        toolbar.addAction(export_action)
        
        # Dark mode
        self.dark_action = QAction("Dark Mode", self)
        self.dark_action.setCheckable(True)
        self.dark_action.setShortcut("Ctrl+D")
        self.dark_action.triggered.connect(self.toggle_dark_mode)
        toolbar.addAction(self.dark_action)
        
        # View thumbnails
        self.thumb_action = QAction("Thumbnails", self)
        self.thumb_action.setCheckable(True)
        self.thumb_action.setChecked(True)
        self.thumb_action.triggered.connect(self.thumbnail_sidebar.setVisible)
        toolbar.addAction(self.thumb_action)
    
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
    
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            self.load_document(path)
    
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
        
        self.page_spin.setMaximum(len(self.doc))
        self.page_spin.setValue(self.current_page + 1)
        
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
            self.page_label.setText(f"Page: {self.current_page + 1}/{len(self.doc)}")
            self.zoom_label.setText(f"Zoom: {int(self.zoom * 100)}%")
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
    
    def go_to_page(self, page: int):
        if self.doc and 1 <= page <= len(self.doc):
            self.current_page = page - 1
            self.scroll_to_page()
    
    def scroll_to_page(self):
        self.page_spin.setValue(self.current_page + 1)
        self.update_ui()
        self.thumbnail_sidebar.set_current_page(self.current_page)
        
        if self.pages_layout.count() > self.current_page:
            widget = self.pages_layout.itemAt(self.current_page).widget()
            if widget:
                self.scroll_area.ensureWidgetVisible(widget)
    
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
        self.zoom_combo.setCurrentText(f"{int(zoom * 100)}%")
    
    def set_zoom_from_combo(self, text: str):
        if text == "Fit Width":
            if self.doc and self.pages_layout.count() > 0:
                widget = self.pages_layout.itemAt(0).widget()
                if widget:
                    viewport_width = self.scroll_area.viewport().width() - 40
                    page_width = widget.pixmap.width()
                    zoom = viewport_width / page_width
                    self.set_zoom(zoom)
            return
        try:
            zoom = int(text.replace("%", "")) / 100
            self.set_zoom(zoom)
        except ValueError:
            pass
    
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
    
    def toggle_annotate(self, checked: bool):
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget.set_annot_mode(checked)
        
        if checked:
            self.select_action.setChecked(False)
            self.status_label.setText("Annotation mode - Click to add note")
        else:
            self.status_label.setText("Ready")
    
    def toggle_select(self, checked: bool):
        for i in range(self.pages_layout.count()):
            widget = self.pages_layout.itemAt(i).widget()
            if isinstance(widget, PDFPageWidget):
                widget.set_select_mode(checked)
        
        if checked:
            self.annotate_action.setChecked(False)
            self.status_label.setText("Select mode - Drag to select text for highlight/underline")
        else:
            self.status_label.setText("Ready")
    
    def on_annotation_added(self, page_num, annot_data):
        self.status_label.setText(f"Annotation added to page {page_num + 1}")
    
    def on_text_selected(self, page_num, text):
        self.status_label.setText(f"Text selected on page {page_num + 1}: {text[:50]}...")
    
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