# Alibaba Cloud ECS migration preparation

Status: **GIT SOURCE AND PYTHON ENVIRONMENT STAGED ON ECS; LIVE CUTOVER PENDING**.

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
Trading credentials and account ledgers have not yet been installed on ECS;
local live workers remain running pending the authenticated cutover.

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
