"""압축 방식에 종속되지 않는 단일 파일 + 일괄 실험 GUI."""
from __future__ import annotations
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QPushButton, QLineEdit, QComboBox, QLabel, QFileDialog, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QProgressBar,
    QPlainTextEdit, QSplitter, QCheckBox, QAbstractItemView, QScrollArea,
)
from algorithms.registry import CODECS
from core.bundle import BUNDLE_SUFFIX, benchmark_bundle, restore_bundle
from core.engine import (compress_file, compress_and_verify, decompress_file,
                         benchmark, export_csv, StageError)
from core.failures import failed_record
from core.result_view import CHART_METRICS, METRIC_INFO, chart_rows, save_records, load_records
from ui.result_chart import HorizontalBarChart


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
    failure = Signal(object)

    def __init__(self, job: str, options: dict):
        super().__init__()
        self.job = job
        self.options = options

    def run(self):
        try:
            progress = lambda frac, message: self.status.emit(int(frac * 100), message)
            if self.job == 'compress':
                result = compress_file(self.options['source'], self.options['target'],
                                       self.options['codec'], progress,
                                       measure_memory=self.options.get('measure_memory', False))
                self.success.emit({'records': [result], 'summary': [], 'message': '압축 완료 (복원 미수행)'})
            elif self.job == 'verify':
                result = compress_and_verify(
                    self.options['source'], self.options['target'], self.options['restored'],
                    self.options['codec'], progress,
                    measure_memory=self.options.get('measure_memory', False))
                self.success.emit({'records': [result], 'summary': [],
                                   'message': ('압축 + 복원 + 원본 바이트 비교 통과' if result['verified']
                                               else '압축 후 복원 불일치 감지! 손실률과 차이 바이트 수를 확인하세요.')})
            elif self.job in ('decompress', 'restore_bundle'):
                source, target = self.options['source'], self.options['target']
                memory = self.options.get('measure_memory', False)
                if self.job == 'restore_bundle':
                    result = restore_bundle(source, target, progress, measure_memory=memory)
                else:
                    result, _ = decompress_file(source, target, progress, measure_memory=memory)
                self.success.emit({'records': [result], 'summary': [],
                                   'message': '복원 + 저장된 SHA-256 확인 완료'})
            elif self.job == 'batch':
                runner = benchmark_bundle if self.options.get('bundle') else benchmark
                records, summary = runner(self.options['paths'], self.options['codecs'],
                                             self.options['output_dir'], progress,
                                             measure_memory=self.options.get('measure_memory', False))
                failures = sum(r.get('status') == '실패' or r.get('verified') is False for r in records)
                self.success.emit({'records': records, 'summary': summary,
                                   'message': f'총 {len(records)}개 실험 완료 (무손실 검증 실패 {failures}개)'})
            else:
                raise RuntimeError('알 수 없는 작업')
        except Exception as exc:
            restoring = self.job in ('decompress', 'restore_bundle')
            stage = (exc.stage if isinstance(exc, StageError) else
                     '복원' if restoring else '압축')
            cause = exc.original if isinstance(exc, StageError) else exc
            source = self.options.get('source', '')
            archive = (source if restoring else self.options.get('target', ''))
            restore = (self.options.get('target', '') if restoring else
                       self.options.get('restored', ''))
            record = failed_record(source, archive, restore,
                                   self.options.get('codec', '헤더 자동 식별'), stage, cause)
            self.failure.emit({'message': f'{stage} 실패: {record["error_category"]} / {record["error_message"]}',
                               'record': record})


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('허프만 코딩 실험실 | 팀 공통 GUI')
        self.resize(1260, 850)
        self.worker = None
        self.records: list[dict] = []
        self.batch_records: list[dict] = []
        self.batch_summary: list[dict] = []
        self.batch_record_indices: list[int] = []
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
        intro = QLabel('바이트 기반 무손실 압축  |  헤더 포함 압축률  |  엔트로피  |  정확도 검증')
        intro.setStyleSheet('color: #64748b;')
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._single_tab(), '개별 파일 압축 / 복원')
        self.tabs.addTab(self._batch_tab(), '다중 파일 실험 / 비교')
        self.tabs.addTab(self._detail_tab(), '상세 결과 / 허프만 코드표')
        self.tabs.addTab(self._chart_tab(), '결과 그래프 / 정렬')
        layout.addWidget(self.tabs, 1)

        bar_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat('%p%')
        self.progress_label = QLabel('준비 완료')
        bar_row.addWidget(self.progress, 3)
        bar_row.addWidget(self.progress_label, 4)
        layout.addLayout(bar_row)

        self.memory_check = QCheckBox('알고리즘 메모리 측정 (tracemalloc + RSS 10ms 샘플; 시간 측정에 영향)')
        self.memory_check.setChecked(False)
        layout.addWidget(self.memory_check)

        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumHeight(84)
        self.logs.setPlaceholderText('작업 결과 및 오류 메시지')
        layout.addWidget(self.logs)

    def _detail_tab(self):
        page = QWidget()
        dlayout = QVBoxLayout(page)
        help_text = QLabel('실행한 모든 파일·압축 방식 조합을 선택해 압축 및 복원 결과와 바이트별 코드표를 확인합니다.')
        help_text.setWordWrap(True)
        help_text.setStyleSheet('color: #64748b;')
        dlayout.addWidget(help_text)
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel('파일 · 알고리즘 · 실행 기록'))
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
        action_row = QHBoxLayout()
        save_btn = QPushButton('상세 결과 JSON 저장')
        save_btn.clicked.connect(self._save_history)
        load_btn = QPushButton('기존 결과 JSON 불러오기')
        load_btn.clicked.connect(self._load_history)
        clear_btn = QPushButton('결과 목록 비우기')
        clear_btn.clicked.connect(self._clear_history)
        all_csv_btn = QPushButton('전체 결과 CSV 저장')
        all_csv_btn.clicked.connect(self._export_all_csv)
        action_row.addWidget(save_btn)
        action_row.addWidget(load_btn)
        action_row.addWidget(all_csv_btn)
        action_row.addStretch()
        action_row.addWidget(clear_btn)
        dlayout.addLayout(action_row)
        return page


    def _chart_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(10)
        info = QLabel('막대를 클릭하면 해당 실행의 상세 결과로 이동합니다.')
        info.setWordWrap(True)
        info.setStyleSheet('color: #64748b;')
        lay.addWidget(info)
        files = QHBoxLayout()
        files.addWidget(QLabel('파일'))
        self.chart_file_combo = QComboBox()
        self.chart_file_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.chart_file_combo.addItem('전체 파일', None)
        self.chart_file_combo.currentIndexChanged.connect(self._update_chart)
        files.addWidget(self.chart_file_combo, 1)
        lay.addLayout(files)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('비교 지표'))
        self.chart_metric_combo = QComboBox()
        for key, label, unit, _ in CHART_METRICS:
            self.chart_metric_combo.addItem(label, key)
        self.chart_metric_combo.currentIndexChanged.connect(self._update_chart)
        controls.addWidget(self.chart_metric_combo, 3)
        controls.addWidget(QLabel('정렬'))
        self.chart_sort_combo = QComboBox()
        for label, value in (
            ('값 큰 순 ↓', 'value_desc'), ('값 작은 순 ↑', 'value_asc'),
            ('파일명 오름차순', 'name_asc'), ('파일명 내림차순', 'name_desc')
        ):
            self.chart_sort_combo.addItem(label, value)
        self.chart_sort_combo.currentIndexChanged.connect(self._update_chart)
        controls.addWidget(self.chart_sort_combo, 2)
        controls.addWidget(QLabel('알고리즘'))
        self.chart_algorithm_combo = QComboBox()
        self.chart_algorithm_combo.addItem('전체 알고리즘', None)
        for codec in CODECS.values():
            self.chart_algorithm_combo.addItem(codec.name, codec.name)
        self.chart_algorithm_combo.currentIndexChanged.connect(self._update_chart)
        controls.addWidget(self.chart_algorithm_combo, 2)
        lay.addLayout(controls)
        tools = QHBoxLayout()
        self.chart_info_label = QLabel('그래프를 표시할 결과가 없습니다.')
        self.chart_info_label.setStyleSheet('color: #475569;')
        tools.addWidget(self.chart_info_label, 1)
        export_image = QPushButton('그래프 PNG 저장')
        export_image.clicked.connect(self._export_chart_png)
        tools.addWidget(export_image)
        lay.addLayout(tools)
        self.chart_scroll = QScrollArea()
        self.chart_scroll.setWidgetResizable(True)
        self.chart = HorizontalBarChart()
        self.chart.recordSelected.connect(self._go_to_record)
        self.chart_scroll.setWidget(self.chart)
        lay.addWidget(self.chart_scroll, 1)
        return page

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
        self.decode_btn = QPushButton('압축 파일 / 묶음 복원')
        self.verify_btn.setObjectName('primary')
        self.compress_btn.clicked.connect(lambda: self._start_single('compress'))
        self.verify_btn.clicked.connect(lambda: self._start_single('verify'))
        self.decode_btn.clicked.connect(lambda: self._start_single('decompress'))
        for button in (self.compress_btn, self.verify_btn, self.decode_btn):
            btn_row.addWidget(button)
        opts.addLayout(btn_row)
        lay.addWidget(options)

        tips = QLabel('※ 압축·복원 실행 시 입력 파일·출력 위치·압축 헤더를 자동 검사합니다. '
                      '오류가 나면 「상세 결과」에 실패 단계와 원인을 기록합니다. '
                      '정확한 손실률(원본 바이트 비교)은 「압축 + 복원 + 손실률 검사」에서 계산합니다. '
                      '압축 파일과 묶음을 복원할 때 알고리즘은 헤더에서 자동 선택합니다.')
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
        actions.addWidget(QLabel('실행 방식'))
        self.batch_mode_combo = QComboBox()
        self.batch_mode_combo.addItem('파일별로 각각 실행', False)
        self.batch_mode_combo.addItem('한 묶음으로 실행 · 폴더로 복원', True)
        actions.addWidget(self.batch_mode_combo, 1)
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
        self.batch_table = QTableWidget(0, 9)
        self.batch_table.setHorizontalHeaderLabels(
            ['파일', '알고리즘', '상태', '원본(byte)', '압축(byte)', '절감률(%)', '엔트로피', '손실률(%)', '오류 원인'])
        self.batch_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.batch_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.batch_table.itemSelectionChanged.connect(self._batch_clicked)
        self.batch_table.cellDoubleClicked.connect(lambda row, col: self._open_batch_detail(row))
        lay.addWidget(self.batch_table, 3)
        navigate = QHBoxLayout()
        navigate.addWidget(QLabel('행을 더블클릭하면 해당 파일·알고리즘의 상세 결과로 이동합니다.'))
        navigate.addStretch()
        detail_btn = QPushButton('선택 실험 상세 보기')
        detail_btn.clicked.connect(self._open_selected_batch_detail)
        chart_btn = QPushButton('전체 결과 그래프 보기')
        chart_btn.clicked.connect(lambda: self.tabs.setCurrentIndex(3))
        navigate.addWidget(detail_btn)
        navigate.addWidget(chart_btn)
        lay.addLayout(navigate)
        return page

    def _browse_single(self):
        path, _ = QFileDialog.getOpenFileName(self, '파일 선택', '',
                                              '텍스트/압축 파일 (*.txt *.huf *.hufbundle *.bin);;모든 파일 (*)')
        if path:
            self.source_input.setText(path)

    def _add_paths(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '실험 파일 선택', '', '모든 파일 (*)')
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

    def _confirm_large_fgk(self, paths: list[str], codecs: list[str]) -> bool:
        """대용량 FGK의 매우 긴 실행 시간을 시작 전에 명시적으로 경고합니다."""
        if 'fgk' not in codecs:
            return True
        large = []
        for path in paths:
            try:
                if Path(path).stat().st_size >= 100_000_000:
                    large.append(Path(path).name)
            except OSError:
                continue
        if not large:
            return True
        answer = QMessageBox.question(
            self, '대용량 FGK 실행 주의',
            'FGK 적응형 허프만은 바이트마다 Python 트리를 갱신합니다.\n'
            '100MB 이상 파일은 압축/복원에 매우 오랜 시간이 걸릴 수 있습니다.\n'
            '특히 enwik9 1GB에서는 정적 허프만부터 테스트하는 것을 권장합니다.\n\n'
            '계속 실행할까요?\n' + ', '.join(large[:5]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _start_single(self, job: str):
        source = self.source_input.text().strip()
        if not source:
            QMessageBox.warning(self, '파일 누락', '입력 파일 경로를 선택하세요.')
            return
        if job == 'decompress':
            if Path(source).suffix.lower() == BUNDLE_SUFFIX:
                parent = QFileDialog.getExistingDirectory(self, '묶음 복원 폴더를 만들 위치',
                                                          str(Path(source).parent))
                if not parent:
                    return
                dest = Path(parent) / (Path(source).stem + '_restored')
                if dest.exists() or dest.is_symlink():
                    QMessageBox.warning(self, '복원 폴더 충돌',
                                        f'다른 위치를 선택하세요. 이미 있는 경로입니다.\n{dest}')
                    return
                self._launch('restore_bundle', {'source': source, 'target': str(dest)})
                return
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
        if job != 'decompress' and not self._confirm_large_fgk([source], [options['codec']]):
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
        if not self._confirm_large_fgk(self.selected_paths, codecs):
            return
        output = QFileDialog.getExistingDirectory(self, '실험 결과 폴더 선택',
                                                  str(Path.cwd() / 'results'))
        if not output:
            return
        self._launch('batch', {'paths': self.selected_paths[:], 'codecs': codecs,
                               'output_dir': output, 'bundle': self.batch_mode_combo.currentData()})

    def _launch(self, job: str, options: dict):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(self, '실행 중', '다른 작업이 실행 중입니다.')
            return
        self.progress.setValue(0)
        self.progress_label.setText('실행 중...')
        self.logs.appendPlainText(f'▶ {job} 실행')
        options = {**options, 'measure_memory': self.memory_check.isChecked()}
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
        new_records = payload['records']
        failures = [r for r in new_records if r.get('status') == '실패' or r.get('verified') is False]
        self.logs.appendPlainText(('⚠ ' if failures else '✓ ') + payload['message'])
        self.progress.setValue(100)
        self.progress_label.setText(payload['message'] + ' · 상세 결과/그래프 탭에서 확인 가능')
        start_index = len(self.records)
        # 작업을 새로 실행해도 이전 실험 기록을 유지해 단일/일괄 데이터를 함께 비교합니다.
        self.records.extend(new_records)
        self._reload_records(selected_index=len(self.records) - 1)
        self._refresh_chart_filters()
        self._update_chart()
        if payload['summary']:
            self.batch_records = new_records
            self.batch_record_indices = list(range(start_index, len(self.records)))
            self.batch_summary = payload['summary']
            self._show_batch()
            self.tabs.setCurrentIndex(1)
        elif new_records:
            self.tabs.setCurrentIndex(2)

    def _on_failure(self, payload: dict):
        message = payload['message']
        record = payload['record']
        self.records.append(record)
        self._reload_records(selected_index=len(self.records)-1)
        self._refresh_chart_filters()
        self._update_chart()
        self.tabs.setCurrentIndex(2)
        self.logs.appendPlainText('✗ ' + message)
        self.progress_label.setText('실패: 상세 결과에 실패 원인이 기록되었습니다.')
        QMessageBox.critical(self, '작업 실패 / 파일 검증 오류', message)

    def _reload_records(self, selected_index: int | None = None):
        if selected_index is None:
            selected_index = self.record_combo.currentData()
        self.record_combo.blockSignals(True)
        self.record_combo.clear()
        for i, record in enumerate(self.records):
            file_name = Path(record.get('source') or '알 수 없는 파일').name
            verified = ('작업 실패' if record.get('status') == '실패' else
                        '복원 OK' if record.get('verified') is True else
                        '검증 실패' if record.get('verified') is False else '복원 미검사')
            label = f"#{i+1:03d} · {file_name} · {record['algorithm']} · {verified}"
            self.record_combo.addItem(label, i)
        self.record_combo.blockSignals(False)
        if self.records:
            if not isinstance(selected_index, int) or not (0 <= selected_index < len(self.records)):
                selected_index = 0
            self.record_combo.setCurrentIndex(selected_index)
            self._show_record(selected_index)
        else:
            self.filter_input.clear()
            self.metrics_table.setRowCount(0)
            self.codes_table.setRowCount(0)
            self.code_note.setText('실험 결과를 선택하면 코드표를 표시합니다.')

    def _show_record(self, index: int):
        if not (0 <= index < len(self.records)):
            return
        result = self.records[index]
        fields = [
            ('작업 상태', result.get('status', '기록 없음')),
            ('실패 단계', result.get('failure_stage') or '—'),
            ('오류 분류', result.get('error_category') or '—'),
            ('오류 종류', result.get('error_type') or '—'),
            ('오류 원인', result.get('error_message') or '—'),
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
            ('압축 소요 시간(파일 읽기~쓰기)', num(result['compression_sec'], 6, ' s')),
            ('압축 스트리밍 인코더 시간(청크 입출력 포함)', num(result.get('compression_algorithm_sec'), 6, ' s')),
            ('압축 Python 추적 피크', num(result.get('compression_trace_peak_mib'), 3, ' MiB')),
            ('압축 RSS 최고 관측값(전체 프로세스)', num(result.get('compression_rss_peak_mib'), 3, ' MiB')),
            ('압축 RSS 증가 피크(시작 대비)', num(result.get('compression_rss_increase_mib'), 3, ' MiB')),
            ('복원 소요 시간(파일 읽기~쓰기)', num(result['decompression_sec'], 6, ' s')),
            ('복원 스트리밍 디코더 시간(청크 입출력 포함)', num(result.get('decompression_algorithm_sec'), 6, ' s')),
            ('복원 Python 추적 피크', num(result.get('decompression_trace_peak_mib'), 3, ' MiB')),
            ('복원 RSS 최고 관측값(전체 프로세스)', num(result.get('decompression_rss_peak_mib'), 3, ' MiB')),
            ('복원 RSS 증가 피크(시작 대비)', num(result.get('decompression_rss_increase_mib'), 3, ' MiB')),
            ('압축 처리량', num(result['compression_mib_s'], suffix=' MiB/s')),
            ('복원 처리량', num(result['decompression_mib_s'], suffix=' MiB/s')),
            ('복원 결과', num(result['verified']) if result['verified'] is not None else '검사 미수행'),
            ('서로 다른 바이트 수', num(result['mismatched_bytes'])),
            ('복원 손실률', num(result['loss_pct'], 6, ' %')),
            ('정확도 확인 방법', result.get('integrity_method', '—')),
            ('자동 사전 검사 결과', result.get('preflight_explanation') or '—'),
            ('원본 파일', result['source']),
            ('압축 파일', result['archive_path']),
            ('복원 파일', result['restored_path'] or '—'),
        ]
        self.metrics_table.setRowCount(len(fields))
        for i, (key, value) in enumerate(fields):
            self.metrics_table.setItem(i, 0, QTableWidgetItem(key))
            item = QTableWidgetItem(str(value))
            if (key == '작업 상태' and result.get('status') == '실패') or (key == '복원 결과' and result['verified'] is False):
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

    def _update_chart(self, *_):
        if not hasattr(self, 'chart'):
            return
        key = self.chart_metric_combo.currentData()
        if key not in METRIC_INFO:
            return
        label, unit, precision = METRIC_INFO[key]
        source = self.chart_file_combo.currentData()
        self.chart_file_combo.setToolTip(source or '')
        filtered, omitted = chart_rows(self.records, key,
                                       self.chart_sort_combo.currentData() or 'value_desc',
                                       self.chart_algorithm_combo.currentData(), source=source)
        self.chart.set_data(filtered, label, unit, precision)
        total = len(filtered) + omitted
        self.chart_info_label.setText(
            f'표시: {len(filtered):,}건 / 선택 조건에 맞는 기록 {total:,}건  |  '
            f'값 없음으로 제외: {omitted:,}건  |  각 막대: 파일 × 압축 방식 × 실행 회차')
        self.chart_scroll.verticalScrollBar().setValue(0)

    def _export_chart_png(self):
        if not self.chart.rows:
            QMessageBox.information(self, '그래프 없음', '표시할 실험 결과가 없습니다.')
            return
        path, _ = QFileDialog.getSaveFileName(self, '그래프 이미지 저장',
                                              'huffman_chart.png', 'PNG 이미지 (*.png)')
        if not path:
            return
        # 스크롤된 영역만 아니라 전체 그래프를 캡처합니다.
        if not self.chart.grab().save(path, 'PNG'):
            QMessageBox.warning(self, '저장 실패', 'PNG 이미지를 저장하지 못했습니다.')
        else:
            self.logs.appendPlainText('✓ 그래프 PNG 저장: ' + path)

    def _export_all_csv(self):
        if not self.records:
            QMessageBox.information(self, '내보낼 결과 없음', '먼저 압축/실험을 실행하세요.')
            return
        path, _ = QFileDialog.getSaveFileName(self, '전체 결과 CSV 저장',
                                              'huffman_all_results.csv', 'CSV 파일 (*.csv)')
        if path:
            try:
                export_csv(self.records, path)
                self.logs.appendPlainText('✓ 전체 결과 CSV 저장: ' + path)
            except Exception as exc:
                QMessageBox.critical(self, 'CSV 내보내기 실패', str(exc))

    def _go_to_record(self, index: int):
        if 0 <= index < len(self.records):
            combo_index = self.record_combo.findData(index)
            if combo_index >= 0:
                self.record_combo.setCurrentIndex(combo_index)
                self.tabs.setCurrentIndex(2)

    def _save_history(self):
        if not self.records:
            QMessageBox.information(self, '저장할 결과 없음', '먼저 압축/실험을 실행하세요.')
            return
        path, _ = QFileDialog.getSaveFileName(self, '상세 결과 JSON 저장',
                                              'huffman_detail_results.json', 'JSON 파일 (*.json)')
        if path:
            try:
                save_records(self.records, path)
                self.logs.appendPlainText('✓ 상세 결과 저장: ' + path)
            except Exception as exc:
                QMessageBox.critical(self, '결과 저장 실패', str(exc))

    def _load_history(self):
        path, _ = QFileDialog.getOpenFileName(self, '상세 결과 JSON 불러오기', '', 'JSON 파일 (*.json)')
        if path:
            try:
                loaded = load_records(path)
                if not loaded:
                    QMessageBox.information(self, '결과 없음', '저장된 실행 결과가 없습니다.')
                    return
                self.records.extend(loaded)
                self._reload_records(selected_index=len(self.records) - 1)
                self._refresh_chart_filters()
                self._update_chart()
                self.logs.appendPlainText(f'✓ 상세 결과 {len(loaded)}건 불러옴: {path}')
            except Exception as exc:
                QMessageBox.critical(self, '결과 불러오기 실패', str(exc))

    def _clear_history(self):
        if not self.records:
            return
        reply = QMessageBox.question(self, '결과 목록 비우기',
                                     '화면에 쌓인 모든 실행 결과를 비울까요?\n'
                                     '실제 압축/복원 파일은 삭제하지 않습니다.')
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.records.clear()
        self.batch_records.clear()
        self.batch_record_indices.clear()
        self.batch_summary.clear()
        self._reload_records()
        self._show_batch()
        self._refresh_chart_filters()
        self._update_chart()
        self.logs.appendPlainText('✓ 화면의 결과 기록을 비웠습니다.')

    def _refresh_chart_filters(self):
        files = self.chart_file_combo
        selected_source = files.currentData()
        sources = sorted({r['source'] for r in self.records if r.get('source')},
                         key=lambda source: (Path(source).name.casefold(), source.casefold(), source))
        files.blockSignals(True)
        files.clear()
        files.addItem('전체 파일', None)
        for source in sources:
            files.addItem(source, source)
            files.setItemData(files.count() - 1, source, Qt.ItemDataRole.ToolTipRole)
        files.setCurrentIndex(max(0, files.findData(selected_source)))
        files.blockSignals(False)

        # 추가 알고리즘 / 저장된 결과의 기존 알고리즘까지 모두 선택 가능.
        selected = self.chart_algorithm_combo.currentData()
        names = sorted({r.get('algorithm', '') for r in self.records if r.get('algorithm')}
                       | {codec.name for codec in CODECS.values()})
        self.chart_algorithm_combo.blockSignals(True)
        self.chart_algorithm_combo.clear()
        self.chart_algorithm_combo.addItem('전체 알고리즘', None)
        for name in names:
            self.chart_algorithm_combo.addItem(name, name)
        next_index = self.chart_algorithm_combo.findData(selected)
        self.chart_algorithm_combo.setCurrentIndex(max(0, next_index))
        self.chart_algorithm_combo.blockSignals(False)

    def _open_batch_detail(self, row: int):
        if 0 <= row < len(self.batch_record_indices):
            record_index = self.batch_record_indices[row]
            combo_index = self.record_combo.findData(record_index)
            if combo_index >= 0:
                self.record_combo.setCurrentIndex(combo_index)
                self.tabs.setCurrentIndex(2)

    def _open_selected_batch_detail(self):
        rows = self.batch_table.selectionModel().selectedRows()
        if rows:
            self._open_batch_detail(rows[0].row())
        else:
            QMessageBox.information(self, '결과 선택', '먼저 비교표에서 행을 선택하세요.')

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
            cols = [Path(item['source']).name, item['algorithm'], item.get('status', '—'),
                    num(item['original_bytes']), num(item['compressed_bytes']),
                    num(item['saving_pct']), num(item['entropy']), num(item['loss_pct'], 6),
                    item.get('error_message', '')]
            for c, value in enumerate(cols):
                self.batch_table.setItem(r, c, QTableWidgetItem(value))

    def _batch_clicked(self):
        rows = self.batch_table.selectionModel().selectedRows()
        if rows:
            row = rows[0].row()
            if row < len(self.batch_record_indices):
                self.record_combo.setCurrentIndex(self.batch_record_indices[row])

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
