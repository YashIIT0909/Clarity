from datetime import date

# Retail events for timing seasonality. Hand-maintained, US market (matches demo listings).
# Dates are each event's first day. None = not announced / not yet known. Categories match DEMO_PRODUCTS["category"];
# "all" = applies to every product, including ones whose category is unknown (non-demo links, screenshots).
# Diwali: Lakshmi Puja per the Indian government holiday (2024 and 2025 vary by a day regionally). Kept for a possible India variant.
EVENTS = {
    "Prime Day": {
        "categories": ["audio", "gaming"],
        "dates": {2021: "2021-06-21", 2022: "2022-07-12", 2023: "2023-07-11", 2024: "2024-07-16", 2025: "2025-07-08",
                  2026: "2026-06-23", 2027: None, 2028: None},
    },
    "Prime Big Deal Days": {
        "categories": "all",
        "dates": {2021: None, 2022: "2022-10-11", 2023: "2023-10-10", 2024: "2024-10-08", 2025: "2025-10-07",
                  2026: "2026-10-06", 2027: None, 2028: None},  # didn't exist before 2022
    },
    "Black Friday": {
        "categories": "all",
        "dates": {2021: "2021-11-26", 2022: "2022-11-25", 2023: "2023-11-24", 2024: "2024-11-29", 2025: "2025-11-28",
                  2026: "2026-11-27", 2027: "2027-11-26", 2028: "2028-11-24"},
    },
    "Cyber Monday": {
        "categories": "all",
        "dates": {2021: "2021-11-29", 2022: "2022-11-28", 2023: "2023-11-27", 2024: "2024-12-02", 2025: "2025-12-01",
                  2026: "2026-11-30", 2027: "2027-11-29", 2028: "2028-11-27"},
    },
    "Diwali": {
        "categories": ["audio", "gaming"],
        "dates": {2021: "2021-11-04", 2022: "2022-10-24", 2023: "2023-11-12", 2024: "2024-10-31", 2025: "2025-10-20",
                  2026: "2026-11-08", 2027: "2027-10-29", 2028: "2028-10-17"},
    },
}

LOOKAHEAD_DAYS = 84  # 12 weeks


def next_event(today: date, category: str | None, lookahead_days: int = LOOKAHEAD_DAYS):
    """Nearest event for this category starting within [today, today + lookahead]: (name, days_away) or None.
    category None = unknown: only events that apply to all categories count (never guessed from the listing)."""
    hits = [((d - today).days, name)
            for name, e in EVENTS.items() if e["categories"] == "all" or (category and category in e["categories"])
            for s in e["dates"].values() if s
            for d in [date.fromisoformat(s)] if 0 <= (d - today).days <= lookahead_days]
    return (min(hits)[1], min(hits)[0]) if hits else None


if __name__ == "__main__":
    from demo_products import DEMO_PRODUCTS

    assert next_event(date(2026, 10, 7), "heating") == ("Black Friday", 51)  # PBDD start passed -> next one
    assert next_event(date(2026, 10, 7), "audio") == ("Diwali", 32)          # category filter works
    assert next_event(date(2026, 12, 1), "audio") is None                    # nothing within 12 weeks
    # Unknown category (non-demo products): all-category events still count, category-specific ones don't.
    assert next_event(date(2026, 9, 29), None) == ("Prime Big Deal Days", 7)
    assert next_event(date(2026, 10, 7), None) == ("Black Friday", 51)       # Diwali (Nov 8, audio/gaming) skipped
    assert next_event(date(2026, 10, 7), "audio") == ("Diwali", 32)
    assert next_event(date(2026, 6, 1), None) is None                        # Prime Day (Jun 23, audio/gaming) skipped
    assert next_event(date(2026, 6, 1), "gaming") == ("Prime Day", 22)
    assert next_event(date(2026, 11, 28), "heating") == ("Cyber Monday", 2)  # "all" events still reach demo categories
    today = date.today()
    for key, p in DEMO_PRODUCTS.items():
        print(f"{today}  {key:24} {p['category']:8} -> {next_event(today, p['category'])}")
    print(f"{today}  {'(unknown category)':24} {'-':8} -> {next_event(today, None)}")
