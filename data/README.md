# Data distribution

GitHub is not used as a raw-data blob store inside ordinary commits. The full
raw corpus is published as release assets next to this repository:

- `data-full-2026-10-28`: complete raw + normalized + derived snapshot.
- `data-latest`: durable core snapshot used by the scheduled GitHub Action and
  refreshed daily after the Hong Kong crawl, metric build and tests pass.

Every release includes a SHA-256 sidecar. Release assets are public and can be
downloaded without credentials.

Smaller reproducible outputs live directly under `data/derived/` in the Git
repository so that map values and methodology can be reviewed without first
downloading the full corpus.

The full normalized table is named
`data/normalized/centaline/transactions_full_latest.parquet` inside
`data-latest`. Legacy dated files are retained in `data-full-2026-10-28` for
exact reproduction of earlier reports.
