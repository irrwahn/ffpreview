# Ffpreview

Ffpreview is a python script that acts as a front-end for ffmpeg to
generate interactive thumbnail previews for video files.

![ffpreview_1r](https://user-images.githubusercontent.com/21294666/119243320-dedc4280-bb65-11eb-8343-29104805447c.png)

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

Tested with Python 3.7.3, PyQt5 5.11.3.

**Ffpreview has only been tested to work on Linux.**

## Configuration

With few exceptions all options available from the command line (see
below), plus the incantations to start external programs, can be specified
in the configuration file. For more detailed information please refer to
the `ffpreview.conf.sample` example configuration file.

## Video filters

The thumbnail sampling method presets available in `ffpreview` translate
to `ffmpeg` filters as follows:

`iframe` (_--iframe_, key frame detection):
>   `-vf select=eq(pict_type,I), ...`

`scene` (_--scene_, automatic scene change detection):
>   `-vf select=gt(scene,SCENE_THRESHOLD), ...`

`skip` (_--nskip_, skip number of frames):
>   `-vf select=not(mod(n,NUM_FRAMES)), ...`

`time` (_--nsecs_, skip number of seconds):
>   `-vf select=not(mod(n,NUM_SECONDS*FPS)), ...`

`customvf` (_--customvf_, custom video filter):
>   `-vf YOUR_CUSTOM_FILTER, ...`

Please consult the official `ffmpeg`
[documentation](https://ffmpeg.org/ffmpeg-filters.html#select_002c-aselect)
to learn more about video frame select filter expressions.

## Usage

Running `ffpreview.py -h` will print the following help text:

```
usage: ffpreview.py [-h] [-b] [-m] [-c F] [-g G] [-w N] [-o P] [-f] [-r] [-i]
                    [-n N] [-N F] [-s F] [-C S] [-S T] [-E T] [-v] [--version]
                    [filename [filename ...]]

Generate interactive video thumbnail preview.

positional arguments:
  filename            input video file

optional arguments:
  -h, --help          show this help message and exit
  -b, --batch         batch mode, do not draw window
  -m, --manage        start with thumbnail manager
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
  ESC               leave full screen view, quit application
  Ctrl+Q, Ctrl-W    quit application
  Alt+Return, F     toggle full screen view
  Ctrl+G            adjust window geometry for optimal fit
  Ctrl+O            show open file dialog
  Ctrl+M            open thumbnail manager
  Ctrl+Alt+P        open preferences dialog
  Alt+H             open about dialog
  Double-click,
  Return, Space     open video at selected position in paused state
  Shift+dbl-click,
  Shift+Return      play video starting at selected position
  Mouse-2, Menu,
  Ctrl+Return       open the context menu
  Up, Down,
  PgUp, PgDown,
  Home, End,
  TAB, Shift+TAB    move highlighted selection marker
```
### Notes

* In GUI mode, the --force flag is reset after the first view is
  loaded, to prevent accidental rebuilds for subsequently opened
  files. A forced rebuild can be initiated anytime via the context
  menu. In batch mode the --force flag is applied to all input files.

* The thumbnail manager, accessible via context menu or command line
  option, provides a simple way to keep track of saved previews
  thumbnails and allows for easy loading of previews or deletion of
  broken/unwanted preview folders.

### Examples

#### Start ffpreview with file open dialog:
```
$ ./ffpreview.py
$ ./ffpreview.py /path/to/some/directory
```

#### Start ffpreview and show thumbnails for a single file:
```
$ ./ffpreview.py my_video.mp4
$ ./ffpreview.py -o ~/scratch -w 256 -g 8x4 -N 10 some_movie.mkv
```

#### Start ffpreview in thumbnail manager mode:
```
$ ./ffpreview.py -m
```

#### Run ffpreview in batch mode (console only, no GUI):
```
$ ./ffpreview.py -b movie1.mkv movie2.mp4 another.mpg
$ ./ffpreview.py -b /some/directory/*
```
**Note:** `ffpreview` does _not_ recursively traverse subdirectories.

## License

Ffpreview is distributed under the Modified ("3-clause") BSD License.
See `LICENSE` file for more information.

----------------------------------------------------------------------
