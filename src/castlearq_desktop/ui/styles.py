from __future__ import annotations
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget

COLOR_BACKGROUND = "#1E1F22"
COLOR_SURFACE = "#2A2B2F"
COLOR_SURFACE_ALT = "#232428"
COLOR_BORDER = "#3A3B40"
COLOR_TEXT_PRIMARY = "#E6E7E9"
COLOR_TEXT_SECONDARY = "#A0A1A6"
COLOR_ACCENT = "#F28C28"
COLOR_ACCENT_HOVER = "#FF9A3D"
COLOR_ACCENT_PRESSED = "#D97316"
COLOR_STATE_INFO = "#4C9AFF"
COLOR_STATE_SUCCESS = "#3FB950"
COLOR_STATE_WARNING = "#F28C28"
COLOR_STATE_ERROR = "#F85149"

FONT_SIZE_CAPTION = 11
FONT_SIZE_BODY = 13
FONT_SIZE_TITLE = 17
FONT_SIZE_HEADING = 20
SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24
RADIUS_SM = 6
RADIUS_MD = 10
CONTROL_HEIGHT = 32
SIDEBAR_WIDTH = 220

def make_font(size, bold=False, mono=False):
    font = QFont()
    if mono:
        font.setFamily("Monospace")
    font.setPointSize(size)
    font.setBold(bold)
    return font

def global_stylesheet():
    return f"""
    QWidget {{ background-color: {COLOR_BACKGROUND}; color: {COLOR_TEXT_PRIMARY}; font-size: {FONT_SIZE_BODY}px; }}
    QMainWindow {{ background-color: {COLOR_BACKGROUND}; }}
    QListWidget {{ background-color: {COLOR_SURFACE_ALT}; border: none; outline: none; padding: {SPACING_SM}px; }}
    QListWidget::item {{ padding: {SPACING_SM}px {SPACING_MD}px; border-radius: {RADIUS_SM}px; margin: 2px 0; }}
    QListWidget::item:selected {{ background-color: {COLOR_ACCENT}; color: #1E1F22; }}
    QListWidget::item:hover {{ background-color: {COLOR_SURFACE}; }}
    QPushButton {{ background-color: {COLOR_SURFACE}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER}; border-radius: {RADIUS_SM}px; padding: 6px 12px; min-height: {CONTROL_HEIGHT}px; }}
    QPushButton:hover {{ background-color: {COLOR_SURFACE_ALT}; border-color: {COLOR_ACCENT}; }}
    QPushButton:pressed {{ background-color: {COLOR_ACCENT_PRESSED}; color: #1E1F22; }}
    QLineEdit, QTextEdit, QPlainTextEdit {{ background-color: {COLOR_SURFACE}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER}; border-radius: {RADIUS_SM}px; padding: 6px; }}
    QLabel {{ color: {COLOR_TEXT_PRIMARY}; }}
    QGroupBox {{ border: 1px solid {COLOR_BORDER}; border-radius: {RADIUS_MD}px; margin-top: 8px; padding: 8px; }}
    QGroupBox::title {{ color: {COLOR_TEXT_SECONDARY}; }}
    QScrollArea {{ border: none; }}
    QSplitter::handle {{ background-color: {COLOR_BORDER}; width: 1px; }}
    """

def apply_app_theme(app_or_widget=None):
    qss = global_stylesheet()
    if isinstance(app_or_widget, QApplication):
        app_or_widget.setStyleSheet(qss)
    elif app_or_widget is not None:
        app_or_widget.setStyleSheet(qss)
    else:
        inst = QApplication.instance()
        if inst is not None:
            inst.setStyleSheet(qss)

def apply_runtime_style(title_label, value_labels, technical_labels=None, page=None):
    if technical_labels is None:
        technical_labels = set()
    title_label.setFont(make_font(FONT_SIZE_TITLE, bold=True))
    title_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; margin-bottom: {SPACING_SM}px;")
    for label in value_labels:
        is_technical = label in technical_labels
        label.setFont(make_font(FONT_SIZE_BODY, mono=is_technical))
        label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; padding: {SPACING_XS}px 0; line-height: 1.4;")
        label.setWordWrap(True)
    if page is not None:
        page.setStyleSheet(f"background-color: {COLOR_BACKGROUND};")

__all__ = ["COLOR_BACKGROUND","COLOR_SURFACE","COLOR_SURFACE_ALT","COLOR_BORDER","COLOR_TEXT_PRIMARY","COLOR_TEXT_SECONDARY","COLOR_ACCENT","COLOR_ACCENT_HOVER","COLOR_ACCENT_PRESSED","COLOR_STATE_INFO","COLOR_STATE_SUCCESS","COLOR_STATE_WARNING","COLOR_STATE_ERROR","FONT_SIZE_CAPTION","FONT_SIZE_BODY","FONT_SIZE_TITLE","FONT_SIZE_HEADING","SPACING_XS","SPACING_SM","SPACING_MD","SPACING_LG","SPACING_XL","RADIUS_SM","RADIUS_MD","CONTROL_HEIGHT","SIDEBAR_WIDTH","make_font","global_stylesheet","apply_app_theme","apply_runtime_style"]
