import io
import os
import signal
import sys
from loguru import logger

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QKeySequence
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                             QDoubleSpinBox, QFileDialog, QGridLayout,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPushButton,
                             QScrollArea, QSpinBox, QStyle, QTextBrowser,
                             QVBoxLayout, QWidget, QShortcut, QMessageBox, QSlider)

import audfprint


class CLIOutputBox(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("CLI Output", parent)
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()
        self.outputTextBrowser = QTextBrowser()
        self.outputTextBrowser.setReadOnly(True)
        layout.addWidget(self.outputTextBrowser)
        self.setLayout(layout)

    def appendText(self, text, color="black"):
        colored_text = f'<span style="color:{color};">{text}</span>'
        self.outputTextBrowser.append(colored_text)

    def info(self, text):
        self.appendText(text, color="blue")

    def debug(self, text):
        self.appendText(text, color="gray")

    def warning(self, text):
        self.appendText(text, color="orange")

    def error(self, text):
        self.appendText(text, color="red")

    def clearText(self):
        self.outputTextBrowser.clear()


class AudfprintGUI(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.initUI()
        self.showMaximized()
        self.isExecuting = False

    def initUI(self):
        mainLayout = QHBoxLayout()
        self.fileListView = QListWidget()
        self.maxPathDepthSlider = QSlider(Qt.Horizontal)

        quitShortcut = QShortcut(QKeySequence('Ctrl+Q'), self)
        quitShortcut.activated.connect(self.close)

        # New Left side: File Picker
        filePickerScrollArea = QScrollArea()
        filePickerScrollArea.setWidgetResizable(True)
        filePickerWidget = QWidget()
        filePickerLayout = QVBoxLayout(filePickerWidget)

        maxPathDepthSliderGroupBox = self.createMaxPathDepthSlider()
        filePickerLayout.addWidget(maxPathDepthSliderGroupBox)

        # File list
        self.fileListGroupBox = self.createFileListGroupBox()
        filePickerLayout.addWidget(self.fileListGroupBox)

        # Add file and directory buttons in a row
        fileDirButtonLayout = QHBoxLayout()

        self.addFileButton = QPushButton()
        self.addFileButton.setIcon(self.style().standardIcon(QStyle.SP_FileIcon))
        self.addFileButton.setStyleSheet(
            "QPushButton { background-color: white; color: black; }"
        )
        self.addFileButton.clicked.connect(self.addFile)
        fileDirButtonLayout.addWidget(self.addFileButton)

        self.addDirButton = QPushButton()
        self.addDirButton.setIcon(self.style().standardIcon(QStyle.SP_DirIcon))
        self.addDirButton.setStyleSheet(
            "QPushButton { background-color: white; color: black; }"
        )
        self.addDirButton.clicked.connect(self.addDirectory)
        fileDirButtonLayout.addWidget(self.addDirButton)

        filePickerLayout.addLayout(fileDirButtonLayout)

        filePickerScrollArea.setWidget(filePickerWidget)
        mainLayout.addWidget(filePickerScrollArea)

        # Central: Input controls (previously left side)
        centralScrollArea = QScrollArea()
        centralScrollArea.setWidgetResizable(True)
        centralWidget = QWidget()
        centralLayout = QVBoxLayout(centralWidget)
        centralLayout.setSizeConstraint(QVBoxLayout.SetNoConstraint)  # Ensure layout does not force widgets to expand

        createBackendGroupBox = self.createBackendGroupBox()
        centralLayout.addWidget(createBackendGroupBox)

        # Command selection
        cmdGroupBox = self.createCommandGroupBox()
        centralLayout.addWidget(cmdGroupBox)

        # Database file selection
        dbaseGroupBox = self.createDatabaseGroupBox()
        centralLayout.addWidget(dbaseGroupBox)

        # Parameter settings
        paramGroupBox = self.createParameterGroupBox()
        centralLayout.addWidget(paramGroupBox)

        # Precompute directory selection
        precompdirGroupBox = self.createPrecomputeDirGroupBox()
        centralLayout.addWidget(precompdirGroupBox)

        # Additional options
        optionsGroupBox = self.createOptionsGroupBox()
        centralLayout.addWidget(optionsGroupBox)

        # Run button
        self.runButton = QPushButton("Run")
        self.runButton.setFont(QFont("Arial", 14))
        self.runButton.clicked.connect(self.runAudfprint)
        centralLayout.addWidget(self.runButton)

        # Miscellaneous parameters
        miscGroupBox = self.createMiscGroupBox()
        centralLayout.addWidget(miscGroupBox)

        centralScrollArea.setWidget(centralWidget)
        mainLayout.addWidget(centralScrollArea)

        # Right side: CLI Output
        self.cliOutputBox = CLIOutputBox(self)
        rightLayout = QVBoxLayout()
        rightLayout.addWidget(self.cliOutputBox)
        rightWidget = QWidget()
        rightWidget.setLayout(rightLayout)
        mainLayout.addWidget(rightWidget)

        self.updateUIBasedOnCommand()
        self.cmdCombo.currentIndexChanged.connect(self.updateUIBasedOnCommand)
        self.fileListView.itemSelectionChanged.connect(lambda: self.maxPathDepthSlider.setMaximum(self.calculateMaxDepth()))
        self.fileListView.itemChanged.connect(lambda: self.maxPathDepthSlider.setMaximum(self.calculateMaxDepth()))
        self.maxPathDepthSlider.valueChanged.connect(self.updateFileList)

        self.setLayout(mainLayout)
        self.setWindowTitle("Audfprint GUI")
        self.show()

    def updateUIBasedOnCommand(self):
        cmd = self.cmdCombo.currentText()
        if "new - Create a new fingerprint database" in cmd:
            self.dbaseBrowseButton.setText("Create")
        else:
            self.dbaseBrowseButton.setText("Browse")

    def browseFile(self):
        cmd = self.cmdCombo.currentText()
        print(f"Browsing file for command: {cmd}")
        if "new - Create a new fingerprint database" in cmd:
            options = QFileDialog.Options()
            options |= QFileDialog.DontConfirmOverwrite
            fname, _ = QFileDialog.getSaveFileName(
                self, "Create New Database", os.path.expanduser("~"), "Database Files (*.db)", options=options
            )
            if fname:
                self.dbaseLineEdit.setText(fname)
        elif "add - Add new files to an existing fingerprint database" in cmd:
            fname, _ = QFileDialog.getOpenFileName(
                self, "Open Database File", os.path.expanduser("~"), "Database Files (*.db)"
            )
            if fname:
                self.dbaseLineEdit.setText(fname)
        else:
            fname, _ = QFileDialog.getOpenFileName(
                self, "Open File", os.path.expanduser("~"), "All Files (*)"
            )
            if fname:
                self.dbaseLineEdit.setText(fname)

    def createFileListGroupBox(self):
        fileListGroupBox = QGroupBox("File List")
        fileListLayout = QVBoxLayout()

        fileListLayout.addWidget(self.fileListView)

        # File type selection input that shows a modal dialog on click
        self.fileTypeLineEdit = QLineEdit(
            "MP3 Files (*.mp3), WAV Files (*.wav), FLAC Files (*.flac)"
        )
        self.fileTypeLineEdit.setReadOnly(True)
        self.fileTypeLineEdit.mousePressEvent = self.showFileTypeDialog
        fileListLayout.addWidget(self.fileTypeLineEdit)

        fileListGroupBox.setLayout(fileListLayout)
        return fileListGroupBox

    def showFileTypeDialog(self, event):
        dialog = QDialog(self)
        dialog.setWindowTitle("Select File Types")
        layout = QVBoxLayout(dialog)

        # List widget for selecting file types
        fileTypeListWidget = QListWidget()
        fileTypeListWidget.setSelectionMode(QListWidget.MultiSelection)
        fileTypeItems = [
            "All Files (*.*)",
            "MP3 Files (*.mp3)",
            "WAV Files (*.wav)",
            "FLAC Files (*.flac)",
            "AAC Files (*.aac)",
            "OGG Files (*.ogg)",
            "M4A Files (*.m4a)"
        ]
        currentSelections = self.fileTypeLineEdit.text().split(", ")
        for item in fileTypeItems:
            listItem = QListWidgetItem(item)
            listItem.setCheckState(
                Qt.Checked
                if any(sel in item for sel in currentSelections)
                else Qt.Unchecked
            )
            fileTypeListWidget.addItem(listItem)

        layout.addWidget(fileTypeListWidget)

        self.handleFileTypeSelection(fileTypeListWidget)

        # Connect item changed signal to a slot that handles enabling/disabling
        fileTypeListWidget.itemChanged.connect(lambda: self.handleFileTypeSelection(fileTypeListWidget))

        # Buttons for OK and Cancel
        buttonsLayout = QHBoxLayout()
        okButton = QPushButton("OK")
        cancelButton = QPushButton("Cancel")
        buttonsLayout.addWidget(okButton)
        buttonsLayout.addWidget(cancelButton)
        layout.addLayout(buttonsLayout)
        okButton.clicked.connect(
            lambda: self.updateSelectedFileTypes(fileTypeListWidget, dialog)
        )
        cancelButton.clicked.connect(dialog.reject)

        warningLabel1 = QLabel("<font color='orange'>Caution: Selecting 'All Files' makes you accountable</font>")
        warningLabel2 = QLabel("<font color='orange'>for verifying that all chosen files are compatible with audfprint and ffmpeg.</font>")
        layout.addWidget(warningLabel1)
        layout.addWidget(warningLabel2)

        dialog.setLayout(layout)
        dialog.exec_()

    def updateFileList(self, depth):
        self.fileListView.clear()
        for i in range(self.fileListView.count()):
            file_path = self.fileListView.item(i).text()
            truncated_path = self.truncatePath(file_path, depth)
            self.fileListView.addItem(truncated_path)

    def truncatePath(self, path, depth):
        parts = path.split(os.sep)
        if depth >= len(parts):
            return path
        else:
            return os.sep.join(["..."] + parts[-depth:])

    def handleFileTypeSelection(self, listWidget):
        # Temporarily block signals to prevent recursion
        listWidget.blockSignals(True)

        allFilesItem = listWidget.item(0)  # Assuming "All Files (*.*)" is the first item
        if allFilesItem.checkState() == Qt.Checked:
            # Disable other items
            for index in range(1, listWidget.count()):
                item = listWidget.item(index)
                item.setCheckState(Qt.Unchecked)
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
        else:
            # Enable other items
            for index in range(1, listWidget.count()):
                item = listWidget.item(index)
                item.setFlags(item.flags() | Qt.ItemIsEnabled)

        # Re-enable signals after modifications
        listWidget.blockSignals(False)

    def updateSelectedFileTypes(self, listWidget, dialog):
        selectedTypes = []
        for index in range(listWidget.count()):
            if listWidget.item(index).checkState() == Qt.Checked:
                selectedTypes.append(listWidget.item(index).text())
        self.fileTypeLineEdit.setText(", ".join(selectedTypes))
        dialog.accept()

    def addFile(self):
        fname, _ = QFileDialog.getOpenFileName(
            self, "Select File", os.path.expanduser("~"), self.fileTypeLineEdit.text()
        )
        if fname:
            self.fileListView.addItem(fname)
            self.updateFileList(self.maxPathDepthSlider.value())
    
    def addDirectory(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Directory", os.path.expanduser("~")
        )
        if dir_path:
            valid_extensions = self.extractExtensions(True)
            logger.info(f"Adding directory: {dir_path}")
            logger.info(f"Valid extensions: {valid_extensions}")
            for file_name in os.listdir(dir_path):
                full_file_path = os.path.join(dir_path, file_name)
                if os.path.isfile(full_file_path) and self.isValidExtension(file_name, valid_extensions):
                    logger.info(f"Adding file: {full_file_path}")
                    self.fileListView.addItem(full_file_path)
            self.updateFileList(self.maxPathDepthSlider.value())

    def extractExtensions(self, is_raw: bool = False):
        extensions = []
        fileTypeText = self.fileTypeLineEdit.text()
        parts = fileTypeText.split(", ")
        for part in parts:
            ext = part.split(" ")[-1].strip("()")
            extensions.extend(ext.split(";"))
        if is_raw:
            extensions = [ext.removeprefix("*.") for ext in extensions]
        return extensions

    def isValidExtension(self, file_name, valid_extensions):
        return any(file_name.lower().endswith(f".{ext}") for ext in valid_extensions)

    def createBackendGroupBox(self):
        backendGroupBox = QGroupBox("Processing backend")
        backendLayout = QHBoxLayout()
        self.backendCombo = QComboBox()
        self.backendCombo.addItems(["audfprint-enhanced", "audfprint"])
        self.backendCombo.setEnabled(False)  # Disable the combo box
        backendLayout.addWidget(self.backendCombo)
        backendGroupBox.setLayout(backendLayout)
        return backendGroupBox

    def createCommandGroupBox(self):
        cmdGroupBox = QGroupBox("Command")
        cmdLayout = QHBoxLayout()
        self.cmdCombo = QComboBox()
        self.cmdCombo.addItems(
            [
                "new - Create a new fingerprint database",
                "add - Add new files to an existing fingerprint database",
                "precompute - Precompute fingerprints for input WAV files",
                "merge - Merge existing fingerprint databases into one",
                "newmerge - Create a new database by merging existing databases",
                "match - Match audio samples against the fingerprint database",
                "list - List operations or configurations",
                "remove - Remove entries or configurations",
            ]
        )
        cmdLayout.addWidget(self.cmdCombo)
        cmdGroupBox.setLayout(cmdLayout)
        return cmdGroupBox

    def createDatabaseGroupBox(self):
        dbaseGroupBox = QGroupBox("Database File")
        dbaseLayout = QHBoxLayout()
        self.dbaseLineEdit = QLineEdit()
        self.dbaseBrowseButton = QPushButton("Browse")
        self.dbaseBrowseButton.clicked.connect(self.browseFile)
        dbaseLayout.addWidget(self.dbaseLineEdit)
        dbaseLayout.addWidget(self.dbaseBrowseButton)
        dbaseGroupBox.setLayout(dbaseLayout)
        return dbaseGroupBox

    def createParameterGroupBox(self):
        paramGroupBox = QGroupBox("Parameters")
        paramLayout = QGridLayout()

        self.densityLabel = QLabel("Density:")
        self.densityLabel.setToolTip("Target hashes per second")
        self.densityLabel.mousePressEvent = lambda event: self.densitySpinBox.setValue(
            20
        )
        self.densitySpinBox = QSpinBox()
        self.densitySpinBox.setRange(1, 100)
        self.densitySpinBox.setValue(20)
        paramLayout.addWidget(self.densityLabel, 0, 0)
        paramLayout.addWidget(self.densitySpinBox, 0, 1)

        self.hashbitsLabel = QLabel("Hash Bits:")
        self.hashbitsLabel.setToolTip("How many bits in each hash")
        self.hashbitsLabel.mousePressEvent = (
            lambda event: self.hashbitsSpinBox.setValue(20)
        )
        self.hashbitsSpinBox = QSpinBox()
        self.hashbitsSpinBox.setRange(1, 32)
        self.hashbitsSpinBox.setValue(20)
        paramLayout.addWidget(self.hashbitsLabel, 1, 0)
        paramLayout.addWidget(self.hashbitsSpinBox, 1, 1)

        self.bucketsizeLabel = QLabel("Bucket Size:")
        self.bucketsizeLabel.setToolTip("Number of entries per bucket")
        self.bucketsizeLabel.mousePressEvent = (
            lambda event: self.bucketsizeSpinBox.setValue(100)
        )
        self.bucketsizeSpinBox = QSpinBox()
        self.bucketsizeSpinBox.setRange(1, 1000)
        self.bucketsizeSpinBox.setValue(100)
        paramLayout.addWidget(self.bucketsizeLabel, 2, 0)
        paramLayout.addWidget(self.bucketsizeSpinBox, 2, 1)

        self.maxtimeLabel = QLabel("Max Time:")
        self.maxtimeLabel.setToolTip("Largest time value stored")
        self.maxtimeLabel.mousePressEvent = lambda event: self.maxtimeSpinBox.setValue(
            16384
        )
        self.maxtimeSpinBox = QSpinBox()
        self.maxtimeSpinBox.setRange(1, 65536)
        self.maxtimeSpinBox.setValue(16384)
        paramLayout.addWidget(self.maxtimeLabel, 3, 0)
        paramLayout.addWidget(self.maxtimeSpinBox, 3, 1)

        self.samplerateLabel = QLabel("Sample Rate:")
        self.samplerateLabel.setToolTip("Resample input files to this rate")
        self.samplerateLabel.mousePressEvent = (
            lambda event: self.samplerateSpinBox.setValue(11025)
        )
        self.samplerateSpinBox = QSpinBox()
        self.samplerateSpinBox.setRange(8000, 48000)
        self.samplerateSpinBox.setValue(11025)
        paramLayout.addWidget(self.samplerateLabel, 4, 0)
        paramLayout.addWidget(self.samplerateSpinBox, 4, 1)

        paramGroupBox.setLayout(paramLayout)
        return paramGroupBox

    def createPrecomputeDirGroupBox(self):
        precompdirGroupBox = QGroupBox("Precompute Directory")
        precompdirLayout = QHBoxLayout()
        self.precompdirLineEdit = QLineEdit("./")
        self.precompdirBrowseButton = QPushButton("Browse")
        self.precompdirBrowseButton.clicked.connect(self.browseDirectory)
        precompdirLayout.addWidget(self.precompdirLineEdit)
        precompdirLayout.addWidget(self.precompdirBrowseButton)
        precompdirGroupBox.setLayout(precompdirLayout)
        return precompdirGroupBox

    def createOptionsGroupBox(self):
        optionsGroupBox = QGroupBox("Options")
        optionsLayout = QVBoxLayout()

        self.skipExistingCheckBox = QCheckBox("Skip Existing")
        optionsLayout.addWidget(self.skipExistingCheckBox)

        self.continueOnErrorCheckBox = QCheckBox("Continue on Error")
        optionsLayout.addWidget(self.continueOnErrorCheckBox)

        self.listCheckBox = QCheckBox("Input Files are Lists")
        optionsLayout.addWidget(self.listCheckBox)

        self.sortByTimeCheckBox = QCheckBox("Sort by Time")
        optionsLayout.addWidget(self.sortByTimeCheckBox)

        optionsGroupBox.setLayout(optionsLayout)
        return optionsGroupBox

    def createMiscGroupBox(self):
        miscGroupBox = QGroupBox("Miscellaneous Parameters")
        miscGroupBox.setCheckable(True)
        miscGroupBox.setChecked(False)
        miscLayout = QGridLayout()

        self.shiftsLabel = QLabel("Shifts:")
        self.shiftsLabel.setToolTip(
            "Use this many subframe shifts building fingerprint"
        )
        self.shiftsLabel.mousePressEvent = lambda event: self.shiftsSpinBox.setValue(0)
        self.shiftsSpinBox = QSpinBox()
        self.shiftsSpinBox.setRange(0, 10)
        self.shiftsSpinBox.setValue(0)
        miscLayout.addWidget(self.shiftsLabel, 0, 0)
        miscLayout.addWidget(self.shiftsSpinBox, 0, 1)

        self.matchWinLabel = QLabel("Match Window:")
        self.matchWinLabel.setToolTip(
            "Maximum tolerable frame skew to count as a match"
        )
        self.matchWinLabel.mousePressEvent = (
            lambda event: self.matchWinSpinBox.setValue(2)
        )
        self.matchWinSpinBox = QSpinBox()
        self.matchWinSpinBox.setRange(1, 10)
        self.matchWinSpinBox.setValue(2)
        miscLayout.addWidget(self.matchWinLabel, 1, 0)
        miscLayout.addWidget(self.matchWinSpinBox, 1, 1)

        self.minCountLabel = QLabel("Min Count:")
        self.minCountLabel.setToolTip(
            "Minimum number of matching landmarks to count as a match"
        )
        self.minCountLabel.mousePressEvent = (
            lambda event: self.minCountSpinBox.setValue(5)
        )
        self.minCountSpinBox = QSpinBox()
        self.minCountSpinBox.setRange(1, 100)
        self.minCountSpinBox.setValue(5)
        miscLayout.addWidget(self.minCountLabel, 2, 0)
        miscLayout.addWidget(self.minCountSpinBox, 2, 1)

        self.maxMatchesLabel = QLabel("Max Matches:")
        self.maxMatchesLabel.setToolTip(
            "Maximum number of matches to report for each query"
        )
        self.maxMatchesLabel.mousePressEvent = (
            lambda event: self.maxMatchesSpinBox.setValue(1)
        )
        self.maxMatchesSpinBox = QSpinBox()
        self.maxMatchesSpinBox.setRange(1, 100)
        self.maxMatchesSpinBox.setValue(1)
        miscLayout.addWidget(self.maxMatchesLabel, 3, 0)
        miscLayout.addWidget(self.maxMatchesSpinBox, 3, 1)

        self.freqSdLabel = QLabel("Frequency SD:")
        self.freqSdLabel.setToolTip("Frequency peak spreading SD in bins")
        self.freqSdLabel.mousePressEvent = lambda event: self.freqSdSpinBox.setValue(
            30.0
        )
        self.freqSdSpinBox = QDoubleSpinBox()
        self.freqSdSpinBox.setRange(0.0, 100.0)
        self.freqSdSpinBox.setValue(30.0)
        miscLayout.addWidget(self.freqSdLabel, 4, 0)
        miscLayout.addWidget(self.freqSdSpinBox, 4, 1)

        self.fanoutLabel = QLabel("Fanout:")
        self.fanoutLabel.setToolTip("Max number of hash pairs per peak")
        self.fanoutLabel.mousePressEvent = lambda event: self.fanoutSpinBox.setValue(3)
        self.fanoutSpinBox = QSpinBox()
        self.fanoutSpinBox.setRange(1, 10)
        self.fanoutSpinBox.setValue(3)
        miscLayout.addWidget(self.fanoutLabel, 5, 0)
        miscLayout.addWidget(self.fanoutSpinBox, 5, 1)

        self.pksPerFrameLabel = QLabel("Peaks Per Frame:")
        self.pksPerFrameLabel.setToolTip("Maximum number of peaks per frame")
        self.pksPerFrameLabel.mousePressEvent = (
            lambda event: self.pksPerFrameSpinBox.setValue(5)
        )
        self.pksPerFrameSpinBox = QSpinBox()
        self.pksPerFrameSpinBox.setRange(1, 10)
        self.pksPerFrameSpinBox.setValue(5)
        miscLayout.addWidget(self.pksPerFrameLabel, 6, 0)
        miscLayout.addWidget(self.pksPerFrameSpinBox, 6, 1)

        self.searchDepthLabel = QLabel("Search Depth:")
        self.searchDepthLabel.setToolTip(
            "How far down to search raw matching track list"
        )
        self.searchDepthLabel.mousePressEvent = (
            lambda event: self.searchDepthSpinBox.setValue(100)
        )
        self.searchDepthSpinBox = QSpinBox()
        self.searchDepthSpinBox.setRange(1, 1000)
        self.searchDepthSpinBox.setValue(100)
        miscLayout.addWidget(self.searchDepthLabel, 7, 0)
        miscLayout.addWidget(self.searchDepthSpinBox, 7, 1)

        self.ncoresLabel = QLabel("Number of Cores:")
        self.ncoresLabel.setToolTip("Number of processor cores to use")
        self.ncoresLabel.mousePressEvent = (
            lambda event: self.ncoresSpinBox.setValue(4)
        )
        self.ncoresSpinBox = QSpinBox()
        self.ncoresSpinBox.setRange(1, 16)
        self.ncoresSpinBox.setValue(4)
        miscLayout.addWidget(self.ncoresLabel, 8, 0)
        miscLayout.addWidget(self.ncoresSpinBox, 8, 1)

        miscGroupBox.setLayout(miscLayout)
        return miscGroupBox

    def createMaxPathDepthSlider(self):
        maxPathDepthSliderGroupBox = QGroupBox("Max Path Depth")
        layout = QVBoxLayout()

        self.maxPathDepthSlider.setMinimum(1)
        self.maxPathDepthSlider.setMaximum(self.calculateMaxDepth())
        self.maxPathDepthSlider.setValue(1)  # Default value
        self.maxPathDepthSlider.setTickPosition(QSlider.TicksBelow)
        self.maxPathDepthSlider.setTickInterval(1)

        # Label to display the current value of the slider
        self.maxPathDepthLabel = QLabel("Current depth: 1")
        self.maxPathDepthSlider.valueChanged.connect(
            lambda value: self.maxPathDepthLabel.setText(f"Current depth: {value}")
        )

        layout.addWidget(self.maxPathDepthSlider)
        layout.addWidget(self.maxPathDepthLabel)
        maxPathDepthSliderGroupBox.setLayout(layout)
        return maxPathDepthSliderGroupBox

    def calculateMaxDepth(self):
        max_depth = 0
        for i in range(self.fileListView.count()):
            file_path = self.fileListView.item(i).text()
            depth = file_path.count(os.sep)
            if depth > max_depth:
                max_depth = depth
        return max_depth

    def browseDirectory(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Directory", os.path.expanduser("~")
        )
        if dir_path:
            self.precompdirLineEdit.setText(dir_path)

    def runAudfprint(self):
        self.isExecuting = True
        try:
            cmd = self.cmdCombo.currentText()
            dbase = self.dbaseLineEdit.text().strip()
            if not dbase:
                self.cliOutputBox.error("Error: Database path is required.")
                return

            self.cliOutputBox.clearText()

            # Collect all the inputs
            cmd = self.cmdCombo.currentText()
            dbase = self.dbaseLineEdit.text()
            density = self.densitySpinBox.value()
            hashbits = self.hashbitsSpinBox.value()
            bucketsize = self.bucketsizeSpinBox.value()
            maxtime = self.maxtimeSpinBox.value()
            samplerate = self.samplerateSpinBox.value()
            precompdir = self.precompdirLineEdit.text()
            skip_existing = self.skipExistingCheckBox.isChecked()
            continue_on_error = self.continueOnErrorCheckBox.isChecked()
            list_files = self.listCheckBox.isChecked()
            sort_by_time = self.sortByTimeCheckBox.isChecked()
            ncores = self.ncoresSpinBox.value()

            # Collect all file paths from the fileListView
            file_paths = [self.fileListView.item(i).text() for i in range(self.fileListView.count())]

            # Construct the command line argument
            args = [
                "--cmd", cmd,
                "--dbase", dbase,
                "--density", str(density),
                "--bucketsize", str(bucketsize),
                "--maxtime", str(maxtime),
                "--samplerate", str(samplerate),
                "--precompdir", precompdir,
                "--skip-existing", str(skip_existing),
                "--continue-on-error", str(continue_on_error),
                "--list", str(list_files),
                "--sort-by-time", str(sort_by_time),
                "--ncores", str(ncores)  # Add number of cores to arguments
            ] + file_paths  # Add file paths to arguments

            # Call the audfprint main function or subprocess with these arguments
            print("Arguments to pass:", args)
            # You would call audfprint.main(args) if it's properly refactored to accept args

            # Example usage of CLIOutputBox
            self.cliOutputBox.debug("Running audfprint with the following arguments:")
            self.cliOutputBox.debug(str(args))

            old_stdout, old_stderr = self.patchStdout()
            try:
                audfprint.main(args)
            finally:
                self.restoreStdout(old_stdout, old_stderr)
        finally:
            self.isExecuting = False

    def patchStdout(self):
        class StdoutOutputInterceptor(io.StringIO):
            def write(self, s):
                super().write(s)
                self.cliOutputBox.info(s)

        class StderrOutputInterceptor(io.StringIO):
            def write(self, s):
                super().write(s)
                self.cliOutputBox.error(s)

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = StdoutOutputInterceptor()
        sys.stderr = StderrOutputInterceptor()
        return old_stdout, old_stderr

    def restoreStdout(self, old_stdout, old_stderr):
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    def closeEvent(self, event):
        if self.isExecuting:
            reply = QMessageBox.question(self, 'Confirm Close',
                                         "Are you sure you want to close the application while a command is being executed?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

def main():
    app = QApplication(sys.argv)
    ex = AudfprintGUI()

    # Handle SIGINT to close the application
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

