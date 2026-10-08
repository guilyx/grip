# Media

Everything here is generated from the recorded demo by `scripts/launch/make_launch.py`, so
it stays in sync with the terminal output. Use it freely when you write or talk about grip.

## Launch video

<video controls muted playsinline width="100%" poster="assets/launch/grip-producthunt-1270x760.png">
  <source src="assets/launch/grip-launch-1080p.mp4" type="video/mp4">
</video>

83 seconds, 1080p, H.264. Title cards around the full demo. It has a silent audio track,
so add music before you upload it anywhere that expects sound.

## Demo

<video controls muted playsinline width="100%">
  <source src="assets/demo.mp4" type="video/mp4">
  <source src="assets/demo.webm" type="video/webm">
</video>

The same 53 second recording as the GIF on the front page: one commit blocked at 12/100,
then accepted at 92/100. Also available as [GIF](assets/demo.gif),
[WebM](assets/demo.webm) and an [asciinema cast](assets/demo.cast).

## Images

![Product Hunt gallery image](assets/launch/grip-producthunt-1270x760.png)

[1270 × 760 gallery image](assets/launch/grip-producthunt-1270x760.png), the size Product
Hunt and most link previews want.

<img src="assets/launch/grip-thumbnail-240.png" alt="grip icon" width="120">

[240 × 240 icon](assets/launch/grip-thumbnail-240.png), also at
[480 × 480](assets/launch/grip-thumbnail-480.png).

## Feature clips

One short clip per feature added since launch, and a 94 second cut of all five with title
cards. Each runs the real `grip` in a real terminal with the offline scripted provider, so
the questions and feedback are canned for the clips' rate-limiter diff; a real model
writes them from your diff.

| Clip | Shows | Length |
| --- | --- | --- |
| [grip-whats-new.mp4](assets/features/grip-whats-new.mp4) | all five, with title cards | 94 s |
| [init.mp4](assets/features/init.mp4) | `grip init`: config, hook and agent rule files | 11 s |
| [agents.mp4](assets/features/agents.mp4) | `grip agents`: detection and the files it writes | 13 s |
| [agent-gate.mp4](assets/features/agent-gate.mp4) | Claude Code's `git push` denied, then the agent quiz flow | 28 s |
| [skip.mp4](assets/features/skip.mp4) | `grip skip`, a pause, `grip statusline`, `grip resume` | 20 s |
| [last.mp4](assets/features/last.mp4) | `grip last`: your answers, ready for a commit message | 7 s |

Every clip also exists as a GIF next to the MP4.

## Regenerating

```bash
pip install -e ".[demo]"
python scripts/demo/make_demo.py all      # re-record and render the demo
python scripts/launch/make_launch.py      # rebuild demo.mp4 and everything under assets/launch
python scripts/demo/make_features.py      # re-record the feature clips and the stitched cut
```

The demo script needs `bash` and a pseudo-terminal. The launch script needs an ffmpeg
with libx264, which the `demo` extra provides through `imageio-ffmpeg`.
