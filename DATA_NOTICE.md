# Data notice

This repository contains transformed public-interest research data and links to
full immutable raw snapshots in GitHub Releases.

- Public transaction records are collected only from endpoints that are
  accessible without login, including Centaline's public transaction-search
  API, 28Hse, Midland and official Hong Kong datasets.
- The repository does not grant any additional rights to third-party source
  data. Source terms and attribution continue to apply.
- `data/derived/` contains normalized/aggregated research outputs. Full raw
  snapshots and the full normalized Parquet table are distributed separately to
  keep Git history practical.
- The project computes **gross** rental yield, not net yield. Fees, vacancy,
  taxes, maintenance and financing costs are not deducted.
- This is a research dataset, not investment, legal or valuation advice.
