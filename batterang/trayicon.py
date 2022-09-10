import os
import sys
from traceback import print_exc

import pydbus

from PyQt5.QtWidgets import QWidget, QProgressBar, QFrame, QLabel, QListWidget, QListWidgetItem, QDesktopWidget, QHBoxLayout, QVBoxLayout, QApplication, QSystemTrayIcon, QMenu, QMainWindow, QAction, qApp
from PyQt5.QtGui import QColor, QPalette, QCursor, QIcon, QWindow, QRegion, QPainterPath, QPainter, QPixmap
from PyQt5.QtCore import Qt,QTimer,QSize, QRectF
from PyQt5.QtCore import pyqtSlot, pyqtSignal

from bluetooth_battery import BatteryStateQuerier


def progressStyle(val):
    if val >= 80:
        col = "#1dbd00"
    elif 60 < val < 80:
        col = "#9abd00"
    elif 40 < val < 60:
        col = "#c1bd00"
    elif 20 < val < 40:
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


class ListItem (QWidget):
    icon_size = QSize(48, 48)

    def __init__(self, name, addr, bat, icon, online=True, parent=None):
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
        
        has_battery_info = True
        if online:
            try:
                self.lv = QProgressBar()
                v = int(bat) if bat else 0
                self.lv.setValue(v)
                self.lv.setStyleSheet(progressStyle(v))
            except Exception as e:
                print_exc()
                has_battery_info = False

        self.lay.addWidget(self.icon, 0)
        self.lay.addLayout(self.rlay, 1)
        
        if online and has_battery_info:
            self.rlay.addWidget(self.lv)
        else:
            if not online:
                self.setDisabled(True)
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
    ret = ListItem(dv.get('name'),dv.get('address'),dv.get('battery'), QIcon.fromTheme(dv.get('icon')))

    return ret


def tintedPixmap(file, palette=None, color=None):
    ret = QPixmap(file)
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
        self.setWindowFlags(Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint)

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
        x = cp.x()-int(w.width()/2)
        y = cp.y()-w.height()-geom.height()

        self.move(x, y)

    def focusOutEvent(self, e):
        self.hide()


class MainWindow(QMainWindow):
    devs = {}
    bat = {}

    bus = pydbus.SystemBus()

    # TODO: might be more than just one adapter !
    adapter = bus.get('org.bluez', '/org/bluez/hci0')
    mngr = bus.get('org.bluez', '/')

    def __init__(self):
        QMainWindow.__init__(self)
        self.tray_icon = QSystemTrayIcon()
        self.floatwin = FloatWin()

        self.onlyconnected = True
        
        self.tray_icon.activated.connect(self.showWid)

        quit_action = QAction("exit", self)
        quit_action.triggered.connect(qApp.quit)

        tray_menu = QMenu()
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        
        self.app_icon = QIcon()
        
        THIS_DIR, THIS_FILENAME = os.path.split(__file__)
        #self.app_icon = self.tintedIcon(QIcon(os.path.join(THIS_DIR, "icon.png")))
        self.app_icon = QIcon(tintedPixmap(os.path.join(THIS_DIR, "icon.png"), self.palette()))

        self.tray_icon.setIcon(self.app_icon)
        self.tray_icon.show()

        self.list_devices(self.onlyconnected)
        self.update()

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
        timer.timeout.connect(self.list_devices, self.onlyconnected)
        timer.timeout.connect(self.update)

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
            self.floatwin.setFocus(True)
            self.floatwin.activateWindow()
        else:
            self.floatwin.hide()
    
    def hello(self):
        self.tray_icon.showMessage('Hello', 'There are currently %s bluetooth devices connected' % len(self.devs), QSystemTrayIcon.Information, 5000)

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
                break

        self.floatwin.update_list(self.devs)

    def rm_dev(self, addr):
        if addr in self.devs:
            self.devs.pop(addr)

        self.floatwin.update_list(self.devs)

    def list_devices(self, connected=True):
        _dvs = {}
        
        mngd_objs = self.mngr.GetManagedObjects()
        for path in mngd_objs:
            meta = mngd_objs[path].get('org.bluez.Device1', {})
            con_state = meta.get('Connected', False)
            if not con_state and connected:
                continue
            addr = meta.get('Address')
            name = meta.get('Name')
            if not addr:
                continue
            _dvs[addr] = {'name':name, 'address': addr, 'online': con_state, 'class':meta.get('Class'), 'icon': meta.get('Icon', 'network-wireless')}
                
        self.devs = _dvs

    def update(self):
        for addr, meta in self.devs.items():
            self.devs[addr]['battery'] = BatteryStateQuerier(addr) if meta.get('online',False) else None

        self.floatwin.update_list(self.devs)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName('batterang')
    mw = MainWindow()
    sys.exit(app.exec())

