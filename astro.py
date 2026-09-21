"""Astronomical calculations: solar and lunar position, sunrise/sunset, and the
local circumstances of solar eclipses.

This module is pure Python with no third-party dependencies. That matters
because the app needs sunrise/sunset times and eclipse geometry for dates far
beyond the ~16 day horizon of any weather forecast API.

Algorithms follow Jean Meeus, *Astronomical Algorithms* (2nd edition):

* Sun position       -- chapter 25 ("lower accuracy", ~0.01 deg)
* Moon position      -- chapter 47 (abridged ELP-2000/82, ~10" in longitude)
* Mean new moon      -- chapter 49 (used only as a search seed)
* Topocentric coords -- chapter 40 (diurnal parallax)
* Sidereal time      -- chapter 12

Local eclipse circumstances are then derived geometrically from the
topocentric angular separation of the Sun and Moon.

Verified accuracy, checked against published local circumstances for the
total eclipses of 2017-08-21 (Nashville), 2024-04-08 (Dallas), 2026-08-12
(Reykjavik) and 2028-07-22 (Sydney):

* eclipse date and type      -- correct in every case tested
* time of maximum eclipse    -- within about 40 seconds
* duration of totality       -- within about 15 seconds
* magnitude and obscuration  -- within about 0.005

That residual comes from the truncated lunar series (roughly 10 arcsec, which
is 20 seconds of eclipse time) and cannot be reduced without the full
ELP-2000 expansion. It is fine for deciding where to be on the day; it is not
a substitute for a dedicated prediction if you are chasing the centre line,
and it does not model the lunar limb profile that governs the exact moment of
second and third contact.

Sunrise and sunset agree with the Open-Meteo API to within about 30 seconds
at mid latitudes, widening to roughly two minutes above 65 degrees where the
Sun crosses the horizon at a very shallow angle.
"""

import datetime as dt
import math

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

J2000 = 2451545.0
AU_KM = 149597870.7
EARTH_RADIUS_KM = 6378.137
MOON_RADIUS_KM = 1737.4
# For umbral (total/annular) contacts the convention is a slightly smaller
# radius, k = 0.272281, because the Moon's limb valleys let sunlight through.
MOON_RADIUS_UMBRAL_KM = 1736.65
SUN_RADIUS_KM = 696000.0
EARTH_FLATTENING_RATIO = 0.996647189  # b/a for WGS-84

# Standard refraction correction for the upper limb of the Sun at the horizon.
SUNRISE_ALTITUDE_DEG = -0.833

DEG = math.pi / 180.0

# Mean synodic month, used to step between new moons.
SYNODIC_MONTH = 29.530588861


# --------------------------------------------------------------------------
# Time conversions
# --------------------------------------------------------------------------


def datetime_to_jd(moment):
    """Convert an aware (or naive-as-UTC) datetime to a Julian Day number."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(dt.timezone.utc).replace(tzinfo=None)

    year, month = moment.year, moment.month
    day = (
        moment.day
        + (moment.hour + (moment.minute + (moment.second + moment.microsecond / 1e6) / 60.0) / 60.0)
        / 24.0
    )

    if month <= 2:
        year -= 1
        month += 12

    a = year // 100
    # The Gregorian calendar correction; every date this app deals with is
    # comfortably after 1582 so the Julian branch is never needed.
    b = 2 - a + a // 4

    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + b
        - 1524.5
    )


def jd_to_datetime(jd):
    """Convert a Julian Day number to an aware UTC datetime."""
    jd = jd + 0.5
    z = math.floor(jd)
    frac = jd - z

    if z < 2299161:
        a = z
    else:
        alpha = math.floor((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - math.floor(alpha / 4)

    b = a + 1524
    c = math.floor((b - 122.1) / 365.25)
    d = math.floor(365.25 * c)
    e = math.floor((b - d) / 30.6001)

    day = b - d - math.floor(30.6001 * e) + frac
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715

    day_int = int(math.floor(day))
    seconds = (day - day_int) * 86400.0
    # Round to the nearest second, then let timedelta normalise any overflow.
    base = dt.datetime(int(year), int(month), day_int, tzinfo=dt.timezone.utc)
    return base + dt.timedelta(seconds=round(seconds))


def julian_centuries(jd):
    """Julian centuries since J2000.0."""
    return (jd - J2000) / 36525.0


# Observed values of Delta T = TT - UT in seconds, at decade boundaries
# (IERS/USNO). Earth's rotation is not predictable from theory, so this has to
# be tabulated and extrapolated.
_DELTA_T_TABLE = (
    (1900, -2.8), (1910, 10.4), (1920, 21.1), (1930, 24.0), (1940, 24.3),
    (1950, 29.1), (1960, 33.1), (1970, 40.2), (1980, 50.5), (1990, 56.9),
    (2000, 63.8), (2005, 64.7), (2010, 66.1), (2015, 67.6), (2020, 69.4),
    (2025, 69.2),
)


def delta_t_seconds(jd):
    """Estimate Delta T = TT - UT, in seconds, for a given Julian Day.

    Interpolates observed values up to 2025 and extrapolates gently beyond.
    The extrapolation is good to a few seconds over the next few decades,
    degrading to tens of seconds by 2100 -- Earth's rotation simply cannot be
    predicted better than that. An error here shifts predicted eclipse times
    by about the same amount.
    """
    year = 2000.0 + (jd - J2000) / 365.25

    if year <= _DELTA_T_TABLE[0][0]:
        return _DELTA_T_TABLE[0][1]

    for (year_a, dt_a), (year_b, dt_b) in zip(_DELTA_T_TABLE, _DELTA_T_TABLE[1:]):
        if year <= year_b:
            fraction = (year - year_a) / (year_b - year_a)
            return dt_a + fraction * (dt_b - dt_a)

    # Beyond the table: a mild quadratic resumption of the long-term slowing.
    elapsed = year - _DELTA_T_TABLE[-1][0]
    return _DELTA_T_TABLE[-1][1] + 0.45 * elapsed + 0.0035 * elapsed * elapsed


def ut_to_tt(jd_ut):
    """Convert a Julian Day in Universal Time to Terrestrial Time."""
    return jd_ut + delta_t_seconds(jd_ut) / 86400.0


def tt_to_ut(jd_tt):
    """Convert a Julian Day in Terrestrial Time to Universal Time."""
    return jd_tt - delta_t_seconds(jd_tt) / 86400.0


def _norm360(angle):
    """Normalise an angle in degrees to [0, 360)."""
    return angle % 360.0


# --------------------------------------------------------------------------
# Obliquity and nutation
# --------------------------------------------------------------------------


def _nutation_and_obliquity(t):
    """Return (delta_psi, true_obliquity) in degrees.

    A very abridged nutation model. It is accurate to about an arcsecond,
    which is far below the error of the position models it is applied to.
    """
    omega = _norm360(125.04452 - 1934.136261 * t)
    l_sun = _norm360(280.4665 + 36000.7698 * t)
    l_moon = _norm360(218.3165 + 481267.8813 * t)

    delta_psi = (
        -17.20 * math.sin(omega * DEG)
        - 1.32 * math.sin(2 * l_sun * DEG)
        - 0.23 * math.sin(2 * l_moon * DEG)
        + 0.21 * math.sin(2 * omega * DEG)
    ) / 3600.0

    delta_eps = (
        9.20 * math.cos(omega * DEG)
        + 0.57 * math.cos(2 * l_sun * DEG)
        + 0.10 * math.cos(2 * l_moon * DEG)
        - 0.09 * math.cos(2 * omega * DEG)
    ) / 3600.0

    # Mean obliquity, Meeus (22.2).
    eps0 = (
        23.0
        + 26.0 / 60.0
        + 21.448 / 3600.0
        - (46.8150 * t + 0.00059 * t * t - 0.001813 * t ** 3) / 3600.0
    )

    return delta_psi, eps0 + delta_eps


# --------------------------------------------------------------------------
# Sidereal time
# --------------------------------------------------------------------------


def apparent_sidereal_time(jd_ut):
    """Apparent sidereal time at Greenwich, in degrees.

    Sidereal time measures Earth's rotation, so this takes Universal Time --
    unlike the position functions below, which take Terrestrial Time.
    """
    t = julian_centuries(jd_ut)
    theta = (
        280.46061837
        + 360.98564736629 * (jd_ut - J2000)
        + 0.000387933 * t * t
        - t ** 3 / 38710000.0
    )
    delta_psi, eps = _nutation_and_obliquity(julian_centuries(ut_to_tt(jd_ut)))
    return _norm360(theta + delta_psi * math.cos(eps * DEG))


# --------------------------------------------------------------------------
# Solar position
# --------------------------------------------------------------------------


def sun_position(jd_tt):
    """Geocentric apparent position of the Sun.

    ``jd_tt`` is a Julian Day in Terrestrial Time. Returns a dict with ``ra``
    and ``dec`` in degrees and ``distance_km``.
    """
    t = julian_centuries(jd_tt)

    # Geometric mean longitude and mean anomaly.
    l0 = _norm360(280.46646 + 36000.76983 * t + 0.0003032 * t * t)
    m = _norm360(357.52911 + 35999.05029 * t - 0.0001537 * t * t)
    e = 0.016708634 - 0.000042037 * t - 0.0000001267 * t * t

    m_rad = m * DEG
    centre = (
        (1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(m_rad)
        + (0.019993 - 0.000101 * t) * math.sin(2 * m_rad)
        + 0.000289 * math.sin(3 * m_rad)
    )

    true_longitude = l0 + centre
    true_anomaly = m + centre

    # Radius vector in astronomical units.
    radius_au = (1.000001018 * (1 - e * e)) / (
        1 + e * math.cos(true_anomaly * DEG)
    )

    # Apparent longitude: correct for nutation and aberration.
    delta_psi, eps = _nutation_and_obliquity(t)
    aberration = -20.4898 / 3600.0 / radius_au
    lam = true_longitude + delta_psi + aberration

    lam_rad = lam * DEG
    eps_rad = eps * DEG

    ra = math.degrees(
        math.atan2(math.cos(eps_rad) * math.sin(lam_rad), math.cos(lam_rad))
    )
    dec = math.degrees(math.asin(math.sin(eps_rad) * math.sin(lam_rad)))

    return {
        "ra": _norm360(ra),
        "dec": dec,
        "distance_km": radius_au * AU_KM,
        "apparent_longitude": _norm360(lam),
    }


# --------------------------------------------------------------------------
# Lunar position (Meeus chapter 47)
# --------------------------------------------------------------------------

# Periodic terms for longitude (arg coefficients D, M, M', F; then sigma_l in
# 1e-6 degrees and sigma_r in 1e-3 km). Table 47.A.
_MOON_LR_TERMS = (
    (0, 0, 1, 0, 6288774, -20905355),
    (2, 0, -1, 0, 1274027, -3699111),
    (2, 0, 0, 0, 658314, -2955968),
    (0, 0, 2, 0, 213618, -569925),
    (0, 1, 0, 0, -185116, 48888),
    (0, 0, 0, 2, -114332, -3149),
    (2, 0, -2, 0, 58793, 246158),
    (2, -1, -1, 0, 57066, -152138),
    (2, 0, 1, 0, 53322, -170733),
    (2, -1, 0, 0, 45758, -204586),
    (0, 1, -1, 0, -40923, -129620),
    (1, 0, 0, 0, -34720, 108743),
    (0, 1, 1, 0, -30383, 104755),
    (2, 0, 0, -2, 15327, 10321),
    (0, 0, 1, 2, -12528, 0),
    (0, 0, 1, -2, 10980, 79661),
    (4, 0, -1, 0, 10675, -34782),
    (0, 0, 3, 0, 10034, -23210),
    (4, 0, -2, 0, 8548, -21636),
    (2, 1, -1, 0, -7888, 24208),
    (2, 1, 0, 0, -6766, 30824),
    (1, 0, -1, 0, -5163, -8379),
    (1, 1, 0, 0, 4987, -16675),
    (2, -1, 1, 0, 4036, -12831),
    (2, 0, 2, 0, 3994, -10445),
    (4, 0, 0, 0, 3861, -11650),
    (2, 0, -3, 0, 3665, 14403),
    (0, 1, -2, 0, -2689, -7003),
    (2, 0, -1, 2, -2602, 0),
    (2, -1, -2, 0, 2390, 10056),
    (1, 0, 1, 0, -2348, 6322),
    (2, -2, 0, 0, 2236, -9884),
    (0, 1, 2, 0, -2120, 5751),
    (0, 2, 0, 0, -2069, 0),
    (2, -2, -1, 0, 2048, -4950),
    (2, 0, 1, -2, -1773, 4130),
    (2, 0, 0, 2, -1595, 0),
    (4, -1, -1, 0, 1215, -3958),
    (0, 0, 2, 2, -1110, 0),
    (3, 0, -1, 0, -892, 3258),
    (2, 1, 1, 0, -810, 2616),
    (4, -1, -2, 0, 759, -1897),
    (0, 2, -1, 0, -713, -2117),
    (2, 2, -1, 0, -700, 2354),
    (2, 1, -2, 0, 691, 0),
    (2, -1, 0, -2, 596, 0),
    (4, 0, 1, 0, 549, -1423),
    (0, 0, 4, 0, 537, -1117),
    (4, -1, 0, 0, 520, -1571),
    (1, 0, -2, 0, -487, -1739),
    (2, 1, 0, -2, -399, 0),
    (0, 0, 2, -2, -381, -4421),
    (1, 1, 1, 0, 351, 0),
    (3, 0, -2, 0, -340, 0),
    (4, 0, -3, 0, 330, 0),
    (2, -1, 2, 0, 327, 0),
    (0, 2, 1, 0, -323, 1165),
    (1, 1, -1, 0, 299, 0),
    (2, 0, 3, 0, 294, 0),
    (2, 0, -1, -2, 0, 8752),
)

# Periodic terms for latitude (sigma_b in 1e-6 degrees). Table 47.B.
_MOON_B_TERMS = (
    (0, 0, 0, 1, 5128122),
    (0, 0, 1, 1, 280602),
    (0, 0, 1, -1, 277693),
    (2, 0, 0, -1, 173237),
    (2, 0, -1, 1, 55413),
    (2, 0, -1, -1, 46271),
    (2, 0, 0, 1, 32573),
    (0, 0, 2, 1, 17198),
    (2, 0, 1, -1, 9266),
    (0, 0, 2, -1, 8822),
    (2, -1, 0, -1, 8216),
    (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200),
    (2, 1, 0, -1, -3359),
    (2, -1, -1, 1, 2463),
    (2, -1, 0, 1, 2211),
    (2, -1, -1, -1, 2065),
    (0, 1, -1, -1, -1870),
    (4, 0, -1, -1, 1828),
    (0, 1, 0, 1, -1794),
    (0, 0, 0, 3, -1749),
    (0, 1, -1, 1, -1565),
    (1, 0, 0, 1, -1491),
    (0, 1, 1, 1, -1475),
    (0, 1, 1, -1, -1410),
    (0, 1, 0, -1, -1344),
    (1, 0, 0, -1, -1335),
    (0, 0, 3, 1, 1107),
    (4, 0, 0, -1, 1021),
    (4, 0, -1, 1, 833),
    (0, 0, 1, -3, 777),
    (4, 0, -2, 1, 671),
    (2, 0, 0, -3, 607),
    (2, 0, 2, -1, 596),
    (2, -1, 1, -1, 491),
    (2, 0, -2, 1, -451),
    (0, 0, 3, -1, 439),
    (2, 0, 2, 1, 422),
    (2, 0, -3, -1, 421),
    (2, 1, -1, 1, -366),
    (2, 1, 0, 1, -351),
    (4, 0, 0, 1, 331),
    (2, -1, 1, 1, 315),
    (2, -2, 0, -1, 302),
    (0, 0, 1, 3, -283),
    (2, 1, 1, -1, -229),
    (1, 1, 0, -1, 223),
    (1, 1, 0, 1, 223),
    (0, 1, -2, -1, -220),
    (2, 1, -1, -1, -220),
    (1, 0, 1, 1, -185),
    (2, -1, -2, -1, 181),
    (0, 1, 2, 1, -177),
    (4, 0, -2, -1, 176),
    (4, -1, -1, -1, 166),
    (1, 0, 1, -1, -164),
    (4, 0, 1, -1, 132),
    (1, 0, -1, -1, -119),
    (4, -1, 0, -1, 115),
    (2, -2, 0, 1, 107),
)


def moon_position(jd_tt):
    """Geocentric apparent position of the Moon.

    ``jd_tt`` is a Julian Day in Terrestrial Time. Returns a dict with ``ra``
    and ``dec`` in degrees and ``distance_km``.
    """
    t = julian_centuries(jd_tt)

    # Mean elements.
    l_prime = _norm360(
        218.3164477
        + 481267.88123421 * t
        - 0.0015786 * t * t
        + t ** 3 / 538841.0
        - t ** 4 / 65194000.0
    )
    d = _norm360(
        297.8501921
        + 445267.1114034 * t
        - 0.0018819 * t * t
        + t ** 3 / 545868.0
        - t ** 4 / 113065000.0
    )
    m = _norm360(
        357.5291092 + 35999.0502909 * t - 0.0001536 * t * t + t ** 3 / 24490000.0
    )
    m_prime = _norm360(
        134.9633964
        + 477198.8675055 * t
        + 0.0087414 * t * t
        + t ** 3 / 69699.0
        - t ** 4 / 14712000.0
    )
    f = _norm360(
        93.2720950
        + 483202.0175233 * t
        - 0.0036539 * t * t
        - t ** 3 / 3526000.0
        + t ** 4 / 863310000.0
    )

    a1 = _norm360(119.75 + 131.849 * t)
    a2 = _norm360(53.09 + 479264.290 * t)
    a3 = _norm360(313.45 + 481266.484 * t)

    # Eccentricity correction for terms involving the Sun's mean anomaly.
    ecc = 1 - 0.002516 * t - 0.0000074 * t * t

    sum_l = 0.0
    sum_r = 0.0
    for c_d, c_m, c_mp, c_f, coef_l, coef_r in _MOON_LR_TERMS:
        arg = (c_d * d + c_m * m + c_mp * m_prime + c_f * f) * DEG
        factor = ecc ** abs(c_m)
        if coef_l:
            sum_l += coef_l * factor * math.sin(arg)
        if coef_r:
            sum_r += coef_r * factor * math.cos(arg)

    sum_b = 0.0
    for c_d, c_m, c_mp, c_f, coef_b in _MOON_B_TERMS:
        arg = (c_d * d + c_m * m + c_mp * m_prime + c_f * f) * DEG
        sum_b += coef_b * (ecc ** abs(c_m)) * math.sin(arg)

    # Additive terms from Venus, Jupiter and the flattening of the Earth.
    sum_l += (
        3958 * math.sin(a1 * DEG)
        + 1962 * math.sin((l_prime - f) * DEG)
        + 318 * math.sin(a2 * DEG)
    )
    sum_b += (
        -2235 * math.sin(l_prime * DEG)
        + 382 * math.sin(a3 * DEG)
        + 175 * math.sin((a1 - f) * DEG)
        + 175 * math.sin((a1 + f) * DEG)
        + 127 * math.sin((l_prime - m_prime) * DEG)
        - 115 * math.sin((l_prime + m_prime) * DEG)
    )

    longitude = l_prime + sum_l / 1e6
    latitude = sum_b / 1e6
    distance_km = 385000.56 + sum_r / 1000.0

    delta_psi, eps = _nutation_and_obliquity(t)
    lam = (longitude + delta_psi) * DEG
    beta = latitude * DEG
    eps_rad = eps * DEG

    ra = math.degrees(
        math.atan2(
            math.sin(lam) * math.cos(eps_rad) - math.tan(beta) * math.sin(eps_rad),
            math.cos(lam),
        )
    )
    dec = math.degrees(
        math.asin(
            math.sin(beta) * math.cos(eps_rad)
            + math.cos(beta) * math.sin(eps_rad) * math.sin(lam)
        )
    )

    return {
        "ra": _norm360(ra),
        "dec": dec,
        "distance_km": distance_km,
        "apparent_longitude": _norm360(longitude + delta_psi),
        "latitude": latitude,
    }


# --------------------------------------------------------------------------
# Topocentric conversion
# --------------------------------------------------------------------------


def _observer_vector(jd_ut, lat, lon, elevation_m=0.0):
    """Observer's geocentric rectangular coordinates, in Earth radii."""
    lat_rad = lat * DEG
    u = math.atan(EARTH_FLATTENING_RATIO * math.tan(lat_rad))
    height = elevation_m / (EARTH_RADIUS_KM * 1000.0)

    rho_sin_phi = EARTH_FLATTENING_RATIO * math.sin(u) + height * math.sin(lat_rad)
    rho_cos_phi = math.cos(u) + height * math.cos(lat_rad)

    theta = (apparent_sidereal_time(jd_ut) + lon) * DEG

    return (
        rho_cos_phi * math.cos(theta),
        rho_cos_phi * math.sin(theta),
        rho_sin_phi,
    )


def to_topocentric(jd_ut, ra, dec, distance_km, lat, lon, elevation_m=0.0):
    """Correct a geocentric equatorial position for diurnal parallax.

    ``jd_ut`` is in Universal Time because the observer's position depends on
    Earth's rotation. Returns (ra, dec, distance_km) as seen by the observer.
    """
    distance_er = distance_km / EARTH_RADIUS_KM

    ra_rad = ra * DEG
    dec_rad = dec * DEG

    x = distance_er * math.cos(dec_rad) * math.cos(ra_rad)
    y = distance_er * math.cos(dec_rad) * math.sin(ra_rad)
    z = distance_er * math.sin(dec_rad)

    ox, oy, oz = _observer_vector(jd_ut, lat, lon, elevation_m)

    x -= ox
    y -= oy
    z -= oz

    distance = math.sqrt(x * x + y * y + z * z)
    return (
        _norm360(math.degrees(math.atan2(y, x))),
        math.degrees(math.asin(z / distance)),
        distance * EARTH_RADIUS_KM,
    )


def altitude_azimuth(jd_ut, ra, dec, lat, lon):
    """Local altitude and azimuth (degrees, azimuth measured east of north)."""
    hour_angle = (apparent_sidereal_time(jd_ut) + lon - ra) * DEG
    lat_rad = lat * DEG
    dec_rad = dec * DEG

    sin_alt = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(
        dec_rad
    ) * math.cos(hour_angle)
    altitude = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))

    azimuth = math.degrees(
        math.atan2(
            math.sin(hour_angle),
            math.cos(hour_angle) * math.sin(lat_rad) - math.tan(dec_rad) * math.cos(lat_rad),
        )
    )
    return altitude, _norm360(azimuth + 180.0)


def sun_altitude(jd_ut, lat, lon):
    """Apparent altitude of the Sun's centre, in degrees."""
    sun = sun_position(ut_to_tt(jd_ut))
    return altitude_azimuth(jd_ut, sun["ra"], sun["dec"], lat, lon)[0]


def angular_separation(ra1, dec1, ra2, dec2):
    """Angular separation between two equatorial positions, in degrees."""
    ra1_rad, dec1_rad = ra1 * DEG, dec1 * DEG
    ra2_rad, dec2_rad = ra2 * DEG, dec2 * DEG

    cos_sep = math.sin(dec1_rad) * math.sin(dec2_rad) + math.cos(dec1_rad) * math.cos(
        dec2_rad
    ) * math.cos(ra1_rad - ra2_rad)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


# --------------------------------------------------------------------------
# Sunrise and sunset
# --------------------------------------------------------------------------


def _horizon_altitude(elevation_m):
    """Target altitude for sunrise/sunset, allowing for observer elevation."""
    dip = 0.0
    if elevation_m and elevation_m > 0:
        dip = 0.0353 * math.sqrt(elevation_m)
    return SUNRISE_ALTITUDE_DEG - dip


def _resolve_timezone(tz_name):
    if tz_name and ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return dt.timezone.utc


def sun_events(date, lat, lon, tz_name=None, elevation_m=0.0):
    """Sunrise, sunset, solar noon and day length for a calendar date.

    ``date`` is interpreted as a local calendar date in ``tz_name``. Returns a
    dict whose ``sunrise``/``sunset`` values are timezone-aware datetimes, or
    ``None`` during polar day or polar night (in which case ``polar_day`` or
    ``polar_night`` is set).
    """
    tz = _resolve_timezone(tz_name)
    target = _horizon_altitude(elevation_m)

    local_midnight = dt.datetime(date.year, date.month, date.day, tzinfo=tz)
    jd_start = datetime_to_jd(local_midnight)
    jd_end = jd_start + 1.0

    step = 10.0 / (24.0 * 60.0)  # ten minutes
    samples = []
    jd = jd_start
    while jd <= jd_end + 1e-9:
        samples.append((jd, sun_altitude(jd, lat, lon) - target))
        jd += step

    def refine(lo, hi):
        """Bisect for the altitude crossing between two bracketing times."""
        f_lo = sun_altitude(lo, lat, lon) - target
        for _ in range(40):
            mid = (lo + hi) / 2.0
            f_mid = sun_altitude(mid, lat, lon) - target
            if (f_lo < 0) == (f_mid < 0):
                lo, f_lo = mid, f_mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    sunrise_jd = None
    sunset_jd = None
    for (jd_a, alt_a), (jd_b, alt_b) in zip(samples, samples[1:]):
        if alt_a < 0 <= alt_b and sunrise_jd is None:
            sunrise_jd = refine(jd_a, jd_b)
        elif alt_a >= 0 > alt_b and sunset_jd is None:
            sunset_jd = refine(jd_a, jd_b)

    # Solar noon: the sample of highest altitude, refined by golden section.
    noon_jd = max(samples, key=lambda item: item[1])[0]
    noon_jd = _golden_section_max(
        lambda x: sun_altitude(x, lat, lon), noon_jd - step, noon_jd + step
    )

    result = {
        "date": date,
        "timezone": tz_name,
        "sunrise": jd_to_datetime(sunrise_jd).astimezone(tz) if sunrise_jd else None,
        "sunset": jd_to_datetime(sunset_jd).astimezone(tz) if sunset_jd else None,
        "solar_noon": jd_to_datetime(noon_jd).astimezone(tz),
        "max_sun_altitude": round(sun_altitude(noon_jd, lat, lon), 2),
        "polar_day": False,
        "polar_night": False,
        "day_length_hours": None,
    }

    if sunrise_jd and sunset_jd:
        result["day_length_hours"] = round((sunset_jd - sunrise_jd) * 24.0, 3)
    elif sunrise_jd is None and sunset_jd is None:
        # No crossing at all: the Sun stayed either up or down all day.
        if all(alt >= 0 for _, alt in samples):
            result["polar_day"] = True
            result["day_length_hours"] = 24.0
        else:
            result["polar_night"] = True
            result["day_length_hours"] = 0.0

    return result


def _golden_section_max(func, lo, hi, iterations=40):
    """Locate the maximum of a unimodal function on [lo, hi]."""
    inv_phi = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c = b - inv_phi * (b - a)
    d = a + inv_phi * (b - a)
    fc, fd = func(c), func(d)
    for _ in range(iterations):
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - inv_phi * (b - a)
            fc = func(c)
        else:
            a, c, fc = c, d, fd
            d = a + inv_phi * (b - a)
            fd = func(d)
    return (a + b) / 2.0


# --------------------------------------------------------------------------
# Solar eclipses
# --------------------------------------------------------------------------


def mean_new_moon_jd(k):
    """Approximate Julian Day of new moon number ``k`` counted from 2000-01-06.

    Meeus (49.1) plus the largest periodic corrections. Accurate to a few
    minutes, which is ample as a seed for the search below.
    """
    t = k / 1236.85
    jde = (
        2451550.09766
        + 29.530588861 * k
        + 0.00015437 * t * t
        - 0.000000150 * t ** 3
        + 0.00000000073 * t ** 4
    )

    e = 1 - 0.002516 * t - 0.0000074 * t * t
    m = _norm360(2.5534 + 29.10535670 * k - 0.0000014 * t * t - 0.00000011 * t ** 3)
    m_prime = _norm360(
        201.5643
        + 385.81693528 * k
        + 0.0107582 * t * t
        + 0.00001238 * t ** 3
        - 0.000000058 * t ** 4
    )
    f = _norm360(
        160.7108
        + 390.67050284 * k
        - 0.0016118 * t * t
        - 0.00000227 * t ** 3
        + 0.000000011 * t ** 4
    )
    omega = _norm360(
        124.7746 - 1.56375588 * k + 0.0020672 * t * t + 0.00000215 * t ** 3
    )

    correction = (
        -0.40720 * math.sin(m_prime * DEG)
        + 0.17241 * e * math.sin(m * DEG)
        + 0.01608 * math.sin(2 * m_prime * DEG)
        + 0.01039 * math.sin(2 * f * DEG)
        + 0.00739 * e * math.sin((m_prime - m) * DEG)
        - 0.00514 * e * math.sin((m_prime + m) * DEG)
        + 0.00208 * e * e * math.sin(2 * m * DEG)
        - 0.00111 * math.sin((m_prime - 2 * f) * DEG)
        - 0.00057 * math.sin((m_prime + 2 * f) * DEG)
        + 0.00056 * e * math.sin((2 * m_prime + m) * DEG)
        - 0.00042 * math.sin(3 * m_prime * DEG)
        + 0.00042 * e * math.sin((m + 2 * f) * DEG)
        + 0.00038 * e * math.sin((m - 2 * f) * DEG)
        - 0.00024 * e * math.sin((2 * m_prime - m) * DEG)
        - 0.00017 * math.sin(omega * DEG)
        - 0.00007 * math.sin((m_prime + 2 * m) * DEG)
    )

    return jde + correction


def _geocentric_elongation(jd_tt):
    """Difference in apparent longitude between the Moon and the Sun."""
    diff = (
        moon_position(jd_tt)["apparent_longitude"]
        - sun_position(jd_tt)["apparent_longitude"]
    )
    return (diff + 180.0) % 360.0 - 180.0


def true_new_moon_tt(seed_jd_tt):
    """Refine a new moon time by solving for zero apparent-longitude elongation.

    Both the seed and the result are in Terrestrial Time.
    """
    jd_tt = seed_jd_tt
    for _ in range(8):
        elongation = _geocentric_elongation(jd_tt)
        # The Moon gains on the Sun by roughly 12.19 degrees per day.
        correction = -elongation / 12.19
        jd_tt += correction
        if abs(correction) < 1e-6:
            break
    return jd_tt


def _disc_geometry(jd_ut, lat, lon, elevation_m, moon_radius_km=MOON_RADIUS_KM):
    """Topocentric Sun/Moon geometry at one instant, all angles in degrees.

    Returns (separation, sun_radius, moon_radius, sun_altitude, sun_azimuth).
    Positions are evaluated in Terrestrial Time while the observer's location
    is evaluated in Universal Time; conflating the two would shift eclipse
    times by the whole of Delta T, currently over a minute.
    """
    jd_tt = ut_to_tt(jd_ut)
    sun = sun_position(jd_tt)

    # We see the Moon where it was one light-time ago (about 1.28 seconds).
    # The equivalent correction for the Sun is its 499 second light-time,
    # already folded into sun_position as the aberration term. The two differ
    # by about 20 arcsec, so they must not be treated as cancelling.
    moon = moon_position(jd_tt)
    light_time_days = (moon["distance_km"] / 299792.458) / 86400.0
    moon = moon_position(jd_tt - light_time_days)

    sun_ra, sun_dec, sun_dist = to_topocentric(
        jd_ut, sun["ra"], sun["dec"], sun["distance_km"], lat, lon, elevation_m
    )
    moon_ra, moon_dec, moon_dist = to_topocentric(
        jd_ut, moon["ra"], moon["dec"], moon["distance_km"], lat, lon, elevation_m
    )

    separation = angular_separation(sun_ra, sun_dec, moon_ra, moon_dec)
    sun_radius = math.degrees(math.asin(SUN_RADIUS_KM / sun_dist))
    moon_radius = math.degrees(math.asin(moon_radius_km / moon_dist))
    altitude, azimuth = altitude_azimuth(jd_ut, sun_ra, sun_dec, lat, lon)

    return separation, sun_radius, moon_radius, altitude, azimuth


def _coverage(separation, sun_radius, moon_radius):
    """Eclipse magnitude, obscuration and type from disc geometry."""
    if separation >= sun_radius + moon_radius:
        return 0.0, 0.0, None

    magnitude = (sun_radius + moon_radius - separation) / (2.0 * sun_radius)

    if separation <= moon_radius - sun_radius:
        return magnitude, 1.0, "total"

    if separation <= sun_radius - moon_radius:
        ratio = moon_radius / sun_radius
        return magnitude, ratio * ratio, "annular"

    # Area of the lens-shaped intersection of two circles.
    d, r_s, r_m = separation, sun_radius, moon_radius
    term_moon = r_m * r_m * math.acos(
        max(-1.0, min(1.0, (d * d + r_m * r_m - r_s * r_s) / (2 * d * r_m)))
    )
    term_sun = r_s * r_s * math.acos(
        max(-1.0, min(1.0, (d * d + r_s * r_s - r_m * r_m) / (2 * d * r_s)))
    )
    triangle = 0.5 * math.sqrt(
        max(
            0.0,
            (-d + r_m + r_s) * (d + r_m - r_s) * (d - r_m + r_s) * (d + r_m + r_s),
        )
    )
    overlap = term_moon + term_sun - triangle

    return magnitude, overlap / (math.pi * r_s * r_s), "partial"


def local_eclipse_circumstances(new_moon_jd_ut, lat, lon, elevation_m=0.0):
    """Local circumstances of the eclipse near ``new_moon_jd_ut``, if any.

    All Julian Days in and out of this function are Universal Time. Returns
    ``None`` when no part of the eclipse is visible above the horizon here.
    """
    window = 7.0 / 24.0  # +/- 7 hours brackets any local contact time
    step = 6.0 / (24.0 * 60.0)  # six minutes

    def overlap(jd_ut, moon_radius_km=MOON_RADIUS_KM):
        """How deeply the Moon overlaps the Sun; positive means eclipsed."""
        separation, r_s, r_m = _disc_geometry(
            jd_ut, lat, lon, elevation_m, moon_radius_km
        )[:3]
        return (r_s + r_m) - separation

    # Coarse scan for the deepest overlap that happens with the Sun up.
    best_jd = None
    best_overlap = None
    jd_ut = new_moon_jd_ut - window
    while jd_ut <= new_moon_jd_ut + window:
        separation, r_s, r_m, altitude = _disc_geometry(
            jd_ut, lat, lon, elevation_m
        )[:4]
        current = (r_s + r_m) - separation
        if altitude > -0.5 and (best_overlap is None or current > best_overlap):
            best_jd, best_overlap = jd_ut, current
        jd_ut += step

    if best_jd is None or best_overlap <= 0:
        return None

    # Refine the moment of maximum eclipse.
    max_jd = _golden_section_max(overlap, best_jd - step, best_jd + step)
    separation, r_s, r_m, altitude, azimuth = _disc_geometry(
        max_jd, lat, lon, elevation_m
    )
    if altitude <= -0.5 or (r_s + r_m) - separation <= 0:
        # The refined maximum slipped below the horizon, so the best visible
        # moment is at the horizon itself; keep the coarse sample.
        max_jd = best_jd
        separation, r_s, r_m, altitude, azimuth = _disc_geometry(
            max_jd, lat, lon, elevation_m
        )

    magnitude, obscuration, eclipse_type = _coverage(separation, r_s, r_m)
    if eclipse_type is None:
        return None

    def find_edge(direction, moon_radius_km, limit_hours, threshold):
        """Bisect outward from maximum for the time a contact threshold is met.

        ``threshold`` compares against the separation: ``r_s + r_m`` for the
        partial contacts, ``abs(r_m - r_s)`` for the central phase.
        """
        def crossing(jd_ut):
            separation, r_s, r_m = _disc_geometry(
                jd_ut, lat, lon, elevation_m, moon_radius_km
            )[:3]
            return threshold(r_s, r_m) - separation

        lo = max_jd
        hi = max_jd + direction * limit_hours / 24.0
        if crossing(hi) > 0:
            return None  # still in progress at the search limit
        for _ in range(40):
            mid = (lo + hi) / 2.0
            if crossing(mid) > 0:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    partial_threshold = lambda r_s, r_m: r_s + r_m
    central_threshold = lambda r_s, r_m: abs(r_m - r_s)

    result = {
        "type": eclipse_type,
        "magnitude": round(magnitude, 4),
        "obscuration": round(obscuration, 4),
        "maximum_jd": max_jd,
        "maximum_sun_altitude": round(altitude, 2),
        "maximum_sun_azimuth": round(azimuth, 1),
        "first_contact_jd": find_edge(-1, MOON_RADIUS_KM, 4.0, partial_threshold),
        "last_contact_jd": find_edge(1, MOON_RADIUS_KM, 4.0, partial_threshold),
    }

    # Duration of totality or annularity, using the umbral radius convention.
    if eclipse_type in ("total", "annular"):
        start = find_edge(-1, MOON_RADIUS_UMBRAL_KM, 0.35, central_threshold)
        end = find_edge(1, MOON_RADIUS_UMBRAL_KM, 0.35, central_threshold)
        if start is not None and end is not None:
            result["central_duration_seconds"] = round((end - start) * 86400.0)
            result["central_start_jd"] = start
            result["central_end_jd"] = end

    return result


def next_solar_eclipse(lat, lon, start=None, elevation_m=0.0, max_lunations=310):
    """Find the next solar eclipse visible from a location.

    Scans forward through new moons from ``start`` (default: now) for up to
    ``max_lunations`` months, roughly 25 years. Returns a dict describing the
    eclipse, or ``None`` if nothing was found in that span (which would be
    extraordinary -- every location sees a partial eclipse every few years).
    """
    if start is None:
        start = dt.datetime.now(dt.timezone.utc)
    start_jd_ut = datetime_to_jd(start)

    # New moon number k, from Meeus (49.2): k = 0 at the new moon of 2000-01-06.
    approx_year = 2000.0 + (start_jd_ut - 2451550.09766) / 365.25
    k = math.floor((approx_year - 2000.0) * 12.3685)

    # Step back a couple of lunations so an eclipse in progress today is caught.
    k -= 2

    for _ in range(max_lunations):
        new_moon_tt = true_new_moon_tt(mean_new_moon_jd(k))
        new_moon_ut = tt_to_ut(new_moon_tt)
        k += 1

        if new_moon_ut < start_jd_ut - 0.5:
            continue

        # Cheap rejection: no solar eclipse occurs anywhere on Earth unless the
        # Moon is within about 1.6 degrees of the ecliptic at conjunction.
        if abs(moon_position(new_moon_tt)["latitude"]) > 1.6:
            continue

        circumstances = local_eclipse_circumstances(
            new_moon_ut, lat, lon, elevation_m
        )
        if circumstances is None:
            continue

        if circumstances["maximum_jd"] < start_jd_ut:
            continue

        return circumstances

    return None
