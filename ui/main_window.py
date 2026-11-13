"""압축 방식에 종속되지 않는 단일 파일 + 일괄 실험 GUI."""
from __future__ import annotations
from pathlib import Path
import traceback

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QPushButton, QLineEdit, QComboBox, QLabel, QFileDialog, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QProgressBar,
    QPlainTextEdit, QSplitter, QCheckBox, QAbstractItemView,
)
from algorithms.registry import CODECS
from core.engine import compress_file, compress_and_verify, decompress_file, benchmark, export_csv


def num(value, decimals=3, suffix=''):
    if value is None:
        return '—'
    if isinstance(value, bool):
        return '통과' if value else '실패'
    if isinstance(value, int):
        return f'{value:,}{suffix}'
    return f'{value:,.{decimals}f}{suffix}'


class NumericItem(QTableWidgetItem):
    """코드표의 빈도/비율/길이를 문자열이 아닌 숫자로 정렬."""
    def __lt__(self, other):
        a = self.data(Qt.ItemDataRole.UserRole)
        b = other.data(Qt.ItemDataRole.UserRole)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a < b
        return super().__lt__(other)


class JobThread(QThread):
    status = Signal(int, str)
    success = Signal(object)
    failure = Signal(str)

    def __init__(self, job: str, options: dict):
        super().__init__()
        self.job = job
        self.options = options

    def run(self):
        try:
            progress = lambda frac, message: self.status.emit(int(frac * 100), message)
            if self.job == 'compress':
                result = compress_file(self.options['source'], self.options['target'],
                                       self.options['codec'], progress)
                self.success.emit({'records': [result], 'summary': [], 'message': '압축 완료 (복원 미수행)'})
            elif self.job == 'verify':
                result = compress_and_verify(
                    self.options['source'], self.options['target'], self.options['restored'],
                    self.options['codec'], progress)
                self.success.emit({'records': [result], 'summary': [],
                                   'message': ('압축 + 복원 + 원본 바이트 비교 통과' if result['verified']
                                               else '압축 후 복원 불일치 감지! 손실률과 차이 바이트 수를 확인하세요.')})
            elif self.job == 'decompress':
                result, _ = decompress_file(self.options['source'], self.options['target'], progress)
                self.success.emit({'records': [result], 'summary': [],
                                   'message': '복원 + 저장된 SHA-256 확인 완료'})
            elif self.job == 'batch':
                records, summary = benchmark(self.options['paths'], self.options['codecs'],
                                             self.options['output_dir'], progress)
                failures = sum(r['verified'] is not True for r in records)
                self.success.emit({'records': records, 'summary': summary,
                                   'message': f'총 {len(records)}개 실험 완료 (무손실 검증 실패 {failures}개)'})
            else:
                raise RuntimeError('알 수 없는 작업')
        except Exception as exc:
            self.failure.emit(f'{type(exc).__name__}: {exc}')


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('허프만 코딩 실험실 | 팀 공통 GUI')
        self.resize(1250, 940)
        self.worker = None
        self.records: list[dict] = []
        self.batch_records: list[dict] = []
        self.batch_summary: list[dict] = []
        self.selected_paths: list[str] = []
        self._setup_ui()

    def _setup_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QLabel('허프만 코딩 · 비교 실험 시스템')
        header.setStyleSheet('font-size: 21px; font-weight: 700;')
        layout.addWidget(header)
        intro = QLabel('바이트 기반 무손실 압축  |  정적 허프만 vs FGK  |  헤더 포함 압축률  |  엔트로피  |  정확도 검증')
        intro.setStyleSheet('color: #64748b;')
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._single_tab(), '개별 파일 압축 / 복원')
        self.tabs.addTab(self._batch_tab(), '다중 파일 실험 / 비교')
        layout.addWidget(self.tabs, 3)

        bar_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat('%p%')
        self.progress_label = QLabel('준비 완료')
        bar_row.addWidget(self.progress, 3)
        bar_row.addWidget(self.progress_label, 4)
        layout.addLayout(bar_row)

        detail = QGroupBox('상세 분석 결과 / 문자별 허프만 코드표')
        dlayout = QVBoxLayout(detail)
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel('분석 대상'))
        self.record_combo = QComboBox()
        self.record_combo.currentIndexChanged.connect(self._show_record)
        sel_row.addWidget(self.record_combo, 2)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText('바이트(0x41), 코드, 출현 문자 필터')
        self.filter_input.textChanged.connect(self._filter_codes)
        sel_row.addWidget(self.filter_input, 1)
        dlayout.addLayout(sel_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.metrics_table = QTableWidget(0, 2)
        self.metrics_table.setHorizontalHeaderLabels(['측정 항목', '결과'])
        self.metrics_table.verticalHeader().setVisible(False)
        self.metrics_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.metrics_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.metrics_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        splitter.addWidget(self.metrics_table)

        self.codes_table = QTableWidget(0, 6)
        self.codes_table.setHorizontalHeaderLabels(['바이트 / 문자', '빈도', '비율', '허프만 코드', '길이(bit)', '빈도 분포'])
        self.codes_table.verticalHeader().setVisible(False)
        self.codes_table.setSortingEnabled(True)
        self.codes_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.codes_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.codes_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self.codes_table)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        dlayout.addWidget(splitter, 1)

        self.code_note = QLabel('알고리즘에 따라 코드표의 의미가 달라집니다.')
        self.code_note.setWordWrap(True)
        self.code_note.setStyleSheet('color: #64748b; font-style: italic;')
        dlayout.addWidget(self.code_note)
        layout.addWidget(detail, 5)

        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumHeight(84)
        self.logs.setPlaceholderText('작업 결과 및 오류 메시지')
        layout.addWidget(self.logs)

    def _single_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)
        source_box = QGroupBox('원본 텍스트 또는 .huf 압축 파일')
        row = QHBoxLayout(source_box)
        self.source_input = QLineEdit()
        self.source_input.setPlaceholderText('텍스트 파일(.txt) 또는 압축 파일(.huf) 선택')
        row.addWidget(self.source_input)
        browse = QPushButton('파일 선택')
        browse.clicked.connect(self._browse_single)
        row.addWidget(browse)
        lay.addWidget(source_box)

        options = QGroupBox('압축 방법 / 실행')
        opts = QVBoxLayout(options)
        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel('알고리즘 선택'))
        self.codec_combo = QComboBox()
        for codec in CODECS.values():
            self.codec_combo.addItem(codec.name, codec.id)
        algo_row.addWidget(self.codec_combo, 1)
        opts.addLayout(algo_row)
        btn_row = QHBoxLayout()
        self.compress_btn = QPushButton('압축만 수행')
        self.verify_btn = QPushButton('압축 + 복원 + 손실률 검사')
        self.decode_btn = QPushButton('.huf 파일 복원')
        self.verify_btn.setObjectName('primary')
        self.compress_btn.clicked.connect(lambda: self._start_single('compress'))
        self.verify_btn.clicked.connect(lambda: self._start_single('verify'))
        self.decode_btn.clicked.connect(lambda: self._start_single('decompress'))
        for button in (self.compress_btn, self.verify_btn, self.decode_btn):
            btn_row.addWidget(button)
        opts.addLayout(btn_row)
        lay.addWidget(options)

        tips = QLabel('※ 복원할 때는 .huf 헤더에서 알고리즘을 자동 선택합니다. '
                      '정확한 손실률(바이트 비교)은 「압축 + 복원 + 손실률 검사」에서 계산합니다.')
        tips.setWordWrap(True)
        tips.setStyleSheet('color: #64748b;')
        lay.addWidget(tips)
        lay.addStretch()
        return page

    def _batch_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        chooser = QHBoxLayout()
        add = QPushButton('실험 파일 추가 (복수 선택)')
        add.clicked.connect(self._add_paths)
        remove = QPushButton('선택 항목 제거')
        remove.clicked.connect(self._remove_paths)
        chooser.addWidget(add)
        chooser.addWidget(remove)
        chooser.addStretch()
        lay.addLayout(chooser)

        self.file_table = QTableWidget(0, 2)
        self.file_table.setHorizontalHeaderLabels(['실험 파일', '전체 경로'])
        self.file_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.setMinimumHeight(85)
        lay.addWidget(self.file_table, 2)
        algorithms = QHBoxLayout()
        algorithms.addWidget(QLabel('실험 알고리즘'))
        self.codec_checks = {}
        for codec in CODECS.values():
            check = QCheckBox(codec.name)
            check.setChecked(True)
            self.codec_checks[codec.id] = check
            algorithms.addWidget(check)
        algorithms.addStretch()
        lay.addLayout(algorithms)

        actions = QHBoxLayout()
        self.batch_btn = QPushButton('실험 시작 · 자동 복원 검증')
        self.batch_btn.setObjectName('primary')
        self.batch_btn.clicked.connect(self._start_batch)
        self.csv_btn = QPushButton('결과 CSV 내보내기')
        self.csv_btn.clicked.connect(self._export_csv)
        actions.addWidget(self.batch_btn)
        actions.addWidget(self.csv_btn)
        lay.addLayout(actions)

        self.summary_table = QTableWidget(0, 7)
        self.summary_table.setHorizontalHeaderLabels(
            ['알고리즘', '실험 수', '평균 절감률', '평균 압축 시간', '평균 복원 시간',
             '평균 엔트로피', '복원 성공'])
        self.summary_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.summary_table.setMinimumHeight(90)
        lay.addWidget(self.summary_table, 2)
        self.batch_table = QTableWidget(0, 7)
        self.batch_table.setHorizontalHeaderLabels(
            ['파일', '알고리즘', '원본(byte)', '압축(byte)', '절감률(%)', '엔트로피', '손실률(%)'])
        self.batch_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.batch_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.batch_table.itemSelectionChanged.connect(self._batch_clicked)
        lay.addWidget(self.batch_table, 3)
        return page

    def _browse_single(self):
        path, _ = QFileDialog.getOpenFileName(self, '파일 선택', '',
                                              '텍스트/압축 파일 (*.txt *.huf *.bin);;모든 파일 (*.*)')
        if path:
            self.source_input.setText(path)

    def _add_paths(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '실험 파일 선택', '', '모든 파일 (*.*)')
        for path in paths:
            if path not in self.selected_paths:
                self.selected_paths.append(path)
        self._refresh_paths()

    def _remove_paths(self):
        selected = {index.row() for index in self.file_table.selectionModel().selectedRows()}
        self.selected_paths = [p for i, p in enumerate(self.selected_paths) if i not in selected]
        self._refresh_paths()

    def _refresh_paths(self):
        self.file_table.setRowCount(len(self.selected_paths))
        for row, path in enumerate(self.selected_paths):
            self.file_table.setItem(row, 0, QTableWidgetItem(Path(path).name))
            self.file_table.setItem(row, 1, QTableWidgetItem(path))

    def _start_single(self, job: str):
        source = self.source_input.text().strip()
        if not source or not Path(source).is_file():
            QMessageBox.warning(self, '파일 누락', '존재하는 입력 파일을 선택하세요.')
            return
        if job == 'decompress':
            proposed = str(Path(source).with_name(Path(source).stem + '_restored.txt'))
            dest, _ = QFileDialog.getSaveFileName(self, '복원 파일 저장', proposed, '모든 파일 (*.*)')
            options = {'source': source, 'target': dest}
        else:
            proposed = str(Path(source).with_name(Path(source).stem + '.huf'))
            dest, _ = QFileDialog.getSaveFileName(self, '압축 파일 저장', proposed,
                                                  'Huffman 파일 (*.huf);;모든 파일 (*.*)')
            restored = str(Path(dest).with_name(Path(dest).stem + '_restored' +
                                                (Path(source).suffix or '.bin'))) if dest else ''
            options = {'source': source, 'target': dest, 'restored': restored,
                       'codec': self.codec_combo.currentData()}
        if not dest:
            return
        # 사용자가 직접 압축 파일을 덮어쓸 때 의도치 않은 삭제를 방지
        if Path(dest).exists():
            ans = QMessageBox.question(self, '덮어쓰기 확인', '출력 파일이 존재합니다. 덮어쓸까요?')
            if ans != QMessageBox.StandardButton.Yes:
                return
        if job == 'verify' and Path(restored).exists():
            ans = QMessageBox.question(self, '복원 파일 덮어쓰기',
                                       f'자동 복원 파일이 존재합니다. 덮어쓸까요?\n{restored}')
            if ans != QMessageBox.StandardButton.Yes:
                return
        self._launch(job, options)

    def _start_batch(self):
        codecs = [key for key, check in self.codec_checks.items() if check.isChecked()]
        if not self.selected_paths or not codecs:
            QMessageBox.warning(self, '실험 항목 부족', '파일 한 개 이상과 알고리즘 한 개 이상을 선택하세요.')
            return
        output = QFileDialog.getExistingDirectory(self, '실험 결과 폴더 선택',
                                                  str(Path.cwd() / 'results'))
        if not output:
            return
        self._launch('batch', {'paths': self.selected_paths[:], 'codecs': codecs,
                               'output_dir': output})

    def _launch(self, job: str, options: dict):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(self, '실행 중', '다른 작업이 실행 중입니다.')
            return
        self.progress.setValue(0)
        self.progress_label.setText('실행 중...')
        self.logs.appendPlainText(f'▶ {job} 실행')
        self.worker = JobThread(job, options)
        self.worker.status.connect(self._on_progress)
        self.worker.success.connect(self._on_success)
        self.worker.failure.connect(self._on_failure)
        self.worker.finished.connect(lambda: self._set_enabled(True))
        self._set_enabled(False)
        self.worker.start()

    def _set_enabled(self, enabled: bool):
        for button in (self.compress_btn, self.verify_btn, self.decode_btn,
                       self.batch_btn, self.csv_btn):
            button.setEnabled(enabled)

    def _on_progress(self, percent: int, message: str):
        self.progress.setValue(percent)
        self.progress_label.setText(message)

    def _on_success(self, payload: dict):
        failures = [r for r in payload['records'] if r['verified'] is False]
        self.logs.appendPlainText(('⚠ ' if failures else '✓ ') + payload['message'])
        self.progress.setValue(100)
        self.progress_label.setText(payload['message'])
        self.records = payload['records']
        self._reload_records()
        if payload['summary']:
            self.batch_records = payload['records']
            self.batch_summary = payload['summary']
            self._show_batch()
            self.tabs.setCurrentIndex(1)

    def _on_failure(self, message: str):
        self.logs.appendPlainText('✗ ' + message)
        self.progress_label.setText('실패: ' + message)
        QMessageBox.critical(self, '작업 실패 / 파일 검증 오류', message)

    def _reload_records(self):
        self.record_combo.blockSignals(True)
        self.record_combo.clear()
        for i, record in enumerate(self.records):
            label = f"{Path(record['source']).name}  |  {record['algorithm']}"
            self.record_combo.addItem(label, i)
        self.record_combo.blockSignals(False)
        if self.records:
            self.record_combo.setCurrentIndex(0)
            self._show_record(0)

    def _show_record(self, index: int):
        if not (0 <= index < len(self.records)):
            return
        result = self.records[index]
        fields = [
            ('알고리즘', result['algorithm']),
            ('원본 크기', num(result['original_bytes'], suffix=' B')),
            ('문자 수 (UTF-8)', num(result['original_chars']) if result['original_chars'] is not None else 'UTF-8 아님'),
            ('압축 후 파일 크기', num(result['compressed_bytes'], suffix=' B')),
            ('실제 파일 기준 용량 절감률', num(result['saving_pct'], suffix=' %')),
            ('압축 배율 (원본 / 압축)', num(result['size_ratio'], suffix=' ×')),
            ('헤더 크기', num(result['header_bytes'], suffix=' B')),
            ('헤더 비중', num(result['header_share_pct'], suffix=' %')),
            ('압축 페이로드 크기', num(result['payload_bytes'], suffix=' B')),
            ('유효 데이터 비트', num(result['payload_bits'], suffix=' bit')),
            ('바이트 종류 수', num(result['symbol_types'])),
            ('정보 엔트로피 H(X)', num(result['entropy'], suffix=' bit/byte')),
            ('실제 평균 코드 비트 수', num(result['avg_payload_bits'], suffix=' bit/byte')),
            ('헤더 포함 평균 비트 수', num(result['avg_file_bits'], suffix=' bit/byte')),
            ('엔트로피 대비 차이', num(result['redundancy_bits'], suffix=' bit/byte')),
            ('엔트로피 대비 효율(H/실제)', num(result['entropy_efficiency_pct'], suffix=' %')),
            ('압축 소요 시간', num(result['compression_sec'], 6, ' s')),
            ('복원 소요 시간', num(result['decompression_sec'], 6, ' s')),
            ('압축 처리량', num(result['compression_mib_s'], suffix=' MiB/s')),
            ('복원 처리량', num(result['decompression_mib_s'], suffix=' MiB/s')),
            ('복원 결과', num(result['verified']) if result['verified'] is not None else '검사 미수행'),
            ('서로 다른 바이트 수', num(result['mismatched_bytes'])),
            ('복원 손실률', num(result['loss_pct'], 6, ' %')),
            ('정확도 확인 방법', result.get('integrity_method', '—')),
            ('원본 파일', result['source']),
            ('압축 파일', result['archive_path']),
            ('복원 파일', result['restored_path'] or '—'),
        ]
        self.metrics_table.setRowCount(len(fields))
        for i, (key, value) in enumerate(fields):
            self.metrics_table.setItem(i, 0, QTableWidgetItem(key))
            item = QTableWidgetItem(str(value))
            if key == '복원 결과' and result['verified'] is False:
                item.setForeground(QColor('#dc2626'))
            self.metrics_table.setItem(i, 1, item)
        self._populate_codes(result['symbols'])
        self.code_note.setText(result['code_note'])
        self._filter_codes(self.filter_input.text())

    def _populate_codes(self, rows: list[dict]):
        self.codes_table.setSortingEnabled(False)
        self.codes_table.setRowCount(len(rows))
        for r, entry in enumerate(rows):
            items = [entry['symbol'], str(entry['frequency']),
                     f"{entry['percentage']:.3f}%", entry['code'],
                     str(entry['length']) if entry['length'] is not None else '—']
            for col, value in enumerate(items):
                item = NumericItem(value) if col in (1, 2, 4) else QTableWidgetItem(value)
                if col in (1, 2, 4):
                    # 수치 정렬을 위해 UserRole에 실제 값을 저장
                    numeric = (entry['frequency'] if col == 1 else
                               entry['percentage'] if col == 2 else entry['length'] or 0)
                    item.setData(Qt.ItemDataRole.UserRole, numeric)
                self.codes_table.setItem(r, col, item)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(entry['percentage'] * 10))
            bar.setTextVisible(False)
            self.codes_table.setCellWidget(r, 5, bar)
        self.codes_table.setSortingEnabled(True)

    def _filter_codes(self, keyword: str):
        needle = keyword.lower().strip()
        for row in range(self.codes_table.rowCount()):
            self.codes_table.setRowHidden(row, bool(needle) and not any(
                needle in (self.codes_table.item(row, col).text().lower()
                           if self.codes_table.item(row, col) else '')
                for col in (0, 3)))

    def _show_batch(self):
        self.summary_table.setRowCount(len(self.batch_summary))
        for r, item in enumerate(self.batch_summary):
            columns = [item['algorithm'], str(item['count']),
                       num(item['saving_pct'], suffix=' %'),
                       num(item['compression_sec'], 5, ' s'),
                       num(item['decompression_sec'], 5, ' s'),
                       num(item['entropy'], suffix=' bit/byte'),
                       f"{item['pass_count']}/{item['count']}"]
            for c, value in enumerate(columns):
                self.summary_table.setItem(r, c, QTableWidgetItem(value))
        self.batch_table.setRowCount(len(self.batch_records))
        for r, item in enumerate(self.batch_records):
            cols = [Path(item['source']).name, item['algorithm'],
                    num(item['original_bytes']), num(item['compressed_bytes']),
                    num(item['saving_pct']), num(item['entropy']), num(item['loss_pct'], 6)]
            for c, value in enumerate(cols):
                self.batch_table.setItem(r, c, QTableWidgetItem(value))

    def _batch_clicked(self):
        rows = self.batch_table.selectionModel().selectedRows()
        if rows:
            row = rows[0].row()
            if row < self.record_combo.count():
                self.record_combo.setCurrentIndex(row)

    def _export_csv(self):
        if not self.batch_records:
            QMessageBox.information(self, '결과 없음', '일괄 실험을 실행한 후 CSV를 저장하세요.')
            return
        path, _ = QFileDialog.getSaveFileName(self, 'CSV 저장', 'huffman_results.csv',
                                              'CSV 파일 (*.csv)')
        if path:
            try:
                export_csv(self.batch_records, path)
                self.logs.appendPlainText('✓ CSV 저장: ' + path)
            except Exception as exc:
                QMessageBox.critical(self, 'CSV 저장 오류', str(exc))
