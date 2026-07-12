"""Date-range rules for a user's first Google Ads history import."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

HistoryPreset = Literal["7", "30", "90", "365", "maximum", "custom"]
ADS_HISTORY_YEARS = 11


@dataclass(frozen=True)
class InitialHistorySelection:
    """A valid, inclusive initial Google Ads reporting range."""

    start_date: date
    end_date: date

    @property
    def estimated_days(self) -> int:
        """Return the number of inclusive reporting days in this range."""
        return (self.end_date - self.start_date).days + 1


def ads_history_boundary(today: date) -> date:
    """Return the earliest date Google Ads reports are expected to provide.

    Google Ads reporting history is available for the most recent 11 years.
    """
    try:
        return today.replace(year=today.year - ADS_HISTORY_YEARS)
    except ValueError:
        return today.replace(year=today.year - ADS_HISTORY_YEARS, day=28)


def select_initial_history(
    preset: HistoryPreset,
    *,
    today: date,
    custom_start_date: date | None = None,
    custom_end_date: date | None = None,
) -> InitialHistorySelection:
    """Resolve and validate a preset or custom initial-history selection."""
    if preset in {"7", "30", "90", "365"}:
        start_date = today - timedelta(days=int(preset) - 1)
        end_date = today
    elif preset == "maximum":
        start_date = ads_history_boundary(today)
        end_date = today
    elif preset == "custom":
        if custom_start_date is None or custom_end_date is None:
            raise ValueError("Both custom start and end dates are required.")
        start_date = custom_start_date
        end_date = custom_end_date
    else:
        raise ValueError("Choose a history period.")

    if start_date > end_date:
        raise ValueError("Start date must be on or before end date.")
    if end_date > today:
        raise ValueError("End date cannot be in the future.")
    if start_date < ads_history_boundary(today):
        raise ValueError(
            "Start date is before the Google Ads 11-year reporting history boundary."
        )
    return InitialHistorySelection(start_date, end_date)
