"""GUI 실행: python main.py (pip install -r requirements.txt 필요)"""
import sys
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet('''
      QPushButton { padding: 7px 12px; }
      QPushButton#primary { background-color: #2563eb; color: white;
                            border-radius: 5px; font-weight: 600; }
      QPushButton#primary:hover { background-color: #1d4ed8; }
      QGroupBox { font-weight: 600; padding-top: 10px; }
      QHeaderView::section { padding: 5px; }
    ''')
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
