# Standalone Freqtrade paper deployment

The running project is `/Users/chrise/GitHub/freqtrade`. CZ-Agent is not imported
and no CZ process executes these accounts. Both markets use the official
`python -m freqtrade trade --dry-run` entry point, independently owned databases,
and 1,000 USDT simulated starting wallets. This release does not enable live trading.

## Source and environment

- Official engine: `c051e9065c5b677f03817dd1f83cb980af1ed684`.
- NFI X8: `1af55971d3a64703ec449078e09fdec257d0d3c6`, v18.0.146; original license retained.
- Official executable files are unchanged. The existing repository additionally
  contains research modules and research-only lint settings in `pyproject.toml`.
  `release.json` pins the allowed metadata file, strategy and both configs;
  `upstream-source.json` pins the original official files.
- `.venv-paper` is independent of the research `.venv` and CZ environment. Its
  third-party package files were copied to preserve the running version set,
  then Freqtrade and ft_client were installed from this project. `pip check`
  passed and the interpreter cannot import `cz`. `requirements.lock` records
  the dependency versions for a fresh install.

For a fresh environment with Python 3.12 or newer:

```sh
python3.12 -m venv .venv-paper
.venv-paper/bin/python -m pip install -r deploy/requirements.lock
.venv-paper/bin/python -m pip install --no-deps -e . -e ft_client
.venv-paper/bin/python deploy/launch.py spot --verify
```

## Local services

User launch agents `io.freqtrade.paper.spot` and `io.freqtrade.paper.futures`
run from `~/Library/LaunchAgents/`. They start on login and restart on process
exit, with a 30-second throttle. This is a local macOS deployment, not a remote
server deployment. Sleep, shutdown or logout can interrupt market processing.

```sh
launchctl print gui/$(id -u)/io.freqtrade.paper.spot
launchctl print gui/$(id -u)/io.freqtrade.paper.futures
# Stop a service without triggering KeepAlive:
launchctl bootout gui/$(id -u)/io.freqtrade.paper.spot
# Start it again:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.freqtrade.paper.spot.plist
```

The futures service uses the same commands with `futures` in place of `spot`.
`deploy/launch.py` only verifies the pinned release, loads notifications and
holds an instance lock before replacing itself with the official CLI. It does
not implement signals, orders, account arithmetic, or trade lifecycle handling.
The CLI always receives `--dry-run`; inherited exchange keys and Freqtrade
environment overrides are removed. Do not start a second CLI against the same DB.

## State and migration

Private state resides in `user_data/deployment/{spot,futures}/`. Each contains
`trades.sqlite`, `migration.json`, native logs and launchd logs. SQLite backup and
integrity checks were performed after gracefully stopping the previous workers.
Complete SQL dumps matched source and destination. Strategy cache JSON files,
when present, were also copied. The source CZ ledgers remain intact for audit.
The superseded six-pair observer is stopped and archived separately under
`user_data/deployment/archives/six-pair/`; its account is not merged into futures.

At cutover, the spot account had one open simulated SAND/USDT trade. The official
new worker found and restored it. The futures account had zero trades. These
observations establish migration continuity, not strategy profitability.

Do not restart the old CZ workers. Rollback requires stopping both new services
first and reconciling their latest databases; using the pre-cutover source DB
would discard subsequent trading activity.

## Enterprise WeChat

The ignored, mode-600 file `user_data/deployment/.env.notifications` contains only
`WECHAT_WORK_WEBHOOK`. The launcher maps it to `FREQTRADE__WEBHOOK__URL`; no
webhook URL, exchange credentials, private DBs or runtime logs belong in Git.
Native JSON Webhook templates identify spot/futures messages as simulated and
cover order placement, fills, cancellation, status and protection events.

The endpoint test returned HTTP 200 and WeCom `errcode=0`. Native Webhook checks
HTTP status, not the WeCom business result for every message, and has no durable
delivery queue. Transport exceptions may include the URL in private local logs;
do not publish those logs without redaction.

## Research boundary

Research and failed/unknown audit outcomes are preserved separately in
`docs/research-20261105/` and `research/crypto/`. X8 has not passed live promotion;
the migration does not change its signals, fees, risk assumptions or eligibility.
Existing historical scripts retain their historical paths and pinned evidence.
