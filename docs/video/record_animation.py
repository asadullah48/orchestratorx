"""Render docs/video/architecture-animation.html to MP4.

Follows the build in prompt-motion-style.md. The page exposes ``window.seek(t)``,
so every frame is a pure function of time: we step it at 60 fps with 3
subframes per frame, blend the subframes with ffmpeg ``tmix`` for motion blur,
and mux the soundtrack the page renders with Web Audio.

    python docs/video/record_animation.py           # full render
    python docs/video/record_animation.py --beats   # one frame per beat, to review first

Outputs land in docs/video/out/ (git-ignored):
    architecture-animation.mp4         H.264 + AAC
    architecture-animation-silent.mp4  same video, no audio track (LinkedIn autoplays muted)
    soundtrack.wav
"""

from __future__ import annotations

import argparse
import base64
import glob
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
HTML = HERE / "architecture-animation.html"
OUT = HERE / "out"
WIDTH, HEIGHT = 1920, 1080


def find_ffmpeg() -> str | None:
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    # winget's Gyan.FFmpeg is not on PATH until a new shell starts.
    pattern = os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe"
    )
    hits = sorted(glob.glob(pattern))
    return hits[-1] if hits else None


def open_page(pw):
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
    page.goto(HTML.as_uri() + "?render=1")
    page.wait_for_function("window.__ready === true")
    return browser, page


def render_beats(page, duration: float) -> None:
    """One still per beat, late in the beat, for a review pass before the full render."""
    beats = OUT / "beats"
    beats.mkdir(parents=True, exist_ok=True)
    for b in range(int(duration / 0.5)):
        page.evaluate(f"seek({b * 0.5 + 0.4})")
        page.screenshot(path=str(beats / f"beat_{b:02d}.png"))
    print(f"wrote {int(duration / 0.5)} beat frames to {beats}")


def render_video(page, duration: float, ffmpeg: str, fps: int, sub: int) -> None:
    wav = OUT / "soundtrack.wav"
    wav.write_bytes(base64.b64decode(page.evaluate("renderAudio()")))
    print(f"wrote {wav}")

    silent = OUT / "architecture-animation-silent.mp4"
    final = OUT / "architecture-animation.mp4"
    # Frame n of the output blends subframes n*sub .. n*sub+sub-1.
    vf = (
        f"tmix=frames={sub},select='eq(mod(n,{sub}),{sub - 1})',setpts=N/({fps}*TB)"
        if sub > 1
        else "null"
    )
    encoder = subprocess.Popen(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "image2pipe",
            "-framerate",
            str(fps * sub),
            "-vcodec",
            "png",
            "-i",
            "-",
            "-vf",
            vf,
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(silent),
        ],
        stdin=subprocess.PIPE,
    )
    cdp = page.context.new_cdp_session(page)
    frames = round(duration * fps)
    started = time.monotonic()
    for f in range(frames):
        for s in range(sub):
            page.evaluate(f"seek({(f + s / sub) / fps})")
            shot = cdp.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True})
            encoder.stdin.write(base64.b64decode(shot["data"]))
        if f % fps == 0:
            print(
                f"  {f / fps:4.0f}s / {duration:.0f}s   ({time.monotonic() - started:.0f}s elapsed)",
                flush=True,
            )
    encoder.stdin.close()
    if encoder.wait() != 0:
        sys.exit("ffmpeg failed while encoding the video")
    print(f"wrote {silent}")

    subprocess.run(
        [
            # 48 kHz stereo is the video norm; the 44.1 kHz WAV played silent in Windows Media Player.
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(silent),
            "-i",
            str(wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            "-movflags",
            "+faststart",
            str(final),
        ],
        check=True,
    )
    print(f"wrote {final}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--beats", action="store_true", help="only render one still per beat")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--subframes", type=int, default=3)
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()
    if not args.beats and not ffmpeg:
        sys.exit(
            "ffmpeg not found. Install it with:  winget install Gyan.FFmpeg  (then open a new terminal)"
        )

    with sync_playwright() as pw:
        browser, page = open_page(pw)
        duration = float(page.evaluate("window.DURATION"))
        if args.beats:
            render_beats(page, duration)
        else:
            render_video(page, duration, ffmpeg, args.fps, args.subframes)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
