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

"""


import io
import glob
import os
import sys
import getopt
import signal
import time
import random
import tempfile
import argparse
import json
from subprocess import PIPE, Popen
from tkinter import *
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
cfg.force = False
cfg.method = 'iframe'
cfg.frame_skip = None
cfg.scene_thresh = None
#cfg.startts = '0'


# parse command line arguments
parser = argparse.ArgumentParser(
    description='Generate clickable video thumbnail preview.',
    epilog='The -i, -f and -s options are mutually exclusive, the last one specified wins.'
)
parser.add_argument('filename', help='input video file')
parser.add_argument('-c', '--grid_cols', type=int, metavar='N', help='number of columns in thumbnail preview ')
parser.add_argument('-w', '--width', type=int, metavar='N', help='thumbnail image width in pixel')
parser.add_argument('-t', '--tmpdir', metavar='path', help='path to thumbnail parent directory')
parser.add_argument('-f', '--force', action='count', help='force rebuilding thumbnails and index')
parser.add_argument('-i', '--iframe', action='count', help='select only I-frames (the default)')
parser.add_argument('-n', '--nskip', type=int, metavar='N', help='select only every Nth frame')
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
    cfg.method = 'skip'
if args.iframe:
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
    'count': 0,
    'width': cfg.thumb_width,
    'method': cfg.method,
    'frame_skip': cfg.frame_skip,
    'scene_thresh': cfg.scene_thresh,
    'date':0,
    'th':[]
}


############################################################
# try to get video container duration

def get_duration(vidfile):
    duration = '-1'
    global proc
    try:
        cmd = 'ffprobe -v error -show_entries'
        cmd += ' format=duration -of default=noprint_wrappers=1:nokey=1'
        cmd += ' "' + vidfile + '"'
        proc = Popen('exec ' + cmd, shell=True, stdout=PIPE, stderr=PIPE)
        stdout, stderr = proc.communicate()
        retval = proc.wait()
        proc = None
        if retval == 0:
            duration = stdout.decode().split('.')[0]
        else:
            eprint('ffprobe:')
            eprint(stderr.decode())
    except Exception as e:
        eprint(str(e))
    return int(duration)


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

def make_thumbs(vidfile, ilabel):
    global proc
    pictemplate = '%08d.png'
    cmd = 'ffmpeg -loglevel info -hide_banner -y -i "' + vidfile + '"'
    if cfg.method == 'scene':
        cmd += ' -vf "select=gt(scene\,' + str(cfg.scene_thresh) + ')'
    elif cfg.method == 'skip':
        cmd += ' -vf "select=not(mod(n\,' + str(cfg.frame_skip) + '))'
    else:
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
                    ilabel.config(text=t.split('.')[0])
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
        eprint(str(e))
        exit(1)


############################################################
# initialize window

root = Tk()
root.wm_title(cfg.vid)
root.bind('<Escape>', die_ev)
root.bind('<Control-w>', die_ev)
root.bind('<Control-q>', die_ev)

container = Frame(root)
container.pack(fill='both', expand=True)

canvas = Canvas(container)
canvas.pack(side='left', fill='both', expand=True)

scrollbar = Scrollbar(container, orient='vertical', command=canvas.yview)
scrollbar.pack(side='right', fill='y')

scrollframe = Frame(canvas)
scrollframe.bind(
    '<Configure>',
    lambda e: canvas.configure(
        scrollregion=canvas.bbox('all')
    )
)

canvas.create_window((0, 0), window=scrollframe, anchor='nw')
canvas.configure(yscrollcommand=scrollbar.set)

def page_scroll(event):
    if event.keysym == 'Next':
        canvas.yview_scroll(1, 'pages')
    if event.keysym == 'Prior':
        canvas.yview_scroll(-1, 'pages')

def home_end_scroll(event):
    if event.keysym == 'Home':
        canvas.yview_moveto(0)
    if event.keysym == 'End':
        canvas.yview_moveto(1)

def mouse_wheel(event):
    if event.num == 5 or event.delta == -120 or event.keysym == 'Down':
        canvas.yview_scroll(1, 'units')
    if event.num == 4 or event.delta == 120 or event.keysym == 'Up':
        canvas.yview_scroll(-1, 'units')

def bind_mousewheel(event):
    canvas.bind_all('<MouseWheel>', mouse_wheel) # Windows mouse wheel event
    canvas.bind_all('<Button-4>', mouse_wheel) # Linux mouse wheel event (Up)
    canvas.bind_all('<Button-5>', mouse_wheel) # Linux mouse wheel event (Down)

def unbind_mousewheel(event):
    canvas.unbind_all('<MouseWheel>')
    canvas.unbind_all('<Button-4>')
    canvas.unbind_all('<Button-5>')

scrollframe.bind('<Enter>', bind_mousewheel)
scrollframe.bind('<Leave>', unbind_mousewheel)
canvas.bind_all('<Up>', mouse_wheel) # CursorUp key
canvas.bind_all('<Down>', mouse_wheel) # CursorDown key
canvas.bind_all('<Home>', home_end_scroll) # Home key
canvas.bind_all('<End>', home_end_scroll) # End key
canvas.bind_all('<Prior>', page_scroll) # PageUp key
canvas.bind_all('<Next>', page_scroll) # PageDn key


############################################################
# rebuild thumbnails and index, if necessary

proc = None
dur = get_duration(cfg.vid)
thinfo['duration'] = dur
thinfo['date'] = int(time.time())
if cfg.force or not chk_idxfile():
    stale = [f for f in glob.glob(cfg.tmpdir + '/*.png') if re.match('^' + cfg.tmpdir + '/\d{8}\.png$', f)]
    stale.append(cfg.idxfile)
    for f in stale:
        try:
            os.unlink(f)
        except Exception as e:
            #eprint(str(e))
            pass
    info1 = Label(scrollframe, text='Processed:', width=10, height=5, anchor='e')
    info2 = Label(scrollframe, text='0', width=10, height=5, anchor='e')
    info3 = Label(scrollframe, text='of ' + (str(dur),'(unknown)')[dur<= 0] + ' s', width=12, height=5, anchor='w')
    info1.pack(side=LEFT)
    info2.pack(side=LEFT)
    info3.pack(side=LEFT)
    root.update()
    make_thumbs(cfg.vid, info2)
    info1.destroy()
    info2.destroy()
    info3.destroy()
    root.update()


############################################################
# generate clickable thumbnail labels

def s2hms(ts):
    s, ms = divmod(float(ts), 1.0)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    res = '%d:%02d:%02d%s' % (h, m, s, ('%.3f' % ms).lstrip('0'))
    return res

def click_thumb(event):
    cmd = 'mpv --start=' + event.widget.cget('text') + ' --pause "' + cfg.vid + '"'
    Popen('exec ' + cmd, shell=True)

try:
    with open(cfg.idxfile, 'r') as idxfile:
        idx = json.load(idxfile)
        thumbs=[]
        x = 0; y = 0
        for th in idx['th']:
            thumb = PhotoImage(file=cfg.tmpdir + '/' + th[1])
            thumbs.append(thumb)
            tlabel = Label(scrollframe, text=s2hms(th[2]), image=thumb, compound='top', relief='solid')
            tlabel.grid(column=x, row=y)
            tlabel.bind('<Button-1>', click_thumb)
            x += 1
            if x == cfg.grid_columns:
                x = 0; y += 1
                root.update()
        root.update()
        canvas.configure(yscrollincrement=tlabel.winfo_height())
except Exception as e:
    eprint(str(e))
    exit(2)


############################################################
# fix window geometry, start main loop

root.update()
root.geometry('%dx%d' % (scrollframe.winfo_width() + scrollbar.winfo_width(), 600) )
root.mainloop()

# EOF
