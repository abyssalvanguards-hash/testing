from datetime import timedelta
import pytest

from cogs.server_care import parse_duration


def test_busy_duration_hours_and_days():
    assert parse_duration("12h") == timedelta(hours=12)
    assert parse_duration("2d") == timedelta(days=2)


@pytest.mark.parametrize("value", ["0h", "31d", "abc", "2w", "-1d", "1.5h", "100h"])
def test_busy_duration_rejects_invalid_or_excessive_values(value):
    with pytest.raises(ValueError):
        parse_duration(value)
