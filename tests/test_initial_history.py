"""Tests for initial Google Ads history selection rules."""

from datetime import date

import pytest

from marketing_control.initial_history import (
    ads_history_boundary,
    select_initial_history,
)


def test_presets_use_inclusive_dates_and_estimate_their_workload() -> None:
    selection = select_initial_history("30", today=date(2026, 7, 12))

    assert selection.start_date == date(2026, 6, 13)
    assert selection.end_date == date(2026, 7, 12)
    assert selection.estimated_days == 30


def test_maximum_selection_starts_at_google_ads_eleven_year_boundary() -> None:
    selection = select_initial_history("maximum", today=date(2026, 7, 12))

    assert ads_history_boundary(date(2026, 7, 12)) == date(2015, 7, 12)
    assert selection.start_date == date(2015, 7, 12)
    assert selection.estimated_days == 4019


@pytest.mark.parametrize(
    ("start_date", "end_date", "message"),
    [
        (date(2026, 7, 13), date(2026, 7, 13), "future"),
        (date(2026, 7, 12), date(2026, 7, 11), "on or before"),
        (date(2015, 7, 11), date(2026, 7, 12), "11-year"),
    ],
)
def test_custom_selection_rejects_invalid_date_ranges(
    start_date: date, end_date: date, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        select_initial_history(
            "custom",
            today=date(2026, 7, 12),
            custom_start_date=start_date,
            custom_end_date=end_date,
        )
