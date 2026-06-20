#!/usr/bin/env python3
"""NYSE trading-day check. Exits 0 if today is a trading session, 1 if not.

Usage: python3 market-check.py
Primary: exchange_calendars (pip install exchange_calendars).
Fallback: hardcoded NYSE holiday list for 2025-2026 when the library is absent.
"""
import sys
import datetime

today = datetime.date.today()

# Fast-path: weekends are never trading days
if today.weekday() >= 5:  # 5=Saturday, 6=Sunday
    sys.exit(1)

try:
    import exchange_calendars as xcals
    cal = xcals.get_calendar("XNYS")
    sys.exit(0 if cal.is_session(today.isoformat()) else 1)
except ImportError:
    # exchange_calendars not installed — fall back to hardcoded NYSE holidays.
    # Covers observed dates (e.g. Jul 3 when Jul 4 falls on Saturday).
    NYSE_HOLIDAYS = {
        # 2025
        "2025-01-01",  # New Year's Day
        "2025-01-09",  # National Day of Mourning (Carter)
        "2025-01-20",  # Martin Luther King Jr. Day
        "2025-02-17",  # Presidents' Day
        "2025-04-18",  # Good Friday
        "2025-05-26",  # Memorial Day
        "2025-06-19",  # Juneteenth
        "2025-07-04",  # Independence Day
        "2025-09-01",  # Labor Day
        "2025-11-27",  # Thanksgiving Day
        "2025-12-25",  # Christmas Day
        # 2026
        "2026-01-01",  # New Year's Day
        "2026-01-19",  # Martin Luther King Jr. Day
        "2026-02-16",  # Presidents' Day
        "2026-04-03",  # Good Friday
        "2026-05-25",  # Memorial Day
        "2026-06-19",  # Juneteenth
        "2026-07-03",  # Independence Day (observed; Jul 4 is Saturday)
        "2026-09-07",  # Labor Day
        "2026-11-26",  # Thanksgiving Day
        "2026-12-25",  # Christmas Day
    }
    sys.exit(1 if today.isoformat() in NYSE_HOLIDAYS else 0)
