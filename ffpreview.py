#!/usr/bin/env python3

"""
ffpreview.py

Copyright 2021 Urban Wallasch <irrwahn35@freenet.de>

Ffpreview is distributed under the Modified ("3-clause") BSD License.
See `LICENSE` file for more information.
"""

_FFPREVIEW_VERSION = '0.2+'

_FFPREVIEW_IDX = 'ffpreview.idx'

_FF_DEBUG = False

import sys

_PYTHON_VERSION = float("%d.%d" % (sys.version_info.major, sys.version_info.minor))
if _PYTHON_VERSION < 3.6:
    raise Exception ('Need Python version 3.6 or later, got version ' + str(sys.version))

import platform
import io
import os
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

def s2hms(ts, frac=True):
    s, ms = divmod(float(ts), 1.0)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    res = '' if h < 1 else '%02d:' % h
    res += '%02d:%02d' % (m, s)
    res += '' if not frac else ('%.3f' % ms).lstrip('0')
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

def hr_size(sz):
    i = 0
    while sz >= 1024:
        sz /= 1024
        i += 1
    return '%.1f %s' % (sz, ['', 'KiB', 'MiB', 'GiB', 'TiB'][i])

def ppdict(dic, excl=[]):
    s = ''
    with io.StringIO() as sf:
        for k, v in dic.items():
            if v is not None and not k in excl:
                print(k+':', v, file=sf)
        s = sf.getvalue()
    return s.strip()

def kill_proc(p=None):
    global proc
    if p is None:
        p = proc
    if p is not None:
        eprint(1, 'killing subprocess: %s' % p.args)
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
    return None

def die(rc):
    global proc
    proc = kill_proc(proc)
    if '_ffdbg_thread' in globals():
        global _ffdbg_thread, _ffdbg_run
        _ffdbg_run = False
        eprint(0, 'waiting for debug thread to finish')
        _ffdbg_thread.join()
    sys.exit(rc)

def sig_handler(signum, frame):
    eprint(0, 'ffpreview caught signal %d, exiting.' % signum)
    die(signum)

############################################################
# configuration

def configure():
    # set defaults
    global cfg
    cfg = {
        'conffile': 'ffpreview.conf',
        'vid': '',
        'outdir': '',
        'grid': '5x5',
        'grid_columns': 5,
        'grid_rows': 5,
        'thumb_width': '128',
        'appstyle': '',
        'selstyle': 'background-color: lightblue;',
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
        'batch': 0,
        'manage': 0,
        'platform': platform.system(),
        'env': os.environ.copy(),
        'exec': 'exec',
        'vformats': '*.3g2 *.3gp *.asf *.avi *.divx *.evo *.f4v *.flv *'
                    '.m2p *.m2ts *.mkv *.mk3d *.mov *.mp4 *.mpeg *.mpg '
                    '*.ogg *.ogv *.ogv *.qt *.rmvb *.vob *.webm *.wmv'
    }
    if cfg['platform'] == 'Windows':
        cfg['env']['PATH'] = sys.path[0] + os.pathsep + cfg['env']['PATH']
        cfg['exec'] = ''

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
               '  ESC               leave full screen view, quit application\n'
               '  Ctrl+Q, Ctrl-W    quit application\n'
               '  Alt+Return, F     toggle full screen view\n'
               '  Ctrl+G            adjust window geometry for optimal fit\n'
               '  Ctrl+O            show open file dialog\n'
               '  Ctrl+M            open thumbnail manager\n'
               '  Double-click,\n'
               '  Return, Space     open video at selected position in paused state\n'
               '  Shift+dbl-click,\n'
               '  Shift+Return      play video starting at selected position\n'
               '  Mouse-2, Menu,\n'
               '  Alt+Return        open the context menu\n'
               '  Up, Down,\n'
               '  PgUp, PgDown,\n'
               '  Home, End,\n'
               '  TAB, Shift+TAB    move highlighted selection marker\n'
    )
    parser.add_argument('filename', nargs='*', default=[os.getcwd()], help='input video file')
    parser.add_argument('-b', '--batch', action='count', help='batch mode, do not draw window')
    parser.add_argument('-m', '--manage', action='count', help='start with thumbnail manager')
    parser.add_argument('-c', '--config', metavar='F', help='read configuration from file F')
    parser.add_argument('-g', '--grid', metavar='G', help='set grid geometry in COLS[xROWS] format')
    parser.add_argument('-w', '--width', type=int, metavar='N', help='thumbnail image width in pixel')
    parser.add_argument('-o', '--outdir', metavar='P', help='set thumbnail parent directory to P')
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
        print('ffpreview version %s running on python %.1f.x (%s)'
                % (_FFPREVIEW_VERSION, _PYTHON_VERSION, cfg['platform']))
        die(0)

    # parse config file
    defconfpath = os.path.join( # try to determine user config file
        os.environ.get('APPDATA') or
        os.environ.get('XDG_CONFIG_HOME') or
        os.path.join(os.environ['HOME'], '.config') or
        sys.path[0],
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
    if args.outdir:
        cfg['outdir'] = args.outdir
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
    if args.batch:
        cfg['batch'] = args.batch
    if args.manage:
        cfg['manage'] = args.manage

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

    # prepare output directory
    if not cfg['outdir']:
        cfg['outdir'] = tempfile.gettempdir()
    cfg['outdir'] = os.path.join(cfg['outdir'], 'ffpreview_thumbs')
    try:
        os.makedirs(cfg['outdir'], exist_ok=True)
    except Exception as e:
        eprint(0, str(e))
        die(1)

    return cfg
    # end of configure()


############################################################
# Qt classes

class ffIcon:
    apply_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAAAA3NCSVQICAjb4U/gAAAACXBIWXMAAAG7AAABuwE67OPiAAAAGXRFWHRTb2Z0d2FyZQB3d3cuaW5rc2NhcGUub3Jnm+48GgAAAQ5QTFRF////AE4AAEkAAFUAAEwAAE8AAE0AAE4AAE0AAEsAAE4AAE0A
AEwAAE0AAEwAAE4AAEwAAlACBFMEBFMEA1IDAk4CAk4CAk0CAkgCAFsAE2QSAVsBDmgMF2YVF2kVFWsSEWsODmgLCV8HEmsQEFwNGF0UF1YRGlQSHFARHFAULIQjR7oOSo0wTX0pToItTosxUKwqVIMtV8IYWKBCWZ9BW5w/XbItZLFQZLoyc7RkdrVjdrxZd8BVebhh
e7tmf8Bof8g1gsZFg8JKiMJ6ib1LicZ7isFXisFbjsRgjsdEj8lckL1rkMFvkcVpkd4+k8Zwk8hqlsNsmNVHmdCPmdKQmshomst7ncltptB3tuqhKezvqAAAACt0Uk5TAA0OGBsdISQyMzQ1NkZrbHKNkJGVmp2go7zW2Nvm5+nr7fX19vf5+fr6/g0OwPEAAACCSURB
VBjTY2AgBbAKMKHw2RStpBmR5RXCIm0lQSx+ZWEgySLvHxLsIwVk8WlFGAsxMMv5ujp7K3EABWRCLQNMRGQ97O0CVThBOgR13M29TN1srMNVuSCmieo7GThamLmoccHMF9Nz0DbyVOdG2Chu6BekyY3sJgldDR5UV/OyE+9DAH7HD8CeLk93AAAAAElFTkSuQmCC
"""
    broken_png = """iVBORw0KGgoAAAANSUhEUgAAAIAAAABJCAYAAAD12S63AAAEi0lEQVR42u1dTUhVQRT+1LIfSehHyoKCwhSsFombKIiwIEm0IiiICApzIRYlRbaxXGTtWrSQapG0chMkZGFSC6NFQiFJEbTyr4gkRDT02WvhEx9yu2/mzs89c+/5YBY+Z+aen2/OPWfuffMABoPBYDAY
DEbskMUm0I4VABrS/r7DJokWcgGcANABICnRWtl07mA/gOeSDhZtfAsgjGTc7J4dQSfWAugmvvqSvNaCowpAu6HwqxLa2wCUScy5i125gNWpJOmbofuuKgE6AFQE1K3CZ94LcXJwLYBeww5WJcARQ/p/8ZEpL0qlUV/IDqacgTtTGXihgbhj+wFcAVBg2S5bU7euSQ+Z
6qNAgoOEnPwOQGVIdrgWMD/JWTRPoU/fn1TrYlttWjHZ0oEBC7edxz59b1ByfpdGIzwFUBOSHvNhOizCT3hcp9mn/xIqBLgqoeQsgCcAikOsMjKF6TAjXoOr+UAmxfaEsMvXB3MVgMlb3OJ8YIkLJAijtCrz2co1XQLaLkPrfPqOUyDAKs0EMOVcHQQotCDDpMd1D/j0
P+9CFPhNoILQQYBKS3JckrTVyrAJcFLBuKrGagFQAqAcwF3DBGjKMH4s1S8vVdGMaMwHyCeFQQ38OoBxtgjKNKuZAD0Bx2cD+KuJkGRJsM9SFNgoKdcxjQQY8xl7TVCeGQl5pjzGb/DpPxqHKBAEjQLzDinqJ4spQX0vS8rRGCYBEoajQKtBcl5XmGPU8MK5LTnmJeUo
0Kp4GzAlV4nCHLrQH4V8IGk4CqwxJFfQOYYN2PCtYD6Q4yoJvinmAl8k5flgkACmMZF2rRmP/7dRJIHpKCCj3AzMbQOfs2jTX6lren2LaNokCYK8Fi4Spnv+8/mbAEQ7nvbZTgC3YOfR6SOLBFiLue8KjHn8LzfD2GVRjgJhPQj6Cnrw06/ItjCDAkbf7DABKOIUtXzA
lSjQLaBLflr/AdDFICUSiGTf9Rp2B1VbtYAuNcRXfzre++ia4Cgg9uRtMW5KkIV6PtBlUxCRJ3KHQ44CInjmyOqfx/YMOpdzFJAjwJBjBAAyn1/ABJCsAFzEfQokeIFgz75tkUAEvXAXfrr/jVMU+ATgDAi+TmUYKzLYxQq5H4ZMgrgjK4N9tlGJAtkGKoJi9j8A4GjY
i+R7CFFgKftdyoaRygUYC2imYjcRAR5oIEGcsRFq30kwihbDUeAPr27ldohCFPgRYPwrdrSWlmtakTrFKOBVEdRxGHfroCwRQT4Kji/i1a2lWT16p0pTLsCODvYCTAEFY4gIu4PDuHJrdnVTwrVVTmF1JwBcdMVg1YJKrePV/d82AKA0yluTFKIABUdPAzgbxdq2TNAA
px0gqe6zA0sRE1CPAjYc3gtgPWKKTRYIcE9hPp2OHsfcSymMAEYWfad9L8TO9LVBgE6iSWykbgOdhrc8ZR3OcCTZUpFrBHPnDzMcJcCwpFztAJbH0Sk2jybPh73zb2VCNf92YgSjwG42dTxJMAICZ+sy/PFZo8PbEc1fP40FEpDbSStlk0UTXj/D3sRmYTAYDAbDKP4B
b2zlnKfZbGYAAAAASUVORK5CYII=
"""
    close_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA3XAAAN1wFCKJt4AAAAB3RJTUUH4wERFhsjmju/7AAAArpJREFUeNplz1to1QUAx/HP/3/O2c7OrudsR8vmamOL2hw5LaQHgxKCLvQSRVgPFhJKo4cyMsmx
KNQgfOuhfKxICQTpocvAIFhai/WSTNIcnK3pbu12tnO2cwsfbZ+H3+MXfgGbfURDgn0xHgiIFskUuPgOE/7njsBxahsYqAnDN1p3765N9/QIYzGL4+Myw8PlbC53fp23j5HZFBhgSxPft27f3tfX36+ure2O+sbKiitnzrg2MjK/xjNH+RUCeJbo41xsSyb3PtR/WLQu
LhIGVAJKlEsUSyUBrp8966+xsdkl+o7zTwiP8mqSvffu6ZMLs+565YAVWRvL1xSWrpufvmHr/v3y1dVu30pHIuk4pxCEiCQ4nIyEYk15heakmm3bdR06Zrq0LjM3ruPNI+o7O8V7uy1NTkjV10vw4ns0Rw6Q7uCTdLwS1LfMyM+Mytxa0r73aVse2adp52Oau7pd/eGC
q4MHNUxPKc4sy1dEylwOt3FPNUH09gTrWhILSr+d9N3pQfGGpHRXjz9/+tHoqRe0t8yqasyK1lKNKlrDdYpVCEqUixRLLBZTep98DkD7zofFO3pVGqlqJlJDDDEK4TkmQlbXc+QWGZ9P2TMwpHXHLpnL541+dVRtMuWp00NmGncpxCkUCLDGWDjJWp6LG0XmJtn6xOu2
PrjLzUvn3fr6JdVXP3btmyNqGlN2vHbC/Dxry5SZ+pTfIyh3MN3Jyxt54cKVYYXVKZPn3peIFkTK5G5ckp34W+bLE7IjeWGOOT78gp8DQPwzBtt5dymkkqTmbuIpYnUEAblZsldIrLLB0AGeX2QlAEDD5xy7n7eWia2GlKsREhaIb9CEZS58wKFRplGJAGD9W36JMXwf
9cmKdF1RIlGgrmS1zOU/GDjIyZv8iwoENguRQKqblnqiI8yUWUAWJQD4D4Cg/5i7WltRAAAAAElFTkSuQmCC
"""
    delete_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAAAA3NCSVQICAjb4U/gAAAACXBIWXMAAAG7AAABuwE67OPiAAAAGXRFWHRTb2Z0d2FyZQB3d3cuaW5rc2NhcGUub3Jnm+48GgAAARdQTFRF////gAAAqAAArQAAhwAAigAAkQAAmAAAnAAAqgAAgAAAqAAA
rwQEnAAAhw4OjgAAbgAAbQAAagAAogAAlisrfx4WgiQb0nh1w0pJyWBdnR8cvzQqxUc/pB8TpB8VpCEapCMepR4Sph4QqyQXrycbszEmsysfvko/w0pAyExDylZMgCMhgSMhoT40skM6aiMfaiMgezwyWSEcWyEbvQAAvgAAvzkzwREKwjMzwk1Iwzo6xTkyxhIMxxIM
yT4+y0Q/y05KzEZG0AAA0BIM01ZW01dU02Vg1GJe1mBd12Nh2xMQ2xQR3xMP3xQS32hh4wAA5S8q5TAs5TIt5TIu5XNx5m1t5nZ16Y2J7xQS7xUT74mH8I2K9QAAY45BIQAAADR0Uk5TADIyMkhISEhISElJSUpLT1BRUtDX3d7k5ury9PX6+vr6+vr6+vr7+/v7+/z8
/Pz9/f3+/tVu3mIAAACbSURBVBgZjcFFFoIAAAXADyjYInZ3d2IHYnfn/c+h8HTPDBSh3AZ8GZwUZKT3mdEC+vTLR0Ji3V6uKaO5cL7crJBoEhNxkS/PxHlSAxkTG/SHrdE0zuCHjnaa7UaExp86zNfqfFiNH5V/LHR7wjiggkwXXO+W1cpqtwmZILHv3/ciy5Ue74MN
EsJzzLIAlzt5CMgIhwVfnIuAEh9DsxNr97RmZAAAAABJRU5ErkJggg==
"""
    error_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAABuwAAAbsBOuzj4gAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAALNSURBVHjapZNNSBRxGMaf/3w6szO7fq3rF8VqaaVBHyCGoHULBeuQ
RB72IBiBdq2Lhw5CHbqlJyEoSY06RKUISSqKiWlWfoRF4Sa6K6676U7rzuzOTLOWsgp16bm8p9/D8748LzFNE/8Q9Wca+IsY7NP7lpZjq8PDT4JjY1GpoOA4Lcsksrg4L5eWMq7KyroTra3zSNKeBEP19fdW+/oaM86U82klpZBcLhBCoKytITA5ifWpKTWnurrjbFfX
9WSDXdjX39dU6PEQXhSx+HoIP5eXIQg87IcK4Sg+iq3wJrzdPWb+hYvtCZNdgwkr9lxb2zt3bQ0PTcPsi5dBXovflli2h2fZHIljX6HstIN35UHdDGF5YFAtaW4+VWatQ8GS19pZzs/iaZrBx96+ECH0scZY7C5DUTHRJvakVFU4WEHGhm8JMS0GKd/FJ5jdKys+XzaT
kY6lD1Ogo9qdJkVZ7ZakLNlhf+M8f66ACUehBXyYGxyGf+ETqNTUbWbHgFL8finG0thaD0DguM4EbLeJ467yk27Mf4OpBDEzMWXkqrFrCAXhW1pEgkmw2wniAL5PT8NQFNCEOG0CPy4KplsfeQtt5SvGpmeM9C3VwwHPBEOHbpnEk4piMNnZCmvGQUFHmk0YZRBxC94A
EA1hdDVoOCy4wTQf6TbbFYE2ITIECSbBbidgc3L8HAAtrEFnIzIfiILYWQyohiFqhueqBT+QJFdWqnBL/xEBZf5mdo9YVFVVt6YLqpDOIb4cAe3k0a8ZIHHcRErKSKfdfiMvU/zMhTcdSOOwYghqgtlTpIdWkbxPHzcdcXIkMxgDbDTCFAMbwyKdNkAFI1hxsJgMaObB
S5fbPclF2tF9y+RLb29jEa/yuRxBJgg4QhAEsKTpmIvy6uGamo6G/VVO1nOrlQtWSbyjo1FJlksp2kqysTF7oKIipdiKXbvvmf77nX8BLtVGqws09hAAAAAASUVORK5CYII=
"""
    ffpreview_png = """iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAACfXpUWHRSYXcgcHJvZmlsZSB0eXBlIGV4aWYAAHja7ZZbjtUwDIbfuwqWEN9iZzlpLhI7YPn8aXt6YBgxg+AF6cRqXaWO7fhzqm7j29e5fcEgj7SpeeSSc8LQooUrHiI9x7j0OUdJj/tj0HWn7d0XDC3Q
ck66Xm/lmn/Y51vD0TsvyN4skDsM/xjY6zXPiX/KyOWO8dzOdc3ZY85x7q5qRhnyuakzxPZwA8MdVZJjWYY4LsOzH1IgkWpqpKmnlnZIo0JMkiYp9Y0qTRrUoRs15Kg82KGZG8sxF+JcuEkSEl1Ck12KdAlhaTxERDfhOxc64pYjXqNA5E4wZYIzwpLfyvaRwWdkzpZQ
IyLsnq5aIS/mxYFWGWXdYQYgNC9udhT4IffYfgArIGhHmQMbrGk/XexGz96SowEEdgZ99hd5X9T46BJFbEMyJECQMolRpuTMTqTCAUAVmbMo7yBAZtyRJKtIBptAHyE21jgdtmx8zuOogI9JFgebIhWwVA394xrooWpiambZ3MKK1S1L1mw5Z8/rzFUXVzfP7h5evIaE
hkUOj4gStXARHEkruXiJUkqtiFl1q1axusKi1p132XW3Pe++x1722tA+TZu13LxFK6127tK1W8/de/TS66CBVtqGDht5+IhRRp3otSlTp808fcYss97ULqq/yB9Qo4saH6SWnd/UMOu+HB0uaH1nbDEDMVYCcV8E0NC8mKUgVV7kFrNUGKfCGEnaYtMp1Y0yEOogtkk3
uye5T3PbUOuPuPFnyG0L3T8gx9uQN9zeodbXl7AdxM5TuGqaBKcP70dUjopi8/nwt/rl6OXo5ejl6OXo5ejl6D93NPHzUPAn9h3zflTECpDwegAAAAZiS0dEAP8A/wD/oL2nkwAAAAlwSFlzAAALEwAACxMBAJqcGAAAAAd0SU1FB+UFAwsEBD9c7vMAAAMMSURBVFjD
7ddPiNVVFAfwz3szzTSpi5hm1GpTzcYyW6WZJQVWGP1ZBFEU7lqEFNWsLIwSV6VSubFdUBSGrRqNyNpMRNbK6e8io8gc6R8Emjo6M22+v7i+93uTQs4QeODBfd977rnnd+45934PDOMoJmf5dxTDjQxuxk9mVy7Hx3AKg+jGqxjDrVHqx54oXhlsKfbhHSwIdhe+xFY0
gg3jGzyS/93YEfu3YCCR+MeB2zP5KL7Iog14Gy/jtWC7sAnv47Fs+D0ewg+4Mfb+xB04Hkdb7Q9gslmEZD4O47OMK+wAvmrBxrLZ/DgwD5/gt2B9mMDefPmFHewrIzAUhX14PXP34Ed8iyeCPRcHfk4o4d0c02Eszobf4aPodtXYbzsCWIL7Y6CSm7AWVbS6cDeWFzoX
4UFcVWALcyz9BVbaH8BkA1PYnmqoHPocuzNXSQO35Yx7OmT2RI7iA0wXeBN34npcUDj9ePVVpXJfkmx3xrJoJ14qMr9OFkRnZ7FRH0awOZu2SXkEipJ5E1vy/+mUY88Z1HdPdJ/J/xfwVmyWUpsDpVyTpJF6XnkWl8zKrIFxXFuj01aGrfJLlLqSSONn4cB41jSTjH90
UmzOwpU7hZNz6UBjpsmmOZbzDpx34H/jwPS/ldO5cGAQv+a+/j3v/JnK4qyZyo06OJPyTI/RiwU1ew+9//VjVMcHerAGh3AfjuVpfQPL4sjxDpv35t0fw8O5gvtCYC8LT5go+UDlwCv4KxMnQ0j21BCSNSEknSJxIoRkbw0hWRsWdRohaT2Cq/FACyVbna8qKdm9uKHQ
mRf6NVRgi7AOlxRYab+ND1Sk8dOEWzaqSOmTwZ4vSGnVP4xgNOsvzQYH8GEoeHeN/TY+sCyG12NVsOVJxm0FtiKUbSRRaIbrrcPBNC4Li15gCS7uYP+0MjyS8lmRcYUNxWiJXYcrMp5OAq9KuI8kcXvjwKkkbZ19ra3ZDuwv+H5/yOloTWu2q2gwqtZsS3FhPYWva1qz
/WVrVjanB+egOR3txsZ8Ye8sO3ACz/4N3Wzp2esUUt0AAAAASUVORK5CYII=
"""
    info_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAABuwAAAbsBOuzj4gAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAAKgSURBVHjadZPfSxRRFMe/d+bOzv7STROVfixhRARmij+ICh+EQuqx
hyAkiKzoF1lhhFgZK/SQaVAg9uBL9FB/QBLZS2GCoQ9FaJKKSz20m7PNus7szszO7d5dVlTyA4d7Oed8v2cOM0MYY9gA4aGIGJ1aKJckQlpqd8UB2DwsHi7WsNFAvvZ0pHE+Zjwu3VJcvbOyuCjgVUg8YRpa0pitKvd39LQdHgPg/M9AOnBusEv2FXW3H69XqypDUBUZ
omw5LmK6gbeTi46mJwZGIie7hMk6g7ITvbe2Vm7vu3O6GVrKhr5iw6/KEBiZLIp8CraVqnj5cRZz89H734fPRwCwnAFpvL6XllZM3W5r9ZsWy00N+ijuntoPQeTVV6RMB4QAIb+Ch68/mDSj1afe3JuWIKBKb3PdPn9ct/hkC0nDgpF2IEtEhLjzXL72c8nAkeoq30oa
kZwUAoIGIqv4y7MFls0Mrg6OQzyhxtdZi1dRAInW5wxIzeUSBP1hIhEuslBgpOcYCrQ+GAVhWH2DqkIAsDCpuVJCkfUSMBfJlA2qOMghe3Co+x0+9R6FIJWlcN0s9OUM9GQSsYQJZG3AJhJl3/o1cvBmfGJmsYIoHijeIGggAEoVJBIJCMaiJrgDF1iAxU9HiDNxNv1s
iULgsh9w0hWMiy3Jw3tUwJGwClUBIuWDuUBaB+z0HDj5Lol0IhVzQOR8s6JC629CAe1RHaD6c3mA8ERU7Nq57kMiTR1D8IUuYEctECwD3yUvovx00vmd9d/A/Dhg6s/ZxMDF9QYcNHYMQ6ZnINVK2BMC7N35qZ4FYOYPJGfSdR37BT4/Ocs4qwZrIQ03WlQfHQKVw1bG
8YiqEvDYlOGXZVrtzljf+03/xo1INZfCosq+DEaxCf8AlwgsXioQQgwAAAAASUVORK5CYII=
"""
    ok_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAAAA3NCSVQICAjb4U/gAAAACXBIWXMAAAG7AAABuwE67OPiAAAAGXRFWHRTb2Z0d2FyZQB3d3cuaW5rc2NhcGUub3Jnm+48GgAAAQ5QTFRF////AAAAABQ7ABI3CyA1CRw5CSM1CB8+Bhw3BR43BRs3CRo8
CBs7Bxo6Bx86Ch06Ch05CR87CR06Cx8/DiJCDiFDDiI/Ch49CyA7DB46Cx83DCVLHjdZDidNGTFVGjJNHTVWHTVaGjVcFjNaFTBXGjRWHTlfHDVTGjFGFzBAGDE+GzQ/L012KkRTLEhpLUZdM0xXNUxaNlJeO1RvRF95RGCDTWp6T2qJUG2JUWuHUm+WVHCTVXGHVnCU
WnaXW3abW3eUXXqWX3yoYH2aYn2YYn6aYoCsYoOlY3uTZICcZYGbZoGfZ4OfZ4SdaIimaYWeaYeob4uocIyodJGpdJS9fZq1fpuyf5uylLLIq8reyu9thgAAAC10Uk5TAAENDhgbHSEuMzg8QUZLamtsco6QkZagqLC2vNTY2ufn6Ors8/X19/n7/Pz+TqYPggAAAIRJ
REFUGNNjYCAFsAkxo/DZlQNkmZDllUIj3aVBLEE1USDJqhgUEREoA2QJ6ASbijCwKLiGh/mocAAF5Bx9vYzF5G38fV1UOUE6hPUsHZwNzbw9PdS5IKaJ65tb29nbmmhww8yX0LVycrPQ5EHYKGkQ4qfFi+QERikjbT4URzLycxPvQwDbMhBdIO5gEAAAAABJRU5ErkJg
gg==
"""
    open_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAAXNSR0IArs4c6QAAAAZiS0dEAP8A/wD/oL2nkwAAAAlwSFlzAAAN1wAADdcBQiibeAAAAAd0SU1FB+MBERYbI5o7v+wAAAJXSURBVDjLdZPNS5RRFMZ/5753ZhohsYiSooww8QNrFwYRtHGRURCtomUb
o3ZhBEF/QRBEtmxbkOGmQAmkTY0YRZG1aAwidZwPc76cd97P2+J9Jy3zgcuFcznP85xz7hEiWMAwoNiKajqdnrNtu8n/cOnui9nec2NmO6zkcqa/v298m1x0o1490ddzFADbtlFKIYDv+zRsm+zCgpmfnx89dfH66N7B81gJDaFBJ1Nfntw+PaBqlSJOcx0ApeIKRBCl
0JZFNpsVEJ7ev0nwc4burgP09vZQWSv2A6jqrxXqdTtKNAYRiTkES2tc1wVgZ/suHty5yszEOKViiVz2fSTqOzaVem1LfSKCZVmkkkkA2js6OHSkm8zEPb7PPsN1op5q13NoNP3IQOxCiaAshRLh7MgIU1NT+J6H4zgktOb5w1vsH7ocEQSeS7nmbqgqhTGwuFykXK1h
TMiefQcJQp/AC/B8n1J5nWohFxE0mjZrhQL5fJHlfBElwlJ+lU85j8MDgwgGQSJ3iejkS/Dq3RxfP36Ylgtjj82Na1coltYwxoAISgkKw7eFMoEXEMblWUAY9yiVauP15CO0WG28nf2B47pIq4Gt72kpTGsq/7whIMpCiwiphG61EAsIAB2rmU3qG3GDsiwEQa+sVniZ
+YwJ/b/HGCfKH+q/oZNpFpcK6K7O3Rw/NkDge8imBLOJqBUL420zQDKV5k0hg642nGnXC4d9L0QEQgM+ggZUq2ADHiAYdMwWELDumUmhc+gMnncympWJBePbmNZyhNEkJTInYjAYdrRlfgOLLCNO/FhvKgAAAABJRU5ErkJggg==
"""
    question_png = """iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAACvElEQVR4AeWTA7A0SQzHz7Zt22WebVsfnm3btm3btm3btnJJb8/xYc7qquwOMv9feMr/5zx2IuSVp46HDpLR9R8q/vix0HdfUIld7xpZgNbBOXhWKWadnv1x4qqx60PTK5BeOwLpNSPQP7EsHiJGfBjF
M+pG4bhLOXzrXMYhSyIhIsQz68dAyqMSTrhVMMg3TqUiISLEsxvGQda7BmQ8q0HKHSGuFSyLrx3FQESIK/jWgbxPLch6VYO0RxXL4phLGXyDgC/tixEyDH3jP0BEi2fVjzNhAbC+uQOb27uwuLoF9b2zYB7VxACf2xZBWrUE8sxhEJptGsUh1tAxkPaswqhrQM5bYmXt
0xBS0AdxZUNApxcFJYBC+MS6ANKqhhCyiJDo9X33hBaI5jyjdpTKQPUmCNWe/lmTT+Jz69gWBqAsv7Arhs9sEGCVDx9a5DFIQ880PHksZHBfAC0RAWhSCHISIdzYvWNyO2xs7cLy2hYYhNaz8nyK0X9siQDzXEgsH4Cmvpn9AZQWNYqWiCDUSAIdd5XYMbyeWdoAOsYR
DRh9EY++AD6yzIMEFO8dO6REQpN/gIzQvLOR/NapjK5xNEvgK4cSFGe1F6Jnkffy+ouZJA5ZYhASFEw4nwniVhLxvn3ERUNozqkcn5OhMJZlH/Ffv81nPfSJ62fPKsZscAiJktHE8LJwcfR54AP7r/CbC9HOFCWOdhHaVfe+bXaSILREtEzUzI8seEOZePTG3a8byKHv
TeTPvzvjKMA5aJeiXYt2212v6ihSlBgtQoYkDR2TiN/xopo6+txHfuTPvzv7KMCZQgY8sjvvekVbiSANvdNszkn8zhfV1fDdA/ReyICX6VQxZTqDO1/OI7vprtd1v3ni28BRsrte0z7Goybha7nfuT+Iiz+n8Z6cx7OiElyGdgkP4FwWzN95vgMy380jSxPW8AAAAABJ
RU5ErkJggg==
"""
    refresh_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAABuwAAAbsBOuzj4gAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAAMgSURBVHjabZPNa1xVGMaf99xz7tckM0knqUknoU2G1hg0TiitjUJo
/aDQov4BRbC4daPg0lVFNwq6EzeCOxcSFBSNRVFqIRWjJiXBjzaJadJ07CRzM537cc4993hHDGbRHzy787zn5Xnfl4wx2A89R1UwXLC6rCdHKiOT5a4yW9lcWapv1S9D40Pzmfnzv3dDAHinwJ6RgfBKebT8xvkT591KfwWDfYOwmIUgCHDj9g3MXJ2Jt9e2LyHDj6VK
8ZMkSOJOgX/NzKFvp06dmjoz+ZRgNsP90FLj6sIV9ev6L3Tx6Zf4ex+/C44cYni1Nvbw1Imxx0QQBqCQ0Gg0sHF7A2QI1eEqvB4PHSaO1sREtYbMGDAQeP77g/0DPZeOHhkXjWYDOtWY/3kef6z8DkcLeLaHK3Pfo7dSxsnjJ9Fb7AUMYBkLjBg4EV6olIacdtxGR4vX
F/F3fdMUXCFDqS4nmdI+E2ebccOeXZ2l6fFpuMYFSYJFBO744ozRoO3mNnaDXWzcWofvi2g3Sh7tJJ53OAkLz5hHiB44fBAt3kJ0L0KwE4AyGC4cVou1QhoEqN+tw9IMbam+3htXl+f2iSLzRK+H9E6CteAmWndaMO0Maay/JP+CkFnbEuCAchSEIkipZ/VMdnbfbrgA
SgIWgYzOcmljlPnUtJhM9DWEKWIRQ5c1zCjguNZ0J1z8zyFG9BFNUJ+uZVwMW6q76MTI4anMvnBt8TgOpIQ+IOvOUC4Kh5bYT+x5+hxE5Nj8XGnYnUuHstOJotW4lG5ay2yLLtIm4Vk85LliHiNw5UAKX9goODbK3IdqaGOB4A3wNKT0r1ili6GUy6qVrUW/qa+iD9Qq
y8NajmL1ur8jlMs7c+d5AQHuEwrHbLImGMn+TBR8u9rl2OMl2x9LFvRr8VZ6BDl7O/tOYyv8puemm/QwD64QoAJh92CMnUMRmoMxWv0JitypRtfl6bAplwB8h5z9x0QAXu4+4L7ljXKvMlZk8bEU7bKEdYvh7lzb6GtZGN5TbwJ4O+9cIud+53wYwIu5nrAYHTcGlBmz
AOCHXO/nxnXs4x9RcmseSXDfLAAAAABJRU5ErkJggg==
"""
    remove_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAABuwAAAbsBOuzj4gAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAADJSURBVHja7ZKxDoIwEIavQEVJ3HQyDsTRp+BZkN3JuDs7qhOLL9LV
iTdgcyNiCGiMBepPQiQ66ODiwJf8Sf+7y901LVNK0S9oUNuggUEGfceAWGNqDp63uoXhIhGCm/CVOtD7ue84uWXb657vLwk8n3HP+XHquiM9TakLz2vpUEVpWXRG7hrHNBHiNJRy8LLBPc/pEkVkSkm6YZCGxlpRECFeIp8EAcVZhlU6JBGraTbYMTbD5I1ZlcB/uIZE
3Xys1JZA+xP/ocEDBq9FNwfzJsMAAAAASUVORK5CYII=
"""
    revert_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAABuwAAAbsBOuzj4gAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAAKXSURBVHjahZNLSJRhFIaf/5t/GkfHGJ1GwwsoNV4pDLuZRLSqbW4q
ghYFEkER1CrdZRZFq5BoUYsWERGFEQ502bhIuxiWXUxcKKjJmKNj4+jMfznJb5NjQb1wvsPh+96HFz6OJiIAaJrmB6pheeT/EuCLzoqqI5HIS1sEpZRTS9BVBaS7c5/r8+3KBGi28E85oNURtUwALpdKP/ydQP2ZwoGsJNHPN5UE2h+OTadjjX55xeCbJ4wO9vIjOkVe
QTGbGw9QtXUfwZKKlQRKLffLJ7Z9iEXH73T2zA4fa6p/FCqzqWsox1+Yi9efw2Iszsy3GIP9k0zMFHPozE1cuttJ4Ha7G7UbZ2vNUEVQ9fROyMEj9aq0Jg+w+FuKiaEZOh985fiFp2T7/MuAjjOV5tGTe12WpeFy2wDYCyZGNIoRG0fP9eMJlqDWuACYjcxz//4Yp669
cAC6bQvmooEosG1ITA4yN/KJkVGb6SjkZZmEijT8tbvxrCvE53WzudKiJ3wLACW2YKSSWGaK+PfPzM0OEH7tRVW1srM5TDJ0mvDwRsbfdpOcimDGF9hUXcTTu20AoptLZiMxjcrx4vJl4fX4KS2dp6i8lrLq7ZTX7GAhfo7w9cMEJsfIWr8BsVMUFwaAMVOfiaW+37vT
V+Bao6N73JqmQERn6vEVyqq2k7M236lA5X6mEr0kU43kFoSIB7s/wvt+AK25ud7ty9L3xGajEv8Rk1QqJYZhiGmaYlmW2LYtL7tuS3dnhzNfvdQyDGQ7e7R0OAU0JBLzkpifk2QyuQqwuBCXge678q7nubS3tQwB+WmfYkUOTikNTQORX19qmSRiEYKFAel69qLvfOvF
uiVjNG3KXGcPsAUy9yUTjgn0i4hBhn4Cdg86oqKzQ2YAAAAASUVORK5CYII=
"""
    warning_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAABuwAAAbsBOuzj4gAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAAHRSURBVHjapZK9a1NRGIef99zv3t58VNPoYIUUVx06dlCK+gco4qKL
ukhRl+IiFBy1CFoURHHSRXEQg9g69C/oKlQRB2kpRWoSmzTJ/cgRcsG72LTSB35wDpzz8HsPR7TW7AfFAKKqfTV571xgEFrrfyZetC5Gi6d60dI53V2wLu10Tu0oDt2H5ti0GKXzyLb94L9G6H0ypsU/cwjDQAxQwcnR6KN9a8+CJPbvGgcnWX5yn+Wnj1CF4+jYnN2T
IF6w7sjw5AHYorHylcaXb4jUUP6JkfCDNbu7IAluG8UKsIajpB/UKkYuoBd7MwMFnaq6ZwSVnBjrYG7iBXn8kVFwWojXRBXHgvY7NQcZ2Ud6Jlar6DaGjiaeWBEY8P1VCZ2EjF9rgIBOHJqfdTsYD/NM6AjAhJTmkDw2C64n+TqY9FO58TPtKPSRXherXPCaK9H88ATX
swYvxW90crXcVNsSN0oFFqzPFwA4PFOHHhCD7nr8rkrU8baL5cu6pQAadV7YFS+9bANOGhPBFAGTrIVqYx8LLLfG878jdNeYCs4Gad14AzSQQOlmLV1vpXtigDLOkTzNpc3T2RsY6u2vNz+uhKuhZlc2ELemlR+/zt5gH/wBPa3CSeYgjXsAAAAASUVORK5CYII=
"""
    def init():
        # NOTE: commented icons are currently unused
        #ffIcon.apply_pxm = sQPixmap(imgdata=ffIcon.apply_png)
        #ffIcon.apply = QIcon(ffIcon.apply_pxm)
        ffIcon.broken_pxm = sQPixmap(imgdata=ffIcon.broken_png)
        ffIcon.broken = QIcon(ffIcon.broken_pxm)
        ffIcon.close_pxm = sQPixmap(imgdata=ffIcon.close_png)
        ffIcon.close = QIcon(ffIcon.close_pxm)
        ffIcon.delete_pxm = sQPixmap(imgdata=ffIcon.delete_png)
        ffIcon.delete = QIcon(ffIcon.delete_pxm)
        ffIcon.error_pxm = sQPixmap(imgdata=ffIcon.error_png)
        ffIcon.error = QIcon(ffIcon.error_pxm)
        ffIcon.ffpreview_pxm = sQPixmap(imgdata=ffIcon.ffpreview_png)
        ffIcon.ffpreview = QIcon(ffIcon.ffpreview_pxm)
        #ffIcon.info_pxm = sQPixmap(imgdata=ffIcon.info_png)
        #ffIcon.info = QIcon(ffIcon.info_pxm)
        ffIcon.ok_pxm = sQPixmap(imgdata=ffIcon.ok_png)
        ffIcon.ok = QIcon(ffIcon.ok_pxm)
        ffIcon.open_pxm = sQPixmap(imgdata=ffIcon.open_png)
        ffIcon.open = QIcon(ffIcon.open_pxm)
        #ffIcon.question_pxm = sQPixmap(imgdata=ffIcon.question_png)
        #ffIcon.question = QIcon(ffIcon.question_pxm)
        ffIcon.refresh_pxm = sQPixmap(imgdata=ffIcon.refresh_png)
        ffIcon.refresh = QIcon(ffIcon.refresh_pxm)
        ffIcon.remove_pxm = sQPixmap(imgdata=ffIcon.remove_png)
        ffIcon.remove = QIcon(ffIcon.remove_pxm)
        ffIcon.revert_pxm = sQPixmap(imgdata=ffIcon.revert_png)
        ffIcon.revert = QIcon(ffIcon.revert_pxm)
        #ffIcon.warning_pxm = sQPixmap(imgdata=ffIcon.warning_png)
        #ffIcon.warning = QIcon(ffIcon.warning_pxm)


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
    __slots__ = ['info']
    notify = pyqtSignal(dict)

    def __init__(self, *args, pixmap=None, text=None, info=None, receptor=None, **kwargs):
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
        self.notify.connect(receptor)
        self.adjustSize()
        self.setMaximumSize(self.width(), self.height())

    def mouseReleaseEvent(self, event):
        self.notify.emit({'type': 'set_cursorw', 'id': self})

    def mouseDoubleClickEvent(self, event):
        self.notify.emit({'type': 'play_video', 'ts': self.info[2],
                    'pause': not (QApplication.keyboardModifiers() & Qt.ShiftModifier)})

    def contextMenuEvent(self, event):
        self.notify.emit({'type': 'context_menu', 'id': self, 'pos': self.mapToGlobal(event.pos())})


class tScrollArea(QScrollArea):
    notify = pyqtSignal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delayTimeout = 200
        self._resizeTimer = QTimer(self)
        self._resizeTimer.timeout.connect(self._delayedUpdate)

    def resizeEvent(self, event):
        self._resizeTimer.start(self.delayTimeout)
        super().resizeEvent(event)

    def _delayedUpdate(self):
        self._resizeTimer.stop()
        # ask parent to call our own do_update()
        self.notify.emit({'type': 'scroll_do_update'})

    def do_update(self, tlwidth, tlheight):
        if tlwidth < 1 or tlheight < 1:
            return
        rows = int(self.viewport().height() / tlheight + 0.5)
        self.verticalScrollBar().setSingleStep(int(tlheight / 5.9287))
        cfg['grid_rows'] = rows
        cols = int((self.viewport().width()) / tlwidth)
        if cols < 1:
            cols = 1
        if cols != cfg['grid_columns']:
            cfg['grid_columns'] = cols
            self.notify.emit({'type': 'rebuild_view'})

    def clear_grid(self):
        layout = self.widget().layout()
        while layout.count():
            chw = layout.takeAt(0).widget()
            chw.deleteLater()

    def fill_grid(self, tlabels, progress_cb=None):
        slave = self.widget()
        layout = slave.layout()
        slave.setUpdatesEnabled(False)
        l = len(tlabels)
        x = 0; y = 0; cnt = 0
        for tl in tlabels:
            layout.removeWidget(tl)
            layout.addWidget(tl, y, x)
            if progress_cb and cnt % 100 == 0:
                progress_cb(cnt, l)
            x += 1
            if x >= cfg['grid_columns']:
                x = 0; y += 1
            cnt += 1
        if y < cfg['grid_rows']:
            cfg['grid_rows'] = y + 1
        if y == 0 and x < cfg['grid_columns']:
            cfg['grid_columns'] = x
        slave.setUpdatesEnabled(True)


class tmDialog(QDialog):
    ilist = []
    outdir = ''
    loadfile = ''
    def __init__(self, *args, odir='', **kwargs):
        super().__init__(*args, **kwargs)
        self.outdir = odir
        self.setWindowTitle("Thumbnail Manager")
        self.resize(800, 700)
        self.dlg_layout = QVBoxLayout(self)
        self.loc_label = QLabel(text='Index of ' + self.outdir + '/')
        self.tree_widget = QTreeWidget()
        self.tree_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tree_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree_widget.setRootIsDecorated(False)
        self.tree_widget.setColumnCount(4)
        self.tree_widget.setHeaderLabels(['Name', 'Count', 'Size', 'Date Modified'])
        self.tree_widget.itemDoubleClicked.connect(self.accept)
        self.tree_widget.itemSelectionChanged.connect(self.sel_changed)
        self.tree_widget.setAlternatingRowColors(True)
        self.btn_layout = QHBoxLayout()
        self.load_button = QPushButton("Load Thumbnails")
        self.load_button.setIcon(ffIcon.open)
        self.load_button.clicked.connect(self.accept)
        self.load_button.setEnabled(False)
        self.load_button.setDefault(True)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setIcon(ffIcon.refresh)
        self.refresh_button.clicked.connect(self.refresh_list)
        self.invert_button = QPushButton("Invert Selection")
        self.invert_button.setIcon(ffIcon.revert)
        self.invert_button.clicked.connect(self.invert_selection)
        self.selbroken_button = QPushButton("Select Broken")
        self.selbroken_button.setIcon(ffIcon.remove)
        self.selbroken_button.clicked.connect(self.select_broken)
        self.remove_button = QPushButton("Remove Selected")
        self.remove_button.setIcon(ffIcon.delete)
        self.remove_button.clicked.connect(self.remove)
        self.remove_button.setEnabled(False)
        self.close_button = QPushButton("Close")
        self.close_button.setIcon(ffIcon.close)
        self.close_button.clicked.connect(self.reject)
        self.btn_layout.addWidget(self.refresh_button)
        self.btn_layout.addWidget(self.invert_button)
        self.btn_layout.addWidget(self.selbroken_button)
        self.btn_layout.addWidget(self.remove_button)
        self.btn_layout.addStretch()
        self.btn_layout.addWidget(QLabel('          '))
        self.btn_layout.addWidget(self.load_button)
        self.btn_layout.addWidget(QLabel('     '))
        self.btn_layout.addWidget(self.close_button)
        self.dlg_layout.addWidget(self.loc_label)
        self.dlg_layout.addWidget(self.tree_widget)
        self.dlg_layout.addLayout(self.btn_layout)
        QShortcut('Del', self).activated.connect(self.remove)
        QShortcut('F5', self).activated.connect(self.refresh_list)
        self.refresh_list()

    def accept(self):
        for item in self.tree_widget.selectedItems():
            if item.vfile:
                self.loadfile = item.vfile
                eprint(1, "load file ", item.vfile)
                super().accept()

    def refresh_list(self):
        self.ilist = get_indexfiles(self.outdir)
        self.tree_widget.clear()
        ncols = self.tree_widget.columnCount()
        for entry in self.ilist:
            item = QTreeWidgetItem([entry['tdir'], str(entry['idx']['count']), hr_size(entry['size']),
                                    time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry['idx']['date']))])
            item.setToolTip(0, ppdict(entry['idx'], ['th']))
            item.setTextAlignment(1, Qt.AlignRight|Qt.AlignVCenter)
            item.setTextAlignment(2, Qt.AlignRight|Qt.AlignVCenter)
            if not entry['idx'] or not entry['vfile']:
                font = item.font(0)
                font.setItalic(True)
                for col in range(ncols):
                    item.setForeground(col, QColor('red'))
                    item.setBackground(col, QColor('lightyellow'))
                    item.setFont(col, font)
                item.setIcon(0, ffIcon.error)
            else:
                item.setIcon(0, ffIcon.ok)
            item.vfile = entry['vfile']
            self.tree_widget.addTopLevelItem(item)
        for col in range(ncols):
            self.tree_widget.resizeColumnToContents(col)

    def select_broken(self):
        for i in range(self.tree_widget.topLevelItemCount()):
            item = self.tree_widget.topLevelItem(i)
            item.setSelected(not item.vfile)

    def sel_changed(self):
        sel = self.tree_widget.selectedItems()
        nsel = len(sel)
        self.remove_button.setEnabled(nsel > 0)
        self.load_button.setEnabled(True if nsel==1 and sel[0].vfile else False)

    def invert_selection(self):
        sel = self.tree_widget.selectedItems()
        for i in range(self.tree_widget.topLevelItemCount()):
            self.tree_widget.topLevelItem(i).setSelected(True)
        for i in sel:
            i.setSelected(False)

    def remove(self):
        dirs = [sel.text(0) for sel in self.tree_widget.selectedItems()]
        l = len(dirs)
        if l < 1:
            return
        mbox = QMessageBox(self)
        mbox.setWindowTitle('Remove Thumbnails')
        mbox.setIcon(QMessageBox.Warning)
        mbox.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        mbox.setDefaultButton(QMessageBox.Cancel)
        mbox.setText('Confirm removal of %d thumbnail folder%s.' % (l, 's' if l>1 else ''))
        if QMessageBox.Ok == mbox.exec_():
            for d in dirs:
                rm = os.path.join(self.outdir, d)
                clear_thumbdir(rm)
                eprint(1, "rmdir: ", rm)
                try:
                    os.rmdir(rm)
                except Exception as e:
                    eprint(0, str(e))
                    mbox = QMessageBox(self)
                    mbox.setWindowTitle('Directory Removal Failed')
                    mbox.setIcon(QMessageBox.Critical)
                    mbox.setStandardButtons(QMessageBox.Ok)
                    mbox.setText(re.sub('^\[.*\]\s*', '', str(e)).replace(':', ':\n\n', 1))
                    mbox.exec_()
            self.refresh_list()

    def get_loadfile(self):
        return self.loadfile


class sMainWindow(QMainWindow):
    """ Application main window class singleton. """
    _instance = None
    px = 50
    py = 50
    tlwidth = 0
    tlheight = 0
    tlabels = []
    thinfo = None
    fname = None
    vfile = None
    vpath = None
    thdir = None
    cur = 0
    _dbg_num_tlabels = 0
    _dbg_num_qobjects = 0
    view_locked = False

    def __new__(cls, *args, title='', **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self, *args, title='', **kwargs):
        super().__init__(*args, **kwargs)
        ffIcon.init()
        self.init_window(title)

    def closeEvent(self, event):
        if type(event) == QCloseEvent:
            event.accept()
        die(0)

    def optimize_extent(self):
        if self.tlwidth > 0 and self.tlheight > 0:
            w = self.tlwidth * cfg['grid_columns'] + self.px
            h = self.tlheight * cfg['grid_rows'] + self.py
            self.resize(w, h)

    def rebuild_view(self):
        self.scroll.fill_grid(self.tlabels, self.show_progress)
        self.set_cursor()

    def clear_view(self):
        self.scroll.clear_grid()
        self.cur = 0
        if self.tlabels:
            self.tlabels.clear()

    def set_cursor(self, idx=None):
        l = len(self.tlabels)
        if l < 1:
            self.cur = 0
            return
        try:
            self.tlabels[self.cur].setStyleSheet('QLabel {}')
            self.cur = min(max(0, self.cur if idx is None else idx), l - 1)
            self.tlabels[self.cur].setStyleSheet( 'QLabel {' + cfg['selstyle'] + '}' )
            self.statdsp[3].setText('%d / %d' % (self.tlabels[self.cur].info[0], l))
            self.scroll.ensureWidgetVisible(self.tlabels[self.cur], 0, 0)
        except:
            pass

    def set_cursorw(self, label):
        try:
            self.set_cursor(idx=self.tlabels.index(label))
        except:
            pass

    def move_cursor(self, amnt):
        self.set_cursor(self.cur + amnt)

    def toggle_fullscreen(self):
        if self.windowState() & Qt.WindowFullScreen:
            self.showNormal()
            for w in self.statdsp:
                w.show()
        else:
            self.showFullScreen()
            for w in self.statdsp:
                w.hide()

    def esc_action(self):
        if self.view_locked:
            self.abort_build()
        elif self.windowState() & Qt.WindowFullScreen:
            self.toggle_fullscreen()
        else:
            self.closeEvent(None)

    def contextMenuEvent(self, event):
        tlabel = None
        if event:
            # genuine click on canvas
            pos = event.pos()
        elif len(self.tlabels) > 0:
            # kbd shortcut, show context menu for active label
            tlabel = self.tlabels[self.cur]
            pos = tlabel.pos()
            pos.setX(pos.x() + self.tlwidth / 2)
            pos.setY(pos.y() + self.tlheight / 2)
        else:
            # kbd shortcut, have no active label
            pos = QPoint(self.width()/2, self.height()/2)
        self.show_contextmenu(tlabel, self.mapToGlobal(pos))

    def show_contextmenu(self, tlabel, pos):
        menu = QMenu()
        if not self.view_locked:
            if tlabel:
                self.set_cursorw(tlabel)
                menu.addAction('Play From Here', lambda: self._play_video(ts=tlabel.info[2]))
            if self.fname:
                menu.addAction('Play From Start', lambda: self._play_video(ts='0'))
            menu.addSeparator()
            if tlabel:
                menu.addAction('Copy Timestamp [H:M:S.ms]', lambda: self.clipboard.setText(s2hms(tlabel.info[2])))
                menu.addAction('Copy Timestamp [S.ms]', lambda: self.clipboard.setText(tlabel.info[2]))
            if self.fname:
                menu.addAction('Copy Original Filename', lambda: self.clipboard.setText(self.fname))
            if tlabel:
                menu.addAction('Copy Thumb Filename', lambda: self.clipboard.setText(os.path.join(self.thdir, tlabel.info[1])))
                menu.addAction('Copy Thumbnail Image', lambda: self.clipboard.setPixmap(tlabel.layout().itemAt(0).widget().pixmap()))
            menu.addSeparator()
            menu.addAction('Optimize Window Extent', self.optimize_extent)
            if self.fname:
                menu.addAction('Force Rebuild', self.force_rebuild)
            menu.addAction('Open Video File...', lambda: self.load_view(self.vpath))
            menu.addAction('Thumbnail Manager', lambda: self.manage_thumbs(cfg['outdir']))
        else:
            menu.addAction('Abort Operation', self.abort_build)
        menu.addSeparator()
        menu.addAction('Quit', lambda: self.closeEvent(None))
        menu.exec_(pos)

    def manage_thumbs(self, outdir):
        if self.view_locked:
            return
        dlg = tmDialog(self, odir=cfg['outdir'])
        res = dlg.exec_()
        if res == QDialog.Accepted:
            lfile = dlg.get_loadfile()
            if lfile:
                self.load_view(lfile)

    def _play_video(self, ts=None, paused=False):
        if self.view_locked:
            return
        if ts is None:
            if len(self.tlabels) < 1:
                return
            ts = self.tlabels[self.cur].info[2]
        play_video(self.fname, ts, paused)

    # handle various notifications emitted by downstream widgets
    @pyqtSlot(dict)
    def notify_receive(self, event):
        eprint(4, 'got event: ', event)
        if event['type'] == 'set_cursorw':
            self.set_cursorw(event['id'])
        elif event['type'] == 'context_menu':
            self.show_contextmenu(event['id'], event['pos'])
        elif event['type'] == 'rebuild_view':
            self.rebuild_view()
        elif event['type'] == 'scroll_do_update':
            self.scroll.do_update(self.tlwidth, self.tlheight)
        elif event['type'] == 'play_video':
            self._play_video(ts=event['ts'], paused=event['pause'])
        elif event['type'] == '_dbg_count':
            self._dbg_num_tlabels = len(self.findChildren(tLabel))
            self._dbg_num_qobjects = len(self.findChildren(QObject))
        else:
            eprint(0, 'event not handled: ', event)

    def init_window(self, title):
        self.setWindowTitle(title)
        self.setWindowIcon(ffIcon.ffpreview)
        self.resize(500, 300)
        self.setStyleSheet(cfg['appstyle'])
        self.clipboard = QApplication.clipboard()
        # set up status bar
        statbar = QHBoxLayout()
        self.statdsp = []
        for i in range(4):
            s = QLabel('')
            s.resize(100, 20)
            s.setStyleSheet('QLabel {margin: 0px 2px 0px 2px;}');
            self.statdsp.append(s)
            statbar.addWidget(s)
        self.progbar = QProgressBar()
        self.progbar.resize(100, 20)
        self.progbar.hide()
        statbar.addWidget(self.progbar)
        # set up thumbnail view area
        thumb_frame = QWidget()
        thumb_layout = QGridLayout(thumb_frame)
        thumb_layout.setContentsMargins(0, 0, 0, 0)
        thumb_layout.setSpacing(0)
        self.scroll = tScrollArea()
        self.scroll.notify.connect(self.notify_receive)
        self.scroll.setWidget(thumb_frame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet('QFrame {border: none;}')
        # set up main window layout
        main_frame = QWidget()
        main_layout = QVBoxLayout(main_frame)
        main_layout.setContentsMargins(0, 2, 0, 0)
        main_layout.addWidget(self.scroll)
        main_layout.addLayout(statbar)
        self.setCentralWidget(main_frame)
        # set tooltip colors
        palette = QToolTip.palette()
        palette.setColor(QPalette.ToolTipBase,QColor('#fffff0'))
        palette.setColor(QPalette.ToolTipText,QColor('#606060'))
        QToolTip.setPalette(palette)
        # register shotcuts
        QShortcut('Esc', self).activated.connect(self.esc_action)
        QShortcut('Ctrl+Q', self).activated.connect(lambda: self.closeEvent(None))
        QShortcut('Ctrl+W', self).activated.connect(lambda: self.closeEvent(None))
        QShortcut('F', self).activated.connect(self.toggle_fullscreen)
        QShortcut('Alt+Return', self).activated.connect(self.toggle_fullscreen)
        QShortcut('Ctrl+G', self).activated.connect(self.optimize_extent)
        QShortcut('Ctrl+O', self).activated.connect(lambda: self.load_view(self.vpath))
        QShortcut('Ctrl+M', self).activated.connect(lambda: self.manage_thumbs(cfg['outdir']))
        QShortcut('Tab', self).activated.connect(lambda: self.move_cursor(1))
        QShortcut('Shift+Tab', self).activated.connect(lambda: self.move_cursor(-1))
        QShortcut('Right', self).activated.connect(lambda: self.move_cursor(1))
        QShortcut('Left', self).activated.connect(lambda: self.move_cursor(-1))
        QShortcut('Up', self).activated.connect(lambda: self.move_cursor(-cfg['grid_columns']))
        QShortcut('Down', self).activated.connect(lambda: self.move_cursor(cfg['grid_columns']))
        QShortcut('PgUp', self).activated.connect(lambda: self.move_cursor(-cfg['grid_rows'] * cfg['grid_columns']))
        QShortcut('PgDown', self).activated.connect(lambda: self.move_cursor(cfg['grid_rows'] * cfg['grid_columns']))
        QShortcut('Home', self).activated.connect(lambda: self.set_cursor(0))
        QShortcut('End', self).activated.connect(lambda: self.set_cursor(len(self.tlabels)-1))
        QShortcut('Space', self).activated.connect(lambda: self._play_video(paused=True))
        QShortcut('Return', self).activated.connect(lambda: self._play_video(paused=True))
        QShortcut('Shift+Return', self).activated.connect(lambda: self._play_video())
        QShortcut('Ctrl+Return', self).activated.connect(lambda: self.contextMenuEvent(None))

    def show_progress(self, n, tot):
        self.statdsp[1].setText('%d / %d' % (n, tot))
        self.progbar.setValue(int(n * 100 / max(0.01, tot)))
        QApplication.processEvents()

    # generate clickable thumbnail labels
    def make_tlabels(self, tlabels):
        dummy_thumb = ffIcon.broken_pxm.scaledToWidth(cfg['thumb_width'])
        tlabels.clear()
        try:
            with open(os.path.join(self.thdir, _FFPREVIEW_IDX), 'r') as idxfile:
                idx = json.load(idxfile)
                if cfg['verbosity'] > 3:
                    eprint(4, 'idx = ' + json.dumps(idx, indent=2))
                self.show_progress(0, idx['count'])
                for th in idx['th']:
                    if th[0] % 100 == 0:
                        self.show_progress(th[0], idx['count'])
                    thumb = QPixmap(os.path.join(self.thdir, th[1]))
                    if thumb.isNull():
                        thumb = dummy_thumb
                    tlabel = tLabel(pixmap=thumb, text=s2hms(th[2]),
                                    info=th, receptor=self.notify_receive)
                    tlabels.append(tlabel)
        except Exception as e:
            eprint(0, str(e))
        if len(tlabels) == 0:
            # no thumbnails available, make a dummy
            tlabels.append(tLabel(pixmap=dummy_thumb, text=s2hms(str(cfg['start'])),
                            info=[0, 'broken', str(cfg['start'])],
                            receptor=self.notify_receive))

    def abort_build(self):
        mbox = QMessageBox(self)
        mbox.setWindowTitle('Abort Process')
        mbox.setIcon(QMessageBox.Warning)
        mbox.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        mbox.setDefaultButton(QMessageBox.No)
        mbox.setText('Aborting now will likely leave you with a broken or '
                     'incomplete set of thumbnails.\n\nAbort anyway?')
        if QMessageBox.Yes == mbox.exec_():
            kill_proc()

    def force_rebuild(self):
        if self.thinfo['duration'] > 300:
            mbox = QMessageBox(self)
            mbox.setWindowTitle('Rebuild Thumbnails')
            mbox.setIcon(QMessageBox.Warning)
            mbox.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            mbox.setDefaultButton(QMessageBox.No)
            mbox.setText('Rebuilding thumbnails may take a while.\n\nAre you sure?')
            rebuild = (mbox.exec_() == QMessageBox.Yes)
        else:
            rebuild = True
        if rebuild:
            cfg['force'] = True
            self.load_view(self.fname)
            cfg['force'] = False

    def load_view(self, fname):
        if self.view_locked:
            return
        self.view_locked = True
        # sanitize file name
        if not fname:
            fname = os.getcwd()
        if not os.path.exists(fname) or not os.access(fname, os.R_OK):
            fname = os.path.dirname(fname)
            if not os.path.isdir(fname):
                fname = os.getcwd()
        if os.path.isdir(fname):
            fname, _ = QFileDialog.getOpenFileName(self, 'Open File', fname,
                        'Video Files ('+ cfg['vformats'] +');;All Files (*)',
                        options=QFileDialog.Options()|QFileDialog.DontUseNativeDialog)
        if not fname or not os.path.exists(fname) or not os.access(fname, os.R_OK):
            self.view_locked = False
            return
        self.fname = os.path.abspath(fname)
        self.vfile = os.path.basename(self.fname)
        self.vpath = os.path.dirname(self.fname)
        self.thdir = os.path.abspath(os.path.join(cfg['outdir'], self.vfile))
        self.setWindowTitle('ffpreview - ' + self.vfile)
        # clear previous view
        for sd in self.statdsp:
            sd.setText('')
            sd.setToolTip('')
        self.statdsp[0].setText('Clearing view')
        QApplication.processEvents()
        self.clear_view()
        # analyze video
        self.statdsp[0].setText('Analyzing')
        QApplication.processEvents()
        if self.thinfo:
            self.thinfo.clear()
        self.thinfo, ok = get_thinfo(self.fname, self.thdir)
        if self.thinfo is None:
            self.statdsp[0].setText('Unrecognized file format')
            self.view_locked = False
            return
        if not ok:
            # (re)generate thumbnails and index file
            self.statdsp[0].setText('Processing')
            clear_thumbdir(self.thdir)
            self.progbar.show()
            self.thinfo, ok = make_thumbs(fname, self.thinfo, self.thdir, self.show_progress)
        # load thumbnails and make labels
        self.statdsp[0].setText('Loading')
        self.progbar.show()
        self.make_tlabels(self.tlabels)
        # roughly fix window geometry
        self.tlwidth = self.tlabels[0].width()
        self.tlheight = self.tlabels[0].height()
        w = self.tlwidth * cfg['grid_columns'] + self.px
        h = self.tlheight * cfg['grid_rows'] + self.py
        self.resize(w, h)
        # build thumbnail view
        tooltip = ppdict(self.thinfo, ['th'])
        for sd in self.statdsp:
            self.statdsp[2].setText('')
            sd.setToolTip(tooltip)
        self.statdsp[0].setText('Building view')
        QApplication.processEvents()
        self.rebuild_view()
        self.set_cursor(0)
        self.progbar.hide()
        QApplication.processEvents()
        # final window touch-up
        self.statdsp[0].setText(s2hms(self.thinfo['duration']))
        self.statdsp[1].setText(str(self.thinfo['method']))
        QApplication.processEvents()
        # calculate the actual window extent surplus WRT to viewport
        self.px = self.width() - self.scroll.viewport().width()
        self.py = self.height() - self.scroll.viewport().height()
        # set window size
        self.setMinimumSize(self.tlwidth + self.px, self.tlheight + self.py)
        self.optimize_extent()
        self.view_locked = False


############################################################
# Helper functions

# get video meta information
def get_meta(vidfile):
    global proc
    meta = { 'frames': -1, 'duration':-1, 'fps':-1.0 }
    if proc:
        return meta, False
    # try ffprobe method
    try:
        cmd = cfg['ffprobe'] + ' -v error -select_streams v:0 -of json -count_packets'
        cmd += ' -show_entries format=duration:stream=nb_read_packets'
        cmd += ' "' + vidfile + '"'
        eprint(2, cmd)
        proc = Popen(cfg['exec'] + ' ' + cmd, shell=True, stdout=PIPE, stderr=PIPE, env=cfg['env'])
        stdout, stderr = proc.communicate()
        retval = proc.wait()
        proc = None
        if retval == 0:
            info = json.loads(stdout.decode())
            meta['frames'] = int(info['streams'][0]['nb_read_packets'])
            d = float(info['format']['duration'])
            meta['duration'] = d
            meta['fps'] = round(meta['frames'] / d, 2)
            return meta, True
        else:
            eprint(0, cmd + '\n  returned %d' % retval)
            eprint(1, stderr.decode())
    except Exception as e:
        eprint(0, cmd + '\n  failed: ' + str(e))
        proc = kill_proc(proc)
    # ffprobe didn't cut it, try ffmpeg instead
    try:
        cmd = cfg['ffmpeg'] + ' -nostats -i "' + vidfile + '"'
        cmd += ' -c:v copy -f rawvideo -y ' + os.devnull
        eprint(2, cmd)
        proc = Popen(cfg['exec'] + ' ' + cmd, shell=True, stdout=PIPE, stderr=PIPE, env=cfg['env'])
        stdout, stderr = proc.communicate()
        retval = proc.wait()
        proc = None
        if retval == 0:
            for line in io.StringIO(stderr.decode()).readlines():
                m = re.match(r'^frame=\s*(\d+).*time=\s*(\d+:\d+:\d+(\.\d+)?)', line)
                if m:
                    meta['frames'] = int(m.group(1))
                    d = hms2s(m.group(2))
                    meta['duration'] = d
                    meta['fps'] = round(meta['frames'] / d, 2)
                    return meta, True
        else:
            eprint(0, cmd + '\n  returned %d' % retval)
            eprint(1, stderr.decode())
    except Exception as e:
        eprint(0, cmd + '\n  failed: ' + str(e))
        proc = kill_proc(proc)
    return meta, False

# extract thumbnails from video and collect timestamps
def make_thumbs(vidfile, thinfo, thdir, prog_cb=None):
    global proc
    rc = False
    if proc:
        return thinfo, rc
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
    cmd += ' -vsync vfr "' + os.path.join(thdir, pictemplate) + '"'
    eprint(2, cmd)
    ebuf = ''
    cnt = 0
    try:
        proc = Popen(cfg['exec'] + ' ' + cmd, shell=True, stderr=PIPE, env=cfg['env'])
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
                    if prog_cb and cnt % 10 == 0:
                        prog_cb(float(t), thinfo['duration'])
        retval = proc.wait()
        proc = None
        if retval != 0:
            eprint(0, cmd + '\n  returned %d' % retval)
            eprint(1, ebuf)
        thinfo['count'] = cnt
        with open(os.path.join(thdir, _FFPREVIEW_IDX), 'w') as idxfile:
            thinfo['date'] = int(time.time())
            json.dump(thinfo, idxfile, indent=2)
            rc = True
    except Exception as e:
        eprint(0, cmd + '\n  failed: ' + str(e))
        proc = kill_proc(proc)
    return thinfo, rc

# open video in player
def play_video(filename, start='0', paused=False):
    if not filename:
        return
    if paused and cfg['plpaused']:
        cmd = cfg['plpaused']
    else:
        cmd = cfg['player']
    cmd = cmd.replace('%t', '"' + start + '"')
    cmd = cmd.replace('%f', '"' + filename + '"')
    eprint(2, cmd)
    Popen(cfg['exec'] + ' ' + cmd, shell=True, stdout=DEVNULL, stderr=DEVNULL,
            env=cfg['env'], start_new_session=True)

# check validity of existing index file
def chk_idxfile(thinfo, thdir):
    idxpath = os.path.join(thdir, _FFPREVIEW_IDX)
    try:
        with open(idxpath, 'r') as idxfile:
            idx = json.load(idxfile)
            if idx['name'] != thinfo['name']:
                return False
            if int(idx['duration']) != int(thinfo['duration']):
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
        eprint(1, idxpath, str(e))
        pass
    return False

# initialize thumbnail info structure
def get_thinfo(vfile, thdir):
    thinfo = {
        'name': os.path.basename(vfile),
        'path': os.path.dirname(vfile),
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
        'ffpreview': _FFPREVIEW_VERSION,
        'th': []
    }
    meta, ok = get_meta(vfile)
    if not ok:
        return None, False
    thinfo.update(meta)
    if not cfg['force']:
        chk = chk_idxfile(thinfo, thdir)
        if chk:
            return chk, True
    return thinfo, False

# clear out thumbnail directory
def clear_thumbdir(thdir):
    if os.path.dirname(thdir) != cfg['outdir']:
        eprint(0, 'clearing of directory %s denied' % thdir)
        return False
    # prepare thumbnail directory
    eprint(2, 'clearing out %s' % thdir)
    try:
        os.makedirs(thdir, exist_ok=True)
    except Exception as e:
        eprint(0, str(e))
        return False
    f = os.path.join(thdir, _FFPREVIEW_IDX)
    if os.path.exists(f):
        try:
            os.unlink(f)
        except Exception as e:
            eprint(0, str(e))
            pass
    for f in os.listdir(thdir):
        if re.match('^\d{8}\.png$', f):
            try:
                os.unlink(os.path.join(thdir, f))
            except Exception as e:
                eprint(0, str(e))
                pass

# process a single file in console-only mode
def batch_process(fname):
    def cons_progress(n, tot):
        print('\r%4d / %4d' % (int(n), int(tot)), end='', file=sys.stderr)
        if tot > 0:
            print(' %3d %%' % int(n * 100 / tot), end='', file=sys.stderr)

    # sanitize file name
    if not os.path.exists(fname) or not os.access(fname, os.R_OK):
        eprint(0, '%s: no permission' % fname)
        return False
    if os.path.isdir(fname):
        eprint(0, '%s is a directory!' % fname)
        return False
    fname = os.path.abspath(fname)
    vfile = os.path.basename(fname)
    thdir = os.path.join(cfg['outdir'], vfile)
    # analyze video
    print('Analyzing  %s ...\r' % vfile, end='', file=sys.stderr)
    thinfo, ok = get_thinfo(fname, thdir)
    if thinfo is None:
        print('\nFailed.', file=sys.stderr)
        return False
    # prepare info and thumbnail files
    if not ok:
        # (re)generate thumbnails and index file
        print('Processing', file=sys.stderr)
        clear_thumbdir(thdir)
        thinfo, ok = make_thumbs(fname, thinfo, thdir, cons_progress)
        print('\r                                  \r', end='', file=sys.stderr)
    else:
        print('', file=sys.stderr)
    if ok:
        print('Ok.        ', file=sys.stderr)
    else:
        print('Failed.    ', file=sys.stderr)
    return ok

# get list of all index files for thumbnail manager
def get_indexfiles(path):
    flist = []
    for sd in os.listdir(path):
        d = os.path.join(path, sd)
        if not os.path.isdir(d):
            continue
        entry = { 'tdir': sd, 'idx': None, 'vfile': '', 'size': 0 }
        fidx = os.path.join(d, _FFPREVIEW_IDX)
        if os.path.isfile(fidx):
            with open(fidx, 'r') as idxfile:
                try:
                    idx = json.load(idxfile)
                except Exception as e:
                    eprint(1, fidx, str(e))
                    idx = {}
                else:
                    idx['th'] = None
                    entry['idx'] = idx.copy()
                    if 'name' in idx and 'path' in idx:
                        opath = os.path.join(idx['path'], idx['name'])
                        if os.path.isfile(opath):
                            entry['vfile'] = opath
        sz = cnt = 0
        for f in os.listdir(d):
            if re.match('^\d{8}\.png$', f):
                cnt += 1
                try:
                    sz += os.path.getsize(os.path.join(d, f))
                except:
                    pass
        entry['size'] = sz
        if not entry['idx']:
            entry['idx'] = { 'count': cnt, 'date': int(os.path.getmtime(d)) }
        flist.append(entry)
    flist = sorted(flist, key=lambda k: k['tdir'])
    if cfg['verbosity'] > 2:
        eprint(3, json.dumps(flist, indent=2))
    return flist

############################################################
# main function

def main():
    # initialization
    global proc, cfg
    proc = None
    cfg = configure()
    if cfg['verbosity'] > 2:
        eprint(3, 'cfg = ' + json.dumps(cfg, indent=2))

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)
    if cfg['platform'] != 'Windows':
        signal.signal(signal.SIGHUP, sig_handler)
        signal.signal(signal.SIGQUIT, sig_handler)
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    global _FF_DEBUG
    if os.environ.get('FFDEBUG'):
        _FF_DEBUG = True

    # run in console batch mode, if requested
    if cfg['batch']:
        errcnt = 0
        for fn in cfg['vid']:
            if not batch_process(fn):
                errcnt += 1
        die(errcnt)

    # set up window
    if not _FF_DEBUG:
        os.environ['QT_LOGGING_RULES'] = 'qt5ct.debug=false'
    app = QApplication(sys.argv)
    app.setApplicationName('ffpreview')
    root = sMainWindow(title='ffpreview %s' % _FFPREVIEW_VERSION)

    # start console debugging thread, if _FF_DEBUG is set
    if _FF_DEBUG:
        import threading, resource, gc
        global _ffdbg_thread, _ffdbg_run
        gc.set_debug(gc.DEBUG_SAVEALL)

        class _dbgProxy(QObject):
            notify = pyqtSignal(dict)
            def __init__(self, *args, receptor=None, **kwargs):
                super().__init__(*args, **kwargs)
                self.notify.connect(receptor)
            def ping(self):
                self.notify.emit({'type': '_dbg_count'})

        def _ffdbg_update(*args):
            tstart = time.time()
            dbg_proxy = _dbgProxy(receptor=root.notify_receive)
            def p(*args):
                print(*args, file=sys.stderr)
            while _ffdbg_run:
                gc.collect()
                time.sleep(0.5)
                dbg_proxy.ping()
                time.sleep(0.5)
                p('----- %.3f -----' % (time.time()-tstart))
                p('max rss:', resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, 'KiB')
                p('tLabel :', args[0]._dbg_num_tlabels)
                p('QObject:', args[0]._dbg_num_qobjects)
                p('gc cnt :', gc.get_count())
            p('gc gen0:', gc.get_stats()[0])
            p('gc gen1:', gc.get_stats()[1])
            p('gc gen2:', gc.get_stats()[2])
        _ffdbg_thread = threading.Thread(target=_ffdbg_update, args=(root,))
        _ffdbg_run = True
        _ffdbg_thread.start()

    # start in selected mode of operation, run main loop
    root.show()
    if cfg['manage']:
        root.manage_thumbs(cfg['outdir'])
    else:
        root.load_view(cfg['vid'][0])
        # reset force flag to avoid accidental rebuild for every file
        cfg['force'] = False
    die(app.exec_())

# run application
if __name__== "__main__":
    main()

# EOF
