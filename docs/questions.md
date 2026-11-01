# Questions for you (collected while you slept)

Answer whenever you like; nothing is blocked on these. The defaults I chose are in brackets.

1. **Access protection.** The web UI is open to every device on the church network (needed for
   tablets). Cross-site attacks from other web pages are already blocked. Do you also want a PIN or
   password for the control UI? Without one, anyone on that network can also see the registered faces'
   small thumbnails (Settings → System can delete them). [no PIN yet]
2. **Project folder name.** The code is called TrackXen, but the folder is still
   `Desktop\birddog-tracker`. Renaming it means recreating the Python environment (a few minutes).
   Rename to `Desktop\TrackXen`? [not renamed yet]
3. **Git commits.** May I commit the work (local git only, nothing pushed anywhere)?
   **Answered 2026-11-01: yes.** The work is committed in the local repository; there is no remote.
4. **Preset numbering.** The UI's preset buttons 1-9 send VISCA presets 0-8. On some cameras "preset 1"
   is VISCA 0, on others VISCA 1. Check on a P400: does button 1 recall BirdUI's preset 1? [assumed 1 → 0]
5. **HYPER mode for 4 cameras.** On this PC (RTX 4080 SUPER), the measured HYPER model at 960 px
   manages 123 images/s, enough for 2 cameras at 30 fps but not 4 (needs 168 with headroom). Moving the
   image preparation onto the GPU could fix that. Worth doing now, or later? [later]
6. **Face recognition model.** For registered faces (Canon step 9) I plan OpenCV's YuNet + SFace (free,
   commercial use allowed). InsightFace is more accurate but its free models are for non-commercial
   *research* only. OK to use SFace? [SFace: done this way; the two models came from OpenCV's model zoo
   with `tools/get_face_models.py`]
7. **Inno Setup for the installer.** The program itself is built (`dist\TrackXen\TrackXen.exe`, tested:
   tracking, faces, hardware test, first-run screen). Turning it into a setup program needs Inno Setup
   (free). Installing it means accepting its licence, so I left that to you:
   `winget install JRSoftware.InnoSetup`, then `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`.
   May I install it next time? [not installed]
8. **Installer size.** NVIDIA's CUDA, cuDNN and TensorRT libraries make TrackXen about 5 GB installed
   (the setup download will be roughly 2-3 GB). A smaller setup could download them on first start
   instead, but then the first start needs internet. Keep everything in the setup? [everything in it]
9. **"Unknown publisher" warning.** Without a code-signing certificate (~100-300 €/year) Windows shows a
   SmartScreen warning when the setup runs ("More info" → "Run anyway"). Fine for church use? [unsigned]
10. **Latency with 4 cameras.** With all four cameras tracking, frame → result is 31 ms (16 ms with
    one camera): the single detection thread is about 90 % busy, so frames wait a little. Tracking
    quality is unaffected in the simulator (98-99 % of the time on target). A two-stage pipeline (image
    preparation and detection in parallel) would bring it down. Now or later? [later]
