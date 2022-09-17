#!/usr/bin/env python3
"""
batterang
"""

from xml.etree import ElementTree

import os
import sys
import time
from traceback import print_exc

# linux only !
import pydbus

from PyQt5.QtCore import Qt, QTimer, QSize, QRectF, QRunnable, QThreadPool, QObject
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QColor, QPalette, QCursor, QIcon, QRegion, QPainterPath, QPainter, QPixmap
from PyQt5.QtWidgets import QWidget, QProgressBar, QFrame, QLabel, QListWidget, QListWidgetItem, QDesktopWidget, \
    QHBoxLayout, QVBoxLayout, QApplication, QSystemTrayIcon, QMenu, QMainWindow, QAction, qApp

from bluetooth_battery import BatteryStateQuerier


def dbus_list_bluez_adapters(bus):
    res = []
    db = bus.get('org.bluez')
    for child in ElementTree.fromstring(db.Introspect()):
        if child.tag == 'node':
            res.append('/org/bluez/%s' % child.attrib['name'])

    return res


def progressStyle(val):
    if val >= 80:
        col = "#1dbd00"
    elif 50 < val < 80:
        col = "#9abd00"
    elif 30 < val < 50:
        col = "#c1bd00"
    elif 10 < val < 30:
        col = "#e69000"
    else:
        col = "#e63e00"

    r = """
        QProgressBar {
           border-radius: 8px; 
           text-align: center;
        }
        QProgressBar::chunk:horizontal {

            background-color: %s 
            }""" % col

    return r


class ListItem(QWidget):
    icon_size = QSize(48, 48)

    def __init__(self, name, addr, bat, icon, online=True, error=None, parent=None):
        super(ListItem, self).__init__(parent)
        self.lay = QHBoxLayout()
        self.rlay = QVBoxLayout()
        self.nameLabel = QLabel(name)
        self.addrLabel = QLabel(addr)
        self.rlay.addWidget(self.nameLabel)
        self.rlay.addWidget(self.addrLabel)
        self.icon = QLabel()
        # self.icon.setFixedSize(self.icon_size)
        if icon:
            self.set_icon(icon)

        has_battery_info = bat is not None
        if online:
            try:
                self.lv = QProgressBar()
                v = int(bat) if bat else 0
                self.lv.setValue(v)
                self.lv.setStyleSheet(progressStyle(v))
            except Exception as e:
                # print_exc()
                has_battery_info = False

        self.lay.addWidget(self.icon, 0)
        self.lay.addLayout(self.rlay, 1)

        if online and has_battery_info:
            self.rlay.addWidget(self.lv)
        else:
            if not online:
                self.setDisabled(True)
            else:
                if error:
                    lab = QLabel('no status: %s' % error)
                    lab.textalignment = Qt.AlignLeft | Qt.TextWrapAnywhere
                    self.rlay.addWidget(lab)
                else:
                    self.rlay.addWidget(QLabel('no battery status available'))

        self.setLayout(self.lay)

        self.addrLabel.setStyleSheet('''
            color: gray;
        ''')

    def set_name(self, text):
        self.nameLabel.setText(text)

    def set_addr(self, addr):
        self.addrLabel.setText(addr)

    def set_icon(self, icon):
        self.icon.setPixmap(icon.pixmap(self.icon_size))


# better use QAbstractItemModel ? ... real MVC ?
def listitemgen(dv):
    ret = ListItem(
        dv.get('name'),
        dv.get('address'),
        dv.get('battery'),
        QIcon.fromTheme(dv.get('icon')),
        error=dv.get('error')
    )

    return ret


def tintedPixmap(file, palette=None, color=None):
    ret = QPixmap(file)
    if not color:
        color = QColor(palette.color(QPalette.Foreground)) if palette else (color if color else QColor(255, 255, 255))
    painter = QPainter(ret)
    painter.setCompositionMode(painter.CompositionMode_SourceIn)
    painter.fillRect(ret.rect(), color)
    painter.end()
    return ret


class FloatWin(QWidget):
    update_signal = pyqtSignal(str, int)

    def __init__(self, ui_widget=None, tray_widget=None, logr=None):
        QWidget.__init__(self)

        self.remove_toolbar = True
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)

        self.lay = QVBoxLayout(self)

        self.lab = QLabel()
        self.lab.setText("batterang")
        self.lab.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lay.addWidget(self.lab)

        self.list = QListWidget()
        pp = self.palette()
        pp.setColor(QPalette.Base, pp.color(QPalette.Window))
        self.list.setPalette(pp)
        self.list.setBackgroundRole(QPalette.NoRole)
        self.list.setFrameStyle(QFrame.NoFrame)
        self.list.setIconSize(QSize(48, 48))
        self.lay.addWidget(self.list)
        self.list.setFocusProxy(self)

        # self.setFixedSize (w, h)
        self.setFixedSize(400, 300)

        radius = 8.0
        path = QPainterPath()
        self.resize(440, 220)
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        mask = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(mask)

        self.setLayout(self.lay)

        self.hide()

    # TODO: really override ? CHECK !
    def update_list(self, lst):
        self.list.clear()
        for a, m in lst.items():
            item = QListWidgetItem()
            item.setBackground(self.palette().color(QPalette.Window))
            i = listitemgen(m)
            item.setSizeHint(i.sizeHint())

            self.list.addItem(item)
            self.list.setItemWidget(item, i)

    def loc(self, geom):
        cp = QCursor.pos()

        # ag = QDesktopWidget().availableGeometry()
        sg = QDesktopWidget().screenGeometry()

        w = self.geometry()
        x = cp.x() - int(w.width() / 2)
        y = cp.y() - w.height() - geom.height()

        self.move(x, y)

    def focusOutEvent(self, e):
        self.hide()


class UpdateSignal(QObject):
    finished = pyqtSignal()


class MainWindow(QMainWindow):
    devs = {}
    bat = {}

    bus = pydbus.SystemBus()

    def __init__(self):
        QMainWindow.__init__(self)


        desk = os.getenv("XDG_CURRENT_DESKTOP")


        try:
            # TODO: might be more than just one adapter !
            self.adapter = self.bus.get('org.bluez', '/org/bluez/hci0')
            self.mngr = self.bus.get('org.bluez', '/')
        except:
            self.mngr = None
            self.adapter = None
            self.bus = None

        self.pool = QThreadPool()

        self.tray_icon = QSystemTrayIcon()
        self.floatwin = FloatWin()

        self.onlyconnected = True

        self.tray_icon.activated.connect(self.showWid)

        quit_action = QAction("exit", self)
        quit_action.triggered.connect(qApp.quit)

        show_action = QAction("show", self)
        show_action.triggered.connect(self.showWid)

        tray_menu = QMenu()
        tray_menu.addAction(show_action)
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)

        self.app_icon = QIcon()

        THIS_DIR, THIS_FILENAME = os.path.split(__file__)
        # self.app_icon = self.tintedIcon(QIcon(os.path.join(THIS_DIR, "icon.png")))
        self.app_icon = QIcon(tintedPixmap(os.path.join(THIS_DIR, "icon.png"), self.palette(), self.palette().color(QPalette.HighlightedText)))

        self.tray_icon.setIcon(self.app_icon)
        self.tray_icon.show()

        self.list_devices()
        self.update_battery()

        if self.bus:
            self.bus.con.signal_subscribe(
                "org.bluez",
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                None,
                None,
                0,
                self.btpc
            )

        # self.hello

        timer = QTimer(self)
        timer.start(60000)
        timer.timeout.connect(self.list_devices)
        timer.timeout.connect(self.update_battery)

    # very general, in our case we'd just load thr png and tint ....
    def tintedIcon(self, icon):
        ret = icon.pixmap(QSize(48, 48))
        color = QColor(self.palette().color(QPalette.Foreground))
        painter = QPainter(ret)
        painter.setCompositionMode(painter.CompositionMode_SourceIn)
        painter.fillRect(ret.rect(), color)
        painter.end()
        return QIcon(ret)

    def btpc(self, con, sender, path, iface, signal, params):

        pun = params.unpack()
        if len(pun) < 3:
            return

        _, dct, _ = pun
        p = path.split('/')[-1].split('_')
        if len(p) > 1 and p[0] == 'dev' and 'Connected' in dct:
            addr = ':'.join(p[1:])
            if dct.get('Connected'):
                self.add_dev(addr)
            else:
                self.rm_dev(addr)
            # print(addr, dct)

    def showWid(self):
        if self.floatwin.isHidden():
            self.floatwin.loc(self.tray_icon.geometry())
            self.floatwin.show()
            self.floatwin.setFocusPolicy(Qt.StrongFocus)
            #self.floatwin.setFocus(True)
            self.floatwin.setFocus()
            self.floatwin.activateWindow()
        else:
            self.floatwin.hide()

    def hello(self):
        self.tray_icon.showMessage(
            'Hello',
            'There are currently %s bluetooth devices connected' % len(self.devs),
            QSystemTrayIcon.Information,
            5000
        )

    def add_dev(self, addr):
        mngd_objs = self.mngr.GetManagedObjects()
        for path in mngd_objs:
            meta = mngd_objs[path].get('org.bluez.Device1', {})
            con_state = meta.get('Connected', False)
            if not con_state:
                continue
            _addr = meta.get('Address')
            if _addr == addr:
                name = meta.get('Name')
                self.devs[addr] = {
                    'name': name,
                    'address': addr,
                    'online': con_state,
                    'class': meta.get('Class'),
                    'icon': meta.get('Icon', 'network-wireless')
                }

                self.update_battery(addr)

                break

        self.floatwin.update_list(self.devs)

    def rm_dev(self, addr):
        if addr in self.devs:
            self.devs.pop(addr)

        self.floatwin.update_list(self.devs)

    def list_devices(self):
        _dvs = {}
        if self.mngr:
            mngd_objs = self.mngr.GetManagedObjects()
            for path in mngd_objs:
                meta = mngd_objs[path].get('org.bluez.Device1', {})


                con_state = meta.get('Connected', False)
                if not con_state and self.onlyconnected:
                    continue
                addr = meta.get('Address')
                name = meta.get('Name')

                print(meta)
                print()

                if not addr:
                    continue
                _dvs[addr] = {'name': name, 'address': addr, 'online': con_state, 'class': meta.get('Class'),
                              'icon': meta.get('Icon', 'network-wireless')}

            self.devs = _dvs
            self.floatwin.update_list(self.devs)
        else:
            # TODO: inform about bus / bt dev absence
            pass

    def update_battery(self, addr=None):
        chks = CheckBat(self, addr)
        chks.signals.finished.connect(lambda *x: self.floatwin.update_list(self.devs))
        self.pool.start(chks)


class CheckBat(QRunnable):
    def __init__(self, inst, addr=None):
        super(CheckBat, self).__init__()
        self.inst = inst
        self.addr = addr
        self.signals = UpdateSignal()

    def run(self):

        print("start query battery for %s" % ('ALL' if not self.addr else self.addr))

        if not self.addr:
            for addr, meta in self.inst.devs.items():
                if addr not in self.inst.devs:
                    continue
                try:
                    self.inst.devs[addr]['battery'] = battery(addr) if meta.get('online', False) else None
                except Exception as e:
                    print_exc()
                    self.inst.devs[addr]['battery'] = None
                    self.inst.devs[addr]['error'] = str(e)
        elif self.addr and self.addr in self.inst.devs:
            try:
                self.inst.devs[self.addr]['battery'] = battery(self.addr)
            except Exception as e:
                print_exc()
                self.inst.devs[self.addr]['battery'] = None
                self.inst.devs[self.addr]['error'] = str(e)

        # self.inst.floatwin.update_list(self.inst.devs)
        self.signals.finished.emit()


def battery(addr):
    retry = 0

    print("resolve battery for ", addr)

    q = BatteryStateQuerier(addr)

    while retry < 5:
        try:
            return int(q)
        except Exception as e:
            # print_exc()
            time.sleep(2.5)
            retry += 1
            print("%s retry " % str(e), retry)
            continue


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName('batterang')
    mw = MainWindow()
    sys.exit(app.exec())
