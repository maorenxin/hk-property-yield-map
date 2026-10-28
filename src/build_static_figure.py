from pathlib import Path
import json
import pandas as pd
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parents[1]
df = pd.read_csv(root / "data/derived/ratio_by_region_month.csv", parse_dates=["period"])
latest = df.loc[df["period"].eq(df["period"].max())].copy()
fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
colors = ["#2166ac", "#67a9cf", "#d1e5f0"]
bars = ax.bar(latest["region"], latest["gross_yield_pct"], color=colors)
ax.set_title(f"Hong Kong gross rental yield by RVD region ({latest['period'].max():%Y-%m})")
ax.set_ylabel("Annual gross rental yield (%)")
ax.set_ylim(0, max(4.5, latest["gross_yield_pct"].max() * 1.25))
ax.grid(axis="y", alpha=0.25)
for b, v in zip(bars, latest["gross_yield_pct"]):
    ax.text(b.get_x() + b.get_width()/2, b.get_height() + 0.04, f"{v:.2f}%", ha="center", va="bottom")
fig.text(0.01, 0.01, "RVD 1.1M + 1.2M; same month/region/class; median across classes A-E. Gross yield = monthly rent × 12 ÷ sale price.", fontsize=8)
fig.tight_layout(rect=[0, 0.05, 1, 1])
fig.savefig(root / "site/ratio_yield_latest.png", bbox_inches="tight")
fig.savefig(root / "site/ratio_yield_latest.svg", bbox_inches="tight")
plt.close(fig)
print("wrote static figures")

