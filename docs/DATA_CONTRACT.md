# Market data contract v1

Cached data consists of `1h.csv`, `15m.csv`, `5m.csv`, `symbol.json`, and `contract.json`.
The CSV header is `timestamp,open,high,low,close,volume`, with optional `spread` in absolute price units.
The contract records source, `dataset_kind` (market/synthetic/unknown), timestamp convention, IANA timezone,
price basis (bid/midpoint), calendar and spread provenance. Unknown provenance is reported; it is not market evidence.

Default MT5 Python timestamps are interpreted as UTC. `MT5_SOURCE_TIMEZONE` is an explicit broker-wall-time
override for a separately verified source contract. No offset is inferred from how far a quote is from the
computer clock. Both history request bounds and returned bars/quotes use the same declared convention.
Ambiguous or nonexistent DST wall times fail. Do not set this override simply to make a freshness gate pass.

Connection diagnostics and serving quotes now share source normalization, price validation and the same
timestamp rule: `-5 <= signed_age_seconds <= STALE_PRICE_SECONDS`. Negative ages are retained, not clamped.
The read-only clock audit records raw tick seconds/milliseconds, raw bars, normalized timestamps and host UTC.
Its `source_contract_verified` field stays false: observations cannot establish a historical timezone contract.
Parameterized winter/summer/transition tests use a synthetic Europe/Helsinki contract to exercise conversion;
they do not assert that MetaQuotes-Demo follows that timezone or its DST transition dates.

`fx_ny` opens Sunday 17:00 and closes Friday 17:00 in America/New_York, including DST. `fx` retains the
legacy fixed Friday/Sunday 22:00 UTC approximation. `24/7` includes all bars. Broker holidays and maintenance
breaks require documented explicit exclusions; they are never forward-filled. A calendar is not inferred from
missing rows because doing so could hide missing observations.

An evaluation window is `[start,end)`. Every timeframe needs the configured indicator warm-up strictly before
the window, finite positive OHLC, finite nonnegative volume/spread, correct OHLC geometry, monotone unique
aligned timestamps and complete expected evaluation/warm-up coverage. Current decisions additionally need
closed candles, a valid bid/ask, and a fresh, nonfuture quote. Bad data is blocked or explicitly diagnostic-only.

The current replay price basis is midpoint. Bid-bar conversion uses the declared observed or configured spread;
constant spread is an assumption, not a measured historical quote series. Costs, constant tick economics and
excluded rollover are labeled in artifacts. Synthetic fixtures are never substituted for an unavailable MT5 feed.

Persistent paper replay commits bar cursor, position state, balance/day P&L and close events in one SQLite
transaction. Incomplete bars cannot advance a cursor. A failed transaction restores in-memory state. Quotes
and decision-time snapshot hashes preserve when each value became observable.
