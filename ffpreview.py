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

"""
TODO:

* support more ffmpeg select filters?
* make player configurable?
* option to specify start and end time?

"""


import io
import os
import sys
import signal
import time
import re
import tempfile
import argparse
import json
from subprocess import PIPE, Popen
import tkinter as tk
from tkinter import ttk
from inspect import currentframe


def eprint(*args, **kwargs):
    print('LINE %d: ' % currentframe().f_back.f_lineno, file=sys.stderr, end = '')
    print(*args, file=sys.stderr, **kwargs)

def die():
    global proc
    if proc is not None:
        eprint('killing subprocess: %s' % proc.args)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    exit()

def die_ev(event):
    die()

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

class Config:
    pass
cfg = Config()
cfg.vid = ''
cfg.tmpdir = None
cfg.idxfile = ''
cfg.grid_columns = 5
cfg.thumb_width = 128
cfg.hightlightcolor="lightsteelblue1"
cfg.force = False
cfg.method = 'iframe'
cfg.frame_skip = None
cfg.time_skip = None
cfg.scene_thresh = None
#cfg.startts = '0'


# parse command line arguments
parser = argparse.ArgumentParser(
    description='Generate clickable video thumbnail preview.',
    epilog='The -i, -N, -n and -s options are mutually exclusive, -i beats -N beats -n beats -s.'
)
parser.add_argument('filename', help='input video file')
parser.add_argument('-c', '--grid_cols', type=int, metavar='N', help='number of columns in thumbnail preview ')
parser.add_argument('-w', '--width', type=int, metavar='N', help='thumbnail image width in pixel')
parser.add_argument('-t', '--tmpdir', metavar='path', help='path to thumbnail parent directory')
parser.add_argument('-f', '--force', action='count', help='force rebuilding thumbnails and index')
parser.add_argument('-i', '--iframe', action='count', help='select only I-frames (the default)')
parser.add_argument('-n', '--nskip', type=int, metavar='N', help='select only every Nth frame')
parser.add_argument('-N', '--nsecs', type=int, metavar='F', help='select one frame every F seconds')
parser.add_argument('-s', '--scene', type=float, metavar='F', help='select by scene change threshold (slow!); 0 < F < 1')
args = parser.parse_args()
cfg.vid = args.filename
cfg.tmpdir = args.tmpdir
if args.grid_cols:
    cfg.grid_columns = args.grid_cols
if args.width:
    cfg.thumb_width = args.width
if args.force:
    cfg.force = True
if args.scene:
    cfg.scene_thresh = args.scene
    cfg.method = 'scene'
if args.nskip:
    cfg.frame_skip = args.nskip
    cfg.scene_thresh = None
    cfg.method = 'skip'
if args.nsecs:
    cfg.time_skip = args.nsecs
    cfg.frame_skip = None
    cfg.scene_thresh = None
    cfg.method = 'time'
if args.iframe:
    cfg.time_skip = None
    cfg.frame_skip = None
    cfg.scene_thresh = None
    cfg.method = 'iframe'

# prepare thumbnail directory
if cfg.tmpdir is None:
    cfg.tmpdir = tempfile.gettempdir()
cfg.tmpdir += '/ffpreview_thumbs/' + os.path.basename(cfg.vid)
try:
    os.makedirs(cfg.tmpdir, exist_ok=True)
except Exception as e:
    eprint(str(e))
    exit(1)
cfg.idxfile = cfg.tmpdir + '/ffpreview.idx'


# Initialize thumbnail info structure
thinfo = {
    'name': os.path.basename(cfg.vid),
    'duration': -1,
    'fps': -1,
    'count': 0,
    'width': cfg.thumb_width,
    'method': cfg.method,
    'frame_skip': cfg.frame_skip,
    'time_skip': cfg.time_skip,
    'scene_thresh': cfg.scene_thresh,
    'date':0,
    'th':[]
}


############################################################
# try to get video container duration

def get_meta(vidfile):
    meta = { 'duration':-1, 'fps':-1.0 }
    global proc
    try:
        cmd = 'ffprobe -v error -select_streams v -of json'
        cmd += ' -show_entries format=duration:stream=avg_frame_rate'
        cmd += ' "' + vidfile + '"'
        proc = Popen('exec ' + cmd, shell=True, stdout=PIPE, stderr=PIPE)
        stdout, stderr = proc.communicate()
        retval = proc.wait()
        proc = None
        if retval == 0:
            info = json.loads(stdout.decode())
            fr = info['streams'][0]['avg_frame_rate'].split('/')
            meta['fps'] = round(float(fr[0]) / float(fr[1]), 2)
            meta['duration'] = int(info['format']['duration'].split('.')[0])
        else:
            eprint('ffprobe:')
            eprint(stderr.decode())
    except Exception as e:
        eprint(str(e))
    return meta


############################################################
# check validity of existing index file

def chk_idxfile():
    global thinfo
    try:
        with open(cfg.idxfile, 'r') as idxfile:
            chk = json.load(idxfile)
            if chk['name'] != thinfo['name']:
                return False
            if chk['duration'] != thinfo['duration']:
                return False
            if chk['width'] != thinfo['width']:
                return False
            if chk['method'] != thinfo['method']:
                return False
            if chk['frame_skip'] != thinfo['frame_skip']:
                return False
            if chk['scene_thresh'] != thinfo['scene_thresh']:
                return False
            if chk['count'] != len(chk['th']):
                return False
            # do something with date?
            thinfo = chk
            return True
    except Exception as e:
        pass
    return False


############################################################
# extract thumbnails from video and collect timestamps

def make_thumbs(vidfile, ilabel, pbar):
    global proc
    pictemplate = '%08d.png'
    cmd = 'ffmpeg -loglevel info -hide_banner -y -i "' + vidfile + '"'
    if cfg.method == 'scene':
        cmd += ' -vf "select=gt(scene\,' + str(cfg.scene_thresh) + ')'
    elif cfg.method == 'skip':
        cmd += ' -vf "select=not(mod(n\,' + str(cfg.frame_skip) + '))'
    elif cfg.method == 'time':
        fs = int(float(cfg.time_skip) * float(thinfo['fps']))
        cmd += ' -vf "select=not(mod(n\,' + str(fs) + '))'
    else: # iframe
        cmd += ' -vf "select=eq(pict_type\,I)'
    cmd += ',showinfo,scale=' + str(cfg.thumb_width) + ':-1"'
    cmd += ' -vsync vfr "' + cfg.tmpdir + '/' + pictemplate + '"'
    eprint(cmd)
    ebuf = ''
    cnt = 0
    try:
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
                    thinfo['th'].append([ cnt, pictemplate % cnt, t ])
                    ilabel.config(text='%s / %d s' % (t.split('.')[0], thinfo['duration']))
                    pbar['value'] = float(t) * 100 / thinfo['duration']
                    root.update()
        retval = proc.wait()
        proc = None
        if retval != 0:
            eprint(ebuf)
            eprint('ffmpeg exit code: %d' % retval)
            exit(retval)
        thinfo['count'] = cnt
        with open(cfg.idxfile, 'w') as idxfile:
            json.dump(thinfo, idxfile, indent=2)
    except Exception as e:
        exit(1)


############################################################
# initialize window

ffpreview_png = '''
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
Ut0AAAAASUVORK5CYII=
'''

root = tk.Tk(className='ffpreview')
root.title('ffpreview - '+ cfg.vid)
ffpreview_ico = tk.PhotoImage(data=ffpreview_png)
root.iconphoto(False, ffpreview_ico)
root.bind('<Escape>', die_ev)
root.bind('<Control-w>', die_ev)
root.bind('<Control-q>', die_ev)

statbar = tk.Frame(root)
statbar.pack(side='bottom', fill='x')
stat = []
for i in range(3):
    s = tk.Label(statbar, text='', width=20, height=1, relief='flat', anchor='sw')
    s.pack(side='left', fill='x')
    stat.append(s)
progbar = ttk.Progressbar(statbar, orient=tk.HORIZONTAL, length=100, mode='determinate')
progbar.pack(expand=True)

container = tk.Frame(root)
container.pack(fill='both', expand=True)
canvas = tk.Canvas(container)
canvas.pack(side='left', fill='both', expand=True)
scrollbar = ttk.Scrollbar(container, orient='vertical', command=canvas.yview)
scrollbar.pack(side='right', fill='y')
scrollframe = tk.Frame(canvas)
scrollframe.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
canvas.create_window((0, 0), window=scrollframe, anchor='nw')
canvas.configure(yscrollcommand=scrollbar.set)

def on_scroll(event):
    if event.keysym == 'Next':
        canvas.yview_scroll(1, 'pages')
    elif event.keysym == 'Prior':
        canvas.yview_scroll(-1, 'pages')
    elif event.keysym == 'Home':
        canvas.yview_moveto(0)
    elif event.keysym == 'End':
        canvas.yview_moveto(1)
    elif event.num == 5 or event.delta == -120 or event.keysym == 'Down':
        canvas.yview_scroll(1, 'units')
    elif event.num == 4 or event.delta == 120 or event.keysym == 'Up':
        canvas.yview_scroll(-1, 'units')

def bind_mousewheel(event):
    canvas.bind_all('<MouseWheel>', on_scroll) # Windows mouse wheel event
    canvas.bind_all('<Button-4>', on_scroll) # Linux mouse wheel event (Up)
    canvas.bind_all('<Button-5>', on_scroll) # Linux mouse wheel event (Down)

def unbind_mousewheel(event):
    canvas.unbind_all('<MouseWheel>')
    canvas.unbind_all('<Button-4>')
    canvas.unbind_all('<Button-5>')

container.bind_all('<Enter>', bind_mousewheel)
container.bind_all('<Leave>', unbind_mousewheel)
container.bind_all('<Up>', on_scroll)    # CursorUp key
container.bind_all('<Down>', on_scroll)  # CursorDown key
container.bind_all('<Home>', on_scroll)  # Home key
container.bind_all('<End>', on_scroll)   # End key
container.bind_all('<Prior>', on_scroll) # PageUp key
container.bind_all('<Next>', on_scroll)  # PageDn key

root.update()

############################################################
# rebuild thumbnails and index, if necessary

proc = None
thinfo.update(get_meta(cfg.vid))
thinfo['date'] = int(time.time())
if cfg.force or not chk_idxfile():
    try:
        os.unlink(cfg.idxfile)
    except Exception as e:
        pass
    for f in os.listdir(cfg.tmpdir):
        if re.match('^\d{8}\.png$', f):
            try:
                os.unlink(cfg.tmpdir + '/' + f)
            except Exception as e:
                pass
    stat[0].config(text='Processing video:'),
    make_thumbs(cfg.vid, stat[1], progbar)


############################################################
# generate clickable thumbnail labels

def s2hms(ts):
    s, ms = divmod(float(ts), 1.0)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    res = '%d:%02d:%02d%s' % (h, m, s, ('%.3f' % ms).lstrip('0'))
    return res

def click_thumb(event):
    cmd = 'mpv --no-ordered-chapters --start=' + event.widget.cget('text') + ' --pause "' + cfg.vid + '"'
    Popen('exec ' + cmd, shell=True)

try:
    with open(cfg.idxfile, 'r') as idxfile:
        idx = json.load(idxfile)
        thumbs=[]
        tlabels=[]
        stat[0].config(text='Loading:')
        for th in idx['th']:
            if th[0] % 100 == 0:
                stat[1].config(text='%d / %d' % (th[0], thinfo['count']))
                progbar['value'] = th[0] * 100 / thinfo['count']
                root.update()
            thumb = tk.PhotoImage(file=cfg.tmpdir + '/' + th[1])
            thumbs.append(thumb)
            tlabel = tk.Label(scrollframe, text=s2hms(th[2]), image=thumb, compound='top', relief='solid')
            tlabel.bind('<Button-1>', click_thumb)
            tlabel.bind("<Enter>", lambda event: event.widget.config(bg=cfg.hightlightcolor))
            tlabel.bind("<Leave>", lambda event: event.widget.config(bg=scrollframe["background"]))
            tlabels.append(tlabel)
        tlwidth = tlabel.winfo_reqwidth()
        tlheight = tlabel.winfo_reqheight()
except Exception as e:
    eprint(str(e))
    exit(2)


############################################################
# fix window geometry, start main loop

def fill_grid(cols):
    x = 0; y = 0
    for tl in tlabels:
        tl.grid(column=x, row=y)
        x += 1
        if x == cols:
            x = 0; y += 1

def on_resize(event):
    cols = cfg.grid_columns
    cw = cols * tlwidth
    rw = canvas.winfo_width()
    if rw < cw and cols > 1:
        cols -= 1
    elif rw > cw + tlwidth:
        cols += 1
    if cols != cfg.grid_columns:
        cfg.grid_columns = cols
        fill_grid(cols)

progbar.forget()
stat[0].config(text=' Duration: ' + str(thinfo["duration"]) + ' s')
stat[1].config(text=' Thumbs: ' + str(thinfo["count"]))
stat[2].config(text=' Method: ' + str(thinfo["method"]))
canvas.configure(yscrollincrement=tlheight)
root.bind("<Configure>", on_resize)
root.minsize(tlwidth, tlheight)
root.geometry('%dx%d' % (tlwidth*cfg.grid_columns+scrollbar.winfo_reqwidth()+1,
                         5.2*tlheight+statbar.winfo_reqheight()) )
root.mainloop()

# EOF
