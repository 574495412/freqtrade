# NFI 14-slot futures release

The candidate retains the existing NFI X8 entries and exits and the 12-way wallet sizing proposal. It raises concurrent capacity to 14 and caps cumulative entry margin on pure short Rebuy tags at twice the initial fill. The live and paper adapters share those callbacks.

Use the existing deployment's Git remote to fetch a specific reviewed commit. An isolated detached worktree holds the release code. Run `cutover.py --activate` from that worktree on the existing host as root. The command reuses the original systemd service, Python runtime, account credentials, and SQLite ledger.

The operator entry checks the original release, candidate resolution, current snapshot and exchange-to-ledger agreement. It stops only the futures service and takes an integrity-checked SQLite backup. Already open positions stay in the original ledger and go to the new worker. If active orders are detected before the stop, the command aborts before changing the service. A failure after stopping removes its own systemd override and resumes the original worker when exchange-to-ledger agreement can be re-established. The backup is evidence for recovery and never restored as part of an automated rollback.

The cutover receipt records the new version's fresh running heartbeat and exchange reconciliation. The existing notifier still reads the archived 12-slot config for its slot display. Use the active worker log and cutover receipt to verify the 14-slot setting.
