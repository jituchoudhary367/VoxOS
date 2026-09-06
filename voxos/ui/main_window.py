import os
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget, QLabel, QTextEdit
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QPalette

class VoxOSOverlay(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("VoxOS Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.init_ui()
        self.center_window()
        
    def init_ui(self):
        self.setFixedSize(400, 150)
        
        central_widget = QWidget(self)
        central_widget.setObjectName("CentralWidget")
        
        # Style to create a dark glassmorphism look
        central_widget.setStyleSheet("""
            QWidget#CentralWidget {
                background-color: rgba(30, 30, 30, 220);
                border-radius: 15px;
                border: 1px solid rgba(255, 255, 255, 30);
            }
        """)
        
        layout = QVBoxLayout(central_widget)
        
        self.status_label = QLabel("VoxOS: Sleeping", self)
        self.status_label.setStyleSheet("color: #00FFCC; font-weight: bold; font-size: 16px; font-family: 'Segoe UI';")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.transcript_area = QTextEdit(self)
        self.transcript_area.setReadOnly(True)
        self.transcript_area.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                color: #FFFFFF;
                border: none;
                font-family: 'Segoe UI';
                font-size: 14px;
            }
        """)
        
        layout.addWidget(self.status_label)
        layout.addWidget(self.transcript_area)
        
        self.setCentralWidget(central_widget)

    def center_window(self):
        # We can center the window relative to the primary screen.
        screen = self.screen()
        if screen:
            screen_geometry = screen.geometry()
            x = (screen_geometry.width() - self.width()) // 2
            # Put it near the top
            y = screen_geometry.height() // 6
            self.move(x, y)
            
    def update_status(self, text: str, color: str = "#00FFCC"):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 16px; font-family: 'Segoe UI';")
        
    def append_transcript(self, text: str):
        self.transcript_area.append(text)
        self.transcript_area.verticalScrollBar().setValue(self.transcript_area.verticalScrollBar().maximum())
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
            
    def mouseMoveEvent(self, event):
        if hasattr(self, '_drag_pos'):
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()
            
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if hasattr(self, '_drag_pos'):
                del self._drag_pos
