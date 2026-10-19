"""Tema visual de Demucs Studio para Windows.

Mismos principios que la web: fondo casi negro, superficies elevadas, bordes
finos, tipografía Segoe UI y un único acento rojo.
"""

from __future__ import annotations

BG = "#0b0b0e"
SURFACE = "#121216"
SURFACE_2 = "#17171c"
SURFACE_3 = "#1d1d23"
BORDER = "#2a2a31"
BORDER_STRONG = "#35353f"
TEXT = "#f4f4f6"
MUTED = "#8d8d98"
MUTED_2 = "#6b6b76"
ACCENT = "#e5484d"
ACCENT_HOVER = "#f05a5f"
SUCCESS = "#4ade80"
INFO = "#60a5fa"
WARNING = "#d29922"
DANGER = "#f87171"

STYLESHEET = f"""
QWidget {{
    color: {TEXT};
    font-family: "Segoe UI", "Segoe UI Variable", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog, QWidget#Root {{
    background-color: {BG};
}}
QFrame#Card {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#HLine {{
    background-color: {BORDER};
    max-height: 1px;
    border: none;
}}
QLabel#AppTitle {{
    font-size: 22px;
    font-weight: 600;
}}
QLabel#AppSubtitle, QLabel#Muted {{
    color: {MUTED};
    font-size: 12px;
}}
QLabel#SectionTitle {{
    font-size: 14px;
    font-weight: 600;
}}
QLabel#FieldLabel {{
    color: {MUTED};
    font-size: 12px;
}}
QLabel#Estimate {{
    color: {MUTED};
    font-size: 12px;
    padding: 2px 0;
}}
QLabel#FormatNote {{
    color: {MUTED_2};
    font-size: 11px;
}}
QLabel#DropIcon {{
    color: {ACCENT};
    font-size: 26px;
}}
QLabel#DropTitle {{
    font-size: 15px;
    font-weight: 600;
}}
QLabel#EmptyTitle {{
    font-size: 15px;
    font-weight: 600;
    color: {MUTED};
}}
QLabel#Pill {{
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#Pill[tone="ok"] {{
    background-color: #10281a;
    color: {SUCCESS};
}}
QLabel#Pill[tone="warn"] {{
    background-color: #2a2110;
    color: {WARNING};
}}
QLabel#Pill[tone="error"] {{
    background-color: #2c1517;
    color: {DANGER};
}}
QLabel#Pill[tone="muted"] {{
    background-color: {SURFACE_2};
    color: {MUTED};
}}
QFrame#DropZone {{
    background-color: #0f0f13;
    border: 1px dashed {BORDER_STRONG};
    border-radius: 12px;
}}
QFrame#DropZone[dragActive="true"] {{
    border-color: {ACCENT};
    background-color: #171014;
}}
QPushButton {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 7px 14px;
    min-height: 18px;
}}
QPushButton:hover {{
    background-color: #202028;
    border-color: #41414c;
}}
QPushButton:pressed {{
    background-color: #262630;
}}
QPushButton:disabled {{
    color: {MUTED_2};
    background-color: #131317;
    border-color: #232329;
}}
QPushButton#Primary {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: #ffffff;
    font-weight: 600;
    padding: 9px 22px;
}}
QPushButton#Primary:hover {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton#Primary:disabled {{
    background-color: #3a2022;
    border-color: #3a2022;
    color: #8f6a6c;
}}
QPushButton#Ghost {{
    background-color: transparent;
    border: none;
    color: {MUTED};
    padding: 5px 8px;
}}
QPushButton#Ghost:hover {{
    color: {TEXT};
    background-color: {SURFACE_2};
}}
QPushButton#RowButton {{
    background-color: transparent;
    border: none;
    color: {MUTED_2};
    padding: 0;
}}
QPushButton#RowButton:hover {{
    color: {DANGER};
    background-color: #2c1517;
    border-radius: 6px;
}}
QToolButton {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 6px 10px;
    color: {MUTED};
}}
QToolButton:hover {{
    background-color: #202028;
    color: {TEXT};
}}
QToolButton::menu-indicator {{
    image: none;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 6px 9px;
    min-height: 20px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {MUTED_2};
    background-color: #131317;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER_STRONG};
    selection-background-color: {SURFACE_3};
    selection-color: {TEXT};
    outline: none;
    padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px;
    border: none;
    background: transparent;
}}
QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background-color: {SURFACE_2};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QRadioButton {{
    spacing: 8px;
}}
QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 8px;
    border: 1px solid {BORDER_STRONG};
    background-color: {SURFACE_2};
}}
QRadioButton::indicator:hover {{
    border-color: {ACCENT};
}}
QRadioButton::indicator:checked {{
    border: 4px solid {ACCENT};
    background-color: {SURFACE_2};
}}
QRadioButton:disabled {{
    color: {MUTED_2};
}}
QRadioButton::indicator:disabled {{
    border-color: #26262c;
    background-color: #131317;
}}
QProgressBar {{
    background-color: {SURFACE_3};
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    font-size: 9px;
    color: {TEXT};
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 5px;
}}
QTableWidget {{
    background-color: transparent;
    border: none;
    gridline-color: {BORDER};
    outline: none;
}}
QHeaderView::section {{
    background-color: {SURFACE};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 4px;
    font-size: 10px;
    font-weight: 600;
}}
QTableWidget::item {{
    padding: 4px;
    border-bottom: 1px solid {SURFACE_3};
}}
QTableWidget::item:selected {{
    background-color: {SURFACE_2};
    color: {TEXT};
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #33333c;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: #454550;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: #33333c;
    border-radius: 5px;
    min-width: 30px;
}}
QPlainTextEdit {{
    background-color: #0f0f12;
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: #a8a8b3;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
    padding: 6px;
}}
QMenu {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER_STRONG};
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 24px 7px 14px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background-color: {SURFACE_3};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 8px;
}}
QToolTip {{
    background-color: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    padding: 5px;
}}
QSplitter::handle {{
    background: transparent;
    width: 10px;
}}
QStatusBar {{
    background: {BG};
    color: {MUTED};
}}
"""
