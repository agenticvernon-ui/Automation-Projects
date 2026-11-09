"""Deterministic simulated restaurant sales (no randomness, so results are reproducible)."""
from datetime import date, timedelta


def generate_sales(start, days, base, weekend_boost=1.4, extra=None):
    rows, first = [], date.fromisoformat(start)
    dishes = list(base)
    for i in range(days):
        day = first + timedelta(days=i)
        mult = weekend_boost if day.weekday() >= 4 else 1.0      # Friday, Saturday, Sunday are busier
        for k, dish in enumerate(dishes):
            qty = round(base[dish] * mult + (extra or {}).get((day.isoformat(), dish), 0))
            rows.append({"sale_id": f"S-{day.isoformat()}-{k}", "day": day.isoformat(), "dish": dish, "qty": qty})
    return rows
