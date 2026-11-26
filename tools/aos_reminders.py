"""Parse David reminder commands and calculate occurrences in the requested local time."""

from __future__ import annotations

import calendar
import datetime as dt
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aos_job_ids import AOS_PATTERN

LOCAL_ZONE = "America/Creston"
UTC = dt.timezone.utc
DAYS = {name.casefold(): i for i, name in enumerate(calendar.day_name)}
DAYS.update({name[:3]: i for name, i in DAYS.copy().items()})
DAY = "|".join(DAYS)
CLOCK = r"\d{1,2}(?::\d{2})?(?:\s*[ap]\.?m\.?(?![A-Za-z]))?|noon|midnight"
ZONE = r"[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)+|UTC|PT|MT|ET"
DATE = rf"(?:today|tomorrow|(?:next\s+)?(?:{DAY})|\d{{4}}-\d{{2}}-\d{{2}})(?:\s+(?:morning|afternoon|evening))?"
SCHEDULE = (
    rf"in\s+(?:\d+|an?)\s+(?:seconds?|minutes?|hours?|days?|weeks?)"
    rf"|(?:every\s+(?:day|weekday|week|{DAY})|daily|weekly|weekdays)\s+(?:at\s+)?(?:{CLOCK})"
    rf"|(?:{DATE})\s+(?:at\s+)?(?:{CLOCK})"
    rf"|(?:at\s+)?(?:{CLOCK})(?:\s+(?:on\s+)?(?:{DATE}))?"
)
TIME_RE = re.compile(rf"(?P<when>{SCHEDULE})(?:\s+(?P<zone>{ZONE}))?", re.I)
CREATE = re.compile(
    r"^(?:/remind\b|remind\s+me\b|send\s+me\s+a\s+reminder\b|"
    r"(?:set(?:\s+up)?|schedule|create|add)\s+(?:me\s+)?(?:a\s+)?reminder\b)[\s,:]*", re.I
)
LIST = re.compile(
    r"^(?:(?:/reminders|/remind\s+list)|(?:show(?:\s+me)?|list|what\s+are|what(?:'s| is)\s+on)\s+"
    r"(?:all\s+)?(?:my\s+)?(?:(?:active|scheduled|upcoming)\s+)?reminders|my\s+reminders)[?.!]*$", re.I
)
CHANGE = re.compile(
    rf"^(?P<action>cancel|delete|stop|edit|change|update|move|reschedule|pause|resume|/remind\s+(?:cancel|edit|pause|resume))"
    rf"\s+(?:(?:my|the)\s+)?(?P<reminder>reminder\s+)?(?P<id>{AOS_PATTERN})\b[\s,:]*(?P<edit>.*)$", re.I
)
RECURRING = re.compile(rf"(?:every\s+(day|weekday|week|{DAY})|(daily|weekly|weekdays))\s+(?:at\s+)?({CLOCK})", re.I)
HELP = ('Try “Remind me tomorrow at 9:30 AM to call Sam” or '
        '“Remind me every Friday at 9 AM to send the update”. Use “list my reminders” for IDs.')


class ReminderError(ValueError):
    pass


def command(text: str) -> tuple[str, str, str] | None:
    cleaned = re.sub(r"^(?:hey\s+david[,\s]+|david[,\s]+|please\s+|i(?:'d\s+like|\s+want)\s+you\s+to\s+|(?:can|could|would|will)\s+you\s+)+",
                     "", text.strip(), flags=re.I)
    cleaned = re.sub(r"\s+please[?.!]*$", "", cleaned, flags=re.I)
    if LIST.fullmatch(cleaned):
        return "list", "", ""
    change = CHANGE.fullmatch(cleaned)
    if change:
        verb = change["action"].lower()
        action = verb.split()[-1]
        if action in {"pause", "resume"} and not change["reminder"] and not verb.startswith("/remind"):
            return None
        if action in {"delete", "stop"}:
            action = "cancel"
        elif action not in {"cancel", "pause", "resume"}:
            action = "edit"
        return action, change["id"].upper(), change["edit"]
    create = CREATE.match(cleaned)
    if create:
        return "create", "", cleaned[create.end():]
    if re.match(r"^(?:cancel|delete|stop|edit|change|update|move|reschedule|pause|resume)(?:\s+\w+){0,2}\s+reminder\b", cleaned, re.I):
        return "help", "", ""
    return None


def _clock(value: str, daypart: str | None = None) -> dt.time:
    value = re.sub(r"[\s.]", "", value.lower())
    if value in {"noon", "midnight"}:
        return dt.time(12 if value == "noon" else 0)
    period = value[-2:] if value.endswith(("am", "pm")) else ""
    hour, _, minute = (value[:-2] if period else value).partition(":")
    hours = int(hour)
    if not period and daypart and 1 <= hours <= 12:
        period = "am" if daypart == "morning" else "pm"
    if period:
        if not 1 <= hours <= 12:
            raise ReminderError("Use a valid clock time, such as 9:30 AM or 14:00.")
        hours = hours % 12 + (12 if period == "pm" else 0)
    elif not minute:
        raise ReminderError("Include AM or PM, or use a 24-hour time such as 14:00.")
    return dt.time(hours, int(minute or 0))


def _wall_time(day: dt.date, clock: dt.time, zone: ZoneInfo) -> dt.datetime | None:
    wall = dt.datetime.combine(day, clock)
    utc = wall.replace(tzinfo=zone, fold=0).astimezone(UTC)
    # Use the first occurrence in a fall-back fold. A spring-forward gap has no occurrence.
    return utc if utc.astimezone(zone).replace(tzinfo=None) == wall else None


def next_occurrence(schedule: dict, after: dt.datetime) -> dt.datetime:
    zone = ZoneInfo(schedule["timezone"])
    day = after.astimezone(zone).date()
    for offset in range(15):
        candidate_day = day + dt.timedelta(days=offset)
        if candidate_day.weekday() in schedule["weekdays"]:
            candidate = _wall_time(candidate_day, dt.time.fromisoformat(schedule["local_time"]), zone)
            if candidate is not None and candidate > after:
                return candidate
    raise ReminderError("Could not find the next occurrence.")


def _spec(when: re.Match, now: dt.datetime, default_zone: str) -> dict:
    value = " ".join(when["when"].casefold().split())
    zone_name = when["zone"] or default_zone
    zone_name = {"PT": "America/Vancouver", "MT": "America/Edmonton", "ET": "America/Toronto",
                 "UTC": "UTC"}.get(zone_name.upper(), zone_name)
    zone = ZoneInfo(zone_name)
    today = now.astimezone(zone).date()
    spec = {"timezone": zone.key, "weekdays": [], "local_time": None}
    recurring = RECURRING.fullmatch(value)
    if value.startswith("in "):
        _, number, unit = value.split()
        due = now + dt.timedelta(**{unit.rstrip("s") + "s": 1 if number in {"a", "an"} else int(number)})
    elif recurring:
        frequency = recurring[1] or recurring[2]
        weekdays = (list(range(7)) if frequency in {"day", "daily"} else
                    list(range(5)) if frequency in {"weekday", "weekdays"} else
                    [today.weekday()] if frequency in {"week", "weekly"} else [DAYS[frequency]])
        spec.update(weekdays=weekdays, local_time=_clock(recurring[3]).isoformat())
        due = next_occurrence(spec, now)
    else:
        day_first = re.fullmatch(rf"({DATE})\s+(?:at\s+)?({CLOCK})", value, re.I)
        clock_first = re.fullmatch(rf"(?:at\s+)?({CLOCK})(?:\s+(?:on\s+)?({DATE}))?", value, re.I)
        date, clock = day_first.groups() if day_first else (clock_first[2], clock_first[1])
        daypart = re.search(r"\s+(morning|afternoon|evening)$", date or "")
        if daypart:
            date = date[:daypart.start()]
        if date == "tomorrow":
            day = today + dt.timedelta(days=1)
        elif date and date[0].isdigit():
            day = dt.date.fromisoformat(date)
        elif date and date != "today":
            offset = (DAYS[date.removeprefix("next ")] - today.weekday()) % 7
            day = today + dt.timedelta(days=offset or (7 if date.startswith("next ") else 0))
        else:
            day = today
        local_clock = _clock(clock, daypart[1] if daypart else None)
        due = _wall_time(day, local_clock, zone)
        if not date and due is not None and due <= now:
            due = _wall_time(day + dt.timedelta(days=1), local_clock, zone)
        if due is None:
            raise ReminderError("That local time does not exist because the clocks move forward. Choose another time.")
        if date in DAYS and due <= now:
            due = _wall_time(day + dt.timedelta(days=7), local_clock, zone)
    if due is None or due <= now:
        raise ReminderError("Choose a future time for the reminder.")
    spec["next_at"] = due.astimezone(UTC).isoformat()
    return spec


def parse(text: str, now: dt.datetime, *, default_zone: str = LOCAL_ZONE) -> dict:
    """Allow either order, e.g. 'tomorrow at 9 to call' or 'to call tomorrow at 9'."""
    text = re.sub(r"^for\s+", "", text.strip().rstrip("?!"), flags=re.I)
    at_start = TIME_RE.match(text)
    if at_start and (separator := re.match(r"(?:\s+to\s+(?:say\s+that\s+)?|\s+that\s+|\s*[:,–—]\s*)", text[at_start.end():], re.I)):
        when, message = at_start, text[at_start.end() + separator.end():]
    elif (when := TIME_RE.fullmatch(text) or TIME_RE.fullmatch(text.removesuffix("."))) is not None:
        message = ""
    else:
        ending = re.search(rf"\s+(?P<schedule>{SCHEDULE})(?:\s+{ZONE})?[.]?$", text, re.I)
        if not ending:
            raise ReminderError(HELP)
        when = TIME_RE.fullmatch(ending[0].strip().removesuffix(".")) or TIME_RE.fullmatch(ending[0].strip())
        message = re.sub(r"^(?:to|that|about)\s+", "", text[:ending.start()], flags=re.I)
        if re.match(rf"^(?:{DATE})\b", when["when"], re.I):
            message = re.sub(r"\s+on$", "", message, flags=re.I)
    # Schedule-only edits retain the previous text.
    try:
        schedule = _spec(when, now, default_zone)
    except ReminderError:
        raise
    except (ValueError, OverflowError, ZoneInfoNotFoundError) as exc:
        raise ReminderError("Use a valid date, time and timezone, such as tomorrow at 9 AM America/Creston.") from exc
    if len(message) > 3000:
        raise ReminderError("Keep the reminder text under 3,001 characters.")
    return {**schedule, "text": message.strip()}


def describe(reminder: dict) -> str:
    zone = ZoneInfo(reminder["timezone"])
    due = dt.datetime.fromisoformat(reminder["next_at"]).astimezone(zone)
    cadence = ""
    days = reminder["weekdays"]
    if days:
        label = "Daily" if len(days) == 7 else "Weekdays" if len(days) == 5 else calendar.day_name[days[0]] + "s"
        cadence = f"{label} at {due:%I:%M %p}. Next "
    clock = due.strftime("%I:%M:%S %p" if due.second else "%I:%M %p").lstrip("0")
    return f"{cadence}{due:%a, %b %d, %Y} at {clock} ({zone.key})"
