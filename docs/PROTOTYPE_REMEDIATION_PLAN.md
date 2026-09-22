# VIGIL AI Prototype Remediation Plan

**Working branch:** `fix/ictu-2026-prototype-remediation`  
**Audit baseline:** `85d86c6cb7b280a32d3123dc77e7b2abec72c380`  
**Prepared:** 2026-09-23  
**Product rule:** VIGIL Local and VIGIL Classroom keep separate perception, calibration, lifecycle and UI implementations. Only explicit domain/operational contracts are shared. An AI incident is review priority, never a cheating verdict.

## Verification baseline

- Local and remote baseline were equal at `85d86c6`; working tree contained only the four audit documents.
- Baseline suite: **237 passed, 1 warning in 13.67 s**.
- Passing tests do not validate model accuracy. In particular, the baseline tests do not cross the first LIVE or REPLAY review incident.

## Audit issue reconciliation before implementation

`VERIFIED` means the current source still contains the reported failure. `PARTIAL` means the condition exists but the final remediation boundary needs an integration test. `DEFERRED` items are explicitly outside the one-week competition-critical path and retain their history in the debt register.

| IDs | Initial status | Current evidence / decision |
|---|---|---|
| TD-P0-001 | VERIFIED | `DemoRuntime` calls `trigger_evidence_clip`, reads `ClassroomEvent.risk_score`, and replay passes unsupported constructor fields. |
| TD-P0-002 | VERIFIED | `PoseClassroomDetector` sets `_is_mock` and returns a synthetic three-person grid without operator consent. |
| TD-P0-003 | VERIFIED | realtime uses caller-thread `get_event_loop`; no lifespan loop capture or runtime callback registration. |
| TD-P1-001..005 | VERIFIED | Stable component identity, prior occupancy, one-shot recidivism and safe lifecycle/reset are absent. |
| TD-P1-006..008 | VERIFIED | Runtime/API/JS contracts drift; demo review can succeed without a DB event; UI invents a verified hash label. |
| TD-P1-009..011 | VERIFIED/PARTIAL | SQLite pragmas and durable uniqueness are absent; runtime camera FK uses a code; broad DB error swallowing remains. |
| TD-P1-012 | VERIFIED | Legacy inference route remains mounted despite calls to obsolete repository methods. |
| TD-P1-013..015 | VERIFIED | Raw upload names, basename `rglob`, permissive CORS/0.0.0.0 launch, and unsafe HTML sinks remain. |
| TD-P1-016..018 | VERIFIED | Local daemon shutdown race, weak calibration UX, and unconditional book policy remain. |
| TD-P1-019..022 | VERIFIED | Evidence readiness, wording, favicon import and evidence identity defects remain. |
| TD-P2-001..002 | VERIFIED | CLI/runtime duplicate the SRS loop; configuration precedence/defaults drift. |
| TD-P2-003..009 | PARTIAL/DEFERRED | No threshold tuning or tracking redesign before acceptance; correctness-safe clamps/identity wording may be included. |
| TD-P2-010..015 | PARTIAL/DEFERRED | Optimize only measured demo bottlenecks; retention and long-run scaling remain post-competition unless blocking validation. |
| TD-P2-016..018 | VERIFIED/DEFERRED | Dependency and legacy-training/data-split work is not safe to rewrite during prototype hardening. |
| TD-P2-019..025 | VERIFIED | Add failure-boundary tests, explicit health, legacy policy, status/config consistency and HEAD-bound metrics; defer admin separation. |
| TD-P3-001..008 | VERIFIED | Replace README/current status and label historical claims; large-module cleanup and generated-artifact policy require post-competition decisions. |

## Phase A — P0 competition blockers

| Issue | Subsystem | Evidence / root cause | Files affected | Proposed change | Regression risk | Tests / acceptance | Effort | Dependency | Commit group |
|---|---|---|---|---|---|---|---|---|---|
| TD-P0-001, P1-006 | Classroom incident boundary | Web runtime bypasses the valid buffer API and assumes DB-shaped fields on `ClassroomEvent`. | `classroom_monitor/demo/runtime.py`, new classroom contract/adapter, tests | Add a minimal versioned `ReviewIncident`/`EvidenceRef` adapter; use metadata for priority; persist before publishing; deserialize replay through the same adapter. | Event JSON/API field changes | LIVE integration crosses one emitted incident; REPLAY crosses one artifact timestamp; both remain reviewable. | M | none | 1 |
| TD-P0-002 | Perception health | Pose initialization silently selects synthetic detections; HPE fallback is not surfaced. | `detector.py`, HPE factory/runtime status, renderer/dashboard, CLI arguments, tests | Introduce REAL/DEGRADED/MOCK/ERROR capability health. Default fail-closed; permit synthetic data only with `allow_mock`; watermark and manifest it. | Existing tests relying on implicit mock | Missing model refuses LIVE; explicit mock is visible in status/frame/manifest. | M | contract | 2 |
| TD-P0-003 | Realtime | Worker thread has no safe server event-loop bridge or registered callbacks. | `api/realtime.py`, `api/main.py`, runtime callbacks, `demo.js`, tests | Capture loop in lifespan, register idempotent callbacks, schedule broadcasts thread-safely, unregister on shutdown, poll `/events` as fallback. | Duplicate delivery/cards | One generated incident yields one browser/API identity without refresh; reconnect catches up by polling. | M | TD-P0-001 | 3 |

## Phase B — P1 alert correctness

| Issue | Subsystem | Evidence / root cause | Files affected | Proposed change | Regression risk | Tests / acceptance | Effort | Dependency | Commit group |
|---|---|---|---|---|---|---|---|---|---|
| TD-P1-001 | Patterns | Cooldown keys ignore component evidence identity. | `behavior_pattern_engine.py`, tests | Deduplicate `(type, seat, sorted episode IDs)` until evidence expires; emit on a changed component set. | Suppressing legitimately new patterns | Same components remain one pattern beyond cooldown; changed/expired evidence can emit. | S | none | 4 |
| TD-P1-002 | Occupancy | EMPTY is eligible without prior OCCUPIED. | temporal engine/observations, tests | Track prior occupied state and gate EMPTY episode/SEAT_LEFT activation on an OCCUPIED→EMPTY transition. | Missing a candidate whose occupied frames are not observed | Empty from t=0 for 60 s gives zero SEAT_LEFT; occupied then empty gives one. | S | none | 4 |
| TD-P1-003 | Risk | Recidivism bonus is applied per update. | `seat_risk_tracker.py`, tests | Bind bonus to a new qualifying evidence/transition marker. | Under-counting new incidents | 10 FPS and 30 FPS with identical evidence produce equivalent contribution. | S | TD-P1-001 | 4 |
| TD-P2-007 | Incident aggregation | Every unrelated pattern increments occurrence and newest pattern becomes primary. | `seat_risk_tracker.py`, contract adapter, tests | Preserve canonical ID/cause; count matching logical occurrences; retain peak-contribution primary and append supporting evidence. | Existing snapshot assertions | Identity and cause survive runtime→JSON→DB→API. | M | prior tasks | 4 |

## Phase C — runtime, evidence and review durability

| Issue | Subsystem | Evidence / root cause | Files affected | Proposed change | Regression risk | Tests / acceptance | Effort | Dependency | Commit group |
|---|---|---|---|---|---|---|---|---|---|
| TD-P1-007 | Human review | Demo endpoint returns success with no durable event and bypasses audit logic. | shared review service, both routes, repositories/tests | One transactional review service: require durable event, upsert `EventReview`, update decision, write audit; return 409 if missing. | Changes demo response errors | Refresh retains decision and audit; missing incident returns conflict. | M | contract/DB | 5 |
| TD-P1-008, P1-020 | Integrity claim | UI substitutes “verified” for missing digest. | evidence service/routes, `demo.js`, docs/tests | Compute and compare server-side; expose VERIFIED/MISMATCH/AVAILABLE_NOT_CHECKED/NOT_AVAILABLE; describe digest honestly. | IO cost opening media | Known file verifies, tampered file mismatches, absent digest is unavailable. | S | evidence identity | 5 |
| TD-P1-019 | Evidence lifecycle | Async completion is not reflected durably. | buffer/runtime/storage/UI/tests | Canonical PENDING→READY/FAILED; only READY after non-empty finalized file and optional hash; persist failure reason. | Timing races | Clip success and encoder failure paths both settle durably and visibly. | M | TD-P0-001 | 5 |
| TD-P1-005 | Video buffer | `reset` recursively takes its non-reentrant lock. | `video_buffer.py`, tests | Flush outside lock with clear ownership; then clear state and shutdown safely. | Pending job loss | Timeout test proves reset completes and jobs are finalized/failed. | XS | none | 6 |
| TD-P1-004 | Demo lifecycle | Shared cancellation event can be cleared while old worker lives; callbacks accumulate. | runtime/routes/tests | Lifecycle lock, per-run cancellation token, deterministic state cleanup, callback unregister; refuse new start if prior worker is alive. | Deadlock/slow stop | Start-stop-start, pause-resume-stop, double start, disconnect/reconnect and slow-worker refusal pass. | M | P0 fixes | 6 |
| TD-P1-021, P2-010 | Visible demo defects | Missing `Response`; MJPEG resends by polling; UI states incomplete. | API/demo JS/CSS/tests | Import response; disconnect-safe sequence-aware MJPEG; server lifecycle errors and loading states. | Stream compatibility | favicon 204; disconnect creates no exception loop. | S | runtime | 6 |

## Phase D — Classroom architecture consolidation

| Issue | Subsystem | Evidence / root cause | Files affected | Proposed change | Regression risk | Tests / acceptance | Effort | Dependency | Commit group |
|---|---|---|---|---|---|---|---|---|---|
| TD-P2-001 | SRS orchestration | CLI and web duplicate detector→risk loops and have diverged. | new `classroom_monitor/pipeline/`, runner/runtime/tests | Extract one frame pipeline returning canonical episodes/patterns/incidents; keep CLI/Web as lifecycle/render/export adapters. | Large behavior regression | Same source/config/range yields equivalent canonical output. | L | all P0 green | 7 |
| TD-P1-006, P2-023 | Domain | AI status, decision, severity and priority drift across layers. | versioned contract, DB/API/exporter/JS adapters | Minimal Classroom-only contract and explicit adapters; do not migrate Local perception. | Compatibility consumers | Contract round-trip tests; priority explicitly not probability. | M | P0 incident adapter | 7 |
| TD-P2-002 | Configuration | Multiple defaults and unclear precedence. | config resolver/pipeline/manifest/tests | Typed resolved config; explicit scene→demo→base→engine precedence and startup dump without numeric tuning. | Changed implicit defaults | Precedence tests and manifest snapshot. | M | shared pipeline | 7 |

## Phase E — VIGIL Local stabilization

| Issue | Subsystem | Evidence / root cause | Files affected | Proposed change | Regression risk | Tests / acceptance | Effort | Dependency | Commit group |
|---|---|---|---|---|---|---|---|---|---|
| TD-P1-016 | Local lifecycle | Daemon worker and short join race source/model release and final save. | `exam_monitor/app.py`, lifecycle helper/tests | Cooperative non-daemon shutdown; finish worker before resource release and final save; explicit timeout error. | UI close latency | Stop/restart and late-event persistence tests. | M | none | 8 |
| TD-P1-017 | Calibration | Samples lack guided neutral quality decision. | engine/app/models/tests | Center-fixation progress, quality result, reject obviously non-neutral calibration, explicit Recalibrate. No threshold tuning. | More rejected sessions | Calibration state/quality and recalibration tests. | M | none | 8 |
| TD-P1-018 | Material policy | Detection equals prohibition; all books alert. | engine/app/session model/tests | Per-session allowed-material policy; object observation remains visible while alert policy filters permitted material. | Existing book alert expectation | Closed-book signals book; open-book permits it. | S | none | 8 |
| TD-P2-013 | Held tracking | Held gaze/head can contribute activation. | engine/event detector/tests | Mark held estimates; allow display/release grace but never create/extend new evidence indefinitely. | Choppy release behavior | Held-only frames cannot activate or perpetually sustain incident. | S | none | 8 |

## Phase F — security and prototype persistence hardening

| Issue | Subsystem | Evidence / root cause | Files affected | Proposed change | Regression risk | Tests / acceptance | Effort | Dependency | Commit group |
|---|---|---|---|---|---|---|---|---|---|
| TD-P1-013, P1-022 | Files/evidence | Raw filename writes and recursive basename lookup are ambiguous/unsafe. | upload routes, evidence endpoints/store/tests | UUID server names, streamed size/type validation, resolved-root containment, immutable evidence ID/run lookup. | Old URLs | Traversal/oversize/wrong-extension rejected; exact evidence served. | M | contract/DB | 9 |
| TD-P1-014 | Network boundary | Public bind and wildcard credentialed CORS. | `server.py`, launchers, API settings/tests | Localhost default; explicit origins; optional demo token for LAN. | LAN demo setup | Default is local; LAN mode refuses without configured token. | M | none | 9 |
| TD-P1-015 | Browser safety | API strings enter `innerHTML`. | dashboard JS/HTML/tests | DOM/textContent or escaping plus CSP-compatible rendering. | Styling changes | Malicious note renders as text. | S | none | 9 |
| TD-P1-009..011 | SQLite | FKs off, wrong camera identity, weak uniqueness/transaction behavior. | database/models/repositories/runtime/tests | Enable FK/WAL/busy timeout; resolve Camera UUID; unique event/evidence identity for new DB; transaction boundaries and surfaced persistence health. | Existing dirty DB schema | Fresh-DB FK/uniqueness/rollback tests; no destructive migration. | M | contract | 9 |
| TD-P1-012, P2-022 | Legacy route | Broken legacy inference is mounted as current. | `api/main.py`, inference route/settings/tests | Competition profile returns explicit 410 unless opt-in; retain code and exports. Canonical API is `/api/v1/demo`. | Legacy clients | Route is unambiguously deprecated, never presented as SRS v2. | S | none | 9 |

## Phase G — validation and measured performance

| Issue | Subsystem | Evidence / root cause | Files affected | Proposed change | Regression risk | Tests / acceptance | Effort | Dependency | Commit group |
|---|---|---|---|---|---|---|---|---|---|
| TD-P2-019, P2-020, P2-021 | Failure boundaries | Current tests are shallow and fallbacks/errors can be silent. | integration/smoke tests, health endpoints | Add the 22 mandated categories; label unit/integration/real-model smoke; expose failure health. | Test duration/hardware variance | Full suite plus real model/media manifests. | L | phases A–F | each group |
| TD-P2-010, P2-011, P2-025 | Performance | Historical FPS is not HEAD-bound; potential JPEG/DB overhead unmeasured. | profiling hooks/current status | Profile stages after correctness; optimize only dominant measured cost. | Semantic drift | Student and India full runs, CLI/Web LIVE/REPLAY, recorded FPS/realtime/events/evidence/GT. | L | all correctness gates | 10 if code changes |
| TD-P2-003..009, P2-015..018, P2-024 | Non-blocking research/platform debt | Requires threshold/data/retention/platform decisions beyond safe prototype scope. | debt register | Keep `DEFERRED — POST COMPETITION` with reason; do not hide limitations. | None | Human accepts deferral. | variable | human/data | deferred |

## Phase H — documentation and human acceptance

| Issue | Subsystem | Evidence / root cause | Files affected | Proposed change | Regression risk | Tests / acceptance | Effort | Dependency | Commit group |
|---|---|---|---|---|---|---|---|---|---|
| TD-P3-001, P3-004, P3-008 | Product docs | README is unrelated and reports mix historical claims. | README, `PROTOTYPE_CURRENT_STATUS.md`, stale docs/debt register | Publish two-product quick start, health/mock disclosure, HEAD-bound metrics and acceptance commands; mark historical/superseded claims. | Documentation drift | Commands executed at final HEAD; claims cite manifest/test output. | M | validation | 10 |
| TD-P3-002..003, P3-005..007 | Cleanup | Generated artifacts, exports, encoding and large modules need owner decisions. | debt/legacy maps | No deletion in remediation; mark explicit post-competition work. | None | Human decision recorded. | variable | human | deferred |

## Commit and push gates

1. `docs(audit): record remediation baseline and implementation plan`
2. `fix(demo): repair live/replay incident and evidence contracts`
3. `fix(perception): fail closed and expose capability health`
4. `fix(realtime): deliver review incidents through websocket and polling`
5. `fix(reasoning): deduplicate patterns and correct occupancy/recidivism`
6. `fix(review): make evidence and human decisions durable and truthful`
7. `fix(runtime): harden lifecycle and evidence synchronization`
8. `refactor(classroom): share one SRS v2 pipeline between CLI and web`
9. `fix(local): harden lifecycle calibration and material policy`
10. `fix(security): harden competition API, storage and evidence paths`
11. `docs: publish HEAD-bound prototype status and acceptance flow`

Before each commit: inspect `git diff --stat` and `git diff`, stage named paths only, run the relevant tests, then push the coherent group. The final remote branch must resolve to the same SHA as local. Human acceptance remains mandatory; this plan cannot authorize a production-ready or frozen claim.
