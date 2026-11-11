# Alibaba Cloud ECS migration preparation

Status: **GIT SOURCE, CREDENTIALS AND FINAL LEDGERS MIGRATED; POST-REBOOT RESOURCE AND LIVE ACCEPTANCE PENDING**.

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
interrupted acceptance; the user rebooted the instance. Fresh native heartbeats,
current account reconciliation and notification acceptance remain required.

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

`python3 -m deploy.aliyun_20261111.systemd --apply`, run as root in the existing
Git checkout, verifies and backs up unit definitions, installs resource limits,
and adds the index for running workers. It never starts or restarts a trader.
Stop the old notification daemon before applying and restart it afterward so it
uses the lower-memory import path. Units allow three starts per 15 minutes with
180-second restart delay, single-threaded numerical libraries and bounded CPU,
memory, swap and task counts. The selector has its own lower resource budget.

| Service | MemoryHigh / MemoryMax | SwapMax | CPU quota |
| --- | --- | --- | --- |
| Spot | 600 / 650 MiB | 256 MiB | 60% of one CPU |
| Futures | 550 / 600 MiB | 256 MiB | 60% of one CPU |
| Notifications | 80 / 120 MiB | 64 MiB | 10% of one CPU |
| Selector | 200 / 300 MiB | 128 MiB | 20% of one CPU |

These are host-protection limits, not proof that both strategy workloads fit.
If native cycles cannot finish within them, preserve/reconcile the latest account
state and stop the affected service rather than entering an OOM/restart loop.
Stagger any necessary worker restarts and verify fresh heartbeats and memory
pressure before starting another heavy process. Do not silently shrink the
strategy universe or change financial rules to fit the host.
