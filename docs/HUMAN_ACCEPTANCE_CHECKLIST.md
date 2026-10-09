# VIGIL AI — Human Acceptance Checklist

This checklist is the final owner-operated gate. Automated tests and real-model validation do not authorize a release freeze.

## Classroom competition flow

Start the server:

```powershell
venv\Scripts\python.exe server.py --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/demo`, then verify:

- [ ] Select India and start REPLAY.
- [ ] Wait until a recorded incident timestamp; the Review Queue updates without refresh.
- [ ] Open a card; snapshot and MP4 load.
- [ ] Hash label is one of VERIFIED, MISMATCH, AVAILABLE/NOT CHECKED or NOT AVAILABLE and matches the endpoint result.
- [ ] Submit a human decision, refresh, and confirm the decision persists.
- [ ] Stop, start Student, and confirm India state/frame/evidence does not leak.
- [ ] Toggle Debug Overlay and confirm canonical episodes/patterns/incidents do not change merely because rendering changed.
- [ ] Start LIVE Student; health becomes `REAL` for pose and head orientation.
- [ ] At least one real incident appears automatically and its evidence loads.
- [ ] Stop; start again; pause/resume/stop; rapidly double-click Start. No overlapping worker or duplicate card appears.
- [ ] Disconnect/reconnect the browser; polling restores the same incident identities without duplicate cards.
- [ ] If `--allow-mock` is used deliberately, persistent MOCK/SIMULATION labelling is visible. Without it, a missing model fails closed.

## VIGIL Local flow

Start Local:

```powershell
venv\Scripts\python.exe main.py
```

- [ ] Explicit simulation mode is visibly labelled and is never presented as camera inference.
- [ ] A real camera/video source starts on the competition laptop.
- [ ] Guided center fixation progresses; off-center calibration is rejected; Recalibrate succeeds.
- [ ] Closed-book policy can signal an observed book; allowed-book policy retains the observation without prohibiting it.
- [ ] Stop waits for the worker, then saves the final session/evidence.
- [ ] Close and restart a session without camera/model resource errors.
- [ ] No UI label or saved event asserts an automatic cheating verdict.

## Acceptance record

- Owner/reviewer: ____________________
- Competition laptop: ____________________
- Branch/HEAD: ____________________
- Date/time: ____________________
- Result: [ ] ACCEPTED  [ ] REJECTED  [ ] ACCEPTED WITH NOTED LIMITATIONS
- Notes: ____________________
