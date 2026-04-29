"""Suppress macOS Objective-C duplicate-class warnings on stderr.

faster-whisper pulls in PyAV, which vendors its own libavdevice, while
torchcodec (a pyannote dep) links against the system FFmpeg. Both copies
register the same AVFoundation receiver classes (AVFFrameReceiver,
AVFAudioReceiver), so the Objective-C runtime prints a noisy warning at
startup and again whenever the second dylib gets loaded:

    objc[12345]: Class AVFFrameReceiver is implemented in both
    .../av/.dylibs/libavdevice.62.1.100.dylib (0x...) and
    /opt/homebrew/.../libavdevice.62.3.100.dylib (0x...). This may cause
    spurious casting failures and mysterious crashes. ...

The classes belong to FFmpeg's `avfoundation` capture-input device, which
neither library actually invokes (both decode files), so the duplicate
registration is harmless — just noisy.

This module installs a tiny stderr filter that drops only those specific
lines. It's a no-op on non-darwin and can be disabled by setting
MM_QUIET_OBJC=0 in the environment.

Implementation note: the warning is emitted by the Objective-C runtime
in C (fprintf(stderr, ...)), so a Python sys.stderr wrapper wouldn't
catch it. We dup2 a pipe over fd 2 and pump filtered output back through
a daemon thread.
"""

from __future__ import annotations

import os
import sys
import threading

_installed = False


def install() -> None:
    """Install the stderr filter. Safe to call multiple times."""
    global _installed
    if _installed:
        return
    if sys.platform != "darwin":
        _installed = True
        return
    if os.environ.get("MM_QUIET_OBJC", "1") == "0":
        _installed = True
        return

    try:
        read_fd, write_fd = os.pipe()
        real_stderr_fd = os.dup(2)
        os.dup2(write_fd, 2)
        os.close(write_fd)
    except OSError:
        # If we can't manipulate stderr (sandboxed runner, weird env), bail
        # silently — the warning isn't worth crashing over.
        _installed = True
        return

    def _pump() -> None:
        # Bytes mode so we never wedge on partial UTF-8 sequences.
        with os.fdopen(read_fd, "rb", buffering=0) as src, os.fdopen(
            real_stderr_fd, "wb", buffering=0
        ) as dst:
            buf = b""
            while True:
                chunk = src.read(4096)
                if not chunk:
                    break
                buf += chunk
                # Process complete lines; keep the trailing partial line in buf.
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not _should_drop(line):
                        dst.write(line + b"\n")
            if buf and not _should_drop(buf):
                dst.write(buf)

    t = threading.Thread(target=_pump, name="mm-stderr-filter", daemon=True)
    t.start()
    _installed = True


def _should_drop(line: bytes) -> bool:
    # Match the exact macOS objc-runtime duplicate-class warning. Keep the
    # match narrow so we never swallow a real error: the line must start with
    # `objc[<pid>]:` AND mention "is implemented in both".
    return line.startswith(b"objc[") and b"is implemented in both" in line
