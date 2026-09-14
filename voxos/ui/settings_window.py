import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QFormLayout, QMessageBox, QFrame, QScrollArea, QSizePolicy
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QIcon, QColor

class SettingsWindow(QWidget):
    def __init__(self, env_path: str = ".env"):
        super().__init__()
        self.env_path = env_path
        self.setWindowTitle("VoxOS Dashboard")
        self.setMinimumSize(800, 550)
        
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
            
            /* Card Styling */
            QFrame#card {
                background-color: #1a1d27;
                border: 1px solid #282c3a;
                border-radius: 12px;
            }
            
            QLabel#section_title {
                font-size: 16px;
                font-weight: 600;
                color: #ffffff;
                margin-top: 5px;
                margin-bottom: 15px;
            }
            
            QLabel#field_label {
                font-size: 13px;
                color: #cbd5e1;
                font-weight: 500;
            }
            
            /* Inputs */
            QLineEdit {
                background-color: #101217;
                border: 1px solid #334155;
                padding: 10px 14px;
                border-radius: 6px;
                font-size: 14px;
                color: #f8fafc;
                selection-background-color: #3b82f6;
            }
            QLineEdit:focus {
                border: 1px solid #3b82f6;
                background-color: #14171f;
            }
            
            /* Buttons */
            QPushButton#primary_btn {
                background-color: #3b82f6;
                color: white;
                border: none;
                padding: 10px 24px;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton#primary_btn:hover {
                background-color: #2563eb;
            }
            QPushButton#primary_btn:pressed {
                background-color: #1d4ed8;
            }
            
            QPushButton#secondary_btn {
                background-color: transparent;
                color: #cbd5e1;
                border: 1px solid #475569;
                padding: 10px 24px;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 500;
            }
            QPushButton#secondary_btn:hover {
                background-color: #1e293b;
                color: white;
            }
        """)
        
        self.env_data = self._load_env()
        self.init_ui()

    def _load_env(self) -> dict:
        data = {}
        if os.path.exists(self.env_path):
            with open(self.env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        data[key.strip()] = val.strip()
        return data

    def _save_env(self):
        self.env_data["GROQ_API_KEY"] = self.groq_input.text()
        self.env_data["DEEPGRAM_API_KEY"] = self.deepgram_input.text()
        self.env_data["POSTGRES_URL"] = self.postgres_input.text()
        
        with open(self.env_path, "w") as f:
            for k, v in self.env_data.items():
                f.write(f"{k}={v}\n")
        
        QMessageBox.information(self, "Success", "Configuration saved successfully!\n\nPlease restart VoxOS for changes to take effect.")
        self.close()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # --- LEFT SIDEBAR ---
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(240)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(25, 30, 25, 30)
        sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        logo_label = QLabel("VoxOS")
        logo_label.setObjectName("sidebar_title")
        version_label = QLabel("Agent Control Panel v1.0")
        version_label.setObjectName("sidebar_subtitle")
        
        # Navigation Items
        nav_api = QPushButton("API Configurations")
        nav_api.setStyleSheet("""
            QPushButton {
                background-color: #262b3d;
                color: #ffffff;
                text-align: left;
                padding: 10px 15px;
                border-radius: 6px;
                border: none;
                font-weight: 600;
                font-size: 13px;
            }
        """)
        
        sidebar_layout.addWidget(logo_label)
        sidebar_layout.addWidget(version_label)
        sidebar_layout.addSpacing(20)
        sidebar_layout.addWidget(nav_api)
        sidebar_layout.addStretch()
        
        # --- RIGHT CONTENT AREA ---
        content = QFrame()
        content.setObjectName("content_area")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(40, 40, 40, 40)
        
        header = QLabel("Integration Settings")
        header.setStyleSheet("font-size: 24px; font-weight: 600; color: #ffffff;")
        header_desc = QLabel("Manage your secure credentials for AI inference, Speech services, and Database infrastructure.")
        header_desc.setStyleSheet("color: #94a3b8; font-size: 13px; margin-top: 5px; margin-bottom: 20px;")
        
        content_layout.addWidget(header)
        content_layout.addWidget(header_desc)
        content_layout.addSpacing(10)
        
        # Form Card
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(30, 30, 30, 30)
        card_layout.setSpacing(20)
        
        card_title = QLabel("Service Credentials")
        card_title.setObjectName("section_title")
        card_layout.addWidget(card_title)
        
        form_layout = QFormLayout()
        form_layout.setSpacing(15)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        # Groq Input
        groq_label = QLabel("Groq API Key")
        groq_label.setObjectName("field_label")
        self.groq_input = QLineEdit(self)
        self.groq_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.groq_input.setPlaceholderText("gsk_...")
        self.groq_input.setText(self.env_data.get("GROQ_API_KEY", ""))
        form_layout.addRow(groq_label, self.groq_input)
        
        # Deepgram Input
        deepgram_label = QLabel("Deepgram API Key")
        deepgram_label.setObjectName("field_label")
        self.deepgram_input = QLineEdit(self)
        self.deepgram_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.deepgram_input.setPlaceholderText("Enter Deepgram Token")
        self.deepgram_input.setText(self.env_data.get("DEEPGRAM_API_KEY", ""))
        form_layout.addRow(deepgram_label, self.deepgram_input)
        
        # Postgres Input
        postgres_label = QLabel("NeonDB (Postgres) URL")
        postgres_label.setObjectName("field_label")
        self.postgres_input = QLineEdit(self)
        self.postgres_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.postgres_input.setPlaceholderText("postgresql://...")
        self.postgres_input.setText(self.env_data.get("POSTGRES_URL", ""))
        form_layout.addRow(postgres_label, self.postgres_input)
        
        card_layout.addLayout(form_layout)
        content_layout.addWidget(card)
        
        content_layout.addStretch()
        
        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(15)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondary_btn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.close)
        
        save_btn = QPushButton("Save Configuration")
        save_btn.setObjectName("primary_btn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._save_env)
        
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        
        content_layout.addLayout(btn_layout)
        
        # Add to Main
        main_layout.addWidget(sidebar)
        main_layout.addWidget(content)
