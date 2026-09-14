import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QPushButton, QFrame, QTextEdit, QScrollArea
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

class InteractionDashboard(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VoxOS Interaction Dashboard")
        self.setMinimumSize(900, 600)
        
        # Professional Dark Theme & Typography
        self.setStyleSheet("""
            QWidget {
                background-color: #0f1115;
                color: #e2e8f0;
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
            }
            
            /* Sidebar Styling */
            #sidebar {
                background-color: #161921;
                border-right: 1px solid #232733;
            }
            #sidebar_title {
                font-size: 24px;
                font-weight: bold;
                color: #ffffff;
                padding: 10px 0px 5px 0px;
                letter-spacing: 1px;
            }
            #sidebar_subtitle {
                font-size: 12px;
                color: #94a3b8;
                padding-bottom: 20px;
            }
            
            /* Main Content Area */
            #content_area {
                background-color: #0f1115;
            }
            
            /* Live Feed Frame */
            QFrame#feed_frame {
                background-color: #1a1d27;
                border: 1px solid #282c3a;
                border-radius: 12px;
            }
            
            QLabel#status_indicator {
                font-size: 16px;
                font-weight: 600;
                color: #3b82f6;
                padding: 10px;
            }
            
            QTextEdit {
                background-color: transparent;
                color: #e2e8f0;
                border: none;
                font-size: 15px;
                line-height: 1.6;
            }
            
            /* Action Buttons */
            QPushButton#nav_btn {
                background-color: #262b3d;
                color: #ffffff;
                text-align: left;
                padding: 12px 15px;
                border-radius: 6px;
                border: none;
                font-weight: 600;
                font-size: 13px;
                margin-bottom: 5px;
            }
            QPushButton#nav_btn:hover {
                background-color: #3b82f6;
            }
        """)
        
        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # --- LEFT SIDEBAR ---
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(250)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(25, 30, 25, 30)
        sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        logo_label = QLabel("VoxOS Hub")
        logo_label.setObjectName("sidebar_title")
        version_label = QLabel("Agent Interaction Dashboard")
        version_label.setObjectName("sidebar_subtitle")
        
        # Navigation Items
        btn_live = QPushButton("Live Feed")
        btn_live.setObjectName("nav_btn")
        btn_live.setStyleSheet("background-color: #3b82f6;") # Active state
        
        btn_history = QPushButton("Session History")
        btn_history.setObjectName("nav_btn")
        
        sidebar_layout.addWidget(logo_label)
        sidebar_layout.addWidget(version_label)
        sidebar_layout.addSpacing(20)
        sidebar_layout.addWidget(btn_live)
        sidebar_layout.addWidget(btn_history)
        sidebar_layout.addStretch()
        
        # --- RIGHT CONTENT AREA ---
        content = QFrame()
        content.setObjectName("content_area")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(40, 40, 40, 40)
        
        header = QLabel("Live Agent Interaction")
        header.setStyleSheet("font-size: 24px; font-weight: 600; color: #ffffff;")
        
        self.status_label = QLabel("Status: Idle")
        self.status_label.setObjectName("status_indicator")
        
        header_layout = QHBoxLayout()
        header_layout.addWidget(header)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)
        
        content_layout.addLayout(header_layout)
        content_layout.addSpacing(15)
        
        # Transcript Feed
        feed_frame = QFrame()
        feed_frame.setObjectName("feed_frame")
        feed_layout = QVBoxLayout(feed_frame)
        feed_layout.setContentsMargins(20, 20, 20, 20)
        
        self.transcript_area = QTextEdit(self)
        self.transcript_area.setReadOnly(True)
        self.transcript_area.setPlaceholderText("Agent thoughts and transcript will appear here...")
        
        feed_layout.addWidget(self.transcript_area)
        content_layout.addWidget(feed_frame)
        
        # Add to Main
        main_layout.addWidget(sidebar)
        main_layout.addWidget(content)

    def update_status(self, text: str, color: str = "#3b82f6"):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"font-size: 16px; font-weight: 600; padding: 10px; color: {color};")
        
    def append_transcript(self, text: str):
        # Format user vs agent
        if text.startswith("You:"):
            formatted = f"<span style='color: #94a3b8;'>{text}</span>"
        elif text.startswith("VoxOS:"):
            formatted = f"<span style='color: #ffffff; font-weight: 500;'>{text}</span>"
        else:
            formatted = text
            
        self.transcript_area.append(formatted)
        self.transcript_area.verticalScrollBar().setValue(self.transcript_area.verticalScrollBar().maximum())
