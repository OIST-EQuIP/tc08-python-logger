from ast import Try
from datetime import datetime
from mailer import Mailer
from os import makedirs
from os.path import expanduser, join, realpath, dirname, exists
from picosdk.functions import assert_pico2000_ok
from picosdk.usbtc08 import usbtc08 as tc08
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QApplication
from pyqtgraph.exporters import ImageExporter

import csv
import ctypes
import numpy as np
import pyqtgraph as pg
import sys

# Qt5.12 high DPI scaling
QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)


class MainWindow(QtWidgets.QMainWindow):
    TC08_CH_ORDER = [4, 5, 3, 6, 2, 7, 1, 8]
    COLOR = [
        None,
        "#e41a1c",
        "#377eb8",
        "#4daf4a",
        "#984ea3",
        "#ff7f00",
        "#ffff33",
        "#a65628",
        "#f781bf",
    ]
    THERMOCOUPLE = {
        "B": 66,
        "E": 69,
        "J": 74,
        "K": 75,
        "N": 78,
        "R": 82,
        "S": 83,
        "T": 84,
        "X": 88,
    }
    CONTROLS = []
    DEV_HANDLE = 0
    BUFFER_LEN = 1
    FILE_PATH = dirname(realpath(__file__))

    save_dir = expanduser(join("~", "Documents", "tc08"))
    makedirs(save_dir, exist_ok=True)
    samp_int = 500

    selected_ch = []
    started = False
    ch_dialogs = [None] * 9
    temp_buffer = (ctypes.c_float * BUFFER_LEN * 9)()
    time_buffer = (ctypes.c_int32 * BUFFER_LEN)()
    overflow = ctypes.c_int16()
    curves = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setWindowTitle("TC-08 Python Logger")

        # menu bar
        menu = self.menuBar()
        act = menu.addAction("&Load")
        act.setStatusTip("Load data and configuration from saved file")
        act.triggered.connect(self.load)
        self.CONTROLS.append(act)
        act = menu.addAction("Save &Directory")
        act.setStatusTip(f"Select save directory, currently using {self.save_dir}")
        act.triggered.connect(self.select_save_dir)
        self.CONTROLS.append(act)
        act = menu.addAction("&Sampling Interval")
        act.setStatusTip(f"TC-08 sampling interval, currently at {self.samp_int} ms")
        act.triggered.connect(self.set_samp_int)
        self.CONTROLS.append(act)
        self.statusBar()

        # channels
        ch_layout = QtWidgets.QGridLayout()
        ch_label = QtWidgets.QLabel("Channels:")
        for i, ch in enumerate(self.TC08_CH_ORDER):
            btn = QtWidgets.QPushButton(f"{ch}")
            btn.setCheckable(True)
            btn.ch = ch
            btn.clicked.connect(self.select_ch)
            ch_layout.addWidget(btn, int(i / 2), i % 2)
            self.CONTROLS.append(btn)

        # thermocouple
        tc_layout = QtWidgets.QHBoxLayout()
        tc_label = QtWidgets.QLabel("Thermocouple type:")
        tc_layout.addWidget(tc_label)
        self.tc_cb = QtWidgets.QComboBox()
        self.tc_cb.addItems(self.THERMOCOUPLE.keys())
        self.tc_cb.setCurrentIndex(3)
        self.CONTROLS.append(self.tc_cb)
        tc_layout.addWidget(self.tc_cb)

        # Mailling list
        mail_label = QtWidgets.QLabel("Mailling list:")
        self.mail_text = QtWidgets.QTextEdit()
        self.mail_text.setAcceptRichText(False)
        self.mail_text.setPlaceholderText("Whitespace separated mail addresses")
        self.mail_text.setFixedHeight(100)
        self.CONTROLS.append(self.mail_text)

        # start
        self.start_btn = QtWidgets.QPushButton("Start")
        self.start_btn.clicked.connect(self.logging)

        # panel
        vspacing = 20
        panel_layout = QtWidgets.QVBoxLayout()
        panel_layout.addWidget(ch_label)
        panel_layout.addLayout(ch_layout)
        panel_layout.addSpacing(vspacing)
        panel_layout.addLayout(tc_layout)
        panel_layout.addSpacing(vspacing)
        panel_layout.addWidget(mail_label)
        panel_layout.addWidget(self.mail_text)
        panel_layout.addSpacing(vspacing)
        panel_layout.addStretch(1)
        panel_layout.addWidget(self.start_btn)

        # plot
        pg.setConfigOption("background", "w")
        pg.setConfigOption("foreground", "k")
        self.plot_widget = pg.PlotWidget(
            axisItems={"bottom": pg.DateAxisItem(utcOffset=0)}
        )
        self.plot_widget.setLabel("left", "Temperature (<font>&#8451;</font>)")
        self.plot_widget.setLabel("bottom", "Time")
        self.plot_widget.addLegend()

        # layout
        main_widget = QtWidgets.QWidget()
        main_layout = QtWidgets.QHBoxLayout()
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)
        main_layout.addLayout(panel_layout)
        main_layout.addWidget(self.plot_widget, 1)

        # initialize mail threadpool and makes it blocking
        self.pool = QtCore.QThreadPool()
        self.pool.setMaxThreadCount(1)

        # initialize mailer
        try:
            self.mailer = Mailer(
                f"Temperature Notification @ {now().split('-')[0]}", "", "TC-08"
            )
        except OSError:
            msg_box = QtWidgets.QMessageBox()
            msg_box.setWindowTitle("SMTP Configuration Issue")
            msg_box.setStandardButtons(QtWidgets.QMessageBox.Close)
            msg_box.setText(
                "Check the README for more information on configuring the SMTP server."
            )
            if msg_box.exec() == QtWidgets.QMessageBox.Close:
                self.close()
                sys.exit(0)

        # initialize plot update timer
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_plot)

        # finalize config initialization
        while self.DEV_HANDLE <= 0:
            self.DEV_HANDLE = tc08.usb_tc08_open_unit()
            if self.DEV_HANDLE <= 0:
                msg_box = QtWidgets.QMessageBox()
                msg_box.setWindowTitle("Device Issue")
                msg_box.setStandardButtons(
                    QtWidgets.QMessageBox.Retry | QtWidgets.QMessageBox.Close
                )
                if self.DEV_HANDLE == 0:
                    msg_box.setText("TC-08 not found. Please reconnect and try again.")
                elif self.DEV_HANDLE == -1:
                    msg_box.setText(f"TC-08 error: {tc08.usb_tc08_get_last_error(0)}")
                if msg_box.exec() == QtWidgets.QMessageBox.Close:
                    self.close()
                    sys.exit(0)
        tc08.usb_tc08_set_mains(self.DEV_HANDLE, 0)
        self.restore_last()

    def restore_last(self) -> None:
        last = join(self.FILE_PATH, "last_opened.txt")
        if exists(last):
            with open(last, "r") as f:
                self.load(f.readline())

    def closeEvent(self, event) -> None:
        if self.DEV_HANDLE:
            tc08.usb_tc08_stop(self.DEV_HANDLE)
            tc08.usb_tc08_close_unit(self.DEV_HANDLE)
        event.accept()

    def load(self, load_file=None) -> None:
        if not load_file:
            load_file, _ = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "Load data and config",
                self.save_dir,
                "Comma-Separated Values File (*.csv)",
            )
        elif not exists(load_file):
            print(f"File {load_file} does not exists")
            return

        if load_file:
            with open(load_file, "r") as f:
                line = f.readline()
                while line[0] == "#":
                    if "#Dir" in line:
                        prev_save_dir = line.split()[1]
                        if exists(prev_save_dir):
                            self.save_dir = prev_save_dir
                    elif "#Samp" in line:
                        self.samp_int = int(line.rsplit(maxsplit=1)[1])
                    elif "#Therm" in line:
                        self.tc_cb.setCurrentIndex(int(line.rsplit(maxsplit=1)[1]))
                    elif "#Ch" in line:
                        elem = line.split(maxsplit=3)
                        ch = int(elem[1])
                        dlg_config = elem[2]
                        print(elem)
                        if self.ch_dialogs[ch] == None:
                            self.ch_dialogs[ch] = ChannelDialog(ch)
                        self.ch_dialogs[ch].name_text.setText(elem[-1].strip())
                        if dlg_config[0] == "/":
                            # channel off /->
                            self.ch_dialogs[ch].temp_text.setText(dlg_config[3:])
                        else:
                            # channel on ->
                            self.CONTROLS[self.TC08_CH_ORDER.index(ch) + 3].toggle()
                            self.selected_ch.append(ch)
                            self.ch_dialogs[ch].temp_text.setText(dlg_config[2:])
                    elif "#Mail " in line:
                        self.mail_text.setPlainText(line[6:])
                    line = f.readline()
                reader = csv.reader(f)
                data = list(zip(*reader))
                self.time = list(np.array(data[0], dtype=float))
                self.temp = list(np.array(data[1:], dtype=float))
            self.init_plot()

    def select_save_dir(self) -> None:
        new_save_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Directory", self.save_dir
        )
        if new_save_dir:
            self.save_dir = new_save_dir
            self.sender().setStatusTip(
                f"Select save directory, currently using {self.save_dir}"
            )

    def set_samp_int(self) -> None:
        layout = QtWidgets.QVBoxLayout()
        label = QtWidgets.QLabel(
            "Sampling interval in ms (lower bounded by device's minimum interval):"
        )
        layout.addWidget(label)

        dlg = QtWidgets.QDialog()
        dlg.setLayout(layout)
        dlg.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        dlg.setWindowTitle("Sampling Interval")

        samp_int_text = QtWidgets.QLineEdit(f"{self.samp_int}")
        validator = QtGui.QDoubleValidator()
        validator.setRange(0, np.inf)
        samp_int_text.setValidator(validator)
        layout.addWidget(samp_int_text)

        btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        btn_box.accepted.connect(dlg.accept)
        layout.addWidget(btn_box)

        if dlg.exec():
            try:
                new_samp_int = float(samp_int_text.text())
                if new_samp_int:
                    self.samp_int = int(new_samp_int)
                else:
                    self.samp_int = tc08.usb_tc08_get_minimum_interval_ms(
                        self.DEV_HANDLE
                    )
                self.sender().setStatusTip(
                    f"TC-08 sampling interval, currently at {self.samp_int} ms"
                )
            except ValueError:
                pass

    def select_ch(self) -> None:
        btn = self.sender()
        if btn.isChecked():
            if self.ch_dialogs[btn.ch]:
                dlg = self.ch_dialogs[btn.ch]
            else:
                dlg = ChannelDialog(btn.ch)
                self.ch_dialogs[btn.ch] = dlg
            if dlg.exec():
                self.selected_ch.append(btn.ch)
            else:
                btn.toggle()
        else:
            del self.selected_ch[self.selected_ch.index(btn.ch)]

    def logging(self) -> None:
        self.enabled_controls(self.started)

        if not self.started:
            # start logging
            self.notify_at = [None] * 9
            for ch in self.selected_ch:
                # initialize notification
                try:
                    self.notify_at[ch] = float(self.ch_dialogs[ch].temp_text.text())
                except (AttributeError, ValueError) as e:
                    pass

                # initialize device
                assert_pico2000_ok(
                    tc08.usb_tc08_set_channel(
                        self.DEV_HANDLE,
                        ch,
                        self.THERMOCOUPLE[self.tc_cb.currentText()],
                    )
                )
            dev_min_int = tc08.usb_tc08_get_minimum_interval_ms(self.DEV_HANDLE)
            new_samp_int = max(self.samp_int, dev_min_int)
            assert_pico2000_ok(tc08.usb_tc08_run(self.DEV_HANDLE, new_samp_int))

            # initialize timer
            self.timer.setInterval(new_samp_int * self.BUFFER_LEN)
            print(f"sampling at {new_samp_int} ms")
            self.mail_addresses = self.mail_text.toPlainText().split()

            if any(self.notify_at):
                if self.mail_addresses:
                    self.mailer.mail_new(mailto=self.mail_addresses)
                    runnable = MailingThread(self.mailer, None, "Logging started")
                    self.pool.start(runnable)
                else:
                    msg_box = QtWidgets.QMessageBox()
                    msg_box.setWindowTitle("Inconsistency")
                    msg_box.setText(
                        "Please enter mailling list or remove temperature notifications"
                    )
                    msg_box.exec()
                    self.enabled_controls(~self.started)
                    return

            # clear plot widget before starting
            self.plot_widget.clear()
            self.selected_ch = sorted(self.selected_ch)
            self.time = []
            self.temp = []
            self.init_plot()
            self.start_btn.setText("Stop")
            self.timer.start()
            self.session_time = now()
        else:
            # stop logging
            self.output_csv()
            self.start_btn.setText("Start")
            self.timer.stop()
            runnable = MailingThread(self.mailer, self.plot_widget, "Logging ended")
            self.pool.start(runnable)

        self.started = ~self.started

    def init_plot(self) -> None:
        # requires selected_ch, time, temp and ch_dialogs to be set
        self.curves = []
        self.temp_buffer = (ctypes.c_float * self.BUFFER_LEN * 9)()
        self.time_buffer = (ctypes.c_int32 * self.BUFFER_LEN)()
        for i, ch in enumerate(self.selected_ch):
            self.temp.append([])
            pen = pg.mkPen(self.COLOR[ch], width=5)
            curve = self.plot_widget.plot(
                self.time,
                self.temp[i],
                name=self.ch_dialogs[ch].name_text.text(),
                pen=pen,
            )
            self.curves.append(curve)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)

    def output_csv(self) -> None:
        csv_file = join(self.save_dir, self.session_time + ".csv")
        with open(csv_file, "w", newline="") as f:
            ch_names = []
            f.write("# Configurations\n")
            f.write(f"#Dir {self.save_dir}\n")
            f.write(f"#Samp {self.samp_int}\n")
            f.write(f"#Therm {self.tc_cb.currentIndex()}\n")
            for ch, dlg in enumerate(self.ch_dialogs):
                if dlg:
                    if ch in self.selected_ch:
                        ch_names.append(dlg.name_text.text())
                        f.write(f"#Ch {ch} ->{dlg.temp_text.text()} {ch_names[-1]}\n")
                    else:
                        f.write(
                            f"#Ch {ch} /->{dlg.temp_text.text()} {dlg.name_text.text()}\n"
                        )
            f.write(f"#Mail {' '.join(self.mail_addresses)}\n")
            f.write("#\n")
            f.write("#" * 30 + "\n")
            writer = csv.writer(f)
            writer.writerow(["Elapsed time (s)"] + ch_names)
            for row in zip(self.time, *self.temp):
                writer.writerow(row)

        with open(join(self.FILE_PATH, "last_opened.txt"), "w") as f:
            f.write(csv_file)

    def enabled_controls(self, enabled) -> None:
        for control in self.CONTROLS:
            control.setEnabled(enabled)

    def update_plot(self) -> None:
        self.time = self.time + [t / 1000 for t in self.time_buffer[: self.BUFFER_LEN]]
        is_minute = round(self.time[-1]) % 60 == 0
        for i, ch in enumerate(self.selected_ch):
            tc08.usb_tc08_get_temp(
                self.DEV_HANDLE,  # handle
                ctypes.byref(self.temp_buffer[ch]),  # temp_buffer
                ctypes.byref(self.time_buffer),  # times_ms_buffer
                self.BUFFER_LEN,  # buffer_length
                ctypes.byref(self.overflow),  # overflow
                ch,  # channel
                tc08.USBTC08_UNITS["USBTC08_UNITS_CENTIGRADE"],  # units
                1,  # fill_missing
            )
            cur_temp = self.temp_buffer[ch][: self.BUFFER_LEN]
            if self.notify_at[ch] and max(cur_temp) >= self.notify_at[ch]:
                self.notify(ch, self.notify_at[ch])
                self.notify_at[ch] = None
            self.temp[i] = self.temp[i] + cur_temp
            self.curves[i].setData(self.time, self.temp[i])

            if is_minute:
                self.check_rapid_change(ch, self.temp[i])

    def check_rapid_change(self, channel, temperature) -> None:
        # warn when more than 1 deg celsius per minute
        prev_idx = 60 * 1000 // self.timer.interval()
        if len(temperature) < prev_idx:
            return

        diff = temperature[-1] - temperature[-prev_idx]
        if abs(diff) > 1:
            ch_name = self.ch_dialogs[channel].name_text.text()
            runnable = MailingThread(
                self.mailer,
                self.plot_widget,
                f"{ch_name} temperature change by {diff}\u2103 in a minute",
            )
            self.pool.start(runnable)

    def notify(self, channel, temperature) -> None:
        ch_name = self.ch_dialogs[channel].name_text.text()
        runnable = MailingThread(
            self.mailer,
            self.plot_widget,
            f"{ch_name} reached {temperature}\u2103",
        )
        self.pool.start(runnable)

    def fake_temperature_data(self, time) -> float:
        return np.random.normal() * 3 + 150 - np.exp((290 - time) / 60)


class ChannelDialog(QtWidgets.QDialog):
    def __init__(self, channel, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)

        self.setWindowTitle(f"Channel {channel} Settings")

        btns = QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        self.btn_box = QtWidgets.QDialogButtonBox(btns)
        self.btn_box.accepted.connect(self.accept)
        self.btn_box.rejected.connect(self.reject)

        name_layout = QtWidgets.QHBoxLayout()
        name_label = QtWidgets.QLabel("Name:")
        self.name_text = QtWidgets.QLineEdit(f"Ch. {channel}")
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_text)

        temp_layout = QtWidgets.QHBoxLayout()
        temp_label = QtWidgets.QLabel("Notify temperature (\u2103):")
        self.temp_text = QtWidgets.QLineEdit()
        self.temp_text.setPlaceholderText("inf")
        temp_layout.addWidget(temp_label)
        temp_layout.addWidget(self.temp_text)
        validator = QtGui.QDoubleValidator()
        validator.setRange(0, np.inf)
        self.temp_text.setValidator(validator)

        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)
        layout.addLayout(name_layout)
        layout.addLayout(temp_layout)
        layout.addSpacing(50)
        layout.addStretch(1)
        layout.addWidget(self.btn_box)


class MailingThread(QtCore.QRunnable):
    def __init__(self, mailer, plot, msg, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mailer = mailer
        self.plot = plot
        self.msg = msg

    @QtCore.pyqtSlot()
    def run(self) -> None:
        self.mailer.mail_new()
        if self.plot:
            self.mail_compile()
        self.mailer.mail_body(self.msg)
        self.mailer.mail_send()

    def mail_compile(self) -> None:
        exporter = ImageExporter(self.plot.plotItem)
        f = QtCore.QBuffer()
        f.open(QtCore.QBuffer.ReadWrite)
        img = exporter.export(toBytes=True)
        img.save(f, "PNG")
        f.seek(0)
        self.mailer.mail_attach(f.readAll(), f"{now()}.png")
        f.close()


def now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
