#!/usr/bin/python3

"""
ffpreview.py

Copyright 2021 Urban Wallasch <irrwahn35@freenet.de>

BSD 3-Clause License

Copyright (c) 2021, Urban Wallasch
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


import sys

if sys.version_info.major < 3 or sys.version_info.minor < 5:
    print('Need Python version 3.5+ or later, got version ' + str(sys.version), file=sys.stderr)
    exit(0)

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

def eprint(*args, **kwargs):
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


############################################################
# low-level initialization

proc = None

def die(event=None):
    global proc
    if proc is not None:
        eprint('killing subprocess: %s' % proc.args)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    exit()

def sigint_handler(signum, frame):
    eprint('ffpreview caught signal %d, exiting.' % signum)
    die()

signal.signal(signal.SIGHUP, sigint_handler)
signal.signal(signal.SIGINT, sigint_handler)
signal.signal(signal.SIGQUIT, sigint_handler)
signal.signal(signal.SIGTERM, sigint_handler)
signal.signal(signal.SIGPIPE, signal.SIG_IGN)


############################################################
# configuration

cfg = {}

# set defaults
cfg['home'] = expanduser("~")
cfg['conffile'] = 'ffpreview.conf'
cfg['vid'] = ''
cfg['tmpdir'] = ''
cfg['thdir'] = ''
cfg['idxfile'] = ''
cfg['grid'] = '5x5'
cfg['grid_columns'] = 5
cfg['grid_rows'] = 5
cfg['thumb_width'] = '128'
cfg['highlightcolor'] = 'lightblue'
cfg['ffprobe'] = 'ffprobe'
cfg['ffmpeg'] = 'ffmpeg'
cfg['player'] = 'mpv --no-ordered-chapters --start=%t %f'
cfg['plpaused'] = 'mpv --no-ordered-chapters --start=%t --pause %f'
cfg['force'] = 'False'
cfg['reuse'] = 'False'
cfg['method'] = 'iframe'
cfg['frame_skip'] = '-1'
cfg['time_skip'] = '-1'
cfg['scene_thresh'] = '-1'
cfg['customvf'] = ''
cfg['start'] = '0'
cfg['end'] = '0'

# parse command line arguments
parser = argparse.ArgumentParser(
    description='Generate clickable video thumbnail preview.',
    epilog='The -C, -i, -N, -n and -s options are mutually exclusive, -C beats -i beats -N beats -n beats -s.'
)
parser.add_argument('filename', help='input video file')
parser.add_argument('-c', '--config', metavar='FILE', help='read configuration from FILE')
parser.add_argument('-g', '--grid', metavar='C[xR]', help='number of columns and rows in preview')
parser.add_argument('-w', '--width', type=int, metavar='N', help='thumbnail image width in pixel')
parser.add_argument('-t', '--tmpdir', metavar='path', help='path to thumbnail top level directory')
parser.add_argument('-f', '--force', action='count', help='force thumbnail and index rebuild')
parser.add_argument('-r', '--reuse', action='count', help='reuse filter settings from index file')
parser.add_argument('-i', '--iframe', action='count', help='select only I-frames (default)')
parser.add_argument('-n', '--nskip', type=int, metavar='N', help='select only every Nth frame')
parser.add_argument('-N', '--nsecs', type=float, metavar='F', help='select one frame every F seconds')
parser.add_argument('-s', '--scene', type=float, metavar='F', help='select by scene change threshold; 0 < F < 1')
parser.add_argument('-C', '--customvf', metavar='S', help='select by custom filter string S')
parser.add_argument('-S', '--start', metavar='TS', help='start video analysis at time TS')
parser.add_argument('-E', '--end', metavar='TS', help='end video analysis at time TS')
args = parser.parse_args()

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
            eprint(str(e))
except Exception as e:
    eprint(str(e))

# fix non-string typed options
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

# prepare temp directory
if not cfg['tmpdir']:
    cfg['tmpdir'] = tempfile.gettempdir()
try:
    os.makedirs(cfg['tmpdir'], exist_ok=True)
except Exception as e:
    eprint(str(e))
    exit(1)

# parse grid geometry
grid = re.split('[xX,;:]', cfg['grid'])
cfg['grid_columns'] = int(grid[0])
if len(grid) > 1:
    cfg['grid_rows'] = int(grid[1])

# end of configuration
############################################################


############################################################
# initialize window

ffpreview_png = '''
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
Ut0AAAAASUVORK5CYII=
'''
broken_img_png = '''
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
Ec1fP40FEpDbSStlk0UTXj/D3sRmYTAYDAbDKP4Bb2zlnKfZbGYAAAAASUVORK5CYII=
'''

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
    def __init__(self, *args, pixmap=None, text=None, info=None, **kwargs):
        super().__init__(*args, **kwargs)
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0,0,0,0)
        self.setLayout(layout)
        self.layout = layout
        if pixmap is not None:
            pl = QLabel()
            pl.setPixmap(pixmap)
            pl.adjustSize()
            pl.setAlignment(Qt.AlignCenter)
            layout.addWidget(pl)
        if text is not None:
            tl = QLabel()
            tl.setText(text)
            tl.adjustSize()
            tl.setAlignment(Qt.AlignCenter)
            layout.addWidget(tl)
        if info is not None:
            self.info = info
        self.adjustSize()
        self.setStyleSheet('::hover {background-color: ' + cfg['highlightcolor'] + ';}')

    def enterEvent(self, event):
        statdsp[3].setText(self.info[1])

    def leaveEvent(self, event):
        statdsp[3].setText('')

    def mouseReleaseEvent(self, event):
        button = event.button()
        #modifiers = event.modifiers()
        if button == Qt.LeftButton:
            play_video(cfg['vid'], self.info[2], True)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        play_here_action = menu.addAction('Play from here')
        play_start_action = menu.addAction('Play from start')
        menu.addSeparator()
        copy_ts1_action = menu.addAction('Copy timestamp [H:M:S.ms]')
        copy_ts2_action = menu.addAction('Copy timestamp [S.ms]')
        menu.addSeparator()
        copy_filename_action = menu.addAction('Copy original filename')
        copy_thumbname_action = menu.addAction('Copy thumb filename')
        copy_thumb_action = menu.addAction('Copy thumbnail image')
        menu.addSeparator()
        quit_action = menu.addAction('Quit')
        action = menu.exec_(self.mapToGlobal(event.pos()))
        if action == play_here_action:
            play_video(cfg['vid'], self.info[2])
        elif action == play_start_action:
            play_video(cfg['vid'])
        elif action == copy_ts1_action:
            clipboard.setText(s2hms(self.info[2]))
        elif action == copy_ts2_action:
            clipboard.setText(self.info[2])
        elif action == copy_filename_action:
            clipboard.setText(cfg['vid'])
        elif action == copy_thumbname_action:
            clipboard.setText(cfg['thdir'] + '/' + self.info[1])
        elif action == copy_thumb_action:
            clipboard.setPixmap(self.layout.itemAt(0).widget().pixmap())
        elif action == quit_action:
            die()

class sMainWindow(QMainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    def closeEvent(self, event):
        self.close()
        die()

app = QApplication(sys.argv)
app.setApplicationName('ffpreview')
clipboard = QApplication.clipboard()
broken_img = sQPixmap(imgdata=broken_img_png)
ffpreview_ico = sQIcon(imgdata=ffpreview_png)
root = sMainWindow()
root.setWindowTitle('ffpreview - ' + cfg['vid'])
root.resize(500, 300)
root.setWindowIcon(ffpreview_ico)
QShortcut('Esc', root).activated.connect(die)
QShortcut('Ctrl+Q', root).activated.connect(die)
QShortcut('Ctrl+W', root).activated.connect(die)

statbar = QHBoxLayout()
statdsp = []
for i in range(4):
    s = QLabel('')
    s.resize(100, 20)
    statdsp.append(s)
    statbar.addWidget(s)
progbar = QProgressBar()
progbar.resize(100, 20)
progbar.hide()
statbar.addWidget(progbar)

thumb_layout = QGridLayout()
thumb_layout.setContentsMargins(0, 0, 0, 0)
thumb_layout.setHorizontalSpacing(0)
thumb_layout.setHorizontalSpacing(0)
tlwidth = tlheight = 0
tlabels = []

def fill_grid(cols):
    thumb_layout.parent().setUpdatesEnabled(False)
    x = 0; y = 0
    for tl in tlabels:
        thumb_layout.removeWidget(tl)
        thumb_layout.addWidget(tl, y, x)
        x += 1
        if x >= cols:
            x = 0; y += 1
    thumb_layout.parent().setUpdatesEnabled(True)

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
        if tlwidth < 1 or tlheight < 1:
            return
        hstep = tlheight + 6
        rows = int(self.viewport().height() / hstep)
        self.verticalScrollBar().setPageStep(rows * hstep)
        self.verticalScrollBar().setSingleStep(hstep)
        cfg['grid_rows'] = rows
        cols = int((self.viewport().width() + 1) / tlwidth)
        if cols < 1:
            cols = 1
        if cols != cfg['grid_columns']:
            cfg['grid_columns'] = cols
            fill_grid(cols)

main_frame = QWidget()
main_layout = QVBoxLayout(main_frame)
main_layout.setContentsMargins(0, 0, 0, 0)

scrollframe = QFrame()
scrollframe.setLayout(thumb_layout)
scroll = tScrollArea()
scroll.setWidget(scrollframe)
scroll.setWidgetResizable(True)
scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

main_layout.addWidget(scroll)
main_layout.addLayout(statbar)

def do_scroll(event):
    if event == 'Home':
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().minimum());
    elif event == 'End':
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum());

QShortcut('Home', root).activated.connect(lambda: do_scroll('Home'))
QShortcut('End', root).activated.connect(lambda: do_scroll('End'))

root.setCentralWidget(main_frame)
root.show()


############################################################
# Helper functions

# get video meta information
def get_meta(vidfile):
    meta = { 'frames': -1, 'duration':-1, 'fps':-1.0 }
    global proc
    # try ffprobe method
    try:
        cmd = cfg['ffprobe'] + ' -v error -select_streams v:0 -of json -count_packets'
        cmd += ' -show_entries format=duration:stream=nb_read_packets'
        cmd += ' "' + vidfile + '"'
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
            eprint(cmd)
            eprint(stderr.decode())
    except Exception as e:
        eprint(cmd + '\n  failed: ' + str(e))
    # ffprobe didn't cut it, try ffmpeg instead
    try:
        cmd = cfg['ffmpeg'] + ' -nostats -i "' + vidfile + '"'
        cmd += ' -c:v copy -f rawvideo -y /dev/null'
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
            eprint(cmd)
            eprint(stderr.decode())
    except Exception as e:
        eprint(cmd + '\n  failed: ' + str(e))
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
    eprint(cmd)
    ebuf = ''
    cnt = 0
    try:
        progbar.show()
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
            eprint(ebuf)
            eprint('ffmpeg exit code: %d' % retval)
            exit(retval)
        thinfo['count'] = cnt
        with open(cfg['idxfile'], 'w') as idxfile:
            json.dump(thinfo, idxfile, indent=2)
    except Exception as e:
        exit(1)

# open video in player
def play_video(filename, start='0', paused=False):
    if paused and cfg['plpaused']:
        cmd = cfg['plpaused']
    else:
        cmd = cfg['player']
    cmd = cmd.replace('%t', '"' + start + '"')
    cmd = cmd.replace('%f', '"' + filename + '"')
    eprint(cmd)
    Popen('exec ' + cmd, shell=True, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)

# check validity of existing index file
def chk_idxfile():
    global thinfo
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
                if idx['scene_thresh'] != thinfo['scene_thresh']:
                    return False
                if idx['customvf'] != thinfo['customvf']:
                    return False
            thinfo = idx
            return True
    except Exception as e:
        pass
    return False


############################################################
# prepare thumbnails

# initialize thumbnail info structure
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

# prepare thumbnail directory
cfg['thdir'] = cfg['tmpdir'] + '/ffpreview_thumbs/' + os.path.basename(cfg['vid'])
try:
    os.makedirs(cfg['thdir'], exist_ok=True)
except Exception as e:
    eprint(str(e))
    exit(1)
cfg['idxfile'] = cfg['thdir'] + '/ffpreview.idx'

# rebuild thumbnails and index, if necessary
statdsp[0].setText('Analysing ...'),
QApplication.processEvents()
thinfo.update(get_meta(cfg['vid']))
thinfo['date'] = int(time.time())
if cfg['force'] or not chk_idxfile():
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
    statdsp[0].setText('Processing video:'),
    make_thumbs(cfg['vid'], thinfo, statdsp[1], progbar)


############################################################
# generate clickable thumbnail labels

try:
    with open(cfg['idxfile'], 'r') as idxfile:
        idx = json.load(idxfile)
        tlabels = []
        statdsp[0].setText('Loading:')
        progbar.show()
        for th in idx['th']:
            if th[0] % 100 == 0:
                statdsp[1].setText('%d / %d' % (th[0], idx['count']))
                progbar.setValue(th[0] * 100 / idx['count'])
                QApplication.processEvents()
            try:
                thumb = QPixmap(cfg['thdir'] + '/' + th[1])
            except:
                thumb = broken_img
            tlabel = tLabel(pixmap=thumb, text=s2hms(th[2]), info=th)
            tlabels.append(tlabel)
        if len(tlabels) == 0: # no thumbnails available :(
            tlabel = tLabel(pixmap=broken_img, text=s2hms(str(cfg['start'])))
            tlabel.th = [0, 'broken', str(cfg['start'])]
            tlabels.append(tlabel)
        tlwidth = tlabel.width()
        tlheight = tlabel.height()
except Exception as e:
    eprint(str(e))
    exit(2)


############################################################
# fix window geometry, start main loop

scwidth = qApp.style().pixelMetric(QStyle.PM_ScrollBarExtent) * 2
root.resize(tlwidth*cfg['grid_columns']+scwidth, (tlheight + 6)*cfg['grid_rows']+22)
root.setMinimumSize(tlwidth+scwidth, tlheight+scwidth)

progbar.hide()
statdsp[0].setText(' Generating view ...')
statdsp[1].setText('')
statdsp[2].setText('')
QApplication.processEvents()

fill_grid(cfg['grid_columns'])
statdsp[0].setText(' Duration: ' + str(thinfo["duration"]) + ' s')
statdsp[1].setText(' Thumbs: ' + str(thinfo["count"]))
statdsp[2].setText(' Method: ' + str(thinfo["method"]))
QApplication.processEvents()

exit(app.exec_())


# EOF
