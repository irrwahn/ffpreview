# Ffpreview

Ffpreview is a python script that acts as a front-end for ffmpeg to
generate interactive thumbnail previews for video files.


## Requirements

To run `ffpreview.py` you will need Python version >= 3.7 installed,
plus the PyQt5 python bindings for Qt5.

As a hard requirement, ffpreview relies on the `ffmpeg` program to
generate the still frame thumbnails.  To make full use of the interactive
aspects you will need a video player application that can be started from
the command line. The `mpv` player is highly recommended, as it readily
accepts all options passed by ffpreview.  Other video players (like e.g.
`vlc`) may only provide slightly degraded functionality.

Additionally, ffpreview will use `ffprobe` (if available) to perform the
initial video file analysis, but will gracefully fall back to `ffmpeg`
for that purpose too, should `ffprobe` fail.

**NOTE: Ffpreview has only been tested to work on Linux.**


## Configuration

With few exceptions all options available from the command line (see
below) plus the invocations to start external programs can be specified
in the configuration file, for details see the `ffpreview.conf.sample`
example configuration.


## Usage

Running `ffpreview.py -h` will print the following help text:

```
usage: ffpreview.py [-h] [-b] [-c F] [-g G] [-w N] [-o P] [-f] [-r] [-i]
                    [-n N] [-N F] [-s F] [-C S] [-S T] [-E T] [-v] [--version]
                    [filename]

Generate interactive video thumbnail preview.

positional arguments:
  filename            input video file

optional arguments:
  -h, --help          show this help message and exit
  -b, --batch         batch mode, do not draw window
  -c F, --config F    read configuration from file F
  -g G, --grid G      set grid geometry in COLS[xROWS] format
  -w N, --width N     thumbnail image width in pixel
  -o P, --outdir P    set thumbnail parent directory to P
  -f, --force         force thumbnail and index rebuild
  -r, --reuse         reuse filter settings from index file
  -i, --iframe        select only I-frames (default)
  -n N, --nskip N     select only every Nth frame
  -N F, --nsecs F     select one frame every F seconds
  -s F, --scene F     select by scene change threshold; 0 < F < 1
  -C S, --customvf S  select frames using custom filter string S
  -S T, --start T     start video analysis at time T
  -E T, --end T       end video analysis at time T
  -v, --verbose       be more verbose; repeat to increase
  --version           print version info and exit

  The -C, -i, -N, -n and -s options are mutually exclusive. If more
  than one is supplied: -C beats -i beats -N beats -n beats -s.

  The -r option causes ffpreview to ignore any of the -w, -C, -i
  -N, -n and -s options, provided that filename, duration, start
  and end times match, and the index file appears to be healthy.

window controls:
  ESC, Ctrl+Q     quit application
  Ctrl+G          adjust window geometry to optimal fit
  Ctrl+O          show open file dialog
  Double-click    open video at clicked position in paused state
  Shift-click     play video starting at clicked position
  Mouse-2         open the context menu
  Up, Down,
  PgUp, PgDown,
  Home, End       move highlighted selection marker
  Enter           open video at selected position in paused state
  Shift+Enter     play video starting at selected position
  Alt+Enter       open the context menu
```
### Examples

#### Start ffpreview with file open dialog:
```
$ ./ffpreview.py
$ ./ffpreview.py /path/to/some/directory
```

#### Start ffpreview and show thumbnails for a single file:
```
$ ./ffpreview.py my_video.mp4
$ ./ffpreview.py -t ~/scratch -w 256 -g 8x4 -N 10 some_movie.mkv
```

#### Run ffpreview in batch mode:
```
$ ./ffpreview.py -b movie1.mkv movie2.mp4 another.mpg
```

## License

Ffpreview is distributed under the Modified ("3-clause") BSD License.
See `LICENSE` file for more information.

----------------------------------------------------------------------
