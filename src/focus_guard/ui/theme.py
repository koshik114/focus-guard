from __future__ import annotations

from PySide6.QtWidgets import QApplication


def apply_light_theme(app: QApplication) -> None:
    app.setStyleSheet(
        """
        QWidget {
            background: #f5f5f2;
            color: #202124;
            font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
            font-size: 14px;
        }

        QMainWindow, QDialog {
            background: #f5f5f2;
        }

        QWidget#AppRoot {
            background: #f5f5f2;
        }

        QLabel#AppTitle {
            font-size: 25px;
            font-weight: 650;
            letter-spacing: 0;
        }

        QLabel#PageTitle {
            font-size: 22px;
            font-weight: 650;
            color: #1f2328;
            letter-spacing: 0;
        }

        QLabel#SectionTitle {
            font-size: 16px;
            font-weight: 650;
        }

        QLabel#Muted {
            color: #69707d;
            line-height: 1.35;
        }

        QLabel#Eyebrow {
            color: #7a3e16;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0;
        }

        QLabel#SoftBadge,
        QLabel#StatusBadgeIdle,
        QLabel#StatusBadgeActive,
        QLabel#StatusBadgeFocused,
        QLabel#StatusBadgeDistracted,
        QLabel#StatusBadgeWarn {
            border-radius: 6px;
            padding: 7px 10px;
            font-weight: 600;
        }

        QLabel#SoftBadge {
            background: #eef2ff;
            color: #3730a3;
            border: 1px solid #dbe1ff;
        }

        QLabel#StatusBadgeIdle {
            background: #f4f4f5;
            color: #52525b;
            border: 1px solid #dedee3;
        }

        QLabel#StatusBadgeActive {
            background: #ecfeff;
            color: #155e75;
            border: 1px solid #bae6fd;
        }

        QLabel#StatusBadgeFocused {
            background: #ecfdf3;
            color: #166534;
            border: 1px solid #bbf7d0;
        }

        QLabel#StatusBadgeDistracted {
            background: #fff1f2;
            color: #9f1239;
            border: 1px solid #fecdd3;
        }

        QLabel#StatusBadgeWarn {
            background: #fffbeb;
            color: #92400e;
            border: 1px solid #fde68a;
        }

        QLabel#MetricLabel {
            color: #71717a;
            font-size: 12px;
            font-weight: 600;
        }

        QLabel#MetricValue {
            color: #18181b;
            font-size: 14px;
            font-weight: 650;
        }

        QLabel#ResultText {
            background: #fafafa;
            border: 1px solid #ececf0;
            border-radius: 7px;
            padding: 10px;
        }

        QFrame#Panel {
            background: #fefefe;
            border: 1px solid #e4e4e7;
            border-radius: 8px;
        }

        QFrame#Sidebar {
            background: #ffffff;
            border: 1px solid #dfdeda;
            border-radius: 8px;
        }

        QFrame#MetricCard {
            background: #ffffff;
            border: 1px solid #e4e4e7;
            border-radius: 8px;
        }

        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
            background: #ffffff;
            border: 1px solid #d9d9df;
            border-radius: 6px;
            padding: 8px;
            selection-background-color: #dbeafe;
        }

        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
            border: 1px solid #8bb4f8;
        }

        QPushButton {
            background: #ffffff;
            border: 1px solid #d9d9df;
            border-radius: 6px;
            padding: 9px 14px;
            font-weight: 600;
            min-height: 18px;
        }

        QPushButton:hover {
            background: #f4f4f5;
            border-color: #c9c9d1;
        }

        QPushButton:disabled {
            color: #a1a1aa;
            background: #f4f4f5;
        }

        QPushButton#PrimaryButton {
            background: #18181b;
            color: #ffffff;
            border-color: #18181b;
        }

        QPushButton#PrimaryButton:hover {
            background: #27272a;
        }

        QPushButton#DangerButton {
            background: #fff1f2;
            color: #9f1239;
            border-color: #fecdd3;
        }

        QCheckBox {
            spacing: 8px;
        }

        QCheckBox::indicator {
            width: 16px;
            height: 16px;
        }

        QTableWidget {
            background: #ffffff;
            border: 1px solid #e4e4e7;
            border-radius: 8px;
            gridline-color: #f0f0f2;
            selection-background-color: #eef2ff;
            selection-color: #18181b;
        }

        QHeaderView::section {
            background: #fafafa;
            border: 0;
            border-bottom: 1px solid #e4e4e7;
            padding: 8px;
            font-weight: 600;
            color: #52525b;
        }
        """
    )
