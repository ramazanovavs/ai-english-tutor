import pytest
from app.services.ielts_scoring import overall_band, criteria_band, writing_band, criterion, half_band_nearest


@pytest.mark.parametrize("bands, expected", [
    ([6, 6, 6, 7], 6.5),        # 6.25 rounds up to 6.5 (not banker's 6.0)
    ([6.5, 6.5, 7, 7], 7.0),    # 6.75 rounds up to 7
    ([7, 7, 7, 7.5], 7.0),      # 7.125 rounds down
    ([6, 6.5, 6.5, 6.5], 6.5),  # 6.375 -> 6.5
    ([9, 9, 9, 9], 9.0),
])
def test_overall_band(bands, expected):
    assert overall_band(bands) == expected


def test_criteria_are_whole_and_mean_rounds_down():
    assert criterion(6.6) == 7 and criterion("x") == 0 and criterion(12) == 9
    assert criteria_band([7, 6, 6, 6]) == 6.0   # 6.25 -> half band below
    assert criteria_band([7, 7, 6, 7]) == 6.5   # 6.75 -> 6.5


def test_writing_task2_counts_double():
    assert writing_band(6, 7) == 6.5   # 6.67
    assert writing_band(7, 6) == 6.5   # 6.33
    assert half_band_nearest(6.25) == 6.5
