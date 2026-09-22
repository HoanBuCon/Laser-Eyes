# VIGIL AI — Technical Debt Register

**Baseline:** `85d86c6cb7b280a32d3123dc77e7b2abec72c380` on 2026-09-23.  
**Rule:** P0 is reserved for a demonstrable correctness or competition-demo integrity blocker. AI accuracy limitations are not promoted to P0 merely because they are important.

Effort: **XS** < half day, **S** about half–one day, **M** 1–3 days, **L** >3 days. No fix in this register was applied during the audit.

## P0 — correctness / demo integrity blockers

| ID | Subsystem | Evidence | Consequence | Recommended fix | Effort | Dependency | Demo blocker? | Production blocker? |
|---|---|---|---|---|---:|---|---|---|
| TD-P0-001 | Web `DemoRuntime` | `classroom_monitor/demo/runtime.py:692` calls nonexistent `EvidenceVideoBuffer.trigger_evidence_clip`; line 696 reads nonexistent `ClassroomEvent.risk_score`. Replay at lines 972–980 passes unsupported `session_id` and `risk_score` to the dataclass. The valid method is `video_buffer.py:130::trigger_clip`. | LIVE enters ERROR at first review incident; REPLAY enters ERROR at first event. Short tests pass only because they do not cross an event. | Reuse the runner evidence adapter and canonical event serializer; add one live and one replay test that must emit/review an incident. | S | TD-P1-006 contract | **Yes** | Yes |
| TD-P0-002 | Perception fallback | `classroom_monitor/detector.py:365–395` sets `_is_mock` and emits synthetic pose detections after import/model failure. `demo/runner.py` and `demo/runtime.py` never gate or expose it. | A live demo can show fabricated people/signals as genuine inference. | Default to fail-closed for competition LIVE; expose `inference_mode`, model/provider health and a persistent MOCK/DEGRADED watermark. Permit synthetic mode only via explicit flag. | S | UI/status schema | **Yes** | Yes |
| TD-P0-003 | Realtime review queue | `api/realtime.py:46–54` obtains an event loop from the caller thread and silently ignores `RuntimeError`; `_loop` is never set. No production code registers runtime event/status callbacks. `dashboard/js/demo.js:22–24` polls only status, while events are fetched only on load/start. | New incidents do not reliably appear in the review queue during a run even if inference continues. | Store the server event loop at startup, register/unregister runtime callbacks, broadcast `REVIEW_INCIDENT`, and poll `/demo/events` as a fallback. Add an end-to-end WebSocket/polling test. | M | TD-P0-001 | **Yes** | Yes |

## P1 — high-impact correctness, reliability, security or architecture

| ID | Subsystem | Evidence | Consequence | Recommended fix | Effort | Dependency | Demo blocker? | Production blocker? |
|---|---|---|---|---|---:|---|---|---|
| TD-P1-001 | Pattern engine | `behavior_pattern_engine.py:104–125,221–255` re-evaluates the same historical component episodes and only applies a 10 s time cooldown; every emission gets a new pattern UUID. | Identical evidence can be scored again after cooldown, inflating risk/occurrence count. | Deduplicate by stable `(pattern type, seat, sorted component episode IDs)` until the component set changes or expires. | S | none | Likely | Yes |
| TD-P1-002 | Seat-left semantics | `SeatOccupancy` starts EMPTY; `seat_manager.py:285–296` makes never-seen seats EMPTY; `_evaluate_seat_left` at `behavior_pattern_engine.py:329–363` checks only a 15 s active empty episode. | An unoccupied configured desk can be reported as a candidate who left. | Track `ever_occupied/current_occupant_seen` and require an OCCUPIED→EMPTY transition. | S | temporal tests | Likely | Yes |
| TD-P1-003 | Risk recidivism | `seat_risk_tracker.py:252–259` adds 10 points on every update while recidivism and score >=60 remain true. | Escalation is frame/update-rate dependent and can jump to review without new evidence. | Make recidivism a one-shot transition contribution with a processed marker. | S | TD-P1-001 | Possible | Yes |
| TD-P1-004 | Runtime lifecycle | `runtime.py:223–292`: start joins only 2 s, then clears the shared stop event and starts a replacement; singleton/callback lists are unsynchronized and callbacks cannot be removed. | Overlapping workers, stale state, duplicate callbacks and resource contention after repeated starts/clients. | Serialize lifecycle, refuse start until termination, use per-run cancellation token, unregister callbacks, clean up in `finally`. | M | TD-P0-001 | Possible | Yes |
| TD-P1-005 | Evidence buffer | `video_buffer.py:243–248` holds `_lock` then calls `flush_all()`, which takes the same non-reentrant lock at line 216. | `reset()` deadlocks deterministically. | Move flush outside lock or introduce a private lock-held flush; add timeout-based test. | XS | none | No unless reset invoked | Yes |
| TD-P1-006 | Domain contract | `ClassroomEvent` has no `risk_score`, `session_id` or `review_status`; runtime/API/JS synthesize different fields. DB duplicates `status` and `review_status`; exporter changes status spelling. | TypeErrors, field loss, ambiguous AI versus human state and brittle adapters. | Add versioned canonical `ReviewIncident`, `EvidenceRef`, `ReviewDecision`; map adapters explicitly. | L | architectural phase | Indirect | Yes |
| TD-P1-007 | Demo review durability | `api/routes/demo.py:173–231` returns SUCCESS even when no DB event exists. Replay dispatch does not persist its event. Demo review bypasses `ReviewRepository`/audit log. | A displayed human decision can disappear and have no review/audit record. | Require durable event existence or return conflict; persist replay event first; route both review APIs through one service and audit transaction. | M | TD-P1-006 | Yes for replay review | Yes |
| TD-P1-008 | Integrity UI | `dashboard/js/demo.js:413` displays `SHA-256 Verified` when `video_sha256` is absent. | False evidence-integrity claim. | Display `HASH NOT AVAILABLE`; verify server-side digest/file match before using “verified”. | XS | evidence status | Yes, trust/integrity | Yes |
| TD-P1-009 | DB referential integrity | `storage/database.py` does not enable SQLite foreign keys. `runtime.py::_init_db_session` stores preset camera code in FK `camera_id`. | Orphaned/inconsistent sessions and misleading relationships. | Enable `PRAGMA foreign_keys=ON`; resolve/create Camera row and use UUID; add FK integration test. | M | migration plan | No | Yes |
| TD-P1-010 | DB idempotency/schema | `DetectionEvent.event_id` and `EvidenceFile.event_id` lack unique constraints; repository does query-then-insert. Seat/room business keys are also weak. | Concurrent duplicate incidents/evidence and multiple “one-to-one” rows. | Add migrations/unique constraints and atomic upsert; decide canonical seat business key. | M | TD-P1-006/009 | No | Yes |
| TD-P1-011 | DB error handling | `database.py:52–80` uses ad-hoc ALTER statements and `except Exception: pass`; runtime DB sync catches/logs debug and continues. No SQLite WAL/busy timeout. | Schema drift and persistence loss can be silent; lock contention under web/evidence workloads. | Adopt Alembic or explicit versioned migrations, fail readiness on mismatch, WAL/busy timeout/retry, expose persistence health. | M | TD-P1-009/010 | Possible | Yes |
| TD-P1-012 | Legacy inference API | `api/routes/inference.py:65–105` calls `record_event` with removed kwargs and nonexistent `SessionRepository.update_metrics/end_session`; active runner metrics are never updated. | Reachable endpoint fails on event/completion and returns misleading zero progress. | Disable route in demo profile or repair via canonical pipeline; mark 410/deprecated until then. | S or L | architecture decision | No if hidden | Yes if exposed |
| TD-P1-013 | Upload/path security | `inference.py:180–198` and `cameras.py:105–150` concatenate raw filename, read all bytes, and lack size/type limits. Evidence serving lacks resolved-root containment; demo uses `rglob(filename)`. | Path traversal/overwrite, memory exhaustion, arbitrary/wrong-file exposure. | Generate server-side UUID names, stream with size/MIME checks, resolve and enforce root containment, use evidence ID not basename. | M | auth boundary | Yes if network exposed | Yes |
| TD-P1-014 | Authentication/CORS | All mutable/read APIs and WS are unauthenticated; server binds `0.0.0.0`; CORS uses `*` with credentials. | Anyone on reachable network can view evidence, alter reviews/calibration/data or start inference. | For demo, bind localhost or add a single-use token and explicit origin; production requires RBAC/audit. | M | deployment config | Yes if public LAN | Yes |
| TD-P1-015 | XSS/dashboard | Multiple dashboard paths inject API/notes/cues through `innerHTML`, e.g. `demo.js:418` and event cards. | Stored metadata/reviewer notes can execute browser script. | Use `textContent`/DOM creation or escaping and CSP. | S | none | No on trusted laptop | Yes |
| TD-P1-016 | Local lifecycle | `exam_monitor/app.py` stop joins 0.8 s then saves/clears; close releases model/source without guaranteed worker exit; worker is daemon. | Lost/late events, truncated session and resource race on close/restart. | Non-daemon worker, cooperative shutdown handshake, join completion before save/release, visible forced-stop error. | M | none | Possible | Yes |
| TD-P1-017 | Local calibration | `engine.py` accepts 36 samples with wide iris gate and no neutral-target validation/drift handling; head pose only gates sample collection. | Initial off-centre look becomes normal baseline; later posture drift biases alerts. | Add guided centre fixation quality checks and explicit recalibrate action; keep thresholds unchanged until video validation. | M | local calibration tests/data | Possible | Yes |
| TD-P1-018 | Local object policy | EfficientDet allowlist includes `book`; any book becomes SUSPICIOUS_OBJECT and cache lasts 1.4 s. No exam-material policy. | Open-book/allowed-material exams produce structurally unavoidable false alerts. | Separate detected object from prohibited-object policy configured per session; preserve review semantics. | S | domain config | Possible | Yes |
| TD-P1-019 | Evidence readiness | Clip work is asynchronous; event/DB may be marked READY or hashed before confirmed successful finalization; writer failures do not consistently update event. | Missing/corrupt evidence advertised as available. | Model `PENDING→READY/FAILED`, await/finalize callbacks, persist size/hash only after successful close. | M | TD-P1-006 | Possible | Yes |
| TD-P1-020 | Hash/chain claims | Docs/UI sometimes imply verification/strong evidence; implementation has only an unsigned SHA-256 digest. | Overclaim of legal custody/integrity. | Use precise “file integrity digest”; if needed later add signatures, trusted timestamps and immutable custody log. | S docs; L controls | TD-P1-008 | Yes, wording | Yes |
| TD-P1-021 | Favicon regression | `api/main.py:16–19,64–67` uses `Response` without importing it. | `/favicon.ico` throws NameError and creates visible demo/server noise. | Import `Response`; add endpoint regression test. | XS | none | Minor but visible | No |
| TD-P1-022 | Evidence/event delivery identity | Evidence URLs use basename recursive search; `event_id` uniqueness is not enforced. | Cards can show another run's same-named file; repeated runs can collide. | Resolve evidence by immutable DB ID/run ID under one root and validate hash. | M | TD-P1-010/013 | Possible | Yes |

## P2 — maintainability, performance and incomplete validation

| ID | Subsystem | Evidence / consequence | Recommendation | Effort | Demo blocker? | Production blocker? |
|---|---|---|---|---:|---|---|
| TD-P2-001 | SRS orchestration | `demo/runner.py` and `demo/runtime.py` duplicate detector→export loops and already diverged. | Extract one `SRSv2Pipeline`; thin CLI/web adapters. | L | No after P0 patch | Yes long-term |
| TD-P2-002 | Configuration | `resolve_runtime_config` comment says scene wins, but demo wins for provider/Hz; base has 32°/500 ms while resolver/engine use 28°/400 ms. | Typed resolved config, explicit precedence, startup dump and consistency tests. No tuning. | M | No | Yes |
| TD-P2-003 | Temporal grace | One 1200 ms missing grace applies to head, torso, wrist and occupancy, while SeatManager adds 4–5 s coasting. | Per-modality grace policies based on sensor semantics. | S | No | No |
| TD-P2-004 | Seat mapping | First containing polygon and bbox-bottom anchor; no overlap validation/nearest assignment/current tracking. | Calibration overlap checks, deterministic score, optional short-lived actor association. | M | No | Yes at scale |
| TD-P2-005 | Neighbor graph | Pixel heuristics auto-enable pairwise relation under perspective. | Mark auto inference UNVALIDATED until human confirmation; store explicit graph in scene files. | M | Maybe | Yes |
| TD-P2-006 | Pattern confidence | Neighbor lean multiplies confidence by 1.15 without final clamp. | Clamp all contract confidences to [0,1]. | XS | No | No |
| TD-P2-007 | Incident semantics | `peak_pattern` is latest, heterogeneous patterns update occurrence before same-behavior check. | Track highest contribution and behavior-specific incident aggregation. | M | No | Yes |
| TD-P2-008 | Completed history | Temporal completed episodes/profile processed IDs grow for a full long run. | Prune after all consumers' rolling/incident windows. | S | No | At long duration |
| TD-P2-009 | Track identity | Current `Detection` has no `track_id`; risk events normally store 0. | Treat seat as explicit business identity or add adapter track ID; stop implying MOT stability. | S/M | No | No |
| TD-P2-010 | MJPEG | Fixed 30 Hz polling/resend and JPEG encode each processed frame. | Sequence counter/condition wait, connected-client awareness, configurable rate/quality. | M | No | At scale |
| TD-P2-011 | DB commits | Runtime/repositories perform multiple synchronous commits per incident. | One transaction per incident/review and background-safe DB service. | M | No | At scale |
| TD-P2-012 | Local confidence | Local events reuse eye confidence for non-eye causes; timestamps do not represent actual onset/end. | Cause-specific confidence/quality and real signal interval fields. | S | No | Yes for analytics |
| TD-P2-013 | Local tracking hold | Held gaze/head can extend events for 0.8 s. | Mark unknown or exclude held frames from activation while allowing short release grace. | S | No | No |
| TD-P2-014 | Audio attribution | Room/proctor speech VAD can combine with candidate mouth motion; no speaker localization. | Display “speech-like audio + lip motion,” not candidate speech; validate cough/noise cases. | S | No | Yes if used for judgment |
| TD-P2-015 | Retention/privacy | JPEG/video/session/notes persist; 90-day JPEG cleanup is not scheduled and excludes MP4/DB. | Central retention config/job and deletion audit; consent/demo data policy. | M | No | Yes |
| TD-P2-016 | Dependencies | Full environment freeze mixes runtime/dev/transitive; duplicate OpenCV versions; CUDA-only torch pins; optional JAX/DB packages. | Split base/local/classroom/HPE/dev lock sets after demo freeze. | M | No | Yes portability |
| TD-P2-017 | Training reproducibility | Absolute `H:/...` dataset path; no explicit seed; semantic classes are legacy. | Parameterize path/seed and label outputs “legacy semantic experiment”. | S | No | No |
| TD-P2-018 | Data leakage evidence | Filename-based leakage report says none, while dataset audit reports temporal near-duplicate cross-split leakage. | Re-split/group by source video/session and record provenance manifest. | M | No | Yes for metric claims |
| TD-P2-019 | Test realism | All 237 tests pass but no true incident runtime, browser E2E, real model/media device, DB lock or security cases. | Add contract/integration tests listed in architecture audit; retain fast unit suite. | M | Indirect | Yes |
| TD-P2-020 | Error swallowing | Dangerous broad catches in `database.py`, `runtime.py`, `realtime.py`, `engine.py`; some are silent. | Catch expected exceptions, propagate health state, structured logs/metrics. | M | Indirect | Yes |
| TD-P2-021 | Camera reference fallback | Failed camera sources return a synthetic calibration image without an explicit response capability flag. | Return 503 by default or explicit `synthetic=true` and watermark. | XS | Possible | Yes |
| TD-P2-022 | API version duplication | Demo router at root, `/api`, `/api/v1`; all main routers duplicated under two prefixes. | Freeze `/api/v1`; make compatibility redirects/deprecation metadata. | S | No | No |
| TD-P2-023 | Status vocabulary | Lowercase domain enum, uppercase exporter/DB/JS, `FLAGGED_FOR_REVIEW` vs `FLAGGED_FOR_HUMAN_REVIEW`. | Canonical uppercase enums with explicit legacy adapters. | M | No | Yes |
| TD-P2-024 | Data/workbench safety | Mutable calibration/dataset endpoints have no transaction-level version/authorization boundary in demo server. | Separate admin app/profile; require auth and revision checks. | M | No | Yes |
| TD-P2-025 | Performance evidence | Current named final artifacts report 4.68/6.93 FPS while older docs claim 25–59+ FPS. | One reproducible HEAD-bound benchmark manifest with hardware/config/command. | S | No | Yes for claims |

## P3 — cleanup and documentation

| ID | Subsystem | Evidence / consequence | Recommendation | Effort | Human decision required? |
|---|---|---|---|---:|---|
| TD-P3-001 | README | README is unrelated to product and has no setup/entrypoint/model/fallback disclosure. | Replace after P0 remediation with two-product quick start. | S | Yes, messaging |
| TD-P3-002 | Generated files | Databases, evidence, output snapshots and caches are extensively Git tracked. | Decide fixture vs release artifact policy; use manifests/LFS/releases as appropriate. | M | Yes |
| TD-P3-003 | Legacy exports | `classroom_monitor/__init__.py` presents legacy classes as primary public API. | Deprecation namespace and warnings after route decision. | S | Yes |
| TD-P3-004 | Terminology | Main dashboard/report strings still say “Cheating Detection,” “Violations,” “Cheater”/legacy semantic language. | Use observable signal, review incident, human decision consistently. | S | Yes |
| TD-P3-005 | Encoding | Several Vietnamese labels render as mojibake in source/config/UI artifacts. | Normalize repository text to UTF-8 and add smoke test. | S | No |
| TD-P3-006 | Large modules | `exam_monitor/app.py`, `demo/runtime.py`, `data_workbench.py` and `data_workbench.js` combine many responsibilities. | Split only after prototype stabilization and contract tests. | L | Yes |
| TD-P3-007 | Unused concepts | `TORSO_ORIENTATION`, wrist velocity consumer gap, unused demo imports and dormant realtime `_loop`. | Remove/archive only after reachability and compatibility decision. | S | Yes |
| TD-P3-008 | Historical claims | Many reports assert 100% recall, production-ready, >30/120/320 FPS or alert reductions for older runs. | Add CURRENT/HISTORICAL headers and link a single current manifest. | S | Yes |

## Recommended validation gates

| Phase | Exact files | Expected impact | Principal risk | Validation |
|---|---|---|---|---|
| 1 — correctness | `demo/runtime.py`, `video_buffer.py`, `detector.py`, `api/realtime.py`, `api/routes/demo.py`, `dashboard/js/demo.js`, `api/main.py`, targeted tests | Competition web flow reaches incident, evidence and human decision without false mock/hash claims | Patching duplicated runtime without runner parity | Live/replay incident E2E; model-missing test; WS+poll fallback; evidence hash/file check |
| 2 — architecture | `demo/runner.py`, `demo/runtime.py`, new core orchestration/domain module, `models.py`, `api/schemas.py`, DB mapping | One processing truth and stable contracts | Regression across CLI artifacts | Golden artifact schema + both adapters against same fixture |
| 3 — performance | renderer/runtime/MJPEG/evidence/DB service | Lower CPU/copies and stable projector stream | Dropped evidence/frame timing | Stage profiler, evidence completeness, 30-minute soak |
| 4 — security/cleanup | upload/evidence routes, DB config/migrations, deployment profile, legacy route | Safe isolated demo and clear active surface | Breaking convenience URLs/data | Traversal/size/auth tests, migration rehearsal, legacy 410 tests |
| 5 — docs/data | README, runbook, report headers, benchmark manifest | Defensible claims and reproducible demo | Reintroducing stale numbers | Fresh run at audited remediation commit; human review of wording |

## Remediation history — 2026-09-23

The issue descriptions above are retained as audit history. Status below is authoritative for source commit `96f04da`; a documentation-only successor may be the branch HEAD. Real-model runs were captured at its functional predecessor `41604e7`.

| ID | Status | Fix commit(s) | Validation | Remaining limitation |
|---|---|---|---|---|
| TD-P0-001 | RESOLVED | `6595493`, `41604e7` | LIVE and REPLAY incident integration tests; real Web LIVE/REPLAY crossed incidents and loaded evidence | Browser acceptance still required |
| TD-P0-002 | RESOLVED | `b237589` | Missing-model fail-closed test; explicit mock test; real YOLO/SixDRepNet full runs | HPE DEGRADED remains allowed but visible |
| TD-P0-003 | RESOLVED | `2d01489` | Captured-loop realtime test, idempotent callback tests, polling test; real HTTP queue update | Manual browser reconnect acceptance remains |
| TD-P1-001 | RESOLVED | `510e377` | Same component set does not re-emit after cooldown | History pruning remains TD-P2-008 |
| TD-P1-002 | RESOLVED | `510e377` | Empty-from-start and OCCUPIED→EMPTY regression tests | Detection misses can still hide prior occupancy |
| TD-P1-003 | RESOLVED | `510e377` | Update-rate-independent recidivism test | Weight/threshold quality is not validated here |
| TD-P1-004 | RESOLVED FOR PROTOTYPE | `6595493` | start/stop/start, double-start refusal, pause/resume and isolation tests | Long soak remains post-competition |
| TD-P1-005 | RESOLVED | `6595493` | timeout-based buffer reset/finalization test | none known |
| TD-P1-006 | RESOLVED FOR CLASSROOM | `6595493` | contract adapter and runtime/API persistence tests | Local intentionally not migrated |
| TD-P1-007 | RESOLVED | `6595493` | durable review, 409 missing target, EventReview and AuditLog tests | production authorization deferred |
| TD-P1-008 | RESOLVED | `6595493` | verified/mismatch/not-available states and real digest comparison | SHA-256 is not chain of custody |
| TD-P1-009 | RESOLVED FOR FRESH DB | `6595493` | SQLite FK/camera identity test | existing installations need formal migrations |
| TD-P1-010 | PARTIALLY RESOLVED | `6595493` | fresh-schema uniqueness and event-scoped evidence tests | atomic upsert/migration race hardening deferred |
| TD-P1-011 | PARTIALLY RESOLVED | `6595493` | WAL, busy timeout and surfaced persistence health | Alembic/versioned migrations deferred |
| TD-P1-012 | RESOLVED FOR COMPETITION | `5b17fa7` | legacy route returns explicit HTTP 410 | legacy code retained post-competition |
| TD-P1-013 | RESOLVED FOR DEMO | `5b17fa7`, `41604e7` | upload validation, containment, ambiguous basename and event identity tests | streaming multipart inspection can be stronger |
| TD-P1-014 | RESOLVED FOR DEMO | `5b17fa7`, `96f04da` | localhost launcher, explicit CORS, LAN token gate and dashboard propagation tests | not production RBAC |
| TD-P1-015 | PARTIALLY RESOLVED | `5b17fa7` | Review Queue escaping/no inline handler test | audit remaining non-demo dashboards post-competition |
| TD-P1-016 | RESOLVED | `ab6e7cc` | cooperative non-daemon shutdown test | real Tkinter/camera acceptance required |
| TD-P1-017 | RESOLVED FOR PROTOTYPE | `ab6e7cc` | neutral accepted/off-center rejected calibration tests | no posture-drift adaptation by design |
| TD-P1-018 | RESOLVED | `ab6e7cc` | open/closed-book material policy test | UI currently exposes the book policy only |
| TD-P1-019 | RESOLVED | `6595493` | READY and FAILED paths; 31 real run clips were non-empty | disk-full soak not executed |
| TD-P1-020 | RESOLVED IN CURRENT UI/DOCS | `6595493`, documentation commit | hash wording and integrity endpoint tests | signed custody controls deferred |
| TD-P1-021 | RESOLVED | `2d01489` | favicon regression test returns 204 | none known |
| TD-P1-022 | RESOLVED | `41604e7` | duplicate-basename event-scoped media test; real replay HTTP 200 | legacy basename endpoints remain compatibility-only and fail ambiguous |
| TD-P2-001 | RESOLVED FOR FRAME SEMANTICS | `3c03f3e` | CLI/Web adapter identity and shared-pipeline tests; both real modes run | lifecycle/render/export remain adapter-specific intentionally |
| TD-P2-002 | RESOLVED FOR PROTOTYPE | `3c03f3e` | precedence tests and effective config/run manifest | numeric tuning not performed |
| TD-P2-003..018 | DEFERRED — POST COMPETITION | — | See original rows | Requires data, architecture or owner decisions beyond safe one-week scope |
| TD-P2-019 | RESOLVED FOR REQUIRED BOUNDARIES | all remediation commits | 259 automated tests plus real full CLI/Web validation | tests do not prove accuracy |
| TD-P2-020..025 | PARTIAL / DEFERRED | multiple | current health/status/profile artifacts | See `PROTOTYPE_CURRENT_STATUS.md` |
| TD-P3-001, TD-P3-008 | RESOLVED | documentation commit | README/current status use measured HEAD-bound facts | historical files retained and labelled |
| TD-P3-002..007 | DEFERRED — POST COMPETITION | — | human decision required | cleanup must not destabilize acceptance build |
