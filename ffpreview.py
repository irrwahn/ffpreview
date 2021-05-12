#!/usr/bin/env python3

"""
ffpreview.py

Copyright 2021 Urban Wallasch <irrwahn35@freenet.de>

Ffpreview is distributed under the Modified ("3-clause") BSD License.
See `LICENSE` file for more information.
"""

_FFPREVIEW_VERSION = '0.2+'

import sys

_PYTHON_VERSION = float("%d.%d" % (sys.version_info.major, sys.version_info.minor))
if _PYTHON_VERSION < 3.6:
    raise Exception ('Need Python version 3.6 or later, got version ' + str(sys.version))

import io
import os
from os.path import expanduser
import signal
import time
import re
import tempfile
import argparse
import json
from configparser import RawConfigParser as ConfigParser
from subprocess import PIPE, Popen, DEVNULL
import base64
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
from PyQt5.QtGui import *
from inspect import currentframe

############################################################
# utility functions

def eprint(lvl=0, *args, **kwargs):
    if lvl <= cfg['verbosity']:
        print('LINE %d: ' % currentframe().f_back.f_lineno, file=sys.stderr, end = '')
        print(*args, file=sys.stderr, **kwargs)

def hms2s(ts):
    h = 0
    m = 0
    s = 0.0
    t = ts.split(':')
    for i in range(len(t)):
        h = m; m = s; s = float(t[i])
    return float(h * 3600) + m * 60 + s

def s2hms(ts):
    s, ms = divmod(float(ts), 1.0)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    res = '%d:%02d:%02d%s' % (h, m, s, ('%.3f' % ms).lstrip('0'))
    return res

def str2bool(s):
    if s:
        return s.lower() in ['true', '1', 'on', 'y', 'yes']
    return False

def str2int(s):
    if s:
        return int(s)
    return 0

def str2float(s):
    if s:
        return float(s)
    return 0.0

def die(rc):
    global proc
    if proc is not None:
        eprint(1, 'killing subprocess: %s' % proc.args)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    exit(rc)

def sig_handler(signum, frame):
    eprint(0, 'ffpreview caught signal %d, exiting.' % signum)
    die(signum)

############################################################
# configuration

def configure():
    # set defaults
    cfg = {
        'home': expanduser("~"),
        'conffile': 'ffpreview.conf',
        'vid': '',
        'tmpdir': '',
        'thdir': '',
        'idxfile': '',
        'grid': '5x5',
        'grid_columns': 5,
        'grid_rows': 5,
        'thumb_width': '128',
        'highlightcolor': 'lightblue',
        'ffprobe': 'ffprobe',
        'ffmpeg': 'ffmpeg',
        'player': 'mpv --no-ordered-chapters --start=%t %f',
        'plpaused': 'mpv --no-ordered-chapters --start=%t --pause %f',
        'force': 'False',
        'reuse': 'False',
        'method': 'iframe',
        'frame_skip': '-1',
        'time_skip': '-1',
        'scene_thresh': '-1',
        'customvf': '',
        'start': '0',
        'end': '0',
        'verbosity': 0,
    }

    # parse command line arguments
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description='Generate interactive video thumbnail preview.',
        epilog='  The -C, -i, -N, -n and -s options are mutually exclusive. If more\n'
               '  than one is supplied: -C beats -i beats -N beats -n beats -s.\n\n'
               '  The -r option causes ffpreview to ignore any of the -w, -C, -i\n'
               '  -N, -n and -s options, provided that filename, duration, start\n'
               '  and end times match, and the index file appears to be healthy.\n'
               '\nwindow controls:\n'
               '  ESC, Ctrl+Q     quit application\n'
               '  Ctrl+G          adjust window geometry to optimal fit\n'
               '  Ctrl+O          show open file dialog\n'
               '  Double-click    open video at clicked position in paused state\n'
               '  Shift-click     play video starting at clicked position\n'
               '  Mouse-2         open the context menu\n'
               '  Up, Down,\n'
               '  PgUp, PgDown,\n'
               '  Home, End       move highlighted selection marker\n'
               '  Enter           open video at selected position in paused state\n'
               '  Shift+Enter     play video starting at selected position\n'
               '  Alt+Enter       open the context menu\n'
    )
    parser.add_argument('filename', nargs='?', default=os.getcwd(), help='input video file')
    parser.add_argument('-c', '--config', metavar='F', help='read configuration from file F')
    parser.add_argument('-g', '--grid', metavar='G', help='set grid geometry in COLS[xROWS] format')
    parser.add_argument('-w', '--width', type=int, metavar='N', help='thumbnail image width in pixel')
    parser.add_argument('-t', '--tmpdir', metavar='P', help='set thumbnail parent directory to P')
    parser.add_argument('-f', '--force', action='count', help='force thumbnail and index rebuild')
    parser.add_argument('-r', '--reuse', action='count', help='reuse filter settings from index file')
    parser.add_argument('-i', '--iframe', action='count', help='select only I-frames (default)')
    parser.add_argument('-n', '--nskip', type=int, metavar='N', help='select only every Nth frame')
    parser.add_argument('-N', '--nsecs', type=float, metavar='F', help='select one frame every F seconds')
    parser.add_argument('-s', '--scene', type=float, metavar='F', help='select by scene change threshold; 0 < F < 1')
    parser.add_argument('-C', '--customvf', metavar='S', help='select frames using custom filter string S')
    parser.add_argument('-S', '--start', metavar='T', help='start video analysis at time T')
    parser.add_argument('-E', '--end', metavar='T', help='end video analysis at time T')
    parser.add_argument('-v', '--verbose', action='count', help='be more verbose; repeat to increase')
    parser.add_argument('--version', action='count', help='print version info and exit')
    args = parser.parse_args()

    if args.version:
        print('ffpreview version %s running on python %.1f.x' % (_FFPREVIEW_VERSION, _PYTHON_VERSION))
        exit(0)

    # parse config file
    defconfpath = os.path.join( # try to determine user config file
        os.environ.get('APPDATA') or
        os.environ.get('XDG_CONFIG_HOME') or
        os.path.join(os.environ['HOME'], '.config'),
        cfg['conffile']
    )
    if args.config:
        cfg['conffile'] = args.config
    cfgfiles = [defconfpath, cfg['conffile']]
    fconf = ConfigParser(allow_no_value=True, defaults=cfg)
    cf = fconf.read(cfgfiles)
    try:
        options = fconf.options('Default')
        for option in options:
            try:
                cfg[option] = fconf.get('Default', option)
            except Exception as e:
                eprint(0, str(e))
    except Exception as e:
        eprint(0, str(e))

    # fix up types of non-string options
    cfg['force'] = str2bool(cfg['force'])
    cfg['reuse'] = str2bool(cfg['reuse'])
    cfg['thumb_width'] = str2int(cfg['thumb_width'])
    cfg['frame_skip'] = str2int(cfg['frame_skip'])
    cfg['time_skip'] = str2float(cfg['time_skip'])
    cfg['scene_thresh'] = str2float(cfg['scene_thresh'])
    cfg['start'] = str2float(cfg['start'])
    cfg['end'] = str2float(cfg['end'])

    # evaluate remaining command line args
    cfg['vid'] = args.filename
    if args.tmpdir:
        cfg['tmpdir'] = args.tmpdir
    if args.start:
        cfg['start'] = hms2s(args.start)
    if args.end:
        cfg['end'] = hms2s(args.end)
    if args.grid:
        cfg['grid'] = args.grid
    if args.width:
        cfg['thumb_width'] = args.width
    if args.force:
        cfg['force'] = True
    if args.reuse:
        cfg['reuse'] = True
    if args.scene:
        cfg['method'] = 'scene'
        cfg['scene_thresh'] = args.scene
    if args.nskip:
        cfg['method'] = 'skip'
        cfg['frame_skip'] = args.nskip
    if args.nsecs:
        cfg['method'] = 'time'
        cfg['time_skip'] = args.nsecs
    if args.iframe:
        cfg['method'] = 'iframe'
    if args.customvf:
        cfg['method'] = 'customvf'
        cfg['customvf'] = args.customvf
    if args.verbose:
        cfg['verbosity'] = args.verbose

    # clear unused method parameters
    if cfg['method'] == 'scene':
        cfg['time_skip'] = None
        cfg['frame_skip'] = None
        cfg['customvf'] = None
    elif cfg['method'] == 'skip':
        cfg['scene_thresh'] = None
        cfg['time_skip'] = None
        cfg['customvf'] = None
    elif cfg['method'] == 'time':
        cfg['scene_thresh'] = None
        cfg['frame_skip'] = None
        cfg['customvf'] = None
    elif cfg['method'] == 'customvf':
        cfg['scene_thresh'] = None
        cfg['time_skip'] = None
        cfg['frame_skip'] = None
    elif cfg['method'] == 'iframe':
        cfg['scene_thresh'] = None
        cfg['time_skip'] = None
        cfg['frame_skip'] = None
        cfg['customvf'] = None

    # parse grid geometry
    grid = re.split('[xX,;:]', cfg['grid'])
    cfg['grid_columns'] = int(grid[0])
    if len(grid) > 1:
        cfg['grid_rows'] = int(grid[1])

    # prepare temp directory
    if not cfg['tmpdir']:
        cfg['tmpdir'] = tempfile.gettempdir()
    try:
        os.makedirs(cfg['tmpdir'], exist_ok=True)
    except Exception as e:
        eprint(0, str(e))
        exit(1)

    return cfg
    # end of configure()


############################################################
# Qt classes

_ffpreview_png = '''
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
Ut0AAAAASUVORK5CYII='''

_broken_img_png = '''
iVBORw0KGgoAAAANSUhEUgAAAIAAAABJCAYAAAD12S63AAAEi0lEQVR42u1dTUhVQRT+1LIfSehHyoKCwhSsFombKIiwIEm0IiiICApzIRYlRbaxXGTtWrSQ
apG0chMkZGFSC6NFQiFJEbTyr4gkRDT02WvhEx9yu2/mzs89c+/5YBY+Z+aen2/OPWfuffMABoPBYDAYDEbskMUm0I4VABrS/r7DJokWcgGcANABICnRWtl0
7mA/gOeSDhZtfAsgjGTc7J4dQSfWAugmvvqSvNaCowpAu6HwqxLa2wCUScy5i125gNWpJOmbofuuKgE6AFQE1K3CZ94LcXJwLYBeww5WJcARQ/p/8ZEpL0ql
UV/IDqacgTtTGXihgbhj+wFcAVBg2S5bU7euSQ+Z6qNAgoOEnPwOQGVIdrgWMD/JWTRPoU/fn1TrYlttWjHZ0oEBC7edxz59b1ByfpdGIzwFUBOSHvNhOizC
T3hcp9mn/xIqBLgqoeQsgCcAikOsMjKF6TAjXoOr+UAmxfaEsMvXB3MVgMlb3OJ8YIkLJAijtCrz2co1XQLaLkPrfPqOUyDAKs0EMOVcHQQotCDDpMd1D/j0
P+9CFPhNoILQQYBKS3JckrTVyrAJcFLBuKrGagFQAqAcwF3DBGjKMH4s1S8vVdGMaMwHyCeFQQ38OoBxtgjKNKuZAD0Bx2cD+KuJkGRJsM9SFNgoKdcxjQQY
8xl7TVCeGQl5pjzGb/DpPxqHKBAEjQLzDinqJ4spQX0vS8rRGCYBEoajQKtBcl5XmGPU8MK5LTnmJeUo0Kp4GzAlV4nCHLrQH4V8IGk4CqwxJFfQOYYN2PCt
YD6Q4yoJvinmAl8k5flgkACmMZF2rRmP/7dRJIHpKCCj3AzMbQOfs2jTX6lren2LaNokCYK8Fi4Spnv+8/mbAEQ7nvbZTgC3YOfR6SOLBFiLue8KjHn8LzfD
2GVRjgJhPQj6Cnrw06/ItjCDAkbf7DABKOIUtXzAlSjQLaBLflr/AdDFICUSiGTf9Rp2B1VbtYAuNcRXfzre++ia4Cgg9uRtMW5KkIV6PtBlUxCRJ3KHQ44C
InjmyOqfx/YMOpdzFJAjwJBjBAAyn1/ABJCsAFzEfQokeIFgz75tkUAEvXAXfrr/jVMU+ATgDAi+TmUYKzLYxQq5H4ZMgrgjK4N9tlGJAtkGKoJi9j8A4GjY
i+R7CFFgKftdyoaRygUYC2imYjcRAR5oIEGcsRFq30kwihbDUeAPr27ldohCFPgRYPwrdrSWlmtakTrFKOBVEdRxGHfroCwRQT4Kji/i1a2lWT16p0pTLsCO
DvYCTAEFY4gIu4PDuHJrdnVTwrVVTmF1JwBcdMVg1YJKrePV/d82AKA0yluTFKIABUdPAzgbxdq2TNAApx0gqe6zA0sRE1CPAjYc3gtgPWKKTRYIcE9hPp2O
HsfcSymMAEYWfad9L8TO9LVBgE6iSWykbgOdhrc8ZR3OcCTZUpFrBHPnDzMcJcCwpFztAJbH0Sk2jybPh73zb2VCNf92YgSjwG42dTxJMAICZ+sy/PFZo8Pb
Ec1fP40FEpDbSStlk0UTXj/D3sRmYTAYDAbDKP4Bb2zlnKfZbGYAAAAASUVORK5CYII='''

class sQPixmap(QPixmap):
    def __init__(self, *args, imgdata=None, **kwargs):
        super().__init__(*args, **kwargs)
        if imgdata is not None:
            super().loadFromData(base64.b64decode(imgdata))

class sQIcon(QIcon):
    def __init__(self, *args, imgdata=None, **kwargs):
        super().__init__(*args, **kwargs)
        if imgdata is not None:
            super().addPixmap(sQPixmap(imgdata=imgdata))

class tLabel(QWidget):
    __slots__ = ['info', 'focus']
    def __init__(self, *args, pixmap=None, text=None, info=None, **kwargs):
        super().__init__(*args, **kwargs)
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0,0,0,0)
        if pixmap is not None:
            pl = QLabel()
            pl.setPixmap(pixmap)
            pl.setAlignment(Qt.AlignCenter)
            pl.setStyleSheet('QLabel {padding: 2px;}')
            layout.addWidget(pl)
        if text is not None:
            tl = QLabel()
            tl.setText(text)
            tl.setAlignment(Qt.AlignCenter)
            layout.addWidget(tl)
        self.info = info
        self.focus = False
        self.adjustSize()

    def setFocus(self):
        self.focus = True
        self.setStyleSheet('QLabel {background-color: %s;}' % cfg['highlightcolor'])
        self.window().statdsp[3].setText(self.info[1])

    def unsetFocus(self):
        self.focus = False
        self.setStyleSheet('QLabel {}')
        self.window().statdsp[3].setText('')

    def mouseReleaseEvent(self, event):
        self.window().set_cursorw(self)
        if QApplication.keyboardModifiers() == Qt.ShiftModifier:
            play_video(cfg['vid'], self.info[2])

    def mouseDoubleClickEvent(self, event):
        play_video(cfg['vid'], self.info[2], True)

    def contextMenuEvent(self, event):
        self.window().set_cursorw(self)
        self.contextMenu_show(event.pos())

    def contextMenu_show(self, pos):
        menu = QMenu(self)
        menu.addAction('Play From Here',
                    lambda: play_video(cfg['vid'], self.info[2]))
        menu.addAction('Play From Start',
                    lambda: play_video(cfg['vid']))
        menu.addSeparator()
        menu.addAction('Copy Timestamp [H:M:S.ms]',
                    lambda: self.window().clipboard.setText(s2hms(self.info[2])))
        menu.addAction('Copy Timestamp [S.ms]',
                    lambda: self.window().clipboard.setText(self.info[2]))
        menu.addSeparator()
        menu.addAction('Copy Original Filename',
                    lambda: self.window().clipboard.setText(os.getcwd()+'/'+cfg['vid']))
        menu.addAction('Copy Thumb Filename',
                    lambda: self.window().clipboard.setText(cfg['thdir'] + '/' + self.info[1]))
        menu.addAction('Copy Thumbnail Image',
                    lambda: self.window().clipboard.setPixmap(self.layout().itemAt(0).widget().pixmap()))
        menu.addSeparator()
        menu.addAction('Optimize Window Extent', lambda: self.window().optimize_extent())
        menu.addSeparator()
        menu.addAction('Open Video File...', lambda: self.window().load_view(os.getcwd()))
        menu.addSeparator()
        menu.addAction('Quit', lambda: die(0))
        menu.exec_(self.mapToGlobal(pos))


class tScrollArea(QScrollArea):
    def __init__(self, *args, imgdata=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.delayTimeout = 200
        self._resizeTimer = QTimer(self)
        self._resizeTimer.timeout.connect(self._delayedUpdate)

    def resizeEvent(self, event):
        self._resizeTimer.start(self.delayTimeout)
        super().resizeEvent(event)

    def _delayedUpdate(self):
        self._resizeTimer.stop()
        tlwidth = self.window().tlwidth
        tlheight = self.window().tlheight
        if tlwidth < 1 or tlheight < 1:
            return
        rows = int(self.viewport().height() / tlheight + 0.5)
        self.verticalScrollBar().setPageStep(rows * tlheight)
        self.verticalScrollBar().setSingleStep(tlheight)
        cfg['grid_rows'] = rows
        cols = int((self.viewport().width()) / tlwidth)
        if cols < 1:
            cols = 1
        if cols != cfg['grid_columns']:
            cfg['grid_columns'] = cols
            self.window().fill_grid()


class sMainWindow(QMainWindow):
    """ Application main window class singleton. """
    _instance = None
    px = 50
    py = 50
    tlwidth = 0
    tlheight = 0
    tlabels = []
    cur = 0

    def __new__(cls, *args, title='', **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self, *args, title='', **kwargs):
        super().__init__(*args, **kwargs)
        self.init_window(title)

    def closeEvent(self, event):
        self.close()
        die(0)

    def calculate_props(self):
        self.px = self.width() - self.scroll.viewport().width()
        self.py = self.height() - self.scroll.viewport().height()

    def optimize_extent(self):
        w = self.tlwidth * cfg['grid_columns'] + self.px
        h = self.tlheight * cfg['grid_rows'] + self.py
        self.resize(w, h)

    def fill_grid(self):
        self.scrollframe.setUpdatesEnabled(False)
        x = 0; y = 0
        for tl in self.tlabels:
            self.thumb_layout.removeWidget(tl)
            self.thumb_layout.addWidget(tl, y, x)
            x += 1
            if x >= cfg['grid_columns']:
                x = 0; y += 1
        self.scrollframe.setUpdatesEnabled(True)
        self.set_cursor()

    def clear_grid(self):
        self.cur = 0
        for tl in self.tlabels:
            self.thumb_layout.removeWidget(tl)
            tl.setParent(None)
            tl.close()
        self.tlabels = []

    def set_cursor(self, idx=None):
        l = self.tlabels
        if len(l) < 1:
            return
        if idx is None:
            idx = self.cur
        l[self.cur].unsetFocus()
        if idx < 0:
            idx = 0
        elif idx >= len(l):
            idx = len(l) - 1
        self.cur = idx
        l[self.cur].setFocus()
        self.scroll.ensureWidgetVisible(l[self.cur], 0, 0)

    def set_cursorw(self, label):
        idx = self.tlabels.index(label)
        self.set_cursor(idx)

    def advance_cursor(self, amnt):
        self.set_cursor(self.cur + amnt)

    def init_window(self, title):
        self.setWindowTitle(title)
        self.broken_img = sQPixmap(imgdata=_broken_img_png)
        self.ffpreview_ico = sQIcon(imgdata=_ffpreview_png)
        self.setWindowIcon(self.ffpreview_ico)
        self.clipboard = QApplication.clipboard()
        self.resize(500, 300)

        self.statbar = QHBoxLayout()
        self.statdsp = []
        for i in range(4):
            s = QLabel('')
            s.resize(100, 20)
            self.statdsp.append(s)
            self.statbar.addWidget(s)
        self.progbar = QProgressBar()
        self.progbar.resize(100, 20)
        self.progbar.hide()
        self.statbar.addWidget(self.progbar)

        self.scrollframe = QFrame()
        self.scroll = tScrollArea()
        self.scroll.setWidget(self.scrollframe)
        self.scroll.setWidgetResizable(True)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet('QFrame {border: none;}')
        self.thumb_layout = QGridLayout(self.scrollframe)
        self.thumb_layout.setContentsMargins(0, 0, 0, 0)
        self.thumb_layout.setSpacing(0)

        self.main_frame = QWidget()
        self.main_layout = QVBoxLayout(self.main_frame)
        self.main_layout.setContentsMargins(0, 2, 0, 0)
        self.main_layout.addWidget(self.scroll)
        self.main_layout.addLayout(self.statbar)
        self.setCentralWidget(self.main_frame)

        QShortcut('Esc', self).activated.connect(lambda: die(0))
        QShortcut('Ctrl+Q', self).activated.connect(lambda: die(0))
        QShortcut('Ctrl+W', self).activated.connect(lambda: die(0))
        QShortcut('Ctrl+G', self).activated.connect(self.optimize_extent)
        QShortcut('Ctrl+O', self).activated.connect(lambda: self.load_view(os.getcwd()))
        QShortcut('Tab', self).activated.connect(lambda: self.advance_cursor(1))
        QShortcut('Shift+Tab', self).activated.connect(lambda: self.advance_cursor(-1))
        QShortcut('Right', self).activated.connect(lambda: self.advance_cursor(1))
        QShortcut('Left', self).activated.connect(lambda: self.advance_cursor(-1))
        QShortcut('Up', self).activated.connect(lambda: self.advance_cursor(-cfg['grid_columns']))
        QShortcut('Down', self).activated.connect(lambda: self.advance_cursor(cfg['grid_columns']))
        QShortcut('PgUp', self).activated.connect(lambda: self.advance_cursor(-cfg['grid_rows'] * cfg['grid_columns']))
        QShortcut('PgDown', self).activated.connect(lambda: self.advance_cursor(cfg['grid_rows'] * cfg['grid_columns']))
        QShortcut('Home', self).activated.connect(lambda: self.set_cursor(0))
        QShortcut('End', self).activated.connect(lambda: self.set_cursor(len(self.tlabels)-1))
        QShortcut('Return', self).activated.connect(lambda: play_video(cfg['vid'], self.tlabels[self.cur].info[2], True))
        QShortcut('Shift+Return', self).activated.connect(lambda: play_video(cfg['vid'], self.tlabels[self.cur].info[2]))
        QShortcut('Alt+Return', self).activated.connect(
                    lambda: self.tlabels[self.cur].contextMenu_show(QPoint(self.tlwidth/2, self.tlheight/2)))
        self.set_cursor(0)

    def load_view(self, fname):
        # sanitize file name
        if not os.path.exists(fname) or not os.access(fname, os.R_OK):
            fname = os.path.dirname(fname)
            if not os.path.isdir(fname):
                fname = os.getcwd()
        if os.path.isdir(fname):
            options = QFileDialog.Options()
            options |= QFileDialog.DontUseNativeDialog
            fname, _ = QFileDialog.getOpenFileName(self, 'Open File', fname,
                        'Video Files (*.avi *.mkv *.mp4);;All Files (*)', options=options)
        if not os.path.exists(fname) or not os.access(fname, os.R_OK):
            return
        cfg['vid'] = os.path.basename(fname)
        os.chdir(os.path.dirname(fname))
        self.setWindowTitle('ffpreview - '+cfg['vid'])

        # prepare thumbnail directory
        cfg['thdir'] = cfg['tmpdir'] + '/ffpreview_thumbs/' + os.path.basename(cfg['vid'])
        try:
            os.makedirs(cfg['thdir'], exist_ok=True)
        except Exception as e:
            eprint(0, str(e))
            exit(1)
        cfg['idxfile'] = cfg['thdir'] + '/ffpreview.idx'

        # clear previous view
        self.statdsp[0].setText('Clearing view ...')
        QApplication.processEvents()
        self.clear_grid()

        # analyze video and prepare info and thumbnail files
        self.statdsp[0].setText('Analyzing ...')
        QApplication.processEvents()
        thinfo, ok = get_thinfo()
        if not ok:
            # (re)generate thumbnails and index file
            self.statdsp[0].setText('Processing video:')
            clear_thumbdir()
            thinfo = make_thumbs(cfg['vid'], thinfo, self.statdsp[1], self.progbar)

        # load thumbnails and make labels
        self.statdsp[0].setText('Loading:')
        self.progbar.show()
        tlabels = make_tlabels(self.tlabels, self.statdsp[1], self.progbar, self.broken_img)

        # roughly fix window geometry
        self.tlwidth = tlabels[0].width()
        self.tlheight = tlabels[0].height()
        w = self.tlwidth * cfg['grid_columns'] + self.px
        h = self.tlheight * cfg['grid_rows'] + self.py
        self.resize(w, h)

        # fill the view grid
        self.progbar.hide()
        self.statdsp[0].setText(' Generating view ...')
        self.statdsp[1].setText('')
        self.statdsp[2].setText('')
        QApplication.processEvents()
        self.fill_grid()
        QApplication.processEvents()

        # final window touch-up
        self.statdsp[0].setText(' Duration: ' + str(thinfo["duration"]) + ' s')
        self.statdsp[1].setText(' Thumbs: ' + str(thinfo["count"]))
        self.statdsp[2].setText(' Method: ' + str(thinfo["method"]))
        QApplication.processEvents()
        self.calculate_props()
        self.setMinimumSize(self.tlwidth + self.px, self.tlheight + self.py)
        self.optimize_extent()

############################################################
# Helper functions

# get video meta information
def get_meta(vidfile):
    global proc
    meta = { 'frames': -1, 'duration':-1, 'fps':-1.0 }
    # try ffprobe method
    try:
        cmd = cfg['ffprobe'] + ' -v error -select_streams v:0 -of json -count_packets'
        cmd += ' -show_entries format=duration:stream=nb_read_packets'
        cmd += ' "' + vidfile + '"'
        eprint(2, cmd)
        proc = Popen('exec ' + cmd, shell=True, stdout=PIPE, stderr=PIPE)
        stdout, stderr = proc.communicate()
        retval = proc.wait()
        proc = None
        if retval == 0:
            info = json.loads(stdout.decode())
            meta['frames'] = int(info['streams'][0]['nb_read_packets'])
            d = float(info['format']['duration'])
            meta['duration'] = int(d)
            meta['fps'] = round(meta['frames'] / d, 2)
            return meta
        else:
            eprint(0, cmd + '\n  returned %d' % retval)
            eprint(1, stderr.decode())
    except Exception as e:
        eprint(0, cmd + '\n  failed: ' + str(e))
    # ffprobe didn't cut it, try ffmpeg instead
    try:
        cmd = cfg['ffmpeg'] + ' -nostats -i "' + vidfile + '"'
        cmd += ' -c:v copy -f rawvideo -y /dev/null'
        eprint(2, cmd)
        proc = Popen('exec ' + cmd, shell=True, stdout=PIPE, stderr=PIPE)
        stdout, stderr = proc.communicate()
        retval = proc.wait()
        proc = None
        if retval == 0:
            for line in io.StringIO(stderr.decode()).readlines():
                m = re.match(r'^frame=\s*(\d+).*time=\s*(\d+:\d+:\d+(\.\d+)?)', line)
                if m:
                    meta['frames'] = int(m.group(1))
                    d = hms2s(m.group(2))
                    meta['duration'] = int(d)
                    meta['fps'] = round(meta['frames'] / d, 2)
                    return meta
        else:
            eprint(0, cmd + '\n  returned %d' % retval)
            eprint(1, stderr.decode())
    except Exception as e:
        eprint(0, cmd + '\n  failed: ' + str(e))
    return meta

# extract thumbnails from video and collect timestamps
def make_thumbs(vidfile, thinfo, ilabel, pbar):
    global proc
    pictemplate = '%08d.png'
    cmd = cfg['ffmpeg'] + ' -loglevel info -hide_banner -y'
    if cfg['start']:
        cmd += ' -ss ' + str(cfg['start'])
    if cfg['end']:
        cmd += ' -to ' + str(cfg['end'])
    cmd += ' -i "' + vidfile + '"'
    if cfg['method'] == 'scene':
        cmd += ' -vf "select=gt(scene\,' + str(cfg['scene_thresh']) + ')'
    elif cfg['method'] == 'skip':
        cmd += ' -vf "select=not(mod(n\,' + str(cfg['frame_skip']) + '))'
    elif cfg['method'] == 'time':
        fs = int(float(cfg['time_skip']) * float(thinfo['fps']))
        cmd += ' -vf "select=not(mod(n\,' + str(fs) + '))'
    elif cfg['method'] == 'customvf':
        cmd += ' -vf "' + cfg['customvf']
    else: # iframe
        cmd += ' -vf "select=eq(pict_type\,I)'
    cmd += ',showinfo,scale=' + str(cfg['thumb_width']) + ':-1"'
    cmd += ' -vsync vfr "' + cfg['thdir'] + '/' + pictemplate + '"'
    eprint(2, cmd)
    ebuf = ''
    cnt = 0
    try:
        pbar.show()
        proc = Popen('exec ' + cmd, shell=True, stderr=PIPE)
        while proc.poll() is None:
            line = proc.stderr.readline()
            if line:
                line = line.decode()
                ebuf += line
                x = re.search('pts_time:\d*\.?\d*', line)
                if x is not None:
                    cnt += 1
                    t = x.group().split(':')[1]
                    if cfg['start']:
                        t = str(float(t) + cfg['start'])
                    thinfo['th'].append([ cnt, pictemplate % cnt, t ])
                    ilabel.setText('%s / %d s' % (t.split('.')[0], thinfo['duration']))
                    pbar.setValue(float(t) * 100 / thinfo['duration'])
                    QApplication.processEvents()
        retval = proc.wait()
        proc = None
        if retval != 0:
            eprint(0, cmd + '\n  returned %d' % retval)
            eprint(1, ebuf)
        thinfo['count'] = cnt
        with open(cfg['idxfile'], 'w') as idxfile:
            json.dump(thinfo, idxfile, indent=2)
    except Exception as e:
        eprint(0, cmd + '\n  failed: ' + str(e))
    return thinfo

# open video in player
def play_video(filename, start='0', paused=False):
    if paused and cfg['plpaused']:
        cmd = cfg['plpaused']
    else:
        cmd = cfg['player']
    cmd = cmd.replace('%t', '"' + start + '"')
    cmd = cmd.replace('%f', '"' + filename + '"')
    eprint(2, cmd)
    Popen('exec ' + cmd, shell=True, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)

# check validity of existing index file
def chk_idxfile(thinfo):
    try:
        with open(cfg['idxfile'], 'r') as idxfile:
            idx = json.load(idxfile)
            if idx['name'] != thinfo['name']:
                return False
            if idx['duration'] != thinfo['duration']:
                return False
            if idx['start'] != thinfo['start']:
                return False
            if idx['end'] != thinfo['end']:
                return False
            if idx['count'] != len(idx['th']):
                return False
            if not cfg['reuse']:
                if idx['width'] != thinfo['width']:
                    return False
                if idx['method'] != thinfo['method']:
                    return False
                if idx['frame_skip'] != thinfo['frame_skip']:
                    return False
                if idx['time_skip'] != thinfo['time_skip']:
                    return False
                if idx['scene_thresh'] != thinfo['scene_thresh']:
                    return False
                if idx['customvf'] != thinfo['customvf']:
                    return False
            return idx
    except Exception as e:
        pass
    return False

# initialize thumbnail info structure
def get_thinfo():
    thinfo = {
        'name': os.path.basename(cfg['vid']),
        'frames': -1,
        'duration': -1,
        'fps': -1,
        'start': cfg['start'],
        'end': cfg['end'],
        'count': 0,
        'width': cfg['thumb_width'],
        'method': cfg['method'],
        'frame_skip': cfg['frame_skip'],
        'time_skip': cfg['time_skip'],
        'scene_thresh': cfg['scene_thresh'],
        'customvf': cfg['customvf'],
        'date': 0,
        'th': []
    }
    thinfo.update(get_meta(cfg['vid']))
    thinfo['date'] = int(time.time())
    if not cfg['force']:
        chk = chk_idxfile(thinfo)
        if chk:
            return chk, True
    return thinfo, False

# clear out thumbnail directory
def clear_thumbdir():
    try:
        os.unlink(cfg['idxfile'])
    except Exception as e:
        pass
    for f in os.listdir(cfg['thdir']):
        if re.match('^\d{8}\.png$', f):
            try:
                os.unlink(cfg['thdir'] + '/' + f)
            except Exception as e:
                pass

# generate clickable thumbnail labels
def make_tlabels(tlabels, ilabel, pbar, dummy_img):
    try:
        with open(cfg['idxfile'], 'r') as idxfile:
            idx = json.load(idxfile)
            if cfg['verbosity'] > 3:
                eprint(3, 'idx = ' + json.dumps(idx, indent=2))
            for th in idx['th']:
                if th[0] % 100 == 0:
                    ilabel.setText('%d / %d' % (th[0], idx['count']))
                    pbar.setValue(th[0] * 100 / idx['count'])
                    QApplication.processEvents()
                thumb = QPixmap(cfg['thdir'] + '/' + th[1])
                if thumb.isNull():
                    thumb = dummy_img.scaledToWidth(cfg['thumb_width'])
                tlabel = tLabel(pixmap=thumb, text=s2hms(th[2]), info=th)
                tlabels.append(tlabel)
    except Exception as e:
        eprint(0, str(e))
    if len(tlabels) == 0:
        # no thumbnails available, make dummy
        th = [0, 'broken', str(cfg['start'])]
        thumb = dummy_img.scaledToWidth(cfg['thumb_width'])
        tlabel = tLabel(pixmap=thumb, text=s2hms(str(cfg['start'])), info=th)
        tlabels.append(tlabel)
    return tlabels


############################################################
# main function

def main():
    # initialization
    global proc, cfg
    proc = None
    cfg = configure()
    eprint(3, 'cfg = ' + json.dumps(cfg, indent=2))

    signal.signal(signal.SIGHUP, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGQUIT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    os.environ['QT_LOGGING_RULES'] = 'qt5ct.debug=false'
    app = QApplication(sys.argv)
    app.setApplicationName('ffpreview')
    root = sMainWindow(title='ffpreview')
    root.show()
    root.load_view(cfg['vid'])

    # start main loop
    exit(app.exec_())

# run application
if __name__== "__main__":
    main()

# EOF
