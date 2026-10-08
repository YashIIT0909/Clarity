from datetime import date

# Retail events for timing seasonality. Hand-maintained, per market (locales.py: "us", "in").
# Dates are each event's first day. None = not announced / not yet known. `markets` maps a market to the categories the
# event applies to there (DEMO_PRODUCTS["category"]); "all" = every product, including ones whose category is unknown
# (non-demo links, screenshots). An event missing from a market never appears there.
# Diwali: Lakshmi Puja per the Indian government holiday (2024 and 2025 vary by a day regionally). In India the festive
# season is a sale for everything; in the US it stays audio/gaming, as before.
EVENTS = {
    "Prime Day": {
        "markets": {"us": ["audio", "gaming"]},
        "dates": {2021: "2021-06-21", 2022: "2022-07-12", 2023: "2023-07-11", 2024: "2024-07-16", 2025: "2025-07-08",
                  2026: "2026-06-23", 2027: None, 2028: None},
    },
    "Prime Big Deal Days": {
        "markets": {"us": "all"},
        "dates": {2021: None, 2022: "2022-10-11", 2023: "2023-10-10", 2024: "2024-10-08", 2025: "2025-10-07",
                  2026: "2026-10-06", 2027: None, 2028: None},  # didn't exist before 2022
    },
    "Black Friday": {
        "markets": {"us": "all"},
        "dates": {2021: "2021-11-26", 2022: "2022-11-25", 2023: "2023-11-24", 2024: "2024-11-29", 2025: "2025-11-28",
                  2026: "2026-11-27", 2027: "2027-11-26", 2028: "2028-11-24"},
    },
    "Cyber Monday": {
        "markets": {"us": "all"},
        "dates": {2021: "2021-11-29", 2022: "2022-11-28", 2023: "2023-11-27", 2024: "2024-12-02", 2025: "2025-12-01",
                  2026: "2026-11-30", 2027: "2027-11-29", 2028: "2028-11-27"},
    },
    "Diwali": {
        "markets": {"us": ["audio", "gaming"], "in": "all"},
        "dates": {2021: "2021-11-04", 2022: "2022-10-24", 2023: "2023-11-12", 2024: "2024-10-31", 2025: "2025-10-20",
                  2026: "2026-11-08", 2027: "2027-10-29", 2028: "2028-10-17"},
    },
    # India's two biggest online sales, general start dates (members get a day early). Checked 2026-10-09 against
    # retailer announcements as reported by 91mobiles, fonearena, digit.in and mysmartprice. 2021 Big Billion Days: no
    # confirmed date found, so None (never guessed).
    "Great Indian Festival": {  # Amazon.in
        "markets": {"in": "all"},
        "dates": {2021: "2021-10-04", 2022: "2022-09-23", 2023: "2023-10-08", 2024: "2024-09-27", 2025: "2025-09-23",
                  2026: "2026-10-08", 2027: None, 2028: None},
    },
    "Big Billion Days": {  # Flipkart
        "markets": {"in": "all"},
        "dates": {2021: None, 2022: "2022-09-23", 2023: "2023-10-08", 2024: "2024-09-27", 2025: "2025-09-23",
                  2026: "2026-10-09", 2027: None, 2028: None},
    },
    # Amazon.in's other sales, checked 2026-10-09 (aboutamazon.in, fonearena, digit.in, thequint, 91mobiles). India's
    # Prime Day has its own dates (US Prime Day is a separate entry) and covers every category.
    "Prime Day India": {
        "markets": {"in": "all"},
        "dates": {2021: "2021-07-26", 2022: "2022-07-23", 2023: "2023-07-15", 2024: "2024-07-20", 2025: "2025-07-12",
                  2026: "2026-07-04", 2027: None, 2028: None},
    },
    "Great Republic Day Sale": {  # mid-January; 2021 not confirmed
        "markets": {"in": "all"},
        "dates": {2021: None, 2022: "2022-01-17", 2023: "2023-01-14", 2024: "2024-01-13", 2025: "2025-01-13",
                  2026: "2026-01-16", 2027: None, 2028: None},
    },
    "Great Freedom Festival": {  # Independence Day, early August; 2021 and 2026 not confirmed
        "markets": {"in": "all"},
        "dates": {2021: None, 2022: "2022-08-06", 2023: "2023-08-05", 2024: "2024-08-06", 2025: "2025-08-01",
                  2026: None, 2027: None, 2028: None},
    },
}

LOOKAHEAD_DAYS = 84  # 12 weeks


def starts(name, days):
    """'Black Friday starts in 49 days' / '... starts in 1 day' / 'Big Billion Days starts today'."""
    return f"{name} starts today" if days == 0 else f"{name} starts in {days} day{'s' if days != 1 else ''}"


def applies(name, category, market=None):
    """Does this event apply to this category in this market? Unknown category: only "all" events."""
    cats = EVENTS[name]["markets"].get(market or "us")
    return cats is not None and (cats == "all" or bool(category and category in cats))


def next_event(today: date, category: str | None, lookahead_days: int = LOOKAHEAD_DAYS, market: str | None = None):
    """Nearest event for this category in this market starting within [today, today + lookahead]: (name, days_away)
    or None. category None = unknown: only events that apply to all categories count (never guessed from the listing)."""
    hits = [((d - today).days, name)
            for name, e in EVENTS.items() if applies(name, category, market)
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
