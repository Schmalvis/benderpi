"""Tests for natural language time parser."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
from time_parser import parse_duration, parse_alarm_time, extract_label


# ── parse_duration ──────────────────────────────────────────────────

class TestParseDuration:
    def test_empty_string(self):
        assert parse_duration("") is None

    def test_no_time_expression(self):
        assert parse_duration("tell me a joke") is None

    def test_seconds_numeric(self):
        assert parse_duration("10 seconds") == 10

    def test_minutes_numeric(self):
        assert parse_duration("5 minutes") == 300

    def test_hours_numeric(self):
        assert parse_duration("2 hours") == 7200

    def test_word_number_minutes(self):
        assert parse_duration("ten minutes") == 600

    def test_word_number_seconds(self):
        assert parse_duration("five seconds") == 5

    def test_word_number_thirty_minutes(self):
        assert parse_duration("thirty minutes") == 1800

    def test_a_minute(self):
        assert parse_duration("a minute") == 60

    def test_an_hour(self):
        assert parse_duration("an hour") == 3600

    def test_a_second(self):
        assert parse_duration("a second") == 1

    def test_half_an_hour(self):
        assert parse_duration("half an hour") == 1800

    def test_half_a_minute(self):
        assert parse_duration("half a minute") == 30

    def test_an_hour_and_a_half(self):
        assert parse_duration("an hour and a half") == 5400

    def test_two_and_a_half_minutes(self):
        assert parse_duration("two and a half minutes") == 150

    def test_one_and_a_half_hours(self):
        assert parse_duration("one and a half hours") == 5400

    def test_compound_hours_minutes(self):
        assert parse_duration("1 hour 30 minutes") == 5400

    def test_compound_hours_and_minutes(self):
        assert parse_duration("1 hour and 30 minutes") == 5400

    def test_compound_hours_and_minutes_large(self):
        assert parse_duration("2 hours and 30 minutes") == 9000

    def test_a_few_minutes(self):
        assert parse_duration("a few minutes") == 180

    def test_twenty_five_minutes(self):
        assert parse_duration("twenty five minutes") == 1500

    def test_fifteen_seconds(self):
        assert parse_duration("fifteen seconds") == 15

    def test_embedded_in_sentence(self):
        assert parse_duration("set a timer for 10 minutes") == 600

    def test_embedded_word_number(self):
        assert parse_duration("set a timer for five minutes") == 300

    def test_forty_five_minutes(self):
        assert parse_duration("forty five minutes") == 2700


# ── parse_alarm_time ────────────────────────────────────────────────

class TestParseAlarmTime:
    def test_empty_string(self):
        assert parse_alarm_time("") is None

    def test_no_time_expression(self):
        assert parse_alarm_time("tell me a joke") is None

    def test_simple_am(self):
        now = datetime(2026, 3, 17, 6, 0)
        with patch("time_parser.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = parse_alarm_time("10am")
        assert result == datetime(2026, 3, 17, 10, 0)

    def test_simple_pm(self):
        now = datetime(2026, 3, 17, 12, 0)
        with patch("time_parser.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = parse_alarm_time("3:30pm")
        assert result == datetime(2026, 3, 17, 15, 30)

    def test_24h_format(self):
        now = datetime(2026, 3, 17, 8, 0)
        with patch("time_parser.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = parse_alarm_time("10:00")
        assert result == datetime(2026, 3, 17, 10, 0)

    def test_past_time_rolls_to_tomorrow(self):
        now = datetime(2026, 3, 17, 16, 0)  # 4pm
        with patch("time_parser.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = parse_alarm_time("3pm")
        assert result == datetime(2026, 3, 18, 15, 0)

    def test_tomorrow_at_time(self):
        now = datetime(2026, 3, 17, 10, 0)
        with patch("time_parser.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = parse_alarm_time("tomorrow at 6pm")
        assert result == datetime(2026, 3, 18, 18, 0)

    def test_tomorrow_at_bare_hour(self):
        now = datetime(2026, 3, 17, 10, 0)
        with patch("time_parser.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = parse_alarm_time("tomorrow at 6")
        assert result == datetime(2026, 3, 18, 6, 0)

    def test_tomorrow_morning(self):
        now = datetime(2026, 3, 17, 22, 0)
        with patch("time_parser.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = parse_alarm_time("tomorrow morning")
        assert result == datetime(2026, 3, 18, 8, 0)

    def test_tomorrow_evening(self):
        now = datetime(2026, 3, 17, 10, 0)
        with patch("time_parser.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = parse_alarm_time("tomorrow evening")
        assert result == datetime(2026, 3, 18, 18, 0)

    def test_6pm_embedded(self):
        now = datetime(2026, 3, 17, 10, 0)
        with patch("time_parser.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = parse_alarm_time("set an alarm for 6pm")
        assert result == datetime(2026, 3, 17, 18, 0)

    def test_10am_embedded(self):
        now = datetime(2026, 3, 17, 8, 0)
        with patch("time_parser.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = parse_alarm_time("alarm at 10am")
        assert result == datetime(2026, 3, 17, 10, 0)


# ── extract_label ───────────────────────────────────────────────────

class TestExtractLabel:
    def test_label_between_for_and_for(self):
        assert extract_label("set a timer for pasta for 10 minutes") == "pasta"

    def test_no_label(self):
        assert extract_label("set a timer for 10 minutes") == "timer"

    def test_timer_for_eggs(self):
        assert extract_label("timer for eggs for 5 minutes") == "eggs"

    def test_alarm_for_label_at_time(self):
        assert extract_label("set an alarm for work at 6am") == "work"

    def test_alarm_no_label(self):
        assert extract_label("alarm at 10am") == "alarm"

    def test_empty_string(self):
        assert extract_label("") == "timer"

    def test_label_multi_word(self):
        assert extract_label("set a timer for hard boiled eggs for 12 minutes") == "hard boiled eggs"
