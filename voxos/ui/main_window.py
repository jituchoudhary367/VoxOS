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
        self.setFixedSize(420, 160)
        
        central_widget = QWidget(self)
        central_widget.setObjectName("CentralWidget")
        
        # Professional frosted glass look
        central_widget.setStyleSheet("""
            QWidget#CentralWidget {
                background-color: rgba(15, 17, 21, 230);
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 40);
            }
        """)
        
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(15, 15, 15, 15)
        
        self.status_label = QLabel("VoxOS: Sleeping", self)
        self.status_label.setStyleSheet("color: #94a3b8; font-weight: 700; font-size: 15px; font-family: 'Segoe UI', sans-serif; letter-spacing: 0.5px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.transcript_area = QTextEdit(self)
        self.transcript_area.setReadOnly(True)
        self.transcript_area.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                color: #f8fafc;
                border: none;
                font-family: 'Segoe UI', sans-serif;
                font-size: 14px;
                line-height: 1.5;
            }
        """)
        
        layout.addWidget(self.status_label)
        layout.addWidget(self.transcript_area)
        
        self.setCentralWidget(central_widget)

    def center_window(self):
        screen = self.screen()
        if screen:
            screen_geometry = screen.geometry()
            x = (screen_geometry.width() - self.width()) // 2
            y = screen_geometry.height() // 8
            self.move(x, y)
            
    def update_status(self, text: str, color: str = "#3b82f6"):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 700; font-size: 15px; font-family: 'Segoe UI', sans-serif; letter-spacing: 0.5px;")
        
    def append_transcript(self, text: str):
        # Format user vs agent for overlay
        if text.startswith("You:"):
            formatted = f"<span style='color: #94a3b8;'>{text}</span>"
        elif text.startswith("VoxOS:"):
            formatted = f"<span style='color: #ffffff; font-weight: bold;'>{text}</span>"
        else:
            formatted = text
            
        self.transcript_area.append(formatted)
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
