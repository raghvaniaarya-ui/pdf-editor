import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QMessageBox,
    QVBoxLayout, QHBoxLayout, QWidget, QToolBar,
    QLabel, QSpinBox, QSlider, QComboBox
)
from PyQt6.QtCore import Qt, QRectF, QSize
from PyQt6.QtGui import QAction, QIcon, QPixmap, QImage, QKeySequence

import pymupdf as fitz


class PDFPageWidget(QWidget):
    """Widget to display a single PDF page with zoom support."""
    
    def __init__(self, page: fitz.Page, page_num: int, zoom: float = 1.0):
        super().__init__()
        self.page = page
        self.page_num = page_num
        self.zoom = zoom
        self.setMinimumSize(100, 100)
        self._render_pixmap()
    
    def _render_pixmap(self):
        mat = fitz.Matrix(self.zoom, self.zoom)
        pix = self.page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        self.pixmap = QPixmap.fromImage(img)
        self.setFixedSize(self.pixmap.size())
    
    def set_zoom(self, zoom: float):
        self.zoom = zoom
        self._render_pixmap()
        self.update()
    
    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.pixmap)


class PDFViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Editor")
        self.resize(1200, 800)
        
        self.doc: fitz.Document | None = None
        self.current_page = 0
        self.zoom = 1.0
        
        self._setup_ui()
        self._setup_toolbar()
        self._setup_shortcuts()
    
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Scroll area for pages
        from PyQt6.QtWidgets import QScrollArea
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
        self.annotate_action = QAction("Annotate", self)
        self.annotate_action.setCheckable(True)
        self.annotate_action.setShortcut("A")
        self.annotate_action.triggered.connect(self.toggle_annotate)
        toolbar.addAction(self.annotate_action)
        
        self.select_action = QAction("Select Text", self)
        self.select_action.setCheckable(True)
        self.select_action.setShortcut("S")
        self.select_action.triggered.connect(self.toggle_select)
        toolbar.addAction(self.select_action)
    
    def _setup_shortcuts(self):
        pass
    
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
        
        # Render all pages
        for i in range(len(self.doc)):
            page = self.doc[i]
            page_widget = PDFPageWidget(page, i, self.zoom)
            self.pages_layout.addWidget(page_widget)
        
        self.page_spin.setMaximum(len(self.doc))
        self.page_spin.setValue(self.current_page + 1)
    
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
        # Scroll to the page widget
        if self.pages_layout.count() > self.current_page:
            widget = self.pages_layout.itemAt(self.current_page).widget()
            if widget:
                self.scroll_area.ensureWidgetVisible(widget)
    
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
            # TODO: Calculate fit width zoom
            return
        try:
            zoom = int(text.replace("%", "")) / 100
            self.set_zoom(zoom)
        except ValueError:
            pass
    
    def save_file(self):
        if self.doc and hasattr(self, 'file_path'):
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
        if checked:
            self.select_action.setChecked(False)
            self.status_label.setText("Annotation mode - Click to add note")
        else:
            self.status_label.setText("Ready")
    
    def toggle_select(self, checked: bool):
        if checked:
            self.annotate_action.setChecked(False)
            self.status_label.setText("Select mode - Drag to select text")
        else:
            self.status_label.setText("Ready")
    
    def closeEvent(self, event):
        if self.doc:
            self.doc.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Editor")
    
    viewer = PDFViewer()
    viewer.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()