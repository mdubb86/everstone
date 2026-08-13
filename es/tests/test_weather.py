"""Weather aggregation tests.

House pattern: pure functions fed literal/captured payloads, no HTTP.

The fixture is a REAL captured Houston payload, not hand-written, so upstream
drift breaks these loudly. It deliberately contains the case that live data
exposed and reasoning did not: hours with `type=CLEAR` ("Sunny") carrying a 70%
thunderstorm probability.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from es.capabilities import weather as w

FIXTURE = Path(__file__).parent / "fixtures" / "weather_houston_hourly.json"
TZ = "America/Chicago"


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def hours(payload):
    return w.GHourlyResponse.model_validate(payload).forecastHours


# ── inbound models ─────────────────────────────────────────────────────────

def test_real_payload_validates_with_no_optional_fields(hours):
    """Every field is required. Verified across four climates; a future omission
    must fail loudly rather than silently becoming None."""
    assert len(hours) == 24
    for h in hours:
        assert h.temperature.value is not None
        assert h.wind.gust.value is not None       # present even when calm
        assert h.feelsLikeTemperature.value is not None


def test_measure_absorbs_all_five_google_key_names():
    for raw, expect in (({"degrees": 13.7, "unit": "C"}, 13.7),
                        ({"value": 8, "unit": "MPH"}, 8.0),
                        ({"quantity": 0.25, "unit": "INCHES"}, 0.25),
                        ({"distance": 16, "unit": "MILES"}, 16.0),
                        ({"thickness": 0, "unit": "INCHES"}, 0.0)):
        assert w.Measure.model_validate(raw).value == expect


def test_measure_fails_loudly_on_a_renamed_key():
    with pytest.raises(Exception):
        w.Measure.model_validate({"temp": 5, "unit": "F"})


# ── categories ─────────────────────────────────────────────────────────────

def test_thundersnow_is_a_lightning_category():
    assert w.category("SNOWSTORM", 0) == 1
    assert w.category("HEAVY_SNOW_STORM", 0) == 1


def test_unknown_type_is_never_clear():
    assert w.category("SOME_FUTURE_ENUM", 0) == w._UNKNOWN_CATEGORY == 3


def test_storm_probability_elevates_category_regardless_of_type():
    assert w.category("CLEAR", 0) == 6
    assert w.category("CLEAR", 70) == 1        # the live-data case
    assert w.category("CLEAR", 49) == 6        # threshold is inclusive at 50
    assert w.category("CLEAR", 50) == 1


def test_bands_split_at_20_and_60():
    assert [w.band(p) for p in (0, 19, 20, 60, 61, 100)] == [0, 0, 1, 1, 2, 2]


# ── the defect live data exposed ───────────────────────────────────────────

def test_clear_sunny_hours_with_storm_risk_do_not_merge_with_calm_clear_hours(hours):
    """THE regression test. In the captured payload six CLEAR/"Sunny" hours carry
    thunderstorm_prob=70 while precip is 15%, and one CLEAR hour between them is
    0%/0%. A two-part predicate (category, precip band) puts all of them in
    category 6 / band 0 and merges them into ONE period reading "Sunny",
    discarding a 70% storm probability across a summer afternoon.
    """
    stormy = [h for h in hours if h.thunderstormProbability >= 50]
    assert len(stormy) == 6, "fixture must retain the CLEAR+storm case"
    assert all(h.weatherCondition.type == "CLEAR" for h in stormy)

    periods = w.build_periods(hours, TZ, window_hours=48)
    hot = [p for p in periods if p.thunderstorm_prob >= 50]
    calm = [p for p in periods if p.thunderstorm_prob == 0]
    assert hot and calm, "storm hours must not be merged into the calm run"
    # No period may average a storm away into a calm summary.
    for p in calm:
        assert p.thunderstorm_prob == 0


def test_merge_key_separates_storm_risk_from_identical_sky_and_precip():
    """Isolated proof the third predicate part is load-bearing: two hours
    identical in condition type AND precipitation, differing only in storm
    probability, must not share a merge key."""
    def hour(storm):
        return w.GForecastHour.model_validate({
            "interval": {"startTime": "2026-08-13T17:00:00Z", "endTime": "2026-08-13T18:00:00Z"},
            "weatherCondition": {"type": "CLEAR", "description": {"text": "Sunny"}},
            "temperature": {"degrees": 93, "unit": "F"},
            "feelsLikeTemperature": {"degrees": 107, "unit": "F"},
            "precipitation": {"probability": {"percent": 15, "type": "RAIN"},
                              "qpf": {"quantity": 0, "unit": "INCHES"}},
            "thunderstormProbability": storm, "relativeHumidity": 59,
            "uvIndex": 8, "cloudCover": 18,
            "wind": {"direction": {"cardinal": "SOUTH"}, "speed": {"value": 10, "unit": "MPH"},
                     "gust": {"value": 19, "unit": "MPH"}},
        })
    assert w.merge_key(hour(70)) != w.merge_key(hour(0))


# ── smoothing (isolated single-hour blips) ─────────────────────────────────

def _h(storm, cond="CLEAR", text="Sunny", precip=0, start="2026-08-13T17:00:00Z"):
    end = start[:11] + f"{int(start[11:13]) + 1:02d}" + start[13:]
    return w.GForecastHour.model_validate({
        "interval": {"startTime": start, "endTime": end},
        "weatherCondition": {"type": cond, "description": {"text": text}},
        "temperature": {"degrees": 90, "unit": "F"},
        "feelsLikeTemperature": {"degrees": 100, "unit": "F"},
        "precipitation": {"probability": {"percent": precip, "type": "RAIN"},
                          "qpf": {"quantity": 0, "unit": "INCHES"}},
        "thunderstormProbability": storm, "relativeHumidity": 50,
        "uvIndex": 5, "cloudCover": 10,
        "wind": {"direction": {"cardinal": "SOUTH"}, "speed": {"value": 5, "unit": "MPH"},
                 "gust": {"value": 9, "unit": "MPH"}},
    })


def _series(storms):
    """Consecutive hours from 12:00Z with the given storm probabilities."""
    return [_h(s, start=f"2026-08-13T{12 + i:02d}:00:00Z") for i, s in enumerate(storms)]


def test_isolated_calm_hour_is_absorbed_into_a_storm_run():
    """Real data arrives as SSSS.SS — that one calm hour is model noise. Without
    smoothing it fragments a 3-day window into 21 runs and forces the escalation
    ladder to day-level, destroying the "when is the rain" signal."""
    runs = w.merge(_series([70, 70, 70, 70, 0, 70, 70]), TZ)
    assert len(runs) == 1


def test_isolated_storm_hour_is_never_absorbed():
    """THE safety asymmetry. Smoothing may only ever absorb a LESS hazardous
    blip; an isolated lightning hour inside a calm run must survive as its own
    period, or smoothing becomes a way to hide a storm."""
    runs = w.merge(_series([0, 0, 0, 70, 0, 0, 0]), TZ)
    assert len(runs) == 3
    assert [len(r) for r in runs] == [3, 1, 3]
    assert runs[1][0].thunderstormProbability == 70


def test_smoothing_does_not_merge_a_genuine_two_hour_transition():
    """Only single-hour blips are absorbed — a real change lasting 2+ hours
    stays a real boundary."""
    runs = w.merge(_series([70, 70, 0, 0, 70, 70]), TZ)
    assert len(runs) == 3


# ── aggregation ────────────────────────────────────────────────────────────

def test_short_window_returns_one_period_per_hour(hours):
    periods = w.build_periods(hours[:8], TZ, window_hours=8)
    assert len(periods) == 8
    for p in periods:
        assert p.temp_high == p.temp_low          # derived, not fabricated


def test_long_window_merges_but_never_across_midnight(hours):
    periods = w.build_periods(hours, TZ, window_hours=48)
    assert 1 < len(periods) <= w._MAX_PERIODS
    for p in periods:
        assert datetime.fromisoformat(p.start).date() == \
               (datetime.fromisoformat(p.end) - timedelta(seconds=1)).date()


def test_probabilities_take_the_max_never_the_mean(hours):
    periods = w.build_periods(hours, TZ, window_hours=48)
    covered = []
    for p in periods:
        covered.append(p.thunderstorm_prob)
    assert max(covered) == max(h.thunderstormProbability for h in hours)


def test_every_period_has_the_same_field_set(hours):
    a = w.build_periods(hours[:1], TZ, window_hours=1)
    b = w.build_periods(hours[:8], TZ, window_hours=8)
    c = w.build_periods(hours, TZ, window_hours=48)
    keys = {frozenset(p.model_dump().keys()) for p in (a + b + c)}
    assert len(keys) == 1, "uniform shape: a 1h and a 24h period must be identical in shape"


def test_wind_direction_comes_from_the_peak_hour(hours):
    p = w.aggregate(list(hours), TZ)
    peak = max(hours, key=lambda h: h.wind.speed.value)
    assert p.wind.speed == peak.wind.speed.value
    assert p.wind.direction == peak.wind.direction.cardinal


# ── time ───────────────────────────────────────────────────────────────────

def test_naive_input_is_resolved_in_the_location_timezone():
    dt = w.parse_input_time("2026-08-15T09:00", "America/Los_Angeles")
    assert dt.utcoffset().total_seconds() == -7 * 3600     # Pacific, not Central


def test_offset_aware_input_is_honoured_as_absolute():
    dt = w.parse_input_time("2026-08-15T09:00-05:00", "America/Los_Angeles")
    assert dt.utcoffset().total_seconds() == -5 * 3600


def test_date_only_is_asymmetric_so_a_range_covers_whole_days():
    s = w.parse_input_time("2026-08-15", TZ)
    e = w.parse_input_time("2026-08-16", TZ, end_of_day=True)
    assert s.hour == 0 and e.hour == 0
    assert (e - s).total_seconds() / 3600 == 48            # the whole weekend


def test_nonexistent_local_time_shifts_forward_past_the_dst_gap():
    dt = w.parse_input_time("2026-03-08T02:30", TZ)        # US spring-forward
    assert dt.hour == 3


def test_ambiguous_local_time_takes_the_first_occurrence():
    dt = w.parse_input_time("2026-11-01T01:30", TZ)        # US fall-back
    assert dt.utcoffset().total_seconds() == -5 * 3600     # CDT, not CST


def test_window_beyond_the_horizon_errors_rather_than_truncating():
    now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    with pytest.raises(w.WeatherError) as e:
        w.hours_needed(now + timedelta(days=15), now)
    assert e.value.es_code == "weather_beyond_horizon"


def test_window_in_the_past_errors():
    now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    with pytest.raises(w.WeatherError) as e:
        w.hours_needed(now - timedelta(hours=1), now)
    assert e.value.es_code == "weather_window_past"


def test_hours_needed_is_driven_by_window_end_not_duration():
    now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    far_short = w.hours_needed(now + timedelta(hours=122), now)
    near_long = w.hours_needed(now + timedelta(hours=24), now)
    assert far_short > near_long


# ── labels ─────────────────────────────────────────────────────────────────

def test_labels_by_span(hours):
    one = w.build_periods(hours[:1], TZ, window_hours=1)[0]
    assert one.label == "now"
    few = w.build_periods(hours[:4], TZ, window_hours=4)
    assert all("–" not in p.label for p in few[1:])        # single hours
