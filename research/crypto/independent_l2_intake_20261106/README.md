# Independent L2 input diagnostics (historical archive)

This directory preserves a registered, auxiliary data-intake diagnostic from
2026-11-06. It does not implement a strategy, account simulator or deployment
decision. It is not an official-engine backtest.

`capture.py` retains bounded public samples in the ignored
`user_data/crypto_quant/rd_program_20261105_v1/independent_l2_v002/` directory.
Registrations and receipts record publisher URLs, revision, byte limits and
SHA-256 hashes. Raw vendor files and signed download links are not included.
The failed first capture, initial result and later registration amendments are
preserved rather than replaced.

`audit.py` uses the existing native-source order-book diagnostic in
`../historical_universe_depth_20261106/audit_universe.py` and the retained inputs.
The recorded BTC sample is outside the frozen windows. Normalized DYDX/DOT
frames matched some native update identities and levels, but lack original
continuity fields and verified snapshot boundaries. `result.json` therefore
retains **zero new frozen-window depth admissions and zero financial accounts**.
The additional CryptoStruct/CryptoHFT registrations and capture receipts record
input acquisition only; they are not additional completed audit results.

On 2026-11-11 the retained capture hashes, audit code hash and full audit output
were checked offline. Recalculation matched `result.json` after excluding only
the observation timestamp; the original result was not overwritten. Receipt
JSON was parsed and additional captured-object hashes were also verified.

To rerun locally after restoring the exact ignored inputs referenced by the
receipts:

```sh
.venv/bin/python research/crypto/independent_l2_intake_20261106/audit.py
```

This command regenerates `result.json`, including its observation timestamp.
Running `capture.py` performs network downloads and refuses to overwrite retained
raw files. Neither script is part of the running live services.
