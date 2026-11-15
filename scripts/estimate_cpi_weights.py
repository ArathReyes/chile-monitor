"""Recover CPI division weights (base 2023) from BDE indices and write config/cpi_weights.yaml.

The 2023-base CPI is a fixed-weight index: headline_t = sum_i w_i * division_i,t. Solving that
over all months since Jan 2023 recovers the basket weights; the max residual (index points)
shows how exact it is. Replace with INE's published table if you have it.

Usage: uv run python scripts/estimate_cpi_weights.py
"""

from __future__ import annotations

from datetime import date

import yaml

from monitor import storage
from monitor.config import CONFIG_DIR, load_series
from monitor.models.transforms import estimate_laspeyres_weights

IDS = [f"cl_cpi_div{i:02d}" for i in range(1, 14)]


def main() -> None:
    conn = storage.connect(read_only=True)
    try:
        wide = storage.read_wide(conn, ["cl_cpi", *IDS]).dropna()
    finally:
        conn.close()
    weights, resid = estimate_laspeyres_weights(wide["cl_cpi"], wide[IDS])
    reg = load_series()
    out = {
        "base": "2023=100",
        "method": "estimated: headline_t = sum_i w_i * division_i,t; least squares, w >= 0, sum 1",
        "sample": f"{wide.index.min():%Y-%m} to {wide.index.max():%Y-%m} ({len(wide)} months)",
        "max_abs_residual_index_pts": round(resid, 4),
        "estimated_on": date.today().isoformat(),
        "source_note": "BDE series F074.IPC.IND.Z.2023.C.M and F074.IPC.IND.DIV*.2023.C.M (INE). "
        "Replace with INE's official basket weights if available.",
        "weights": {
            sid: {
                "name": reg.by_id(sid).name.split(": ", 1)[-1].replace(" (base 2023)", ""),
                "weight": round(w, 5),
            }
            for sid, w in weights.items()
        },
    }
    (CONFIG_DIR / "cpi_weights.yaml").write_text(
        "# CPI division weights (see method / source_note)\n"
        + yaml.safe_dump(out, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"wrote config/cpi_weights.yaml, max residual {resid:.4f} index pts")


if __name__ == "__main__":
    main()
