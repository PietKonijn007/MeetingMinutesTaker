"""Meeting Minutes Taker — local-first meeting recording and transcription tool."""

# Install the macOS objc duplicate-class stderr filter as early as possible —
# before any submodule import can pull in faster-whisper (PyAV) or torchcodec
# (Homebrew FFmpeg), which together produce noisy duplicate-class warnings on
# darwin. No-op elsewhere; opt out via MM_QUIET_OBJC=0.
from . import _quiet_objc as _quiet_objc

_quiet_objc.install()

__version__ = "0.1.0"
