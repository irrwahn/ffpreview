#!/usr/bin/python3

"""
ffpreview.py

Copyright 2021 Urban Wallasch <irrwahn35@freenet.de>

BSD 3-Clause License

Copyright (c) 2018, Urban Wallasch
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
* reuse existing index / thumbnail files?

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
from subprocess import PIPE, Popen
from tkinter import *
from inspect import currentframe


def eprint(*args, **kwargs):
    print("LINE %d: " % currentframe().f_back.f_lineno, file=sys.stderr, end = '')
    print(*args, file=sys.stderr, **kwargs)

def die():
    global proc
    if proc is not None:
        eprint("killing subprocess: %s" % proc.args)
        proc.kill()
        time.sleep(2)
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
cfg.grid_columns = 5
cfg.thumb_width = 128
cfg.scene_thresh = 0.2 #0.15
#cfg.startts = '0'


# parse command line arguments
parser = argparse.ArgumentParser(description='Generate clickable video thumbnail preview.')
parser.add_argument('filename', help='input video file')
parser.add_argument('-c', '--grid_cols', type=int, metavar='INT', help='number of columns in thumbnail preview ')
parser.add_argument('-s', '--scene_thresh', type=float, metavar='FLOAT', help='scene change detection threshold')
parser.add_argument('-t', '--tmpdir', metavar='PATH', help='path to thumbnail parent directory')
parser.add_argument('-w', '--width', type=int, metavar='INT', help='thumbnail image width in pixel')
args = parser.parse_args()
cfg.vid = args.filename
cfg.tmpdir = args.tmpdir
if args.grid_cols:
    cfg.grid_columns = args.grid_cols
if args.width:
    cfg.thumb_width = args.width
if args.scene_thresh:
    cfg.scene_thresh = args.scene_thresh

# prepare thumbnail directory
if cfg.tmpdir is None:
    cfg.tmpdir = tempfile.gettempdir()
cfg.tmpdir += '/ffpreview_thumbs/' + cfg.vid
try:
    os.makedirs(cfg.tmpdir, exist_ok=True)
except Exception as e:
    eprint(str(e))
    exit(1)
cfg.idxfile = cfg.tmpdir + '/ffpreview.idx'


############################################################
# try to get video container duration

def get_duration(vidfile):
    duration = '(unknown)'
    global proc
    try:
        cmd = 'ffprobe -v error -show_entries'
        cmd += ' format=duration -of default=noprint_wrappers=1:nokey=1'
        cmd += ' "' + vidfile + '"'
        proc = Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE)
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
    return duration

"""
def check_present():
    # TODO: check existing index & thumbs?
    return '0'
"""

############################################################
# extract thumbnails from video and collect timestamps

def make_thumbs(vidfile, ilabel):
    global proc
    pic = cfg.tmpdir + '/%08d.ppm'
    cmd = 'ffmpeg -loglevel info -hide_banner -y -i "' + vidfile + '"'
    cmd += ' -vf "select=gt(scene\,' + str(cfg.scene_thresh) + ')'
    cmd += ',showinfo,scale=' + str(cfg.thumb_width) + ':-1"'
    cmd += ' -vsync vfr "' + pic + '"'
    #eprint(cmd);exit()
    ebuf = ''
    i = 1
    try:
        with open(cfg.idxfile, 'w') as fidx:
            proc = Popen(cmd, shell=True, stderr=PIPE)
            while proc.poll() is None:
                line = proc.stderr.readline()
                if line:
                    line = line.decode()
                    ebuf += line
                    x = re.search("pts_time:\d*\.?\d*", line)
                    if x is not None:
                        t = x.group().split(':')[1]
                        print("%d %08d.ppm %s" % (i, i, t), file=fidx)
                        i += 1
                        ilabel.config(text=t.split('.')[0])
                        root.update()
            retval = proc.wait()
            proc = None
            if retval != 0:
                eprint(ebuf)
                eprint("ffmpeg exit code: %d" % retval)
                exit(retval)
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
container.pack(fill="both", expand=True)

canvas = Canvas(container)
canvas.pack(side="left", fill="both", expand=True)

scrollbar = Scrollbar(container, orient="vertical", command=canvas.yview)
scrollbar.pack(side="right", fill="y")

scrollframe = Frame(canvas)
scrollframe.bind(
    "<Configure>",
    lambda e: canvas.configure(
        scrollregion=canvas.bbox("all")
    )
)

canvas.create_window((0, 0), window=scrollframe, anchor="nw")
canvas.configure(yscrollcommand=scrollbar.set)

def mouse_wheel(event):
    eprint(event)
    direction = 0
    if event.num == 5 or event.delta == -120 or event.keysym == 'Down':
        direction = 1
    if event.num == 4 or event.delta == 120 or event.keysym == 'Up':
        direction = -1
    canvas.yview_scroll(direction, "units")

def bind_mousewheel(event):
    canvas.bind_all("<MouseWheel>", mouse_wheel) # Windows mouse wheel event
    canvas.bind_all("<Button-4>", mouse_wheel) # Linux mouse wheel event (Up)
    canvas.bind_all("<Button-5>", mouse_wheel) # Linux mouse wheel event (Down)
    canvas.bind_all("<Up>", mouse_wheel) # Cursor up key
    canvas.bind_all("<Down>", mouse_wheel) # Cursor down key

def unbind_mousewheel(event):
    canvas.unbind_all("<MouseWheel>")
    canvas.unbind_all("<Button-4>")
    canvas.unbind_all("<Button-5>")
    canvas.unbind_all("<Up>")
    canvas.unbind_all("<Down>")

scrollframe.bind('<Enter>', bind_mousewheel)
scrollframe.bind('<Leave>', unbind_mousewheel)

info1 = Label(scrollframe, text="Processed:", width=10, height=5, anchor="e")
info2 = Label(scrollframe, text="0", width=10, height=5, anchor="e")
info3 = Label(scrollframe, text="", width=12, height=5, anchor="w")
info1.pack(side=LEFT)
info2.pack(side=LEFT)
info3.pack(side=LEFT)

# do the heavy lifting
proc = None
info3.config(text = 'of ' + get_duration(cfg.vid) + ' s')
root.update()
#check_present()
make_thumbs(cfg.vid, info2)


############################################################
# generate clickable thumbnail labels

info1.destroy()
info2.destroy()
info3.destroy()
root.update()

def s2hms(ts):
    s, ms = divmod(float(ts), 1.0)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    res = "%d:%02d:%02d%s" % (h, m, s, ("%.3f" % ms).lstrip('0'))
    return res

def click_thumb(event):
    num = event.widget.grid_info()["row"] * cfg.grid_columns \
        + event.widget.grid_info()["column"] + 1
    cmd = 'mpv --start=' + event.widget.cget("text") + ' --pause "' + cfg.vid + '"'
    Popen(cmd, shell=True)

try:
    with open(cfg.idxfile, 'r') as fidx:
        thumbs=[]
        idx = fidx.readlines()
        x = 0; y = 0
        for line in idx:
            l = line.strip().split(' ')
            i = int(l[0])
            t = l[2]
            thumb=PhotoImage(file=cfg.tmpdir + '/' + l[1])
            thumbs.append(thumb)
            tlabel = Label(scrollframe, text=s2hms(t), image=thumb, compound='top', relief="solid")
            tlabel.grid(column=x, row=y)
            tlabel.bind("<Button-1>", click_thumb)
            x += 1
            if x == cfg.grid_columns:
                x = 0; y += 1
                root.update()
except Exception as e:
    eprint(str(e))
    exit(2)


############################################################
# fix window geometry, start main loop

root.update()
root.geometry("%dx%d" % (scrollframe.winfo_width() + scrollbar.winfo_width(), 600) )
root.mainloop()

# EOF
