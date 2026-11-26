# Alibaba Cloud ECS migration preparation

This runbook includes the November 15 recovery and November 16 bounded selector
acceptance. The status and measurements in those sections are historical observations.

## November 16 bounded daily-input backend

At the November 16 acceptance, the live publisher used `deploy/nfi80_selected/light_selector.py`; the
original pandas/CCXT publisher and pinned historical RuleBook remain the parity
reference. Both are local input-preparation extensions. Native Freqtrade still
owns signals, accounts, orders and position management. The policy hash, frozen
candidate universe, structural blacklist, minimum 60 closed daily rows, previous
completed UTC-day turnover ranking, tie order, 80-pair count, contract identity
checks and expiry gate are retained. No financial setting or running worker was
changed to fit memory.

Full admission now shares the pure funding validator with native wallet-class
selection. A release-pinned 104-KiB input projection avoids deserializing the
4-MiB historical schedule and 5-MiB market archive every day. Every refresh still
hashes the original source files and eligible seed inputs. The projection is
generated from those exact archives and checked against them in validation.
The original same-day shortcut stays lightweight too.

The first second-day runtime attempt failed after 12.456 seconds because a
public reader inherited a 10-second local request-budget wait. This was a failed
verification, not an OOM or a successful full refresh. Its log and partial input
cache were preserved. Unsigned public readers now wait within the remaining
bounded preparation budget; signed diagnostics keep their original timeout, and
shared quotas, leases and cooldowns are unchanged. A fresh second-day cache is
used for the follow-up runtime acceptance.

Closed public JSON inputs are saved per UTC day and contract under private
`closed-daily-cache/` directories. A failed or bounded attempt retains completed
pairs; a later attempt repeats source/current-market gates and fetches missing
pairs. No partial result replaces the consumer snapshot. A job that crosses UTC
midnight must retry the new day instead of relabeling old inputs or extending
their expiry. Successful candidate snapshots are published atomically.

The selector child is bounded to 64/80 MiB MemoryHigh/MemoryMax and 32 MiB swap,
inside the unchanged aggregate 832/900-MiB and 256-MiB swap budgets. Admission
requires 80 MiB effective aggregate headroom, 480 MiB host availability and full
pressure avg10 below 1%. It retries nonblocking admission within seconds 75–165
of a five-minute interval, releasing the compute lock while waiting. Work has a
135-second maximum preparation deadline, shortened late in the window to leave
time before the next candle. The service has a 210-second overall runtime bound;
the existing timer remains at second 75. Boot/failure restarts remain disabled.

Local validation: **92 tests passed**, targeted Ruff checks passed, and all
result fields matched the unmodified RuleBook on three captured complete runs
(November 9, 10 and 11; 267 candidate frames each). Synthetic boundary fixtures
also compare native CCXT/Freqtrade market filtering, ties, blacklist matching,
minimum history, malformed data, interrupted retries and UTC rollover. These
are input/operational checks, not return or backtest evidence.

The first ECS nonpublishing full recomputation on November 16 completed 268
public GETs in **91.039 seconds**, without loading pandas/NumPy/CCXT/SQLAlchemy.
Its peak RSS was **59.5 MiB**; sampled child memory peaked at **63.9 MiB** and
aggregate memory at **828.8 MiB**. Maximum sampled full pressure avg10 was
**0.02%**, minimum host availability **603.2 MiB**, and no OOM kill was added.
Both original native PIDs (spot 14955/futures 14981) retained RUNNING heartbeats
through the workload and the subsequent 90-second observation. The consumer
snapshot was unchanged during this verification. Private receipts are on ECS
under `user_data/deployment/refresh-memory-20261116/`.

Final acceptance: **2026-11-16 21:24:40 Shanghai**, execution source
`80917bd14dee6842dae91b9155058e307163d367`. The selector-only unit was promoted;
the existing timer was restored and a new natural timer trigger completed with
exit code zero. The observed 80-pair pool had decision day November 16, closed inputs
through November 15, and expired **November 17 at 08:00 Shanghai**. The native
futures consumer's actual logged whitelist matched that pool. Both original
market PIDs retained fresh RUNNING heartbeats without observer faults, and
authenticated exchange GET reconciliation and fresh notification delivery passed.

After the public-wait correction, a fresh nonpublishing November 15 input run
completed in **91.149 seconds** with peak RSS **59.3 MiB**, sampled child memory
**63.9 MiB**, aggregate memory **831.9 MiB**, maximum sampled full pressure
avg10 **0%**, and zero additional OOM kills. Its workload did not replace the
current November 16 consumer snapshot. At final acceptance group RAM was
**794.9 MiB** and swap **73.9 MiB**. The unchanged aggregate limits were verified
as 832/900 MiB plus 256 MiB swap. Local live and paper LaunchAgents remain
disabled, and no matching local live trading process was found.

The final private receipt is
`user_data/deployment/refresh-memory-20261116/final.private.json` on ECS. The
initial budget-wait failure log and partial cache remain archived in the same
private incident directory. Verification-script failures (systemd's formatted
monotonic timestamp and the earlier failed budget-wait workload) remain in Cloud
Assistant history and are not counted as successful acceptance.

Validation covers two real closed-input decision days, the native/current-day
timer, and offline UTC-boundary cases. It is not a recording of the next natural
UTC midnight transition, which has not yet occurred at this acceptance time.

Closed-day data cannot be published before the UTC cutover. The expiry guard
continues to reject stale new-entry inputs during preparation or a source/API
outage; this change does not promise an uninterrupted pool or guaranteed future
availability. In particular, it never solves an expired pool by extending old
data's validity.

## November 15 operational recovery

Latest verification: **2026-11-15 10:19:32 Shanghai**. Spot PID 14955 and futures
PID 14981 completed native analysis and reported RUNNING with no observer faults.
Three final observations from 10:17:56 to 10:19:17 retained both PIDs and fresh
heartbeats. Authenticated GET-only account/ledger reconciliation passed after
startup, notification health returned HTTP 200 with fresh delivery receipts, and
the selector timer was active. Local trading LaunchAgents remain disabled and no
matching local live process was found.

The incident began with both old PIDs still present but heartbeats more than six
hours old, aggregate full memory pressure around 89%, an expired November 13
pool and notification health HTTP 503. Kernel evidence showed the futures process
in `mem_cgroup_handle_over_high`, approximately 839 MiB of anonymous memory, and
zero swap use despite an existing 1-GiB swap file. Host `vm.swappiness=0` prevented
effective anonymous-page reclamation under this cgroup pressure. On the user's
repair/deployment instruction, fresh exchange GETs and native ledgers first
confirmed both accounts flat with no open orders. Runtime admission was withdrawn,
the stalled workers stopped through systemd, and both SQLite ledgers passed
integrity checks and were backed up without resetting history.

The operational repair sets `vm.swappiness=60`, persisted in
`/etc/sysctl.d/99-z-freqtrade-memory.conf` after the existing Alibaba/sysctl
definitions that set zero. Aggregate MemoryHigh/MemoryMax remain **832/900 MiB**,
and MemorySwapMax remains **256 MiB**. No strategy, capital, leverage, request
budget, credential or account-mode change was made. The pinned native selector
completed a full standalone recomputation in 110 seconds before sequential spot
and futures startup. The 80-pair pool uses closed inputs through November 14 and
expires **November 16 at 08:00 Shanghai**. ECS source remains `da1184efc`.

Final aggregate RAM was **770.3 MiB**, swap **50.4 MiB**, full pressure avg10 **0**,
host CPU **7.5%**, and host available memory **612.3 MiB**. The historical
`oom_kill=1` counter did not increase. Automatic boot/failure restarts remain
disabled. Private receipts, account observations and original-ledger backups are
on ECS under `user_data/deployment/incident-20261115/`, including
`final.private.json`; failed maintenance/verification attempts remain in Cloud
Assistant history. An early verifier incorrectly treated ExecStartPre's zero
MainPID as an exit; another applied a stricter 90-second heartbeat limit than the
existing observer's 180-second contract. Neither is reported as a successful
acceptance attempt. Final verification used the unchanged observer contract.

That recovery restored both live services; it does **not** establish reliable
unattended cross-day selection alongside both resident workers. The existing
selector memory gate is retained and may defer a new-day refresh; snapshot expiry
still blocks new futures entries. Operational recovery is not strategy-return or
live/backtest-parity evidence.

Previous verification: **2026-11-13 10:11:46 Shanghai**. Both native workers were
RUNNING without observer faults after more than 90 stable seconds. Spot PID
3015 was retained; futures PID 5434 completed fresh 80-pair analysis. Current
group RAM was 785.3 MiB, host CPU 3.5%, swap zero, and aggregate hard/soft limits
remained 900/832 MiB. The historical synthetic OOM-kill counter stayed at one;
this recovery added no OOM kill. Automatic boot/failure restart remain disabled.
The futures ledger recorded one completed strategy trade, two filled orders and
no remaining open trade/order; these observations are not return validation.

The old snapshot expired at 08:00 Shanghai and blocked new futures entries.
Fresh GET-only account reconciliation confirmed flat futures with no orders,
flat spot and one unowned spot order, which was preserved. Futures alone was
stopped while spot remained live. The existing pinned selector ran in its idle
window, published decision day November 13 with closed inputs through November
12, and futures then restarted. The pool expires **November 14 at 08:00
Shanghai**. Private evidence is retained on ECS under
`user_data/deployment/restore-today-20261113/`.

ECS checkout is `da1184efc`; the retained spot process predates that checkout.
This revision extracts the same SQLite admission state machine into a module
without CCXT/pandas dependencies. Native clients retain their CCXT exception
facade. The authenticated auxiliary preflight now uses the lightweight GET
client, with the same persistent budgets, leases and cooldowns. Forty related
tests and targeted Ruff/source-pin checks pass. This reduces preflight imports;
it does **not** solve full-selector concurrency. The selector timer is active
but remains resource gated, and reliable unattended new-day refresh alongside
both resident engines is still unproven. A current restored pool must not be
reported as completion of that outstanding scheduling/memory requirement.

At 16:38 UTC on 2026-11-12 (00:38 Shanghai on November 13), both native live
workers were RUNNING with fresh heartbeats and no observer faults. ECS execution
source was `4061507eb`. The notification relay and selector timer were active.
Observed group RAM was about 799 MiB, live-restoration peak about 832 MiB,
host CPU 6.2% and host available memory about 583 MiB. The kernel retained
900-MiB MemoryMax, 832-MiB MemoryHigh and 256-MiB MemorySwapMax. Automatic boot
and automatic failure restart remain disabled. Private authenticated preflight,
native heartbeat/ledger observations, delivery receipts and resource evidence
are retained privately on ECS; they are not financial-return evidence.

Restoration followed the user's explicit core-priority, noncore-staggering
instruction: both native core pipelines/rechecks and the notification reserve
stabilized; then all probes stopped and a full native selector recomputation ran
independently before trading startup. That selector phase passed in 111 seconds
with an approximately 404-MiB child peak and published the valid 80-pair pool.
Source/config/account checks preceded notification, spot, then futures startup.
Futures waited for the current spot process's completed native analysis. Fresh
RUNNING heartbeats were observed for 90 stable seconds before timer release.
The related 167 local tests and targeted Ruff checks passed.

The earlier simultaneous-retention controller did **not** pass overall: its core
phase passed, but full selection was deferred by memory admission until its
deadline. That failure is preserved; it was not reclassified as a concurrent
capacity pass. Current operational admission is phased core startup with
noncore resource deferral. The daily timer may continue deferring a full new-day
refresh while both engines retain their memory. If a snapshot expires before
refresh completes, the existing NFI snapshot gate rejects new futures entries;
existing trade management stays in the native worker. The observed snapshot
expires at 00:00 UTC on November 13 (08:00 Shanghai). Continuous future refresh
availability under both resident engines has **not** been established.

Historical incident and rejected capacity observations follow.

The heap/checked-bytecode revision completed both native analysis pipelines
(36 spot / 80 futures) twice, followed by both two-cycle rechecks at 15:37 UTC.
It had no additional OOM, no original-ledger changes and pressure avg10=0 at the
end, but reached the original 600-second deadline before its final continuous
60-second stable observation completed. Its `passed=false, reason=deadline`
record is preserved; it is not operational admission. This revision preceded
the lazy SciPy and shared-compute-lock changes.

The reproducible `capacity` controller runs under the pinned Python interpreter
with every real service/timer held. It verifies source and kernel budgets,
builds checked bytecode in a bounded transient unit, initializes spot then futures,
performs full analysis rechecks, and requires 60 stable seconds. Only then does
it add a 64-MiB notification reserve and execute the full native selector in the
idle window, followed by another 60 stable seconds. A 1200-second deadline allows
both observation windows. Sustained reclaim, host headroom, changed kernel limits,
OOM, early core exit or changed original ledger hashes prevent success. Cleanup
stops only its own transient units. It never creates the live admission marker.

The next memory candidate extends native DataProvider with an explicit live-only
post-analysis cache limit. Default/historical storage is unchanged. The pinned
manual NFI adapter retains the final two analyzed rows, while every raw OHLCV
row still enters the original indicator and signal computation at float64
precision. An AST contract checks all 16 upstream analyzed-frame consumers:
they use only `iloc[-1]`, `iloc[-2]`, and length guards at one or two rows.
Native long/short entry/exit parity, exact values/dtypes/index, independent tail
storage and rejection of historical/API/external consumers are tested. The ECS
read-only probe checks every computed frame's retained tail before publishing
success and measures actual cache bytes. Runtime capacity is still pending.
glibc allocation arenas and trim thresholds are bounded in the systemd
environment; numeric types, strategy parameters and account settings do not change.

The first ECS cache revision reduced spot analyzed-cache bytes from 68000476 to
140940 with exact tail values/dtypes, but sequential dual-engine initialization
still hit the pressure guard at 15:20 UTC. No additional OOM or ledger mutation
occurred. The next candidate explicitly collects unreachable temporaries and
returns free glibc heap pages after initialization/full analysis, recording RSS
before/after. Unchanged-candle loops skip this reclamation. This changes memory
lifetime only; native indicator/entry/exit code remains inherited. See
[Python GC](https://docs.python.org/3.12/library/gc.html) and
[Linux malloc_trim](https://www.man7.org/linux/man-pages/man3/malloc_trim.3.html).
Its actual savings and dual-engine capacity are pending native verification.

Another native extension moves SciPy statistics import into the report-only
trade p-value calculation. The numerical implementation remains unchanged and
all 43 native metrics tests pass. A fresh live-engine import test verifies that
SciPy statistics is absent until requested. Checked-source Python bytecode is
built once, under the aggregate limit with all workers held, to avoid repeatedly
compiling the 3-MiB upstream strategy during live initialization. Caches retain
Python's source-hash validation; changed source invalidates the cached bytecode.
The `bytecode` helper forces conversion of pre-existing timestamp caches as well.

The scheduling candidate uses one shared heavy-compute lock. Core workers take
it only for actual indicator work; unchanged-candle trade-management loops stay
available. Futures ExecStartPre waits for a fresh spot analysis marker matching
both the current systemd MainPID and Linux process-start ticks. Stale PID files
cannot admit futures initialization. The daily-input timer starts at second 75
of each five-minute interval. Full selection additionally requires an idle lock,
256 MiB effective group headroom, 656 MiB host availability and full-pressure
avg10 below 1%. Only inactive file cache is discounted as reclaimable. Selection
defers without notifications when admission is unavailable and has a 180-second
runtime bound, ending before the next candle boundary if admitted in its
75–105-second window. These are local operational extensions; native strategy
and selection rules remain pinned. Runtime core/noncore validation is pending.

At 15:03 UTC the 832-MiB soft / 900-MiB hard sequential diagnostic stopped
automatically after sustained memory pressure. No selector or notification
reserve ran. Spot completed two full 36-pair analysis cycles; futures had not
finished initialization. Shared sampled peak was 883810304 bytes (about 843 MiB),
minimum host available memory was 418447360 bytes, and the OOM-kill count did not
increase. Original ledger hashes were unchanged. This rejects the tested
workload under the current thresholds; it does not establish an absolute RAM
minimum or justify increasing the hard cap.

Final authenticated GETs at 15:05 UTC reconciled both accounts with zero
positions/native open trades. The existing unowned spot order was preserved;
futures had no open orders. At final inspection ECS code was `1bf86b6a6`, all
four live MainPIDs were zero, all four services plus the selector timer remained
disabled, and the runtime admission marker was absent. The selector retained
its historical failed state; it was not restarted. Kernel values were
memory.max=943718400, memory.high=872415232 and memory.swap.max=268435456.
Host CPU sampled 1.8% and available memory was 1288248 KiB after diagnostics.
No live worker or order mutation was started. Local trading agents also remained
disabled and no matching local process was found. Further memory engineering
and successful core admission are required before any trading restoration;
noncore daily scheduling needs its own budget after that.

The initial concurrent diagnostic was stopped at 14:48 UTC after sustained
memory reclaim pressure; the shared peak was 813592576 bytes, with no additional
OOM kill and unchanged live ledgers. This is a failed diagnostic, not capacity
acceptance. On user instruction, admission testing now starts core workers
sequentially and does not require daily selection or a synthetic notification
reserve to initialize concurrently. A separate operational plan must still keep
noncore work inside the aggregate budget before daily scheduling is released.
The first sequential attempt stopped before native initialization completed:
disabled probe RPC sections lacked schema-required fields. Those isolated
diagnostic fields are corrected and checked against the native schema; that
attempt establishes no workload-capacity result.

The corrected sequential diagnostic completed 36-pair spot analysis twice at
14:56 UTC. Starting futures afterward triggered the pressure guard at 14:57 UTC
with no noncore jobs, no additional OOM and unchanged live ledgers. The next
candidate raises only the shared soft threshold from 768 to 832 MiB, retaining
the kernel-tested 900-MiB hard ceiling. This tests whether early reclaim caused
the initialization stall; it is not a capacity pass or trading admission.

The user reported another memory exhaustion/server outage after the historical
acceptance below. After recovery, ECS code `ff62a1a12` installed the aggregate
limit and startup hold. On 2026-11-12 at 13:58 UTC all four service MainPIDs were
zero, all four services and the selector timer were disabled, and Restart=no
was confirmed. Local native ledgers had zero open trades/orders at 14:06 UTC;
the later authenticated read is recorded above. The historical
acceptance below does not establish current trading availability.

An isolated test at 14:03 UTC confirmed kernel `memory.max=943718400` (900 MiB):
the test process was killed inside the shared slice, its oom_kill counter rose
by one and the host remained accessible. The 768-MiB soft threshold was raised
to 900 MiB only during this hard-limit test and restored afterward. The initial
test reached the soft threshold and timed out without triggering the hard limit;
that failed attempt is preserved. These are enforcement checks, not workload
capacity acceptance. No trading workers started and no exchange orders were sent.

The real daily selector check then failed in manual.py:58 because the manual
release manifest still pinned the pre-optimization `live_selector.py` hash. The
valid same-day shortcut had hidden this until a full new-day check was needed.
The manifest correction registers the reviewed selector bytes; it changes no
strategy rule, private configuration, research hold, input policy or account
state. A regression exercises the shipped source manifest through the real
manual gate using isolated private fixtures. Keep startup held until a full
native daily recomputation and combined worker capacity checks pass.

The corrected full daily recomputation passed at 14:26 UTC in an isolated
600-MiB diagnostic child under the unchanged aggregate 900-MiB ceiling. Its
sampled peak was 364716032 bytes (about 348 MiB), exceeding the previous selector
300-MiB child ceiling. The 300-MiB attempt timed out after five minutes at its
250-MiB soft threshold; that failure is retained. The revised selector candidate
uses 400/450 MiB soft/hard bounds and 40% CPU, still under the same aggregate
ceiling. Runtime validation of this revision and combined workloads is pending.

`native_probe.py` measures native initialization and two full candle/indicator
analysis cycles using a fresh database and private directory. It requires the
held aggregate cgroup, blocks non-GET HTTP transport plus native order mutation,
and never calls the native startup or trade loop. Original credentials are read
through the existing configuration overlay, without copying them to the probe.
RPC and notifications are disabled in the isolated process. Successful probes
are analysis-pipeline capacity evidence only; actual live heartbeats, account
reconciliation and order protection remain separate recovery requirements.
After initialization, an explicit private `recheck` marker triggers another two
full analysis cycles while the daily publisher runs, so the combined test covers
analysis allocation bursts as well as retained idle process memory. Original
ledger hashes must still match before publishing either success marker.
The native publisher accepts an explicit `force_refresh=True` for resource
recomputation of a valid same-day pool. Its default scheduled behavior still
deduplicates completed UTC days; source/identity/closed-candle gates and selection
rules remain identical. This allows combined resource tests without hiding the
currently valid published snapshot from other readers.

The user requested migration of the existing Freqtrade live services to the ECS
instance visible in Alibaba Cloud Client, `i-j6c21s3zs1jgm5rpgg5l`, public address
`47.239.218.211` (Hong Kong). No new instance was purchased or created.

The client's Cloud Assistant executed read-only environment commands. The first
task (`t-hk072solj0c5tds`) showed Ubuntu/systemd services and no matching Freqtrade
process; its exit code 2 came from the final directory-listing command, not a
trading service. Further task `t-hk072sos2bvspa8` completed, but its full result
was not reliably readable through the current UI session. Native UI interaction
also returned `noWindowsAvailable`. SSH to the observed address reached the
server but failed public-key authentication; the local SSH agent had no identities.
Existing Alibaba account login material was not modified or decrypted.

The user subsequently opened a working SSH session in Alibaba Cloud Client.
Both public Binance time endpoints responded from ECS. Python 3.12.16 and uv
0.13.7 were installed outside protected home directories. At the user's request,
the initial archive staging directory was retained separately and the deployment
directory `/opt/freqtrade` was recreated with `git clone` from
`https://github.com/574495412/freqtrade.git`, branch
`codex/live-preflight-notifications-20261109`. Python/runtime directories were
moved back without copying repository source. Origin and commit were verified.

The locked requirements initially failed because the repository's rolling
`exclude-newer` setting excluded an exact pinned release. Installation with
`uv --no-config pip install -r deploy/requirements.lock` retained every runtime
version pin and succeeded: 95 packages passed `uv pip check`, with CCXT4.5.89.
Both authenticated account checks subsequently passed from ECS, and the final
ledgers, credentials and persistent request-protection state were migrated with
user authorization. Local live workers and notification/selector agents are
unloaded and persistently disabled. Remote units started, but resource pressure
interrupted acceptance; the user rebooted the instance. The subsequent recovery passed fresh native heartbeats, authenticated account
reconciliation, notification delivery and the daily-input timer checks. The
private runtime receipt records the exact observation time and service state.

## Candidate

`prepare.py` derives private Linux configurations and systemd units from the
currently verified local deployment. It does not instantiate a trading bot,
copy exchange credentials, stop local services, or install/start remote services.
Generated files live under ignored `user_data/deployment/aliyun-candidate-*` with
private permissions; they contain an existing local notification token and must
not be committed or published.

The candidate uses `/opt/freqtrade`, a dedicated `freqtrade` service account and
Python 3.12+. The existing pinned dependency lock is retained. Native worker,
strategy, exclusive ledger lock and capital extension remain the execution path.
Budget/ceiling, leverage, slots and trading rules are unchanged. Only filesystem
paths and macOS loopback proxy settings change in the candidate; remote direct
transport is a pending connectivity gate, not assumed to work.

Five offline tests cover path relocation, financial-contract preservation,
stopped configuration and native worker service definitions. The candidate
includes derived manifests so original upstream provenance and strategy hashes
remain checked after path relocation.

The native daily-input selector additionally accepts an ignored
`user_data/deployment/live-selector-runtime.json` containing only `source_root`
and `https_proxy`. On Linux, registered absolute source paths are relocated
inside the deployment root while retaining the original SHA-256 checks. Setting
`https_proxy` to null removes only the macOS loopback route; candidate universe,
rulebook, timestamps, weights and input hashes are unchanged. Without the runtime
file, existing local behavior remains unchanged. Offline tests reject financial
overrides, changed input bytes and paths outside the registered source root.

## Required cutover sequence

On 2026-11-11 the user explicitly removed the 100-USDT cap on both live accounts.
The active Linux configuration must therefore use `live_capital_mode=available_balance`,
omit `available_capital` and `capital_ceiling_usdt`, and use the upstream native
`Wallets` class. The existing 0.99 tradable-balance ratio, stake allocation,
strategy, slots and isolated 3x futures leverage remain unchanged. Native wallet
allocation uses free USDT plus strategy-owned tied stake; externally locked USDT
is not available stake. Funding changes do not transfer funds or adopt/cancel
external orders. Historical 100-USDT evidence remains a separate fixed-budget
record and is not evidence for the expanded account contract.

The fixed-cap wallet remains supported for historical/local rollback files.
Unknown funding modes and mixed available-balance/fixed-cap configurations fail
closed. Native wallet tests cover expansion beyond 100 USDT and exclusion of
the observed external spot-order lock; notification cards report the dynamic
funding mode instead of a historical fixed cap.

1. Authenticate to this exact ECS instance and inspect resources, installed
   runtimes, existing services and public Binance/notification connectivity.
   Do not stop or replace unrelated services.
2. Stage the reviewed source, locked dependencies and candidate without starting
   a trading worker. Validate native strategy/config schemas, derived manifests,
   systemd units and destination permissions. Keep Python outside protected home
   directories when `ProtectHome=true` is enabled.
3. Confirm the current account state; gracefully unload and persistently disable
   the two local trading LaunchAgents before enabling any remote trading worker.
4. After local workers exit, back up both original SQLite ledgers, verify SQL
   equality/integrity and copy them with existing profit caches. Transfer the two
   distinct credential overlays, notification secret/token, daily-universe state
   and shared request-protection state through an authenticated private channel.
   Do not reset history, discard cooldowns, adopt external orders, or merge accounts.
5. Reconcile authenticated exchange GETs from ECS, including API IP admission,
   owned positions/orders and external spot inventory/orders. Preserve external
   orders; a discrepancy prevents startup rather than triggering automatic cleanup.
6. Start notifications and the live selector, then the existing spot/futures
   workers through systemd. Verify fresh native RUNNING heartbeats, wallet sync,
   retained trades/orders, broker reconciliation and notification receipts.
7. Verify local workers remain stopped and exactly one remote worker owns each
   account. Save remote commit/source hashes and runtime evidence. Push alone,
   a prepared unit or a successful process-start request does not establish deployment.

If staging fails, leave local execution running. If failure occurs after the
cutover, stop remote workers and reconcile their latest ledgers before restoring
local execution; never restart from a stale pre-cutover copy.

## ECS resource protection

The observed host has two vCPUs and 1608 MiB RAM plus 1024 MiB swap. Post-reboot
checks showed substantial memory/I/O waiting, without a confirmed previous OOM
record. CPU saturation and an OOM crash are not established.

The shared native request guard now indexes `(expires, lease_until)` so public
cache cleanup does not scan response payloads on every cache write. The index is
additive: budget, cooldown, active leases and account ledgers remain intact. The
notification daemon loads only its read-only dependencies; exchange/numerical
imports are delayed until an authenticated preflight is explicitly invoked.

The old child hard limits total 1670 MiB, exceeding physical RAM before system
and unrelated workloads. No aggregate slice constrained that sum. This is a
confirmed configuration defect; the actual outage cause still requires kernel
logs, cgroup memory events and resource evidence from the host.

The aggregate protection revision is installed on ECS. To apply an incident hold, run
`python3 -m deploy.aliyun_20261111.systemd --hold` as root in the existing Git
checkout. It removes runtime admission, installs boot/restart blocking drop-ins,
disables all four services and the selector timer, and requests a nonblocking
graceful stop. This acknowledges a stop request only: verify every MainPID is
zero and all units/timer are inactive before proceeding.

`python3 -m deploy.aliyun_20261111.systemd --apply` refuses active, transitioning
or unknown unit/timer states and an existing admission marker. It verifies and
backs up definitions, installs a shared `freqtrade-live.slice`, and adds the cache
index. Every service joins that slice and has `Restart=no` plus
`ConditionPathExists=/run/freqtrade-live-admitted`. The marker is absent by
default and disappears on reboot. The helper never grants admission or starts
a worker. A stop is not an account liquidation; reconcile owned positions and
exchange protection separately through authenticated reads.

The candidate aggregate ceiling is MemoryHigh=832 MiB, MemoryMax=900 MiB,
MemorySwapMax=256 MiB, CPUQuota=100% of one CPU and TasksMax=192. This constrains
the entire deployment even when child ceilings sum higher. It does not establish
that both workers fit, nor protect against unrelated applications consuming the
remaining host memory. Keep startup held until observed peak usage, other host
workloads and a complete native daily selector recomputation fit a reviewed
capacity budget. The same-day selector shortcut is insufficient capacity evidence.

| Service | MemoryHigh / MemoryMax | SwapMax | CPU quota |
| --- | --- | --- | --- |
| Spot | 600 / 650 MiB | 256 MiB | 60% of one CPU |
| Futures | 575 / 600 MiB | 256 MiB | 60% of one CPU |
| Notifications | 80 / 120 MiB | 64 MiB | 10% of one CPU |
| Selector (bounded JSON backend) | 64 / 80 MiB | 32 MiB | 40% of one CPU |

These are child ceilings, additionally constrained by the aggregate slice.
If native cycles cannot finish within them, preserve/reconcile the latest account
state and stop the affected service rather than entering an OOM/restart loop.
Stagger any necessary worker restarts and verify fresh heartbeats and memory
pressure before starting another heavy process. Do not silently shrink the
strategy universe or change financial rules to fit the host.

For a bloated cache file, stop both trading workers and the selector/timer, then
run the same helper with `--apply --compact-cache`. It checks worker MainPIDs,
backs up the complete cache database, vacuums free pages, and compares streaming
SQL dump hashes before/after plus integrity checks. It retains every cache row,
budget/cooldown counter and lease, including expired ones. Resume one worker at
a time only after capacity admission and authenticated ledger reconciliation.

During Git updates use a public-source umask (022); private runtime files remain
0600. Check source readability as the `freqtrade` service account before restarting
a daemon. A private umask inherited by Git can otherwise make newly checked-out
Python files unreadable to that account.

The live selector verifies the pinned source chain and private config hash before
returning an unchanged, valid snapshot for the same completed UTC day. This path
loads no exchange, pandas or NumPy modules. Changed policy, UTC day, expiry or
pair count falls through to the existing fully verified native input publisher.
The trading universe and source/data checks remain intact. This avoids loading
the full trading dependency graph on every five-minute timer tick.

## Historical runtime observation, superseded by incident

Deployment code `ff3ed3ee778743c33168490f71e739cff4df9446` passed operational
acceptance on 2026-11-11 at 17:54 UTC. Both native workers reported fresh RUNNING
heartbeats with no current faults; the notification relay passed its health check
and received successful upstream receipts for natural events. Exactly one worker
owned each account, the daily-input timer completed successfully, and the local
live LaunchAgents remained unloaded and persistently disabled.

The final source/notification/cache/selector checks passed 74 offline tests and
Ruff. Authenticated reconciliation and detailed runtime/resource evidence remain
private under `user_data/deployment/`; they are not published in Git. The earlier
resource-pressure acceptance attempt and its rejected review were retained.

This is a point-in-time runtime observation. The small host still has limited RAM
headroom. Resource limits do not resolve the existing research audit findings or
establish future availability, strategy profitability or live/backtest parity.
