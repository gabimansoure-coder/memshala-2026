"""Hebrew dates and Israeli statutory rest days (Shabbat, Yom Tov, Yom HaAtzmaut)."""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from pyluach import dates as hd

WEEKDAYS_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
MONTHS_HE = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי",
             "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]


def hebrew_date(d: date) -> str:
    """'כ״ח חשון תשפ״ז' for a Gregorian date."""
    return hd.GregorianDate(d.year, d.month, d.day).to_heb().hebrew_date_string()


def weekday_he(d: date) -> str:
    return "יום " + WEEKDAYS_HE[d.weekday()] if d.weekday() != 5 else "שבת"


def gregorian_he(d: date) -> str:
    """'8 בנובמבר 2026'."""
    return f"{d.day} ב{MONTHS_HE[d.month - 1]} {d.year}"


@lru_cache(maxsize=64)
def _yom_haatzmaut(hebrew_year: int) -> date:
    """Yom HaAtzmaut after the statutory shifts (5 Iyar, moved off Fri/Sat/Mon)."""
    d = hd.HebrewDate(hebrew_year, 2, 5).to_pydate()
    wd = d.weekday()  # Mon=0 ... Sun=6
    if wd == 4:      # Friday -> Thursday
        d -= timedelta(days=1)
    elif wd == 5:    # Shabbat -> Thursday
        d -= timedelta(days=2)
    elif wd == 0:    # Monday -> Tuesday
        d += timedelta(days=1)
    return d


def rest_day_name(d: date) -> str | None:
    """Name of the statutory rest day on `d`, or None for a regular weekday."""
    if d.weekday() == 5:
        return "שבת"
    h = hd.GregorianDate(d.year, d.month, d.day).to_heb()
    yom_tov = h.festival(israel=True, hebrew=True, include_working_days=False)
    if yom_tov:
        return yom_tov
    if d == _yom_haatzmaut(h.year):
        return "יום העצמאות"
    return None


def holiday_name(d: date) -> str | None:
    """Any Jewish holiday or rest day (including working days like Purim)."""
    rest = rest_day_name(d)
    if rest:
        return rest
    h = hd.GregorianDate(d.year, d.month, d.day).to_heb()
    if d == _yom_haatzmaut(h.year) - timedelta(days=1):
        return "יום הזיכרון לחללי מערכות ישראל"
    return h.festival(israel=True, hebrew=True)


def next_working_day(d: date) -> date:
    while rest_day_name(d):
        d += timedelta(days=1)
    return d
