import os
import math
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QPushButton, QFrame, QTextEdit, QLineEdit, QStackedWidget,
    QSizePolicy, QGraphicsDropShadowEffect
)
from PySide6.QtCore import Qt, Signal, QTimer, Property, QPropertyAnimation, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QBrush, QFont

class VoiceAnimationWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.state = "Idle"
        
        # Animation variables
        self._radius = 50.0
        self._pulse_phase = 0.0
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(16)  # ~60 fps
        
    def set_state(self, state: str):
        self.state = state
        self.update()
        
    def update_animation(self):
        # Update phase based on state
        speed = 0.05
        if "Listening" in self.state:
            speed = 0.15
        elif "Thinking" in self.state:
            speed = 0.08
        elif "Speaking" in self.state:
            speed = 0.2
        elif "Error" in self.state or "Failed" in self.state:
            speed = 0.02
            
        self._pulse_phase += speed
        if self._pulse_phase > math.pi * 2:
            self._pulse_phase -= math.pi * 2
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        center = self.rect().center()
        
        # Determine color based on state
        base_color = QColor(148, 163, 184)  # Gray for idle
        if "Listening" in self.state:
            base_color = QColor(59, 130, 246)  # Blue
        elif "Thinking" in self.state:
            base_color = QColor(168, 85, 247)  # Purple
        elif "Speaking" in self.state:
            base_color = QColor(16, 185, 129)  # Green
        elif "Error" in self.state or "Failed" in self.state:
            base_color = QColor(239, 68, 68)   # Red
            
        # Draw pulsing rings
        pulse_scale = 1.0
        if self.state != "Idle":
            pulse_scale = 1.0 + 0.2 * math.sin(self._pulse_phase)
            
        max_radius = min(self.width(), self.height()) / 3
        current_radius = 50 * pulse_scale
        
        # Outer glow ring
        if self.state != "Idle" and not ("Error" in self.state):
            glow_radius = current_radius + 20 + 10 * math.sin(self._pulse_phase * 1.5)
            glow_color = QColor(base_color)
            glow_color.setAlpha(40)
            painter.setBrush(QBrush(glow_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(center, glow_radius, glow_radius)
            
            # Second ring
            ring2_radius = current_radius + 40 + 15 * math.sin(self._pulse_phase * 0.8)
            glow_color.setAlpha(20)
            painter.setBrush(QBrush(glow_color))
            painter.drawEllipse(center, ring2_radius, ring2_radius)
        
        # Center circle
        painter.setBrush(QBrush(base_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, current_radius, current_radius)
        
        # Draw Microphone Icon (simplified)
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        mic_width = 16
        mic_height = 32
        mic_rect = QRectF(center.x() - mic_width/2, center.y() - mic_height/2 - 4, mic_width, mic_height)
        painter.drawRoundedRect(mic_rect, mic_width/2, mic_width/2)
        
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(255, 255, 255), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(int(center.x() - mic_width), int(center.y() - mic_height/2 - 4), int(mic_width*2), int(mic_height + 8), 180 * 16, 180 * 16)
        painter.drawLine(int(center.x()), int(center.y() + mic_height/2 + 4), int(center.x()), int(center.y() + mic_height/2 + 14))
        painter.drawLine(int(center.x() - 10), int(center.y() + mic_height/2 + 14), int(center.x() + 10), int(center.y() + mic_height/2 + 14))


class InteractionDashboard(QWidget):
    chat_message_sent = Signal(str)
    voice_toggle_requested = Signal()

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
            
            QLineEdit#chat_input {
                background-color: #232733;
                color: #ffffff;
                border: 1px solid #3b82f6;
                border-radius: 8px;
                padding: 10px 15px;
                font-size: 15px;
            }
            
            QPushButton#send_btn {
                background-color: #3b82f6;
                color: white;
                border-radius: 8px;
                padding: 10px 20px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton#send_btn:hover {
                background-color: #2563eb;
            }
            
            /* Action Buttons */
            QPushButton.nav_btn {
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
            QPushButton.nav_btn:hover {
                background-color: #374151;
            }
            QPushButton.nav_btn:checked {
                background-color: #3b82f6;
            }
            
            QPushButton#voice_action_btn {
                background-color: #10b981;
                color: white;
                border-radius: 25px;
                padding: 15px 30px;
                font-weight: bold;
                font-size: 16px;
            }
            QPushButton#voice_action_btn:hover {
                background-color: #059669;
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
        self.btn_chat_mode = QPushButton("💬 Chat Mode")
        self.btn_chat_mode.setProperty("class", "nav_btn")
        self.btn_chat_mode.setCheckable(True)
        self.btn_chat_mode.setChecked(True)
        
        self.btn_voice_mode = QPushButton("🎙️ Voice Mode")
        self.btn_voice_mode.setProperty("class", "nav_btn")
        self.btn_voice_mode.setCheckable(True)
        
        self.btn_chat_mode.clicked.connect(lambda: self.switch_mode(0))
        self.btn_voice_mode.clicked.connect(lambda: self.switch_mode(1))
        
        sidebar_layout.addWidget(logo_label)
        sidebar_layout.addWidget(version_label)
        sidebar_layout.addSpacing(20)
        sidebar_layout.addWidget(self.btn_chat_mode)
        sidebar_layout.addWidget(self.btn_voice_mode)
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
        
        self.stacked_widget = QStackedWidget()
        
        # 1. Chat Mode View
        chat_view = QWidget()
        chat_layout = QVBoxLayout(chat_view)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        
        feed_frame = QFrame()
        feed_frame.setObjectName("feed_frame")
        feed_layout = QVBoxLayout(feed_frame)
        feed_layout.setContentsMargins(20, 20, 20, 20)
        
        self.transcript_area = QTextEdit()
        self.transcript_area.setReadOnly(True)
        self.transcript_area.setPlaceholderText("Agent thoughts and transcript will appear here...")
        feed_layout.addWidget(self.transcript_area)
        
        input_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setObjectName("chat_input")
        self.chat_input.setPlaceholderText("Type a command for VoxOS...")
        self.chat_input.returnPressed.connect(self.send_chat_message)
        
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("send_btn")
        self.send_btn.clicked.connect(self.send_chat_message)
        
        input_layout.addWidget(self.chat_input)
        input_layout.addWidget(self.send_btn)
        
        chat_layout.addWidget(feed_frame)
        chat_layout.addSpacing(15)
        chat_layout.addLayout(input_layout)
        
        # 2. Voice Mode View
        voice_view = QWidget()
        voice_layout = QVBoxLayout(voice_view)
        self.voice_anim_widget = VoiceAnimationWidget()
        
        voice_instruction = QLabel("Press Ctrl+Space, use Wake Word, or click below to start.")
        voice_instruction.setStyleSheet("color: #94a3b8; font-size: 14px;")
        voice_instruction.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.btn_toggle_voice = QPushButton("Activate Microphone")
        self.btn_toggle_voice.setObjectName("voice_action_btn")
        self.btn_toggle_voice.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_voice.clicked.connect(self.voice_toggle_requested.emit)
        
        voice_layout.addStretch()
        voice_layout.addWidget(self.voice_anim_widget, alignment=Qt.AlignmentFlag.AlignCenter)
        voice_layout.addSpacing(20)
        voice_layout.addWidget(voice_instruction)
        voice_layout.addSpacing(15)
        voice_layout.addWidget(self.btn_toggle_voice, alignment=Qt.AlignmentFlag.AlignCenter)
        voice_layout.addStretch()
        
        self.stacked_widget.addWidget(chat_view)
        self.stacked_widget.addWidget(voice_view)
        
        content_layout.addWidget(self.stacked_widget)
        
        # Add to Main
        main_layout.addWidget(sidebar)
        main_layout.addWidget(content)

    def switch_mode(self, index: int):
        self.stacked_widget.setCurrentIndex(index)
        if index == 0:
            self.btn_chat_mode.setChecked(True)
            self.btn_voice_mode.setChecked(False)
        else:
            self.btn_chat_mode.setChecked(False)
            self.btn_voice_mode.setChecked(True)

    def send_chat_message(self):
        text = self.chat_input.text().strip()
        if text:
            self.append_transcript(f"You: {text}")
            self.chat_message_sent.emit(text)
            self.chat_input.clear()

    def update_status(self, text: str, color: str = "#3b82f6"):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"font-size: 16px; font-weight: 600; padding: 10px; color: {color};")
        self.voice_anim_widget.set_state(text)
        
        if "Wake Word Only" in text or "Idle" in text:
            self.btn_toggle_voice.setText("Activate Microphone")
            self.btn_toggle_voice.setStyleSheet("""
                QPushButton#voice_action_btn {
                    background-color: #10b981; color: white; border-radius: 25px; padding: 15px 30px; font-weight: bold; font-size: 16px;
                }
                QPushButton#voice_action_btn:hover { background-color: #059669; }
            """)
        elif "Listening" in text or "Thinking" in text or "Speaking" in text:
            self.btn_toggle_voice.setText("Stop Microphone")
            self.btn_toggle_voice.setStyleSheet("""
                QPushButton#voice_action_btn {
                    background-color: #ef4444; color: white; border-radius: 25px; padding: 15px 30px; font-weight: bold; font-size: 16px;
                }
                QPushButton#voice_action_btn:hover { background-color: #dc2626; }
            """)
            
    def append_transcript(self, text: str):
        # Format user vs agent
        if text.startswith("You:"):
            formatted = f"<span style='color: #94a3b8;'>{text}</span>"
        elif text.startswith("VoxOS ["):
            formatted = f"<span style='color: #10b981; font-weight: 500;'>{text}</span>"
        elif text.startswith("VoxOS:"):
            formatted = f"<span style='color: #ffffff; font-weight: 500;'>{text}</span>"
        else:
            formatted = text
            
        self.transcript_area.append(formatted)
        self.transcript_area.verticalScrollBar().setValue(self.transcript_area.verticalScrollBar().maximum())
