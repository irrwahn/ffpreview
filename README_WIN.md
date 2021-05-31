# Running FFpreview on Windows

The steps outlined below _should_ make it possible to run `ffpreview` on
Windows. Note, however, that neither was this tested extensively, nor can
the author provide any detailed technical support for this scenario.

## Install the required software packages

If any of the software mentioned below is already installed, just skip
the corresponding step and adjust the configuration to fit your actual
setup.

1. **Install Python 3.** At least version 3.7.x is recommended, but
versions 3.8.x and 3.9.x should work fine, too. Official releases can be
found at [python.org](https://www.python.org/downloads/windows/).

2. **Install PyQt5.** Run `pip install pyqt5` from the command prompt.

3. **Install FFmpeg.** _(mandatory)_ FFmpeg binaries can be downloaded by
following the links listed on [ffmpeg.org](https://ffmpeg.org/download.html#build-windows).
For the sole purpose of running FFpreview any of the 'essentials' builds
should be sufficient.

4. **Install mpv.** _(optional)_ Though not strictly necessary, the mpv
video player is strongly recommended as player back end for FFpreview.
Links to download locations for mpv binaries are listed at
[mpv.io](https://mpv.io/installation/). Other media players like e.g. vlc
may work as well, yet some may not provide all the options desirable for
optimal use with FFpreview.

5. **Create `ffpreview.conf`** configuration file containing at least
the essential settings described in the next section.

## Configure FFpreview

The following instructions assume that `ffpreview.py`, `ffmpeg` and `mpv`
have all been installed in a directory structure like this:

```
...(some_directory)
   |
   +--ffpreview
   |  |
   .  +--ffmpeg
   .  |  +--ffmpeg.exe
   .  |  +--ffprobe.exe
      |  + ...
      |
      +--mpv
      |  +--mpv.exe
      |  +--...(mpv support files)
      |
      +--ffpreview.py
      +--ffpreview.conf
```
The `ffpreview.conf` file can be created by appropriately renaming a copy
of `ffpreview.conf.sample`, included in the FFpreview repository.

Taking as a basis the directory structure outlined above, and further
assuming `ffpreview.py` is started from within the `ffpreview` directory
itself, `ffpreview.conf` should at the very least contain the following
settings:
```
[Default]

ffprobe=ffmpeg\ffprobe.exe

ffmpeg=ffmpeg\ffmpeg.exe

player=mpv\mpv --no-ordered-chapters --start=%t %f

plpaused=mpv\mpv --no-ordered-chapters --start=%t --pause %f
```
In case FFmpeg or mpv are installed in some other location the respective
paths to these tools have to be adjusted accordingly. All other settings
can be tweaked as desired following the hints in `ffpreview.conf.sample`,
or can simply be omitted to use the default values.

Should things not work as expected, the messages FFpreview prints to the
console when it was started from the command prompt may give some clues.
Passing the `-v` option one or more times prompts FFpreview to produce
more verbose output which may further aid in debugging the setup.

Once again: While it has been shown that FFpreview can in principle be
made to run on Windows, the author cannot offer any individual support
in case things go sideways. **You have been warned!** If, on the other
hand, you have the strong suspicion you found a genuine bug in FFpreview
itself, or would like to make any suggestions on how to improve it (or
these very instructions, for that matter), please feel encouraged to
report the issue on [GitHub](https://github.com/irrwahn/ffpreview).

----------------------------------------------------------------------
