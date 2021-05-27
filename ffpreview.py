#!/usr/bin/env python3

"""
ffpreview.py

Copyright (c) 2021 Urban Wallasch <irrwahn35@freenet.de>

Ffpreview is distributed under the Modified ("3-clause") BSD License.
See `LICENSE` file for more information.
"""

_FFPREVIEW_VERSION = '0.3+'

_FFPREVIEW_IDX = 'ffpreview.idx'

_FFPREVIEW_CFG = 'ffpreview.conf'

_FF_DEBUG = False

_FFPREVIEW_HELP = """
<style>
  td {padding: 0.5em 0em 0em 0.5em;}
  td.m {font-family: mono;}
</style>
<h3>Keyboard Shortcuts</h3>
<table><tbody><tr>
      <td class="m" width="30%">
      Up, Down, PgUp, PgDown, Home, End, TAB, Shift+TAB</td>
      <td>Navigate thumbnails</td>
    </tr><tr>
      <td class="m">Double-click, Return, Space</td>
      <td>Open video at selected position in paused state</td>
    </tr><tr>
      <td class="m">Shift+dbl-click, Shift+Return</td>
      <td>Play video starting at selected position</td>
    </tr><tr>
      <td class="m">Mouse-2, Menu, Alt+Return</td>
      <td>Open the context menu</td>
    </tr><tr>
      <td class="m">ESC</td>
      <td>Exit full screen view; quit application</td>
    </tr><tr>
      <td class="m">Ctrl+Q, Ctrl-W</td>
      <td>Quit application</td>
    </tr><tr>
      <td class="m">Alt+Return, F</td>
      <td>Toggle full screen view</td>
    </tr><tr>
      <td class="m">Ctrl+G</td>
      <td>Adjust window geometry for optimal fit</td>
    </tr><tr>
      <td class="m">Ctrl+O</td>
      <td>Show open file dialog</td>
    </tr><tr>
      <td class="m">Ctrl+M</td>
      <td>Open thumbnail manager</td>
    </tr>
</tbody></table>
"""

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
import shlex
import base64
from copy import deepcopy
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
from PyQt5.QtGui import *
from inspect import currentframe

############################################################
# utility functions

def eprint(lvl, *args, vo=0, **kwargs):
    v = cfg['verbosity'] if 'cfg' in globals() else vo
    if lvl <= v:
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

def s2hms(ts, frac=True, zerohours=False):
    s, ms = divmod(float(ts), 1.0)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    res = '' if h < 1 and zerohours == False else '%02d:' % h
    res += '%02d:%02d' % (m, s)
    res += '' if not frac else ('%.3f' % ms).lstrip('0')
    return res

def str2bool(s):
    if type(s) == type(True):
        return s
    if s and type(s) == type(' '):
        return s.lower() in ['true', '1', 'on', 'y', 'yes']
    return False

def str2int(s):
    if type(s) == type(1):
        return s
    if s and type(s) == type(' '):
        return int(re.match(r'^\s*([+-]?\d+)', s).groups()[0])
    return 0

def str2float(s):
    if type(s) == type(1.1):
        return s
    if s and type(s) == type(' '):
        m = re.match(r'^\s*([+-]?([0-9]+([.][0-9]*)?|[.][0-9]+))', s)
        return float(m.groups()[0])
    return 0.0

def sfrac2float(s):
    a = s.split('/')
    d = 1 if len(a) < 2 else str2float(a[1])
    return str2float(a[0]) / (d if d else 1)

def hr_size(sz, prec=1):
    i = 0
    while sz >= 1024:
        sz /= 1024
        i += 1
    prec = prec if i else 0
    return '%.*f %s' % (prec, sz, ['', 'KiB', 'MiB', 'GiB', 'TiB'][i])

def ppdict(dic, excl=[]):
    s = ''
    with io.StringIO() as sf:
        for k, v in dic.items():
            if v is not None and not k in excl:
                print(k+':', v, file=sf)
        s = sf.getvalue()
    return s.strip()

def proc_running():
    global proc
    return proc is not None

def kill_proc(p=None):
    if p is None and 'proc' in globals():
        global proc
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
    kill_proc()
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

class ffConfig:
    """ Configuration class with only class attributes, not instantiated."""
    cfg = None
    cfg_dflt = {
        'conffile': _FFPREVIEW_CFG,
        'vid': [''],
        'outdir': '',
        'grid_columns': 5,
        'grid_rows': 4,
        'thumb_width': 192,
        'ffprobe': 'ffprobe',
        'ffmpeg': 'ffmpeg',
        'player': 'mpv --no-ordered-chapters --start=%t %f',
        'plpaused': 'mpv --no-ordered-chapters --start=%t --pause %f',
        'force': 'False',
        'reuse': 'False',
        'method': 'iframe',
        'frame_skip': 200,
        'time_skip': 60,
        'scene_thresh': '0.2',
        'customvf': 'scdet=s=1:t=12',
        'start': 0,
        'end': 0,
        'addss': -1,
        'verbosity': 0,
        'batch': 0,
        'manage': 0,
        'platform': platform.system(),
        'env': os.environ.copy(),
        'vformats': '*.3g2 *.3gp *.asf *.avi *.divx *.evo *.f4v *.flv *'
                    '.m2p *.m2ts *.mkv *.mk3d *.mov *.mp4 *.mpeg *.mpg '
                    '*.ogg *.ogv *.ogv *.qt *.rmvb *.vob *.webm *.wmv'
    }
    def __new__(cls):
        if cls.cfg is None:
            cls.init()
        return cls

    @classmethod
    def init(cls):
        # initialize default values
        if cls.cfg_dflt['platform'] == 'Windows':
            cls.cfg_dflt['env']['PATH'] = sys.path[0] + os.pathsep + cls.cfg_dflt['env']['PATH']
        cfg = cls.get_defaults()
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
                   '  Ctrl+Alt+P        open preferences dialog\n'
                   '  Alt+H             open about dialog\n'
                   '  Double-click,\n'
                   '  Return, Space     open video at selected position in paused state\n'
                   '  Shift+dbl-click,\n'
                   '  Shift+Return      play video starting at selected position\n'
                   '  Mouse-2, Menu,\n'
                   '  Ctrl+Return       open the context menu\n'
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
        parser.add_argument('-a', '--addss', nargs='?', type=int, const=0, metavar='N', help='add subtitles from stream N')
        parser.add_argument('-v', '--verbose', action='count', help='be more verbose; repeat to increase')
        parser.add_argument('--version', action='count', help='print version info and exit')
        args = parser.parse_args()
        # if requested print only version and exit
        if args.version:
            print('ffpreview version %s running on python %.1f.x (%s)'
                    % (_FFPREVIEW_VERSION, _PYTHON_VERSION, cfg['platform']))
            die(0)
        # parse config file
        vo = args.verbose if args.verbose else 0
        if args.config:
            cfg['conffile'] = args.config
            cls.load_cfgfile(cfg, cfg['conffile'], vo)
        else:
            cdirs = [ os.path.dirname(os.path.realpath(__file__)) ]
            if os.environ.get('APPDATA'):
                cdirs.append(os.environ.get('APPDATA'))
            if os.environ.get('XDG_CONFIG_HOME'):
                cdirs.append(os.environ.get('XDG_CONFIG_HOME'))
            if os.environ.get('HOME'):
                cdirs.append(os.path.join(os.environ.get('HOME'), '.config'))
            for d in cdirs:
                cf = os.path.join(d, _FFPREVIEW_CFG)
                if cls.load_cfgfile(cfg, cf, vo):
                    cfg['conffile'] = cf
                    break
        # evaluate remaining command line args
        cfg['vid'] = args.filename
        if args.outdir:
            cfg['outdir'] = args.outdir
        if args.start:
            cfg['start'] = hms2s(args.start)
        if args.end:
            cfg['end'] = hms2s(args.end)
        if args.addss is not None:
            cfg['addss'] = args.addss
        if args.grid:
            grid = re.split(r'[xX,;:]', args.grid)
            cfg['grid_columns'] = int(grid[0])
            if len(grid) > 1:
                cfg['grid_rows'] = int(grid[1])
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
        # prepare output directory
        if not cfg['outdir']:
            cfg['outdir'] = tempfile.gettempdir()
        cfg['outdir'] = make_outdir(cfg['outdir'])
        eprint(1, 'outdir =', cfg['outdir'])
        # commit to successfully prepared config
        cls.fixup_cfg(cfg)
        return cls.set(cfg)

    @classmethod
    def load_cfgfile(cls, cfg, fname, vo=1):
        fconf = ConfigParser(allow_no_value=True, defaults=cfg)
        try:
            cf = fconf.read(fname)
            for option in fconf.options('Default'):
                cfg[option] = fconf.get('Default', option)
        except Exception as e:
            eprint(1, str(e), '(config file', fname, 'missing or corrupt)', vo=vo)
            return False
        eprint(1, 'read config from', fname, vo=vo)
        return cls.fixup_cfg(cfg)

    @classmethod
    def fixup_cfg(cls, cfg):
        # fix up types of non-string options
        cfg['force'] = str2bool(cfg['force'])
        cfg['reuse'] = str2bool(cfg['reuse'])
        cfg['grid_rows'] = str2int(cfg['grid_rows'])
        cfg['grid_columns'] = str2int(cfg['grid_columns'])
        cfg['thumb_width'] = str2int(cfg['thumb_width'])
        cfg['frame_skip'] = str2int(cfg['frame_skip'])
        cfg['time_skip'] = str2float(cfg['time_skip'])
        cfg['scene_thresh'] = str2float(cfg['scene_thresh'])
        cfg['start'] = str2float(cfg['start'])
        cfg['end'] = str2float(cfg['end'])
        cfg['addss'] = str2int(cfg['addss'])
        return True

    @classmethod
    def get(cls):
        return cls.cfg

    @classmethod
    def set(cls, newcfg=None):
        if cls.cfg:
            cls.cfg.clear()
        cls.update(newcfg)
        return cls.cfg

    @classmethod
    def update(cls, updcfg=None):
        if cls.cfg is None:
            cls.cfg = {}
        if updcfg:
            cls.cfg.update(deepcopy(updcfg))
        return cls.cfg

    @classmethod
    def get_defaults(cls):
        return deepcopy(cls.cfg_dflt)


############################################################
# Qt classes

class ffIcon:
    """ Icon resource storage with only class attributes, not instantiated."""
    initialized = False
    apply_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAABDlBMVEX///8ATwAATAAASQAATQAOaAsBWwEATgARaw4AWwAATgASaxAEUwQATAAVaxITZBIATAAshCMEUwQAVQAXaRUJXwcATQAOaAwDUgMXZhUQXA0ASwACUAIYXRQATgACTgIXVhECTgIaVBIATQAC
TQIcUBQCSAIcUBEATAAATQAATgB2tWOay3u26qF5uGGTxnCZ0pCZ0I+QwW+m0HdYoEKRxWmJxnuIwnqQvWuayGhztGSTyGpZn0GOxGB/wGh7u2aWw2xKjTCKwVtksVCPyVxbnD+KwVd3wFV2vFmdyW1OizGDwkpQrCqCxkVkujJdsi2JvUtOgi1/yDVHug5XwhiOx0RU
gy2R3j6Y1UdNfSlq55gUAAAAK3RSTlMAHXIOIe3YDeu8bPWRG+nWa/6QGOf1MtuV5vYzjfc0mvmd+TWg+qP6NkYkIiPNwAAAAIJJREFUGNNjYCAFMDIxo/BZWLXZ2JHlOXR09ThBLC5uHiDJy6dvYGjED2QJCBqbCDEIi5iamVuIigEFxC2trG0kJG3t7B2kpEE6ZByd
nF1c3dw9PGXlIKbJe3n7+Pr5ByjIwcxXDAwKDglVUkbYqBIWHqGqjOwmtUh1DVRXa2oR70MAwogP6KXmWqMAAAAASUVORK5CYII=
"""
    broken_png = """iVBORw0KGgoAAAANSUhEUgAAAIAAAABJCAAAAADQsnFrAAAERUlEQVRo3u2ZTUhUURTHz4uglVvbzMqF7VoIroRw40JaFAQyiW1dBBYTSC1qEc1GzMpw0cJNuJCMCKIEm8hMU9Eio8xhRKMPMoxRRHPQGee0ePPuu/e+cz/ezBMXdTbz3p1zzv93zzt35r37HISDtUMH
rP8f4OABDu9H0sIkwAlbZ4zQCvP9XOZhqxgnkmX49ckFcnIWoVEAOOrymoPLasK3txzHcRyzo4VLqB7IDJChUsahFX9w1ZjTDJAbbjewu2f9y0LYsjs6Wz5AbjZpWbwMGd/hOu2GBijMX7VsBVNqOy92NGUp2zmxrcm3PlwPADBtTeB9vWxWvr6ozTQmtEoREbfcw1NW
ABrhWqm9ApZQXZ/SonlpAdAbzHAvrVVd939pSewGRMSUe7xnBnjtR1bfz2qVc16t9QAwZdcG8iX4ofGdFZeHAQCKiLhnJAj0AOW00qO6yjoAQESccQ8bjQA7BACtGwDYUjnUIyJ+cY/fmQDYNJq0k6IAFpUeb7hUeRPAJzGvnGsk+/OpAmC0dN6Mu+l4sA0MjUhcSURE
7OLzbDCnagKgWzgvVgUgtQT+8DdlCTY5/4UgQLN7OsZ8anyfOq5JWgwA6hIIAePeaJsUxzvVsdgJzmPcABBTlEC6ufSGX4kD8vyE6NLxbT2AGMNdAzpzVhwIpu60agMKQC5BjgQQB1qp8t5gbVBUEgCVu13qgg7O5woNgAprAKhBRBxSEZAAwYXAXGqABFD+0CGehGeI
iLUKAmEg5yXvlkoAAJ8RV58DxaQrACIijvHTKegANCUIGh90Hi2sFLemAzjnZd8IB2Cjjx/pRUVSWpSgxwvZAUhYAbDp6QBYj08jotQForEbtrT909U1NzSmAbAvQdGLSEHaqCxl7+WG5IdT9ne3BAAA6hKw584MHANbW3M/Lq5QvRSyBCygLczz7d2AauUA4fZYHhnD
73gKdXoCFpAMBeDlq1LzG0twaY66dLaWL2XxuIOJBg0E5SrLM1xXAfiK7kqTFkLWXklhC8JMiE2qlodssgAgbfTsRbCz6SVEAHqXjCkGASLYU3vRJGTTTuiBLFqh/tZZx3GapEHiIo1IX3pnxyu58ikCaAkVPyPM4wx/fjNCadcKKoAZqQRdAAAzZWhvxkFnqALwS3CZ
na9ZqxonXjJ344cGyAS6IFLpHm6fTfXI6NnvSGsOAClZyQAQYurGiccmKSU6WZpFbaOFmSeeUJVSuW9gWwKjdO17bbwqv3/T9EEPoBVvMLcQmBOXCZD8YxTXAmyqAB6LY4R045yVtAHAz+zfxn9PBKEk8T6rpg0H4Lr0CTokQF9IbQOAtr0kr3jOrKSw8t6ctnLHA/GK
3r5qgneOKL86zdWiEnXQv7hUv/X7dbRCWd90t2Qqtng+On39PSHxQnigiDgY6St3fY/GfMdkyH/mypeha/0AAKP7oo2IUb2+368e+CcA/gJT9EOt+V1/bgAAAABJRU5ErkJggg==
"""
    error_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAACSVBMVEUAAACEAACOAACTAACUAACQAACLAAB9AACLAACUAgKZCQmNAACGAAB+AACOAACSAACIAABMAACNAACSAACDAACFAACLAAAAAAAAAACPAACVAAB5AABgAACZAACKAAB6AACQAABzAACVAAAcAACo
AACLAAAAAACgAACrAAAgAAByAAC1AACbAAAAAAAAAACZAACwAAAQAAAAAACaAADAAACxAAAyAAAAAABlAACrAAC6AAC8AACzAACIAAAAAAAAAAAqAAA3AAACAAAAAACwNTXFYGDHZWW3RESqHR3biYnnp6fop6fimpq3NTWrERHafXreiYLhi4PhioThh4TfhITbgYHc
gYG4KSmeAADKSELRf2725eLUi3fbfF/ceGDMbWLy29vUiIjNVFSnAwOvAwLPWDrMkn/////58/HEeVi+ZD/u29bctbK+ODe5EBC5CQDRRAfGVRHEknn38e7q2tTXuLCxNhXFIAy7AgCgAAC9EADSRQDaXwDBXAC2jX/TvrmlQAXSSgDLLwC/DQCnAACmAAC+DQDQQQDW
XACxWBLj08307u2tYjLRWQDRQgDCFgCsAAC3BADHMQC1SRLo1s3hx63NpHf28O27aDLMSgC9EQCtAACxAACzFQDMi3jmxq3JbgXUfADaqnPnyri5NwCzAwCuAgCsGwHQgma/TQXOYQDUbADOaQDal1zCVBCxEAC0AAC6AACrAgCqEwCzLAC7PgDASgDBSwC4NgCsCwC2
AACyAACnAgClCQClCwCnBgCvAAC/AAC7AAB8252hAAAARHRSTlMADlF5fV8dC4n0/aseFs7sNwTC6htlowEDzvkUFv5RMW0taBD0Pway5QtC+3UCCIi6DQ6I+rAYCkew7PTBXgweMjYgD4hci68AAAECSURBVBjTY2AAAUYmZhZWNgYYYOfgdHF1c+fi5oHwefk8PL28
vb19fPkFQHxBIT//gMCg4JDQsHBhEaCAaERkVHRMbFx8QmJSspg4g4RkSmpaekZmVnZ6Tm6elDSDjGx+QWFRenFJemlZeUWlHIN8ZVV1TW1denp9Q2NTc4sCg2JrW3tHZ1d6endPb1//BCUGZZWJkyZPSZ86LX36jJmzZqsyqKnPmTsvff6ChYvSFy9ZqqHJIK6lvWz5
ipWrVq9Zu279Bh1dBgY9/Y2bNm/Zum37jp27DAxBTjUyNtm9Z+++/QcOmpqJgz1jbmFpdWijtY2tkTjUu7p29g6OTs4SIDYAHdlQzb5sNMYAAAAASUVORK5CYII=
"""
    delete_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAABF1BMVEX///+tAADJYF2vBATSeHWoAAC/NCrKVkyqAACoAADITEPFRz+iAADDSkCcAACOAACyQzqcAACzKx+hPjR7PDK+Sj+YAACvJxuzMSaRAACrJBemHhCKAAClHhKHAACkHxOkHxWAAACkIRpbIRtZ
IRykIx6HDg5/HhZqIx9uAABqAABqIyCWKyuAAACdHxyAIyGBIyGCJBttAADDSknviYfwjYrfaGHmbW3mdnXlc3HpjYnTV1TTVlbXY2HUYl7WYF3LRD/JPj7LTkrCTUjMRkbTZWDFOTLCMzPDOjq/OTPBEQq9AAC+AADQEgzQAADGEgzHEgzfEw/jAADbExDbFBHfFBLl
Lyr1AADvFBLvFRPlMCzlMi3lMi5AN76JAAAANHRSTlMAMupJ5DL0+0hJ+/XQ+0pP/Ej7/P37SPr6SPr6SPpI+vpJ+v7++kvd/VBS/dcy8vz83lHmy4y2TQAAAJ5JREFUGNNjYCAGMDIxgyhmFkYIn5XNhJ2DgYGTy5SbFSzAY2ZuwcvHL2BpbsUDFhAUsraxFRaxs7EX
FYToERN3cHRydnGVEIOZKinl5u7hKS0Jt0ZG1svbx0tWBsaXk/f18w/w81WQg/AVlQKDgpVVQoJCVdXAAuph4REamlrakeFROmABXb1ofU0GBi2DGD1diB5dQyMQpWWsS5RPAZg2FJZPz1t8AAAAAElFTkSuQmCC
"""
    close_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAABv1BMVEX////////Pe3u4PDymAgKyJCS1MTGrERG4PT2/WVmoDw+mDw+mEQ+jCgnJe3ueDw+TCgiaBASmCQmtDg65FBOqPDyRDAqUCwuqCwu3GRieIyOUBASmCwu3GhmSCwudCgq0FhWLCQegAACOCwug
CwuxFBKKCQedAACmAACSIyOxFhSKBgKZAACjCwuXPDyHBgKUAACgCwu5GxS9e3uCDg6IAgKUCAiiDQ27HxWhWVmCBQKdWVl4Dg6xe3uKPDxrAAB3DQ15EBBsAQG7SEfMc3PYiorZi4vNdna5QkLJb23QeXewNzelHByuJSO8SUbLcG/CY2G/XVq/V1S5SkbAYWG5U1Cr
KyXCYF7BU06+U1OxSES0TUuqKyavPjelMSvMYVnFRka4PzqGBwWcJiKsOzaqQzqcHhfOUkrMNzfEOjScFRCTHhaMEgmaEgu+HxTLFRXGHRSlEw2QDQSREQWcEgi1FAjHAADLDQSuFgyaEQWXEQWWBQKuGwq8AADIDgWrBgKcEAWfFwiqAAC/FQeACAO/DgarFwiqGgi+
CAO+EwaxFQq8GQ/OHA3QCgO2GQieFQe4GwjMGwnSHArAHAqjFgdfTWjIAAAAQ3RSTlMAAirVzfr70dWT+Pj+/ir47XQcNfDV7hcX3e12Ft75Gd79Z/ku3v1bF+3y/Vtw1f1bF/Aq+HQedvGT/pP4KtXN+fnNf0ybtwAAANpJREFUGNNjYIAARkYGJMDEzMLKxs7BBONz
cjm7uLq5e3BzQvk8nl7ePr5+/gG8YBE+/sAgAUEhYZHgkFB+PqCAaFi4mDiDhGREZFR0jChQQCo2TppBRjY+ITEpOUUKaJ9capq8jEJ6RqaiUla2HCMDo3JOropqXn6BmrpGYZEy0DmaxSVapWXl2joMuhWVmkAz9Kqqa2rr9A0YDI3qG/SAAsYmjU3NLaZm5hatbSbG
IIdYWrV3dHZ19/T2WVlCnGpt0z9h4qTJU2ysYZ6xtbN3cHSys0X2MNz7AP4nLgM0DCzVAAAAAElFTkSuQmCC
"""
    ffpreview_png = """iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAACVUlEQVQ4y9WQT0iTcRzGn9/vfX33m3NbIm6619ycCwwM7CARS/CyPIhI9AfTi1RCB09C0Ck6RB4iCqSLYYGVGIIHbaF2MSsJpET6g6HMNd272HRjG9vrXt+9bwcVhWCee47P5/M9fB9SjsIhkZSlAE5Z
KDULU/0xhuEnOZZ5/Jqy8L1ZxpbuLjNhqj9mYXQJocX2QXwx1s3Afya7hIFOf0S+f7NXCS22DwJUgWIXk9iudMjIipWKnqiqyKklzoSm2MUkdL4atoWUF85HJRdR90q6Q5qertoE+8Alg20h5QUhCdkYj9XykHIuogdK7FBXHFbIa26GeKxWZWQsZwBX7SYAgkEVAMC7
XAD0wLoKReCuaBzyb381UO3ltEgBAMq4dIqoQ/MOgjxHErIR0EbLWj7+vM7tfZ8fOtk0s9lBgW22e0NbRvGmbZ+Da/Nj9Pwe2q1Mn/Sw6WBAU1h/Z8Rh4d9Y6BHCDo4Q8H8KtKCQ8RIxc9BmRHIue1jQpq+idSK/z/OTreiY1gAAZCxnQP5z5TVeG/nezAMA1Nn6Tqo+
k85yUAQypgjgj7sJgN/B3X2LXE4A+lpIhSKQhGyMRz08wrkaoq+aK7Cz4jiGbMDDEI96VMZ1FWf6tqT6lQffrOL7iYnT1uc/hn30dnKqOdm3JdXxNIRoY/c8Qhc6lrHc1RrSP9zwxOTN3nEl2tg9D50KECIbVjBJMqJ4QxJI6fofA58KllIhsmEF4R5qZem5Hqvtq3SZ
VU2W+bgTL3wNRe6RW6IlPddj4omUNhcYOm0m5SgqIOzgL/oO5qijSLZZAAAAAElFTkSuQmCC
"""
    info_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAABwlBMVEX///////////////+7zd69z9////////////////9skrZKfKxcjLp5pMhvm8Rdjbp/o8T///8uYpYxa6Nai71IfKwTSoMmYZoANG4ERoUHUJMLSYEAOHMAQYEAOXMAQ4UALWkAOXMAQ4QALWoA
KnEAOHEAQH8AKGsAN3EAO3gANXAANnAAN3EAOHQAOHUANnAANW8ANXAAAAAANXAAOHQCOXUANXAAM3AHPHQHQHcFRHkJQ3kDOnMBLWgALWiErM2Vtda3zeW+0ui90ei3zeahvtwmYp6evNyMsNd9pdKHrNWKrtekwN5Jg7h6os1xnMxwm8x8pND5+/2AptFCd65lk8di
kcZynMz8/f5kk8dDfLVDerVViL9olMVnk8RLgbpEfLZIf7n///9HgLtIf7tPhLo2cLE6dbI6eLg5e7o7fb85e7wqbrIbYKQFT5sASJIBTZgGVaQKYK8OZLTu7u4AX7YAXbQAWKwAUqIAS5YARIoAUJ4AV6oAXrYAYLgAY70AZL8ASpMAUaEAW7EAa8kAV6sAPn4AWq4A
ZsIAa8oActQAdtwAb88APn8AOqQAX7MAWtYAX90AZbwAPagBOXMAOXMa0d8aAAAAP3RSTlMAAQYLISINCAIDOMXx/vXPPwR8+PuKc34n9fYpq6zf3xH9/RES/f0T6OjExTn7+zqZmQGf/f2gQtv5+t9DHh7QNZoPAAAA5klEQVQY02NgAAJGJmYWVjZ2DgYo4OTi5uG1
5+MXEITwOYSEHRydnF1cRUQhImJu7h6eXl6e3j6+4oxAvoSkn39AYFBQYIB/sJQ0UEAmJDQsLDwiIjwsLDJKFiggFx0DBLFxIDJenoFBQTEhMTExKSkpOSUxMVVJmUFFNS09PSMzKSkrOyc3T02dgUEjv6CwqLikpLSsvKJSE2iGVlV1TW1dSUl9Q21NozZQQEe3qbm+
BAha6lv19EEOM2hr7+js6u7qqes1BLvUyNikr3/CxEmTTc2MoL4zt7CcMtXK2oYBCdjaQRkADNM7nD2IGIMAAAAASUVORK5CYII=
"""
    ok_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAABDlBMVEX///8JIzUJHToAEjcIHz4WM1oOJ00AFDsaNVwMJUsJHzsdOV8OIUMJHDkdNVoeN1kKHTkvTXYOIkILIDUdNVYVMFcGHDcZMVUOIj8aMk0aNFYFHjcLHz8cNVMFGzcKHj0aMUYJGjwLIDsXMEAI
GzsMHjoYMT4HGjoAAAALHzcbND8HHzoKHTpph6h9mrWryt5ig6VwjKiUsshvi6h0kalEYINifpp/m7J+m7Jng59depZnhJ1lgZs7VG9VcYdphZ5ifZgsSGlRa4dbd5RgfZo1TFpNanpmgZ9kgJxje5MtRl1PaolUcJNadpdbdptWcJREX3kqRFNSb5ZffKhigKxQbYkz
TFd0lL1oiKY2Ul62MpKDAAAALXRSTlMAHXIOIezYDeq8bPWRG+jUa/6QGOfzLtqW5/Uzjvc4oPk8qPtBsPxGAbb8S2r7wD5BAAAAhElEQVQY02NgIAUwMjGj8FlYddnYkeU59PQNOEEsLm4eIMnLZ2hkZMwPZAkImpgKMQiLmJlbWIqKAQXEraxtbCUk7eytHaSkQTpk
HJ2cXVzd3D08ZeUgpsl7efv4+vkHKCjCzFcKDAoOCVVWQdioGhYeoaaO5AQNzUgtbRRHaugoMhANAOfbEF197TngAAAAAElFTkSuQmCC
"""
    open_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAAB41BMVEUAAABUf7UAAAAAAAAAAAAAAAAJCQlUf7UqKinExMRYh8KTsctKdaZFX4AAGDgAEzMAFDMAEzIAEzIAEzEAEzEAEjAAEjEAGz0oTHb////o5+dKc6dRcpv29vb4+Pj5+Pj19PTr6+v///47UWpR
frhUg7xUgbs9ZJJKapD5+fnv7u6goKDk4d5OYXd4otd8qeFJdq0vUno+Xof49/f39/fy8vKamprx7+/h3ds3T21olM0wUnsrS3H19fWhoKD//PpGXXkqS3MlRmohQ236+fn7+/q4uLi0s7OtrKyysLD08vAOKkoiQWQRNF77+vr6+frf4eXP09jO0tbMz9XJzdHGyM7B
xMrg4uUAFDEYOVsAHkXo6OrO0dXQ0ta1u8OHor6nzeuozeqpzeuozeuqzuu84PxPdp+84PunzOqmy+mmzOqozuuqzuqFq9qCpteAptd/pteHreFIXXhQeamDqdx8o9Z9o9Z+o9aBptiAp9iCp9iBp9iHruFJX3tReqqKruCDqduGqduHqduFqduEqduEqdqJseRKX3w5
Z5tAdrQ/dLBAdLBDerknRGZYf62Hrd+FqdqGqdqDqdqKsONQZIGBqN19o9h+pNh8o9h4o9l8o9l+pNl+o9l5o9l6o9h+o9iBqeD0enAlAAAAGHRSTlMAAANJSz0IyZb+1bq5r0F6e3x9fn+Bf0Lax4JAAAABAElEQVQY02NgYGJmAQFWNgYIYJeQBAMpDjCXkUFaRlJW
Tl5BkVNJWUVVjYuBQV1DUk5OU1ZLW1JHV0/fgIHB0EjSWFPTxNRM0tzC0sqagcHGECQgZ2sHNMfewZGBwclZ0kTOxdVF0c3dw9PLm4HBx1dSzs8/IDAoOCQ0LDyCgSEyKjomNi4+ITEpOTk5JZWbIS09IzMrOyc3L78gLz+/sIihuKS0rKy8vKKysrKqurqmlqGuvqGh
saGpuQVINra2tTN0dHYBQTcQgkBPL0Nff+uEiRNaQcSkCa2TpzDwTJ02fcbMWbNnz5k7b/r8BbwMfPwCgkLCIqKiYsJCggL84gBhOUmZU0MiDgAAAABJRU5ErkJggg==
"""
    question_png = """iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAMAAADXqc3KAAABlVBMVEUAAAAyaKQ2Z6UzZqVIdK1FcqxIda1EcqxEcqs1ZqQAAAAuXJVFcaxEcawsWI8AAAAAAAAAAAAAAAApU4ZEcasnT4AAAAAAAAAAAAAAAAAAAAAAAAAmTXwkSXYAAAAAAAAAAAAmTHslSXcAAAAA
AAAAAAAmT300ZaEmTnsAAAAAAAAAAAAAAAAAAADa5PDV4e7i6/S3zeS3zOTf6PPj6/S4zuVnlMdlk8bf6PK5z+ZqmMlolshkksW70Odtm8xsmctql8lolchjkcVwnc5vnM1tmstrmMpmlMZkkcVhj8O3zOPe5/K6z+f3+fz5+/3x9frP3e2HqtJfjcK2y+O4zuZrmcpt
msxum8zG1+ukwN6uxuL////d5/JfjcFci8C2yuLe5/G4zeVplsiLrdRgjsNejMFcir9ZiL7Q3Otpl8mRs9f4+v3z9vqApc9di8Bbib9Zh72xx+DS3uxnlcdmlMfs8viDqNBgjsJcisBaib6wx+Dd5vG4zeRlksZjkcRikMTd5/FejcFbir9fjsJaiL62y+LQ3eveT0o1
AAAALnRSTlMATOFV9/f39/fhBl74+GINBQ4WafhuHgQIERkicXcqByVydiwBE2TlZgkCChIQvDkr4AAAAUpJREFUKM9jYKAAMDIxMWITZ2bR02dlxiLOZmBoZIwpw8xmYmpmbmSBLgMUt7SyNrNBlwGK29rZOziaO6HKgMSdXVzdrN09PL28ETJAcR8XZxdfP/+AQM+g
YG92qAwji0FIaFh4eERkVHSMZ2xcfAIHxD9MeonWDqH2oVZJydEmKalp6fEZnBAJ/UQz68zMTOus7JzcoLz8gsIiiAQjq3Giu1lxcUlpdFl5akVlVTXUKAZmVuMaG/Nam7r6lNi8/MIGDoSzWC1q6uvro6Mb85oKEziQPcJq4VXeHJsKEmdH9joXN4+FV15eWn5hAi8f
vwBCXFBImMc7uLKlqkFEVExIUBwmISEpJS3Dm9BaWC0iKyctJSkPkxAQFBJTUORtKxJRUlYQE+JXgZslzq8qJaamrq4hLSalqqmCZDuDFpe2oKSOLr+mOAMBAAAs80S883HicAAAAABJRU5ErkJggg==
"""
    refresh_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAACNFBMVEUhxhIcMVMcMlUhO2MvVI42YaMoSHkdM1YcMVMcMVMcMVMcMVMcMlQrTIEqS34cMlUcMVMnRnYhO2MeNls6aK88a7UrTYIcMlUzW5ocMVMnRXUcMlQwVpEfNlwhOmIdNFgdM1YfN1wkQGwhOmIh
OmIcMVMfN10kP2skP2sdNFcgOmEgOWAiPGYdM1ccMVMcMVMcMlUdM1YdM1YcMlQfNlwhOmIcMVMcMlUtUIctUYgoR3cjPmgcMlQdM1YzW5kiPWccMlQcM1UfNlwrTIE4ZKk2X6EzWpklQnAcMVMgOF83YaQ3YqVDc8BAcb89bLc6Zq00XZ0lQnAcMVMnRXREdME+brkj
PmkcMVMcMVMcMVMsTYNNe8QtUIYcMlQcMVMkP2sxV5M1Xp8hO2McMVMcMVMcMVMcMVMcMVMdM1YcMlUcMVMcMVMcMVNahch8ntOctt6fuN+Jp9hficqAodWIp9d1mdEjPWhyltBvlM9wlc9TgMY5ZqxxltBzl9AjPmhNe8RYg8dTf8YtUYgcMlUgOF8dM1cpSXtKecNg
icpfiMojP2pAcb8qSn0uUYlRfsUkQG0vVI04Y6cwVpEcMVM0XJxId8JXg8dMesMtUIcwVZAmQ3EbMFEmRHI8arQqS34mQ3IeNVkfN14hO2MgOWAaLk4nRnYsToQmRHMvU4wrTYIzW5o2YaM6Z68+bro2X6E6Zq1Hd8JSfsVPfcQ8a7VDdMBkjMtuk89sks5hispJeMJ/
oNRii8v+uJI/AAAAbXRSTlMAI37A8fPJhSQcIAJz9/d6RfLqwfr6+3T6J/eE/v74tLH4/sX1Mfb+9bBFtvq5CBcYFRINxPciuSwZDQV6/v70qar0vWxMMhop9/7++tyvhmcgdPrZrtxAAbj4/O6ybfT9/vR6Bh9/uu3uwIUdBOvzJgAAAQtJREFUGNNjYAACRiZmFlY2
dg4GBk4uIJebhzc3L7+gsIiPX0BQCMgXFikuAYNiUbHSMgYGcYnyisqq6urcioqaito6Bkmp+obGpubmlta29o7Ozi4GaZnu7h5ZOXkFRaXevqr6fgblCRMnTVbhYFBVmzJ12vRqdQaNGTNnzdZkYNCaM7d6yrz52gw6CxYuWqwLtF1P38DQyJiBwWTJ4qXLTCUZGMzM
LSytrBkYbGyXr5hiZ+/g6OTs4urmzsDg4bly1eo1a728fXz9/AMCgXqDgtetX7thY8O0TSGhYeFAgYjIqM1bqrZu274jOiY2AuTXiLj4hJ0Vu3YnJiWnMEBAalp6RmZWdg6IDQC1PFKUTLcgtQAAAABJRU5ErkJggg==
"""
    remove_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAAAllBMVEX////////////HZGS/QUHsQUH2YmKhBATqBASbAADlAACWAACeAADaAADiAACwAAC3AAC+AADEAADLAADSAADYAADfAADMZ2fPWlrXWlrcWlrhWlrkWlriZGTRV1fHRUXBUlLITU3SUVHZUFDe
TU3hSUnfSUnQKCi+HBy+IiKlAACrAACyAAC5AAC/AADGAADNAADTAABMyJi7AAAAD3RSTlMAAQSE+vuI5Ozk7GT5+mqpk8vSAAAATUlEQVQY02NgoA1gZIKxmBjBFDMLv4CgkLCIqBgrG1iAXVxCUkpaRkZWTp4DLMCpoKikrKKqpq6hyQUW4ObR0tbR1dM3MOTlo5Ez
MQAAgFYE6RdXIhUAAAAASUVORK5CYII=
"""
    revert_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAAB11BMVEX///8AAAAAAAAAAAB5URuIaDV5VhoqHQqXcyu8mUCDYSaVcCeTbiaSbCWOZh4AAAB9VxiNZBaQahqLhHcAAAAAAABmZjMMCASLh3sAAACGhHsAAAAAAAD29vTo2bmEgnkAAADq6uro6enq6+vs
7e3t7u7u7+/v8PDw8fHx8vLz9PTo6ery8vL09PTr7Ozx8fHz8/P09fX19fX29vbOxbemkGuHaDN6Vh6LbDm5p4rh29H29/f39/f4+fmMbz2xjzvhyFzx32n673Ty4Gjhx1m5l0Gfhlz39vX6+vr7+/vavFn35mr35mf35mndwFiObDHx7+vz2mjw12nz3Gfx02PswVHn
sjbrv0by1GL02mXkxFqObj7v0mPu1GPu1WPkvTvWnRC2fg6jbA60dArZjgvpuUjwzV+8lTvCspjqzFzr0F3iwTnWrQ6NZxm4porb0sWtmXiQai/OhQrpuEnswVbpvlV8WBzlwknnzVLlzUHfyB6qhBPSx7X8/Pzz8Oyqg0XhoC3puFDotkqXby+UcR+Vcx6UcheVeT/y
7+v9/f3+/v69qo3MoFXFspDSyLXx8O708/HGtp7CsZX29vX29fL5+fn49/TTwqbOx7z49/P6+vn7+vnu6N7UEScRAAAAIXRSTlMAEDk6E9J0UPz+/fz8/Pw16P7+0wUBBT/cCtoSOP7+zAeKvFaxAAAA+UlEQVQY02NgYGBkYmKGASZGBgYGJkUlZRVVNXUNDU1NLSag
ALM2VEBTR0eXGSSgp6eipq6vY6BraGTMzMDCwKxqYmpmbmFpZW1jY8vMwMrGbmfv4Ojk7OLq5u7hyczAwcnl5e0DAr5+/gFAAe7AoODgkNCw8IjIyKhooABPTGxcfEJiUnJKalpaegYzA29mVnZObl5+QWFRcUlpGRMDX3lFZVV1jWdtXX1DY1MzP4OAoFBLa1t7R2dX
d09vn7AIg6iYeP8ED8/azq6JkzolJBlAfpk8ZSpQYNr0GVLSID6DjOzMWbNqZ8+RkwfLMzAoyID9z8QvAuIBALefO7A/pgxdAAAAAElFTkSuQmCC
"""
    save_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAAA9lBMVEX///9NmQRPmQZOmgZOmAROmQZOmgZMmAVOmgZOmwZOmgZOmgZOmgZOmgVOmQZPnAdQmwhOmwau339yxSFOmgbQ866K4jSu4nu66Y11uDZ6uzy76o6c2mCX2lef51nH8Z/Q862d51ac5lN4zSdt
wB6M4Tif51iJ4DNtvh2/wbuChYZATD9FfRNaqRB70ClaqA8xPzEuNDa6vbbT1ND////29/X19vT7+/r+/v75+vj09fPv8O3p6+fj5uK1uLGkpqHv8O/k5eLh4d/t7ezNzsri4+Dl5uSdoJqGiYPr7OrQ0c65ureQko2IioWNj4tvcW3x8vFYWlaeE5PLAAAAEnRSTlMA
PNPFPt3IL9fL4czKvs7w7S42D9ScAAAAjUlEQVQY02NgAANGJmYWBmTAKiTMhiIgIiomQpEAOwcnSICLG2YRj7iEpKiYlLQML1SAT1ZOXkFMUUmZGSrAwq+iqiamriEgCOFramnr6Orp6esYGBqBBYxNTM3MLSytrG1s7cAC9g6OTiYmJs4urm7uYAEPT1cvbx9fPxdX
Tw+wgH8AHPiDBQKRAAMDAFjyF6ty/R1iAAAAAElFTkSuQmCC
"""
    warning_png = """iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAABQVBMVEX////6sgX8sQb8uQX7uUD5uAX7sw36rgv9uQL6tgX6tQ35sgn7twT5tAT3tBH3sA33sgH2rwH/mQD0qgjyqQb/zADzowHzpAH/ogDwmg7wmQroogDwkgDwkQDpdwDqfATpegLrggblZgDmbgbo
eQD7vlL/16v+06X8vlL/2q7/1qX8u0r/0o3LbzjLbjb+y3j7wUj/0oC8RxL/zGf7vTj+0mr/0mXDSRP/zkv+y0X6wTP/1VH/1lLMViLOWyn/1UD/1Dn5wSv+yQX/zgD/0QDddRXccg//1gD/1wD+1Af3uhD/2AD/3AD/4AD/4gD/5AD/5QD/4wD3wBD+3AX/6AD/7QDm
fxD/8wD/8QD+5gn2vBf/6gD/7wD/9ADqfxHqgBH/9wD/8gD2vhf92CT/5hf/5gD/6QD/7AD/5wD/4xf92yY5YL/DAAAAJXRSTlMAY1VX/lzn6Wtv7O17fvHyiYsF9vYFmZcL+voLqKbq6pb49/aTMf8OLAAAAJ5JREFUGNNjYMABGJlQ+cwsqqwoAmxq6uzIfA4NTS1t
TiQBLh1dPX1uBJ/HwNDIyNiEFy7AZ2pmbm5hyQ/jC1hZ29ja2Ts4CkL4QsJOzi6ubu4eniKiYAExL28fXz//gIDAIHEQX0Iy2D8kNCwsPCI0UkoaKCAT5R8dExsXn5AYGpIkCxSQS05JTUsPTY/OCMzMkgcKKCgqwYGyCqa3AZWSG22RwdIDAAAAAElFTkSuQmCC
"""
    def __new__(cls):
        if cls.initialized:
            return
        cls.initialized = True
        # NOTE: commented icons are currently unused
        cls.apply_pxm = sQPixmap(imgdata=ffIcon.apply_png)
        cls.apply = QIcon(ffIcon.apply_pxm)
        cls.broken_pxm = sQPixmap(imgdata=ffIcon.broken_png)
        cls.broken = QIcon(ffIcon.broken_pxm)
        cls.close_pxm = sQPixmap(imgdata=ffIcon.close_png)
        cls.close = QIcon(ffIcon.close_pxm)
        cls.delete_pxm = sQPixmap(imgdata=ffIcon.delete_png)
        cls.delete = QIcon(ffIcon.delete_pxm)
        cls.error_pxm = sQPixmap(imgdata=ffIcon.error_png)
        cls.error = QIcon(ffIcon.error_pxm)
        cls.ffpreview_pxm = sQPixmap(imgdata=ffIcon.ffpreview_png)
        cls.ffpreview = QIcon(ffIcon.ffpreview_pxm)
        #cls.info_pxm = sQPixmap(imgdata=ffIcon.info_png)
        #cls.info = QIcon(ffIcon.info_pxm)
        cls.ok_pxm = sQPixmap(imgdata=ffIcon.ok_png)
        cls.ok = QIcon(ffIcon.ok_pxm)
        cls.open_pxm = sQPixmap(imgdata=ffIcon.open_png)
        cls.open = QIcon(ffIcon.open_pxm)
        #cls.question_pxm = sQPixmap(imgdata=ffIcon.question_png)
        #cls.question = QIcon(ffIcon.question_pxm)
        cls.refresh_pxm = sQPixmap(imgdata=ffIcon.refresh_png)
        cls.refresh = QIcon(ffIcon.refresh_pxm)
        cls.remove_pxm = sQPixmap(imgdata=ffIcon.remove_png)
        cls.remove = QIcon(ffIcon.remove_pxm)
        cls.revert_pxm = sQPixmap(imgdata=ffIcon.revert_png)
        cls.revert = QIcon(ffIcon.revert_pxm)
        cls.save_xpm = sQPixmap(imgdata=ffIcon.save_png)
        cls.save = QIcon(ffIcon.save_xpm)
        #cls.warning_pxm = sQPixmap(imgdata=ffIcon.warning_png)
        #cls.warning = QIcon(ffIcon.warning_pxm)


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


class tFlowLayout(QLayout):
    """ Based on Qt flowlayout example, heavily optimized for speed
        in this specific use case, stripped down to bare minimum. """
    def __init__(self, parent=None, size=1):
        super().__init__(parent)
        self._items = [None] * size
        self._icnt = 0
        self._layout_enabled = False

    def enableLayout(self):
        self._layout_enabled = True

    def addItem(self, item):
        self._items[self._icnt] = item
        self._icnt += 1

    def itemAt(self, index):
        if 0 <= index < self._icnt:
            return self._items[index]

    def hasHeightForWidth(self):
        return self._layout_enabled

    def heightForWidth(self, width):
        if self._layout_enabled:
            return self.doLayout(QRect(0, 0, width, 0), True)
        return -1

    def setGeometry(self, rect):
        if self._layout_enabled:
            self.doLayout(rect, False)

    def sizeHint(self):
        return QSize()

    def doLayout(self, rect, testonly):
        if not self._icnt:
            return 0
        x = rect.x()
        y = rect.y()
        right = rect.right() + 1
        iszhint = self._items[0].sizeHint()
        iwidth = iszhint.width()
        iheight = iszhint.height()
        ngaps = int(right / iwidth)
        gap = 0 if ngaps < 1 else int((right % iwidth) / ngaps)
        for i in range(self._icnt):
            nextX = x + iwidth
            if nextX > right:
                x = rect.x()
                y = y + iheight
                nextX = x + iwidth + gap
            else:
                nextX += gap
            if not testonly:
                self._items[i].setGeometry(QRect(QPoint(x, y), iszhint))
            x = nextX
        return y + iheight - rect.y()


class tScrollArea(QScrollArea):
    notify = pyqtSignal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delayTimeout = 50
        self._resizeTimer = QTimer(self)
        self._resizeTimer.timeout.connect(self._delayedUpdate)

    def resizeEvent(self, event):
        self._resizeTimer.start(self.delayTimeout)
        self.rsz_event = event

    def _delayedUpdate(self):
        self._resizeTimer.stop()
        # ask parent to call our own do_update()
        self.notify.emit({'type': 'scroll_do_update'})

    def do_update(self, tlwidth, tlheight):
        super().resizeEvent(self.rsz_event)
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

    def clear_grid(self):
        if self.widget():
            self.takeWidget().deleteLater()

    def fill_grid(self, tlabels, progress_cb=None):
        self.setUpdatesEnabled(False)
        l = len(tlabels)
        thumb_pane = QWidget()
        self.setWidget(thumb_pane)
        layout = tFlowLayout(thumb_pane, l)
        x = 0; y = 0; cnt = 0
        for tl in tlabels:
            layout.addWidget(tl)
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
        layout.enableLayout()
        self.setUpdatesEnabled(True)


class tmQTreeWidget(QTreeWidget):
    def __init__(self, *args, load_action=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.load_action = load_action

    def contextMenuEvent(self, event):
        menu = QMenu()
        if self.load_action and len(self.selectedItems()) == 1:
            menu.addAction('Load Thumbnails', self.load_action)
            menu.addSeparator()
        menu.addAction('Select All', self.select_all)
        menu.addAction('Select None', self.select_none)
        menu.addAction('Invert Selection', self.invert_selection)
        menu.exec_(self.mapToGlobal(event.pos()))

    def select_all(self, sel=True):
        for i in range(self.topLevelItemCount()):
            self.topLevelItem(i).setSelected(sel)

    def select_none(self):
        self.select_all(False)

    def invert_selection(self):
        sel = self.selectedItems()
        self.select_all()
        for i in sel:
            i.setSelected(False)

class tmDialog(QDialog):
    ilist = []
    outdir = ''
    loadfile = ''
    def __init__(self, *args, odir='', **kwargs):
        super().__init__(*args, **kwargs)
        self.outdir = odir
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Thumbnail Manager")
        self.resize(800, 700)
        self.dlg_layout = QVBoxLayout(self)
        self.hdr_layout = QHBoxLayout()
        self.loc_label = QLabel(text='Index of ' + self.outdir + '/')
        self.tot_label = QLabel(text='--')
        self.tot_label.setAlignment(Qt.AlignRight)
        self.tot_label.setToolTip('Approximate size of displayed items')
        self.hdr_layout.addWidget(self.loc_label)
        self.hdr_layout.addWidget(self.tot_label)
        self.tree_widget = tmQTreeWidget(load_action=self.accept)
        self.tree_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tree_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree_widget.setRootIsDecorated(False)
        self.tree_widget.setColumnCount(4)
        self.tree_widget.setHeaderLabels(['Name', 'Count', 'Size', 'Date Modified'])
        self.tree_widget.itemDoubleClicked.connect(self.accept)
        self.tree_widget.itemSelectionChanged.connect(self.sel_changed)
        self.tree_widget.setAlternatingRowColors(True)
        self.filter_layout = QHBoxLayout()
        self.filter_check = QCheckBox('Filter:')
        self.filter_check.setTristate(False)
        self.filter_check.setCheckState(Qt.Checked)
        self.filter_check.setToolTip('Activate or deactivate filter')
        self.filter_check.stateChanged.connect(self.redraw_list)
        self.filter_edit = QLineEdit()
        self.filter_edit.setToolTip('Filter list by text contained in name')
        self.filter_edit.textChanged.connect(self.redraw_list)
        self.filter_layout.addWidget(self.filter_check, 1)
        self.filter_layout.addWidget(self.filter_edit, 200)
        self.btn_layout = QHBoxLayout()
        self.load_button = QPushButton("Load Thumbnails")
        self.load_button.setIcon(ffIcon.open)
        self.load_button.setToolTip('Load selected video thumbnail preview')
        self.load_button.clicked.connect(self.accept)
        self.load_button.setEnabled(False)
        self.load_button.setDefault(True)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setIcon(ffIcon.refresh)
        self.refresh_button.setToolTip('Rescan the thumbnail library and update list')
        self.refresh_button.clicked.connect(self.refresh_list)
        self.invert_button = QPushButton("Invert Selection")
        self.invert_button.setIcon(ffIcon.revert)
        self.invert_button.setToolTip('Invert the current selection')
        self.invert_button.clicked.connect(self.tree_widget.invert_selection)
        self.selbroken_button = QPushButton("Select Broken")
        self.selbroken_button.setIcon(ffIcon.remove)
        self.selbroken_button.setToolTip('Select orphaned or otherwise corrupted thumbnail directories')
        self.selbroken_button.clicked.connect(self.select_broken)
        self.remove_button = QPushButton("Remove Selected")
        self.remove_button.setIcon(ffIcon.delete)
        self.remove_button.setToolTip('Remove selected preview thumbnail directories')
        self.remove_button.clicked.connect(self.remove)
        self.remove_button.setEnabled(False)
        self.close_button = QPushButton("Close")
        self.close_button.setIcon(ffIcon.close)
        self.close_button.setToolTip('Close thumbnail manager')
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
        self.dlg_layout.addLayout(self.hdr_layout)
        self.dlg_layout.addWidget(self.tree_widget)
        self.dlg_layout.addLayout(self.filter_layout)
        self.dlg_layout.addLayout(self.btn_layout)
        QShortcut('Del', self).activated.connect(self.remove)
        QShortcut('F5', self).activated.connect(self.refresh_list)
        self.open()
        self.refresh_list()
        hint = self.tree_widget.sizeHintForColumn(0)
        mwid = int(self.width() / 8 * 5)
        self.tree_widget.setColumnWidth(0, min(mwid, hint))
        for col in range(1, self.tree_widget.columnCount()):
            self.tree_widget.resizeColumnToContents(col)

    def accept(self):
        for item in self.tree_widget.selectedItems():
            if item.vfile:
                self.loadfile = item.vfile
                eprint(1, "load file ", item.vfile)
                break
        super().accept()

    def refresh_list(self):
        def show_progress(n, tot):
            self.tot_label.setText('Scanning %d/%d' % (n, tot))
            QApplication.processEvents()
        self.ilist = get_indexfiles(self.outdir, show_progress)
        self.redraw_list()
        self.filter_edit.setFocus()

    def redraw_list(self):
        selected = [item.text(0) for item in self.tree_widget.selectedItems()]
        self.tree_widget.setUpdatesEnabled(False)
        self.tree_widget.clear()
        ncols = self.tree_widget.columnCount()
        total_size = 0
        cnt_broken = 0
        flt = self.filter_edit.text().strip().lower() if self.filter_check.isChecked() else None
        for entry in self.ilist:
            if flt and not flt in entry['tdir'].lower():
                continue
            total_size += entry['size']
            item = QTreeWidgetItem([entry['tdir'], str(entry['idx']['count']), hr_size(entry['size']),
                                    time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry['idx']['date']))])
            item.setToolTip(0, ppdict(entry['idx'], ['th']))
            item.setTextAlignment(1, Qt.AlignRight|Qt.AlignVCenter)
            item.setTextAlignment(2, Qt.AlignRight|Qt.AlignVCenter)
            if not entry['idx'] or not entry['vfile']:
                cnt_broken += 1
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
            if entry['tdir'] in selected:
                item.setSelected(True)
                selected.remove(entry['tdir'])
        self.tot_label.setText('~ ' + hr_size(total_size, 0))
        self.selbroken_button.setEnabled(cnt_broken > 0)
        self.tree_widget.setUpdatesEnabled(True)

    def select_broken(self):
        for i in range(self.tree_widget.topLevelItemCount()):
            item = self.tree_widget.topLevelItem(i)
            item.setSelected(not item.vfile)

    def sel_changed(self):
        sel = self.tree_widget.selectedItems()
        nsel = len(sel)
        self.remove_button.setEnabled(nsel > 0)
        self.load_button.setEnabled(True if nsel==1 and sel[0].vfile else False)

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
                    mbox.setText(re.sub(r'^\[.*\]\s*', '', str(e)).replace(':', ':\n\n', 1))
                    mbox.exec_()
            self.refresh_list()

    def get_loadfile(self):
        return self.loadfile


class aboutDialog(QDialog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('Help & About')
        self.setFixedSize(600, 600)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.icon_label = QLabel()
        self.icon_label.setPixmap(ffIcon.ffpreview_pxm)
        self.tag_label = QLabel('ffpreview %s\n'
                                'Copyright (c) 2021, Urban Wallasch\n'
                                'BSD 3-Clause License' % _FFPREVIEW_VERSION)
        self.tag_label.setAlignment(Qt.AlignCenter)
        self.hdr_layout = QHBoxLayout()
        self.hdr_layout.addWidget(self.icon_label, 1)
        self.hdr_layout.addWidget(self.tag_label, 100)
        self.help_pane = QTextEdit()
        self.help_pane.setReadOnly(True)
        self.help_pane.setStyleSheet('QTextEdit {border: none;}')
        self.help_pane.setHtml(_FFPREVIEW_HELP)
        self.qt_button = QPushButton('About Qt')
        self.qt_button.clicked.connect(lambda: QMessageBox.aboutQt(self))
        self.ok_button = QPushButton('Ok')
        self.ok_button.setIcon(ffIcon.ok)
        self.ok_button.clicked.connect(self.accept)
        self.btn_layout = QHBoxLayout()
        self.btn_layout.addWidget(self.qt_button)
        self.btn_layout.addStretch()
        self.btn_layout.addWidget(self.ok_button)
        self.dlg_layout = QVBoxLayout(self)
        self.dlg_layout.addLayout(self.hdr_layout)
        self.dlg_layout.addWidget(self.help_pane)
        self.dlg_layout.addLayout(self.btn_layout)


class cfgDialog(QDialog):
    ilist = []
    outdir = ''
    loadfile = ''
    opt = [ ['outdir', ('sfile', True, 0), 'Thumbnail storage directory'],
            ['ffprobe', ('sfile', False, 0), 'Command to start ffprobe'],
            ['ffmpeg', ('sfile', False, 0), 'Command to start ffmpeg'],
            ['player', ('sfile', False, 0), 'Command to open video player'],
            ['plpaused', ('sfile', False, 0), 'Command to open player in paused mode'],
            ['grid_columns', ('spin', 1, 999), 'Number of columns in thumbnail view'],
            ['grid_rows', ('spin', 1, 999), 'Number of rows in thumbnail view'],
            ['force', ('check', 0, 0), 'Forcibly rebuild preview when opening a file (reset after each view load)'],
            ['reuse', ('check', 0, 0), 'If possible, reuse existing thumbnail parameters when viewing'],
            ['thumb_width', ('spin', 1, 9999), 'Width in pixel for thumbnail creation'],
            ['start', ('time', 0, 0), 'Start time for thumbnail creation'],
            ['end', ('time', 0, 0), 'End time for thumbnail creation'],
            ['method', ('mcombo', 0, 0), 'Select video filter method for thumbnail creation'],
            ['frame_skip', ('spin', 1, 99999), 'Number of frames to skip for method \'skip\''],
            ['time_skip', ('spin', 1, 9999), 'Number of seconds to skip for method \'time\''],
            ['scene_thresh', ('dblspin', 0.0, 1.0), 'Scene detection threshold for method \'scene\''],
            ['customvf', ('edit', 199, 0), 'Filter expression for method \'customvf\''],
            ['addss', ('spin', -1, 99), 'Add subtitles from stream'],
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('Preferences')
        self.table_widget = QTableWidget()
        self.table_widget.setSelectionMode(QAbstractItemView.NoSelection)
        self.table_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_widget.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table_widget.horizontalHeader().setVisible(False)
        self.table_widget.setShowGrid(False)
        self.table_widget.setStyleSheet('QTableView::item {border-bottom: 1px solid lightgrey;}')
        self.table_widget.setRowCount(len(self.opt))
        self.table_widget.setColumnCount(1)
        self.resize(self.table_widget.width() + 150, self.table_widget.height()+120)
        self.btn_layout = QHBoxLayout()
        self.reset_button = QPushButton('Reset')
        self.reset_button.setIcon(ffIcon.revert)
        self.reset_button.setToolTip('Revert to previous settings')
        self.reset_button.clicked.connect(self.reset)
        self.load_button = QPushButton('Load')
        self.load_button.setIcon(ffIcon.open)
        self.load_button.setToolTip('Load settings from file')
        self.load_button.clicked.connect(self.load)
        self.apply_button = QPushButton('Apply')
        self.apply_button.setIcon(ffIcon.apply)
        self.apply_button.setToolTip('Apply current changes')
        self.apply_button.clicked.connect(self.apply)
        self.save_button = QPushButton('Save')
        self.save_button.setIcon(ffIcon.save)
        self.save_button.setToolTip('Apply current changes and save to file')
        self.save_button.clicked.connect(self.save)
        self.close_button = QPushButton('Cancel')
        self.close_button.setIcon(ffIcon.close)
        self.close_button.setToolTip('Close dialog without applying changes')
        self.close_button.clicked.connect(self.reject)
        self.ok_button = QPushButton('Ok')
        self.ok_button.setIcon(ffIcon.ok)
        self.ok_button.setToolTip('Apply current changes and close dialog')
        self.ok_button.clicked.connect(self.accept)
        self.ok_button.setDefault(True)
        self.btn_layout.addWidget(self.reset_button)
        self.btn_layout.addWidget(self.load_button)
        self.btn_layout.addStretch()
        self.btn_layout.addWidget(self.apply_button)
        self.btn_layout.addWidget(self.save_button)
        self.btn_layout.addWidget(self.close_button)
        self.btn_layout.addWidget(self.ok_button)
        self.dlg_layout = QVBoxLayout(self)
        self.dlg_layout.addWidget(self.table_widget)
        self.dlg_layout.addLayout(self.btn_layout)
        self.refresh()

    def accept(self):
        self.apply()
        super().accept()

    def reset(self):
        ffConfig.init()
        self.refresh()

    def changed(self, _=True):
        self.reset_button.setEnabled(True)

    def load(self):
        fn, _ = QFileDialog.getOpenFileName(self, 'Load Preferences', self.cfg['conffile'],
                            'Config Files (*.conf);;All Files (*)',
                            options=QFileDialog.DontUseNativeDialog)
        if not fn:
            return
        if not ffConfig.load_cfgfile(self.cfg, fn, self.cfg['verbosity']):
            mbox = QMessageBox(self)
            mbox.setWindowTitle('Load Preferences Failed')
            mbox.setIcon(QMessageBox.Critical)
            mbox.setStandardButtons(QMessageBox.Ok)
            mbox.setText('%s:\nFile inaccessible or corrupt.' % fn)
            mbox.exec_()
        self.refresh_view()
        self.changed()

    def save(self):
        fn, _ = QFileDialog.getSaveFileName(self, 'Save Preferences', self.cfg['conffile'],
                            'Config Files (*.conf);;All Files (*)',
                            options=QFileDialog.DontUseNativeDialog)
        if not fn:
            return
        eprint(1, 'saving config to:', self.cfg['conffile'])
        self.apply()
        try:
            with open(fn) as file:
                lines = [line.rstrip() for line in file]
        except Exception as e:
            eprint(1, str(e))
            lines = []
        if '[Default]' not in lines:
            lines = ['[Default]']
        for o in self.opt:
            found = False
            repl = '%s=%s' % (o[0], str(self.cfg[o[0]]))
            for i in range(len(lines)):
                if re.match(r'^\s*%s\s*=' % o[0], lines[i]):
                    lines[i] = repl
                    found = True
                    break
            if not found:
                lines.append(repl)
        lines.append('')
        cont = '\n'.join(lines)
        try:
            with open(fn, 'wt') as file:
                file.write(cont)
            self.cfg['conffile'] = fn
        except Exception as e:
            eprint(0, str(e))
            mbox = QMessageBox(self)
            mbox.setWindowTitle('Save Preferences Failed')
            mbox.setIcon(QMessageBox.Critical)
            mbox.setStandardButtons(QMessageBox.Ok)
            mbox.setText(str(e))
            mbox.exec_()
        if self.cfg['verbosity'] > 2:
            eprint(3, cont)

    def apply(self):
        for i in range(len(self.opt)):
            o = self.opt[i]
            w = self.table_widget.cellWidget(i, 0)
            if o[1][0] == 'sfile':
                self.cfg[o[0]] = w.children()[1].text()
            elif o[1][0] == 'edit':
                self.cfg[o[0]] = w.text()
            elif o[1][0] == 'spin' or o[1][0] == 'dblspin':
                self.cfg[o[0]] = w.value()
            elif o[1][0] == 'check':
                self.cfg[o[0]] = w.isChecked()
            elif o[1][0] == 'time':
                t = w.children()[1].time()
                self.cfg[o[0]] = t.hour()*3600 + t.minute()*60 + t.second() + t.msec()/1000
            elif o[1][0] == 'mcombo':
                self.cfg[o[0]] = w.currentText()
            eprint(3, 'apply:', o[0], '=', self.cfg[o[0]])
        self.cfg['outdir'] = make_outdir(self.cfg['outdir'])
        ffConfig.update(self.cfg)
        self.refresh()

    def _fs_browse(self, path, dironly=False):
        def _filedlg():
            if dironly:
                fn = QFileDialog.getExistingDirectory(self, 'Open Directory',
                    path, QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog)
            else:
                fn, _ = QFileDialog.getOpenFileName(self, 'Open File', path,
                            options=QFileDialog.DontUseNativeDialog)
            if fn:
                edit.setText(fn)
        widget = QWidget()
        edit = QLineEdit(path)
        edit.textChanged.connect(self.changed)
        browse = QPushButton('Browse...')
        browse.clicked.connect(_filedlg)
        layout = QHBoxLayout()
        layout.addWidget(edit)
        layout.addWidget(browse)
        layout.setContentsMargins(0, 0, 0, 0)
        widget.setLayout(layout)
        return widget

    def _time_edit(self, h=0, m=0, s=0, ms=0):
        widget = QWidget()
        edit = QTimeEdit(QTime(h, m, s, ms))
        edit.timeChanged.connect(self.changed)
        edit.setDisplayFormat('hh:mm:ss.zzz')
        zero = QPushButton('→ 00:00')
        zero.clicked.connect(lambda: edit.setTime(QTime(0, 0, 0, 0)))
        layout = QHBoxLayout()
        layout.addWidget(edit, 10)
        layout.addWidget(zero, 1)
        layout.setContentsMargins(0, 0, 0, 0)
        widget.setLayout(layout)
        return widget

    def refresh(self):
        self.cfg = ffConfig.get()
        self.refresh_view()

    def refresh_view(self):
        self.table_widget.setUpdatesEnabled(False)
        for i in range(len(self.opt)):
            o = self.opt[i]
            eprint(3, 'refresh:', o[0], '=', self.cfg[o[0]])
            self.table_widget.setVerticalHeaderItem(i, QTableWidgetItem(o[0]))
            self.table_widget.verticalHeaderItem(i).setToolTip(o[2])
            if o[1][0] == 'sfile':
                w = self._fs_browse(self.cfg[o[0]], dironly=o[1][1])
                w.setToolTip(o[2])
            elif o[1][0] == 'edit':
                w = QLineEdit(self.cfg[o[0]])
                w.setMaxLength(o[1][1])
                w.setToolTip(o[2])
                w.textChanged.connect(self.changed)
            elif o[1][0] == 'spin':
                w = QSpinBox()
                w.setRange(o[1][1], o[1][2])
                w.setValue(int(self.cfg[o[0]]))
                w.setToolTip(o[2])
                w.valueChanged.connect(self.changed)
            elif o[1][0] == 'dblspin':
                w = QDoubleSpinBox()
                w.setRange(o[1][1], o[1][2])
                w.setSingleStep(0.05)
                w.setDecimals(2)
                w.setValue(self.cfg[o[0]])
                w.setToolTip(o[2])
                w.valueChanged.connect(self.changed)
            elif o[1][0] == 'check':
                w = QCheckBox('                          ')
                w.setTristate(False)
                w.setCheckState(2 if self.cfg[o[0]] else 0)
                w.setToolTip(o[2])
                w.stateChanged.connect(self.changed)
            elif o[1][0] == 'time':
                rs = self.cfg[o[0]]
                s = round(rs, 0)
                ms = (rs - s) * 1000
                h = s / 3600
                s = s % 3600
                m = s / 60
                s = s % 60
                w = self._time_edit(int(h), int(m), int(s), int(ms))
                w.setToolTip(o[2])
            elif o[1][0] == 'mcombo':
                w = QComboBox()
                w.addItems(['iframe', 'scene', 'skip', 'time', 'customvf'])
                w.setCurrentIndex(w.findText(self.cfg[o[0]]))
                w.setToolTip(o[2])
                w.currentIndexChanged.connect(self.changed)
            self.table_widget.setCellWidget(i, 0, w)
        self.table_widget.setUpdatesEnabled(True)
        self.reset_button.setEnabled(False)


class sMainWindow(QMainWindow):
    """ Application main window class singleton. """
    _instance = None
    tlwidth = 100
    tlheight = 100
    tlabels = []
    thinfo = None
    fname = None
    vfile = None
    vpath = None
    thdir = None
    cur = 0
    view_locked = 0
    _dbg_num_tlabels = 0
    _dbg_num_qobjects = 0

    def __new__(cls, *args, title='', **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self, *args, title='', **kwargs):
        super().__init__(*args, **kwargs)
        ffIcon()
        self.init_window(title)

    def closeEvent(self, event):
        if type(event) == QCloseEvent:
            event.accept()
        die(0)

    # calculate optimal window geometry in ten easy steps
    def optimize_geometry(self):
        if self.windowState() & (Qt.WindowFullScreen | Qt.WindowMaximized):
            return
        # get current window geometry (excluding WM decorations)
        wg = self.geometry()
        wx = max(wg.x(), 0)
        wy = max(wg.y(), 0)
        ww = wg.width()
        wh = wg.height()
        # get frame geometry (including WM dewcorations)
        fg = self.frameGeometry()
        fx = fg.x()
        fy = fg.y()
        fw = fg.width()
        fh = fg.height()
        eprint(3, 'w', wx, wy, ww, wh, 'f', fx, fy, fw, fh)
        # calculate overhead WRT to thumbnail viewport
        scpol = self.scroll.verticalScrollBarPolicy()
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        ow = ww - self.scroll.viewport().width()
        oh = wh - self.scroll.viewport().height()
        self.scroll.setVerticalScrollBarPolicy(scpol)
        # grid granularity (i.e. thumbnail label dimension)
        gw = self.tlwidth
        gh = self.tlheight
        # set minimum window size (i.e. flip-book sized)
        minw = gw + ow
        minh = gh + oh
        self.setMinimumSize(minw, minh)
        eprint(3, 'o', ow, oh, 'g', gw, gh, 'c,r', cfg['grid_columns'], cfg['grid_rows'])
        # get current available(!) screen geometry
        screens = QGuiApplication.screens()
        for sc in reversed(screens):
            scg = sc.availableGeometry()
            sx = scg.x()
            sy = scg.y()
            sw = scg.width()
            sh = scg.height()
            if wx >= sx and wy >= sy and wx < sx+sw and wy < sy+sh:
                break
        eprint(3, 's', sx, sy, sw, sh)
        # tentative (wanted) window geometry
        tx = max(wx, sx)
        ty = max(wy, sy)
        tw = gw * cfg['grid_columns'] + ow
        th = gh * cfg['grid_rows'] + oh
        # available remaining screen estate (right and below)
        aw = sw - (tx - sx)
        ah = sh - (ty - sy)
        eprint(3, 't', tx, ty, tw, th, 'a', aw, ah)
        # try to fit the window on screen, move or resize if necessary
        if tw > aw - (fw - ww):
            frame_left = (fx + fw) - (wx +ww)
            tx = tx - (tw - aw) - frame_left
            tx = max(tx, sx)
            aw = sw - (tx - sx)
            tw = max(minw, min(tw, aw))
        if th > ah - (fh - wh):
            frame_bottom = (fy + fh) - (wy + wh)
            ty = ty - (th - ah) - frame_bottom
            ty = max(ty, sy)
            ah = sh - (ty - sy)
            th = max(minh, min(th, ah))
        # round down window dimensions to thumb grid
        tw = int((tw - ow) / gw) * gw + ow
        th = int((th - oh) / gh) * gh + oh
        eprint(3, 't', tx, ty, tw, th)
        # set final size
        self.setGeometry(tx, ty, tw, th)

    def lock_view(self, lock=True):
        if lock:
            self.view_locked += 1
            self.scroll.setEnabled(False)
            self.set_cursor(disable=True)
        else:
            if self.view_locked > 0:
                self.view_locked -= 1
            if self.view_locked == 0:
                self.scroll.setEnabled(True)
                self.set_cursor(disable=False)

    def rebuild_view(self):
        self.lock_view(True)
        self.scroll.fill_grid(self.tlabels, self.show_progress)
        self.lock_view(False)
        self.set_cursor()

    def clear_view(self):
        self.lock_view(True)
        self.scroll.clear_grid()
        self.cur = 0
        self.tlabels.clear()
        self.lock_view(False)

    def set_cursor(self, idx=None, disable=False):
        l = len(self.tlabels)
        if l < 1:
            self.cur = 0
            return
        try:
            self.tlabels[self.cur].setStyleSheet('')
            if disable:
                return
            bg_hl = self.palette().highlight().color().name()
            fg_hl = self.palette().highlightedText().color().name()
            style_hl = 'QLabel {background-color: %s; color: %s;}' % (bg_hl, fg_hl)
            self.cur = min(max(0, self.cur if idx is None else idx), l - 1)
            self.tlabels[self.cur].setStyleSheet(style_hl)
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
            if proc_running():
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
            pos.setX(pos.x() + int(self.tlwidth / 2))
            pos.setY(pos.y() + int(self.tlheight / 2))
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
            menu.addAction('Open Video File...', lambda: self.load_view(self.vpath))
            if self.fname:
                menu.addAction('Reload', lambda: self.load_view(self.fname))
                menu.addAction('Force Rebuild', self.force_rebuild)
            menu.addSeparator()
            if tlabel or self.fname:
                copymenu = menu.addMenu('Copy')
                if tlabel:
                    copymenu.addAction('Timestamp [H:M:S.ms]', lambda: self.clipboard.setText(s2hms(tlabel.info[2], zerohours=True)))
                    copymenu.addAction('Timestamp [S.ms]', lambda: self.clipboard.setText(tlabel.info[2]))
                if self.fname:
                    copymenu.addAction('Original Filename', lambda: self.clipboard.setText(self.fname))
                if tlabel:
                    copymenu.addAction('Thumb Filename', lambda: self.clipboard.setText(os.path.join(self.thdir, tlabel.info[1])))
                    copymenu.addAction('Thumbnail Image', lambda: self.clipboard.setPixmap(tlabel.layout().itemAt(0).widget().pixmap()))
            menu.addSeparator()
            if not (self.windowState() & (Qt.WindowFullScreen | Qt.WindowMaximized)):
                menu.addAction('Window Best Fit', self.optimize_geometry)
            menu.addAction('Thumbnail Manager', lambda: self.manage_thumbs(cfg['outdir']))
            menu.addAction('Preferences', lambda: self.config_dlg())
        else:
            if proc_running():
                menu.addAction('Abort Operation', self.abort_build)
        menu.addSeparator()
        menu.addAction('Help && About', self.about_dlg)
        menu.addSeparator()
        menu.addAction('Quit', lambda: self.closeEvent(None))
        menu.exec_(pos)

    def manage_thumbs(self, outdir):
        if self.view_locked:
            return
        self.lock_view(True)
        dlg = tmDialog(self, odir=cfg['outdir'])
        res = dlg.exec_()
        if res == QDialog.Accepted:
            lfile = dlg.get_loadfile()
            if lfile:
                self.load_view(lfile)
        self.lock_view(False)

    def config_dlg(self):
        if self.view_locked:
            return
        self.lock_view(True)
        dlg = cfgDialog(self)
        res = dlg.exec_()
        if res == QDialog.Accepted:
            self.load_view(self.fname)
        self.lock_view(False)

    def about_dlg(self):
        dlg = aboutDialog(self)
        res = dlg.exec_()

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
        elif event['type'] == 'scroll_do_update':
            if not self.view_locked:
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
        thumb_layout = tFlowLayout(thumb_frame)
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
        # register shotcuts
        QShortcut('Esc', self).activated.connect(self.esc_action)
        QShortcut('Ctrl+Q', self).activated.connect(lambda: self.closeEvent(None))
        QShortcut('Ctrl+W', self).activated.connect(lambda: self.closeEvent(None))
        QShortcut('F', self).activated.connect(self.toggle_fullscreen)
        QShortcut('Alt+Return', self).activated.connect(self.toggle_fullscreen)
        QShortcut('Ctrl+G', self).activated.connect(self.optimize_geometry)
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
        QShortcut('Ctrl+Alt+P', self).activated.connect(self.config_dlg)
        QShortcut('Alt+H', self).activated.connect(self.about_dlg)


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
        mbox.setWindowTitle('Abort Operation')
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

    def load_view(self, fname):
        self.lock_view(True)
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
            self.lock_view(False)
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
            self.lock_view(False)
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
        self.tlwidth = self.tlabels[0].width()
        self.tlheight = self.tlabels[0].height()
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
        self.optimize_geometry()
        QApplication.processEvents()
        # reset force flag to avoid accidental rebuild for every file
        cfg['force'] = False
        self.lock_view(False)


############################################################
# Helper functions

def proc_cmd(cmd):
    global proc
    if proc:
        return '', '', None
    retval = 0
    try:
        eprint(2, 'run', cmd)
        proc = Popen(cmd, shell=False, stdout=PIPE, stderr=PIPE, env=cfg['env'])
        stdout, stderr = proc.communicate()
        stdout = stdout.decode()
        stderr = stderr.decode()
        retval = proc.wait()
        proc = None
        if retval != 0:
            eprint(0, cmd, '\n  returned %d' % retval)
            eprint(1, stderr)
    except Exception as e:
        eprint(0, cmd, '\n  failed: ' + str(e))
        proc = kill_proc(proc)
    return stdout, stderr, retval

# get video meta information
def get_meta(vidfile):
    meta = { 'frames': -1, 'duration':-1, 'fps':-1.0, 'nsubs': -1 }
    global proc
    if proc:
        return meta, False
    # count subtitle streams
    cmd = [cfg['ffprobe'], '-v', 'error', '-select_streams', 's',
           '-show_entries', 'stream=index', '-of', 'csv=p=0', vidfile]
    out, err, rc = proc_cmd(cmd)
    if rc == 0:
        meta['nsubs'] = len(out.splitlines())
        eprint(2, 'number of subtitle streams:', meta['nsubs'])
    # get frames / duration / fps
    # try ffprobe fast method
    cmd = [cfg['ffprobe'], '-v', 'error', '-select_streams', 'v:0',
           '-show_streams', '-show_format', '-of', 'json', vidfile]
    out, err, rc = proc_cmd(cmd)
    if rc == 0:
        info = json.loads(out)
        strinf = info['streams'][0]
        fmtinf = info['format']
        d = f = None
        fps = -1
        if 'duration' in strinf:
            d = float(strinf['duration'])
        elif 'duration' in fmtinf:
            d = float(fmtinf['duration'])
        if d is not None:
            d = max(d, 0.000001)
            if 'nb_frames' in strinf:
                f = int(strinf['nb_frames'])
                fps = f / d
            elif 'avg_frame_rate' in strinf:
                fps = sfrac2float(strinf['avg_frame_rate'])
                f = int(fps * d)
            if f is not None:
                meta['duration'] = d
                meta['frames'] = f
                meta['fps'] = fps
                return meta, True
    # no dice, try ffprobe slow method
    cmd = [cfg['ffprobe'], '-v', 'error', '-select_streams', 'v:0', '-of', 'json', '-count_packets',
           '-show_entries', 'format=duration:stream=nb_read_packets', vidfile]
    out, err, rc = proc_cmd(cmd)
    if rc == 0:
        info = json.loads(out)
        meta['frames'] = int(info['streams'][0]['nb_read_packets'])
        d = float(info['format']['duration'])
        meta['duration'] = max(d, 0.0001)
        meta['fps'] = round(meta['frames'] / meta['duration'], 2)
        return meta, True
    # ffprobe didn't cut it, try ffmpeg instead
    cmd = [cfg['ffmpeg'], '-nostats', '-i', vidfile, '-c:v', 'copy',
           '-f', 'rawvideo', '-y', os.devnull]
    out, err, rc = proc_cmd(cmd)
    if rc == 0:
        for line in io.StringIO(err).readlines():
            m = re.match(r'^frame=\s*(\d+).*time=\s*(\d+:\d+:\d+(\.\d+)?)', line)
            if m:
                meta['frames'] = int(m.group(1))
                d = hms2s(m.group(2))
                meta['duration'] = max(d, 0.0001)
                meta['fps'] = round(meta['frames'] / meta['duration'], 2)
                return meta, True
    # not our lucky day, eh?!
    return meta, False

# extract thumbnails from video and collect timestamps
def make_thumbs(vidfile, thinfo, thdir, prog_cb=None):
    global proc
    rc = False
    if proc:
        return thinfo, rc

    # ffmpeg filter escaping, see:
    # https://ffmpeg.org/ffmpeg-filters.html#Notes-on-filtergraph-escaping
    def fff_esc(s):
        # 1. escape ' and :
        s = s.replace("'", r"\'").replace(':', r'\:')
        # 2. escape \ and ' and ,
        s = s.replace('\\', '\\\\').replace("'", r"\'")
        # 3. apparently [ and ] also have to be escaped?!
        s = s.replace('[', r'\[').replace(']', r'\]')
        # 4. time will tell, if we're still missing some
        return s

    # generate thumbnail images from video
    pictemplate = '%08d.png'
    cmd = [cfg['ffmpeg'], '-loglevel', 'info', '-hide_banner', '-y']
    if cfg['start']:
        cmd.extend( ['-ss', str(cfg['start'])] )
    if cfg['end']:
        cmd.extend( ['-to', str(cfg['end'])] )
    cmd.extend( ['-i', vidfile] )

    if cfg['method'] == 'scene':
        flt = 'select=gt(scene\,' + str(cfg['scene_thresh']) + ')'
    elif cfg['method'] == 'skip':
        flt = 'select=not(mod(n\,' + str(cfg['frame_skip']) + '))'
    elif cfg['method'] == 'time':
        fs = int(float(cfg['time_skip']) * float(thinfo['fps']))
        flt = 'select=not(mod(n\,' + str(fs) + '))'
    elif cfg['method'] == 'customvf':
        flt = cfg['customvf']
    else: # iframe
        flt = 'select=eq(pict_type\,I)'
    flt += ',showinfo,scale=' + str(cfg['thumb_width']) + ':-1'
    if thinfo['addss'] >= 0 and not cfg['start']:
        flt += ',subtitles=' + fff_esc(vidfile) + ':si=' + str(thinfo['addss'])
    cmd.extend( ['-vf', flt, '-vsync', 'vfr', os.path.join(thdir, pictemplate)] )
    eprint(2, cmd)
    ebuf = ''
    cnt = 0
    try:
        proc = Popen(cmd, shell=False, stderr=PIPE, env=cfg['env'])
        while proc.poll() is None:
            line = proc.stderr.readline()
            if line:
                line = line.decode()
                ebuf += line
                x = re.search(r'pts_time:\d*\.?\d*', line)
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
            eprint(0, cmd, '\n  returned %d' % retval)
            eprint(1, ebuf)
        thinfo['count'] = cnt
        with open(os.path.join(thdir, _FFPREVIEW_IDX), 'w') as idxfile:
            thinfo['date'] = int(time.time())
            json.dump(thinfo, idxfile, indent=2)
            rc = True
    except Exception as e:
        eprint(0, cmd, '\n  failed:', str(e))
        proc = kill_proc(proc)
    return thinfo, rc

# open video in player
def play_video(filename, start='0', paused=False):
    if not filename:
        return

    # keep this for Windows, for the time being
    if cfg['platform'] == 'Windows':
        # prepare argument vector
        cmd = cfg['plpaused'] if paused and cfg['plpaused'] else cfg['player']
        args = shlex.split(cmd)
        for i in range(len(args)):
            args[i] = args[i].replace('%t', start).replace('%f', filename)
        if cfg['verbosity'] > 0:
            cstr = ''
            for a in args:
                cstr += "'" + a + "', "
            eprint(1, 'args = [', cstr + ']')
        Popen(args, shell=False, stdout=DEVNULL, stderr=DEVNULL,
                env=cfg['env'], start_new_session=True)
        return

    # Linux; Darwin?
    # double fork to avoid accumulating zombie processes
    try:
        pid = os.fork()
        if pid > 0:
            eprint(2, '1st fork ok')
            status = os.waitid(os.P_PID, pid, os.WEXITED)
            if status.si_status:
                eprint(0, 'child exit error:', status)
            else:
                eprint(2, 'child exit ok')
            return  # parent: back to business
    except Exception as e:
        eprint(0, '1st fork failed:', str(e))
        os._exit(1)
    # child
    # become session leader and fork a second time
    os.setsid()
    try:
        pid = os.fork()
        if pid > 0:
            eprint(2, '2nd fork ok')
            os._exit(0) # child done
    except Exception as e:
        eprint(0, '2nd fork failed:', str(e))
        os._exit(1)
    # grandchild
    # restore default signal handlers
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGHUP, signal.SIG_DFL)
    signal.signal(signal.SIGQUIT, signal.SIG_DFL)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    # prepare argument vector
    cmd = cfg['plpaused'] if paused and cfg['plpaused'] else cfg['player']
    args = shlex.split(cmd)
    for i in range(len(args)):
        args[i] = args[i].replace('%t', start).replace('%f', filename)
    if cfg['verbosity'] > 0:
        cstr = ''
        for a in args:
            cstr += "'" + a + "', "
        eprint(1, 'args = [', cstr + ']')
    # close all fds and redirect stdin, stdout and stderr to /dev/null
    sys.stdout.flush()
    sys.stderr.flush()
    for fd in range(1024):  # more than enough for us
        try:
            os.close(fd)
        except:
            pass
    os.open(os.devnull, os.O_RDWR)
    os.dup2(0, 1)
    os.dup2(0, 2)
    # execute command
    os.execvpe(args[0], args, cfg['env'])
    os._exit(255)


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
                if idx['nsubs'] != thinfo['nsubs']:
                    return False
                if idx['addss'] != thinfo['addss']:
                    return False
                if idx['method'] != thinfo['method']:
                    return False
                if idx['method'] == 'skip':
                    if not 'frame_skip' in idx or idx['frame_skip'] != thinfo['frame_skip']:
                        return False
                elif idx['method'] == 'time':
                    if not 'time_skip' in idx or idx['time_skip'] != thinfo['time_skip']:
                        return False
                elif idx['method'] == 'scene':
                    if not 'scene_thresh' in idx or idx['scene_thresh'] != thinfo['scene_thresh']:
                        return False
                elif idx['method'] == 'customvf':
                    if not 'customvf' in idx or idx['customvf'] != thinfo['customvf']:
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
        'nsubs': -1,
        'start': cfg['start'],
        'end': cfg['end'],
        'count': 0,
        'width': cfg['thumb_width'],
        'method': cfg['method'],
    }
    # include method specific parameters (only)
    if cfg['method'] == 'scene':
        thinfo['scene_thresh'] = cfg['scene_thresh']
    elif cfg['method'] == 'skip':
        thinfo['frame_skip'] = cfg['frame_skip']
    elif cfg['method'] == 'time':
        thinfo['time_skip'] = cfg['time_skip']
    elif cfg['method'] == 'customvf':
        thinfo['customvf'] = cfg['customvf']
    # set these here for neater ordering
    thinfo['addss'] = cfg['addss']
    thinfo['ffpreview'] = _FFPREVIEW_VERSION
    thinfo['date'] = 0
    thinfo['th'] = []
    # get video file meta info (frames, duration, fps)
    meta, ok = get_meta(vfile)
    if not ok:
        return None, False
    thinfo.update(meta)
    if thinfo['addss'] >= thinfo['nsubs']:
        thinfo['addss'] = -1
    if not cfg['force']:
        chk = chk_idxfile(thinfo, thdir)
        if chk:
            return chk, True
    return thinfo, False

# create output directory
def make_outdir(outdir):
    suffix = 'ffpreview_thumbs'
    if os.path.basename(outdir) != suffix:
        outdir = os.path.join(outdir, suffix)
    try:
        os.makedirs(outdir, exist_ok=True)
        eprint(1, 'outdir', outdir, 'ok')
    except Exception as e:
        eprint(0, str(e))
        return False
    return outdir

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
        if re.match(r'^\d{8}\.png$', f):
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
def get_indexfiles(path, prog_cb=None):
    flist = []
    dlist = os.listdir(path)
    dlen = len(dlist)
    dcnt = 0
    for sd in dlist:
        if prog_cb and not dcnt % 20:
            prog_cb(dcnt, dlen)
        dcnt += 1
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
            if re.match(r'^\d{8}\.png$', f):
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
    cfg = ffConfig().get()
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
    die(app.exec_())

# run application
if __name__== "__main__":
    main()

# EOF
