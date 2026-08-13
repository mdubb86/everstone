"""es weather helpers — Google Maps Platform Weather API (hourly endpoint only).

Design: docs/superpowers/specs/2026-08-13-weather-and-timezones-design.md

Only the hourly endpoint is used. Its 240h horizon equals the daily endpoint's, so
daily is never needed for coverage — and daily cannot share a shape with hourly
observations without null padding (its day-parts carry no temperature). Current
conditions is redundant: the first forecast hour IS the current hour.

Everything below the HTTP boundary is pure and unit-tested against a captured
payload; only fetch_hours() touches the network.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from es import config

_HOURLY_URL = "https://weather.googleapis.com/v1/forecast/hours:lookup"
_MAX_HOURS = 240          # API cap; also the forecast horizon
_MAX_PAGE = 24            # API cap on pageSize — cannot be raised
_MAX_PERIODS = 24         # our cap, to bound token volume. Matches what the
                          # hourly branch can already return for a 24h window —
                          # a LOWER cap here would give a 3-day trip fewer
                          # periods than a single day, which is backwards.
_HOURLY_THRESHOLD = 24    # windows this short stay hour-by-hour
_STORM_ELEVATION = 50     # thunderstorm_prob at/above this forces category 1


class WeatherError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.es_code = code


# ── inbound: mirrors Google's payload, names and all ────────────────────────

class Measure(BaseModel):
    """Google wraps every scalar as {<name>, unit} using FIVE different key
    names. `thickness` appears in no docs sample — found in a live payload."""
    model_config = ConfigDict(extra="ignore")
    value: float = Field(validation_alias=AliasChoices(
        "degrees", "value", "quantity", "distance", "thickness"))
    unit: Optional[str] = None


class GProbability(BaseModel):
    model_config = ConfigDict(extra="ignore")
    percent: int
    type: Optional[str] = None


class GPrecipitation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    probability: GProbability
    qpf: Measure


class GDescription(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str


class GCondition(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    description: GDescription


class GDirection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    cardinal: str


class GWind(BaseModel):
    model_config = ConfigDict(extra="ignore")
    direction: GDirection
    speed: Measure
    gust: Measure


class GInterval(BaseModel):
    model_config = ConfigDict(extra="ignore")
    startTime: str
    endTime: str


class GForecastHour(BaseModel):
    """No field is Optional: verified present across Houston, Arctic Alaska,
    Sydney and Reykjavik. A future omission must fail loudly, not become None."""
    model_config = ConfigDict(extra="ignore")
    interval: GInterval
    weatherCondition: GCondition
    temperature: Measure
    feelsLikeTemperature: Measure
    precipitation: GPrecipitation
    thunderstormProbability: int
    relativeHumidity: int
    uvIndex: int
    cloudCover: int
    wind: GWind


class GTimeZone(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str


class GHourlyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    forecastHours: list[GForecastHour]
    timeZone: GTimeZone
    nextPageToken: Optional[str] = None


# ── outbound: the one uniform shape the agent sees ──────────────────────────

class Wind(BaseModel):
    speed: float
    gust: float
    direction: str


class Location(BaseModel):
    address: str
    lat: float
    lng: float
    timezone: str


class Period(BaseModel):
    """One span. Every field is computed from real hourly samples, so a 1-hour
    and a 24-hour period are the same shape — no nulls, no padding. For a single
    hour temp_high == temp_low, which is derived, not fabricated."""
    start: str
    end: str
    label: str
    condition: str
    temp_high: float
    temp_low: float
    feels_like_high: float
    precip_prob: int
    precip_amount: float
    thunderstorm_prob: int
    humidity: int
    uv_index: int
    cloud_cover: int
    wind: Wind


class WeatherReport(BaseModel):
    location: Location
    units: str
    periods: list[Period]


# ── condition categories (lower == more hazardous) ──────────────────────────
# Enumerated from the live WeatherCondition.Type enum: every member is placed.
# There is no fog/haze/smoke category — the enum contains no such members.

_CATEGORY = {}


def _cat(n, *types):
    for t in types:
        _CATEGORY[t] = n


# 1 — Lightning. SNOWSTORM/HEAVY_SNOW_STORM are thundersnow ("snow with
# possible thunder and lightning"), which is why this tier is named for the
# hazard rather than for thunderstorms.
_cat(1, "THUNDERSTORM", "HEAVY_THUNDERSTORM", "THUNDERSHOWER",
     "SCATTERED_THUNDERSTORMS", "LIGHT_THUNDERSTORM_RAIN",
     "SNOWSTORM", "HEAVY_SNOW_STORM")
# 2 — heavy / severe
_cat(2, "HAIL", "HAIL_SHOWERS", "HEAVY_RAIN", "HEAVY_RAIN_SHOWERS",
     "MODERATE_TO_HEAVY_RAIN", "RAIN_PERIODICALLY_HEAVY", "HEAVY_SNOW",
     "HEAVY_SNOW_SHOWERS", "MODERATE_TO_HEAVY_SNOW", "SNOW_PERIODICALLY_HEAVY",
     "BLOWING_SNOW", "WIND_AND_RAIN")
# 3 — light / moderate precipitation (also the fail-safe for unknown types)
_cat(3, "RAIN", "LIGHT_RAIN", "LIGHT_TO_MODERATE_RAIN", "LIGHT_RAIN_SHOWERS",
     "CHANCE_OF_SHOWERS", "SCATTERED_SHOWERS", "RAIN_SHOWERS", "SNOW",
     "LIGHT_SNOW", "LIGHT_TO_MODERATE_SNOW", "LIGHT_SNOW_SHOWERS",
     "CHANCE_OF_SNOW_SHOWERS", "SCATTERED_SNOW_SHOWERS", "SNOW_SHOWERS",
     "RAIN_AND_SNOW")
_cat(4, "WINDY")
_cat(5, "CLOUDY", "MOSTLY_CLOUDY", "PARTLY_CLOUDY", "MOSTLY_CLEAR")
_cat(6, "CLEAR")

_UNKNOWN_CATEGORY = 3      # never 6 — an unrecognised type must not read "clear"


def category(cond_type: str, thunderstorm_prob: int) -> int:
    """Hazard tier. thunderstorm_prob >= 50 forces tier 1 REGARDLESS of type:
    Google models sky state and convective risk independently, so a CLEAR
    ("Sunny") hour can carry a 70% thunderstorm probability. Without this
    elevation the storm risk never reaches the category at all and merges away
    into a fair-weather period."""
    if thunderstorm_prob >= _STORM_ELEVATION:
        return 1
    return _CATEGORY.get(cond_type, _UNKNOWN_CATEGORY)


def band(percent: int) -> int:
    """NWS phrasing: slight chance / chance / likely. The wide middle band is
    deliberate — it stops a normal diurnal ramp fragmenting into five periods."""
    if percent < 20:
        return 0
    if percent <= 60:
        return 1
    return 2


def merge_key(hour: GForecastHour) -> tuple:
    """Three-part predicate. The thunderstorm band is NOT redundant with the
    condition category — see category()."""
    return (
        category(hour.weatherCondition.type, hour.thunderstormProbability),
        band(hour.precipitation.probability.percent),
        band(hour.thunderstormProbability),
    )


# ── time ───────────────────────────────────────────────────────────────────

def parse_input_time(raw: str, tzname: str, end_of_day: bool = False) -> datetime:
    """Accepts naive local, offset-aware, or date-only.

    Date-only is asymmetric: as a start it means 00:00, as an end it means the
    following midnight — so start=Sat,end=Sun is the whole weekend rather than a
    zero-length instant.
    """
    tz = ZoneInfo(tzname)
    if "T" not in raw and " " not in raw:            # date-only
        d = datetime.fromisoformat(raw)
        if end_of_day:
            d += timedelta(days=1)
        return _localize(d, tz)
    dt = datetime.fromisoformat(raw.replace(" ", "T"))
    if dt.tzinfo is not None:                        # offset given: absolute
        return dt
    return _localize(dt, tz)


def _localize(naive: datetime, tz: ZoneInfo) -> datetime:
    """Naive local -> aware. fold=0 for the ambiguous (fall-back) case; shift
    forward past a nonexistent (spring-forward) time."""
    dt = naive.replace(tzinfo=tz, fold=0)
    roundtrip = dt.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
    if roundtrip != naive:                           # nonexistent local time
        return (naive + timedelta(hours=1)).replace(tzinfo=tz, fold=0)
    return dt


def hours_needed(end: datetime, now: datetime) -> int:
    """Hourly runs forward from the current hour with no startTime parameter, so
    cost is driven by how far out the window ENDS, not by its duration."""
    delta = (end - now).total_seconds() / 3600.0
    if delta <= 0:
        raise WeatherError("weather_window_past", "That window is in the past.")
    if delta > _MAX_HOURS:
        raise WeatherError(
            "weather_beyond_horizon",
            f"Forecasts only run {_MAX_HOURS // 24} days out; that window ends later.")
    return min(_MAX_HOURS, int(delta) + 1)


# ── aggregation ────────────────────────────────────────────────────────────

def _label(start: datetime, end: datetime, is_now: bool) -> str:
    if is_now:
        return "now"
    span = (end - start).total_seconds() / 3600.0
    if span >= 23 and start.hour == 0:
        return start.strftime("%A")
    fmt = lambda d: d.strftime("%-I %p")             # noqa: E731
    if span <= 1:
        return fmt(start)
    return f"{fmt(start)}–{fmt(end)}"


def aggregate(hours: list[GForecastHour], tzname: str, is_now: bool = False) -> Period:
    """Collapse a homogeneous run into one Period. Probabilities take the MAX,
    never the mean — averaging a storm hour into a calm run is how a risk gets
    hidden."""
    tz = ZoneInfo(tzname)
    start = datetime.fromisoformat(hours[0].interval.startTime.replace("Z", "+00:00")).astimezone(tz)
    end = datetime.fromisoformat(hours[-1].interval.endTime.replace("Z", "+00:00")).astimezone(tz)
    worst = min(hours, key=lambda h: (category(h.weatherCondition.type, h.thunderstormProbability),
                                      -h.thunderstormProbability,
                                      -h.precipitation.probability.percent))
    peak = max(hours, key=lambda h: h.wind.speed.value)
    n = len(hours)
    return Period(
        start=start.isoformat(),
        end=end.isoformat(),
        label=_label(start, end, is_now),
        condition=worst.weatherCondition.description.text,
        temp_high=max(h.temperature.value for h in hours),
        temp_low=min(h.temperature.value for h in hours),
        feels_like_high=max(h.feelsLikeTemperature.value for h in hours),
        precip_prob=max(h.precipitation.probability.percent for h in hours),
        precip_amount=round(sum(h.precipitation.qpf.value for h in hours), 4),
        thunderstorm_prob=max(h.thunderstormProbability for h in hours),
        humidity=round(sum(h.relativeHumidity for h in hours) / n),
        uv_index=max(h.uvIndex for h in hours),
        cloud_cover=round(sum(h.cloudCover for h in hours) / n),
        wind=Wind(speed=peak.wind.speed.value, gust=max(h.wind.gust.value for h in hours),
                  direction=peak.wind.direction.cardinal),
    )


def _local_day(hour: GForecastHour, tz: ZoneInfo):
    return datetime.fromisoformat(
        hour.interval.startTime.replace("Z", "+00:00")).astimezone(tz).date()


def _smoothed_keys(hours: list[GForecastHour], keyfn) -> list:
    """Absorb isolated single-hour deviations into their surrounding run.

    Real forecast data oscillates hour to hour — a 7-hour storm afternoon
    arrives as `SSSS.SS`, where that one calm hour is model noise, not a break
    in the weather. Splitting on it fragments a 3-day window into 21 runs and
    forces the escalation ladder straight to day-level, destroying the "when is
    the rain" signal the merge exists to produce.

    SAFETY ASYMMETRY: only a LESS hazardous blip is absorbed. An isolated MORE
    hazardous hour (`....S....`) is always kept as its own period — smoothing
    must never be able to erase a lightning hour.
    """
    keys = [keyfn(h) for h in hours]
    cats = [category(h.weatherCondition.type, h.thunderstormProbability) for h in hours]
    out = list(keys)
    for i in range(1, len(keys) - 1):
        if keys[i - 1] == keys[i + 1] != keys[i] and cats[i] > cats[i - 1]:
            out[i] = keys[i - 1]
    return out


def merge(hours: list[GForecastHour], tzname: str, keyfn=merge_key) -> list[list[GForecastHour]]:
    """Group adjacent hours sharing a merge key. Never across local midnight, so
    days stay legible."""
    tz = ZoneInfo(tzname)
    keys = _smoothed_keys(hours, keyfn)
    runs, cur = [], []
    for h, k in zip(hours, keys):
        if cur and k == cur[-1][1] and _local_day(h, tz) == _local_day(cur[-1][0], tz):
            cur.append((h, k))
        else:
            if cur:
                runs.append([x for x, _ in cur])
            cur = [(h, k)]
    if cur:
        runs.append([x for x, _ in cur])
    return runs


def build_periods(hours: list[GForecastHour], tzname: str, window_hours: float) -> list[Period]:
    """Window duration proxies the question: a few hours means "the soccer game"
    and wants precision; several days means "our trip" and wants highs, lows and
    when rain is actually likely."""
    if window_hours <= _HOURLY_THRESHOLD:
        return [aggregate([h], tzname, is_now=(i == 0 and window_hours <= 1))
                for i, h in enumerate(hours)]
    runs = merge(hours, tzname)
    if len(runs) > _MAX_PERIODS:                     # widen tolerance, never truncate
        runs = merge(hours, tzname, keyfn=lambda h: merge_key(h)[0])
    if len(runs) > _MAX_PERIODS:
        tz = ZoneInfo(tzname)
        runs = merge(hours, tzname, keyfn=lambda h: _local_day(h, tz))
    return [aggregate(r, tzname) for r in runs]


# ── network ────────────────────────────────────────────────────────────────

def units() -> str:
    u = (config.weather_config() or {}).get("units", "imperial")
    if u not in ("imperial", "metric"):
        raise WeatherError("weather_bad_units", f"weather.units must be imperial or metric, got {u!r}")
    return u


def fetch_hours(lat: float, lng: float, want_hours: int, api_key: str, unit_system: str):
    """Fetch `want_hours` forward from now, following pageTokens.

    pageSize caps at 24 and pageToken is sequential, so pages cannot be
    parallelised; a window ending 122h out costs 6 calls. That is affordable
    against 10k free calls/month and is the accepted cost of one uniform shape.
    """
    hours, token, tzname = [], None, None
    while len(hours) < want_hours:
        params = {"location.latitude": lat, "location.longitude": lng,
                  "hours": want_hours, "pageSize": _MAX_PAGE,
                  "unitsSystem": unit_system.upper()}
        if token:
            params["pageToken"] = token
        try:
            r = httpx.get(_HOURLY_URL, params=params, timeout=30,
                          headers={"X-Goog-Api-Key": api_key})
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise WeatherError("weather_error",
                               f"Weather request failed: HTTP {e.response.status_code}")
        page = GHourlyResponse.model_validate(r.json())
        tzname = tzname or page.timeZone.id
        hours.extend(page.forecastHours)
        token = page.nextPageToken
        if not token:
            break
    return hours[:want_hours], tzname
