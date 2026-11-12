# Alibaba Cloud ECS migration preparation

Status: **KERNEL AGGREGATE LIMIT VERIFIED; CORE CAPACITY FAILED; STARTUP HELD**.

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
| Selector | 400 / 450 MiB | 128 MiB | 40% of one CPU |

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
