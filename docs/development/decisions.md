# Design decisions (2026-11-01)

Decisions taken on 2026-11-01 and built the same day (progress.md, "2026-11-01: work from the
decisions in decisions.md"). Only decision 4 is still open: it is checked on a P400.

1. **Access protection.** The web UI is open to every device on the church network (needed for
   tablets); cross-site requests from other web pages are blocked. Without a PIN, anyone on that
   network can also see the registered faces' small thumbnails (Settings → System can delete them).
   **Decision: an optional PIN in Settings.**
2. **Project folder.** The code is called TrackXen, but the folder was still `Desktop\birddog-tracker`.
   **Decision: renamed to `Desktop\TrackXen`** (the Python environment recreated).
3. **Version control.** **Decision: the work is kept in git**, locally at first and later on GitHub,
   where every push to `main` builds a release.
4. **Preset numbering.** The UI's preset buttons 1-9 send VISCA presets 0-8. On some cameras "preset 1"
   is VISCA 0, on others VISCA 1. **Open:** assumed 1 → 0; to be checked on a P400 (does button 1
   recall BirdUI's preset 1?).
5. **HYPER mode for 4 cameras.** On the development PC (RTX 4080 SUPER), the HYPER model at 960 px
   managed 123 images/s: enough for 2 cameras at 30 fps but not 4 (needs 168 with headroom). Moving
   the image preparation onto the GPU fixes that. **Decision: done at once.**
6. **Face recognition model.** OpenCV's YuNet + SFace (free, commercial use allowed). InsightFace is
   more accurate, but its free models are for non-commercial *research* only. **Decision: SFace.** (The
   two models come from OpenCV's model zoo with `tools/get_face_models.py`.)
7. **Inno Setup for the installer.** The program itself builds as `dist\TrackXen\TrackXen.exe`; turning
   it into a setup program needs Inno Setup (free; installing it means accepting its licence).
   **Decision: Inno Setup.**
8. **Installer size.** NVIDIA's CUDA, cuDNN and TensorRT libraries make TrackXen about 5 GB installed
   (a setup download of roughly 2-3 GB). A smaller setup can download them on first start instead, but
   then the first start needs internet. **Decision: download them on the first start.**
9. **"Unknown publisher" warning.** Without a code-signing certificate (~100-300 €/year) Windows shows a
   SmartScreen warning when the setup runs ("More info" → "Run anyway"). **Decision: unsigned is
   fine.**
10. **Latency with 4 cameras.** With all four cameras tracking, frame → result measured 31 ms (16 ms with
    one camera): the single detection thread was about 90 % busy, so frames waited a little. Tracking
    quality was unaffected in the simulator (98-99 % of the time on target). A two-stage pipeline
    (image preparation and detection in parallel) brings it down. **Decision: done at once.** Result
    the same day: 8-17 ms with four cameras, measured exactly. The "31 ms" came from a Windows clock
    that only ticks every 15.6 ms; see progress.md.
