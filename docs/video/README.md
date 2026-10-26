# Demo video assets

| File | What it is |
|---|---|
| `architecture-animation.html` | 30 s animated diagram (1920×1080, 120 BPM). Self-contained; open it to play once, `?t=12.5` to freeze a moment. |
| `record_animation.py` | Renders the HTML to MP4 with Playwright (60 fps × 3 subframes, ffmpeg `tmix`), plus a silent MP4 and the Web Audio soundtrack. |
| `requirements-video.txt` | Render-only packages (kept out of the main requirements). |
| `prompt-motion-style.md` | The style template this follows. |
| `demo-storyboard.md` | Plan for the 60–90 s demo video with a real screen recording. |

The animation **explains** the design; it is not a recording of a run. Route, field names and values are
taken from `orchestratorx demo`, run [2] "Concentrated and over-levered".

## Render (Windows)

```powershell
pip install -r docs/video/requirements-video.txt
python -m playwright install chromium
winget install Gyan.FFmpeg          # if ffmpeg is missing; the script also finds winget's copy off PATH
python docs/video/record_animation.py --beats   # one still per beat, review first
python docs/video/record_animation.py           # -> docs/video/out/*.mp4
```

## Credits

Visual style based on the Prompt Motion template 'Alan agent platform promo' by @prasad_pilla (prompt-motion.com/customize/prasad-pilla-2c0cba).

Font: IBM Plex Mono, SIL Open Font License 1.1, embedded in the HTML.
