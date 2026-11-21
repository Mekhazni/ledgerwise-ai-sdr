"""Build scheduling drafts from Cal.com data. Booking never sends through Gmail."""
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import phonenumbers

from ai_reply import DISCLOSURE, HUMAN_REQUEST, INJECTION
from gmail_inbox import clear_opt_out, current_reply_text


def instant(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Cal.com returned a time without a timezone.")
    return result.astimezone(timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def reply(body, reason, slots=()):
    return {"action": "scheduling_interest", "intent": "meeting_scheduling", "reason": reason,
            "body": f"Hi,\n\n{body}\n\nBest,\nLedgerwise Team", "needs_human": False,
            "offered_slots": list(slots), "facts": []}


def handoff(reason):
    return {"action": "human_assistance", "intent": "meeting_scheduling",
            "reason": reason, "body": "", "needs_human": True, "facts": []}


def slot_label(slot):
    local = instant(slot["start"]).astimezone(ZoneInfo(slot["zone"]))
    return local.strftime("%a, %b %d, %Y at %I:%M %p %Z")


def offer_reply(slots):
    slots = [{**slot, "option": index} for index, slot in enumerate(slots, 1)]
    choices = "\n".join(f"Option {slot['option']}: {slot_label(slot)}" for slot in slots)
    return reply(f"Here are a few times for a brief call:\n{choices}\n\n"
                 "Please reply with the option number you confirm.",
                 "Cal.com availability checked. This offer requires approval.", slots)


class Calcom:
    def __init__(self, client):
        self.client = client
        self.key = os.environ["CALCOM_API_KEY"].strip()
        self.event_type = int(os.environ["CALCOM_EVENT_TYPE_ID"])
        self.zone = str(ZoneInfo(os.environ["CALCOM_TIME_ZONE"]))
        if not self.key or self.event_type < 1:
            raise ValueError("Configure the Cal.com event and API key.")

    async def request(self, method, endpoint, version, **kwargs):
        response = await self.client.request(method, f"https://api.cal.com/v2/{endpoint}",
            headers={"Authorization": f"Bearer {self.key}", "cal-api-version": version}, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise ValueError("Cal.com did not confirm the request.")
        return payload["data"]

    async def slots(self, start, end):
        data = await self.request("GET", "slots", "2024-09-04", params={
            "eventTypeId": self.event_type, "start": stamp(start), "end": stamp(end), "timeZone": self.zone,
        })
        times = {instant(item["start"] if isinstance(item, dict) else item)
                 for items in data.values() for item in items}
        return [{"start": stamp(time), "zone": self.zone, "option": index}
                for index, time in enumerate(sorted(t for t in times if start <= t <= end), 1)]

    async def offer(self, *, next_week=False):
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=10)
        if next_week:
            local = start.astimezone(ZoneInfo(self.zone))
            monday = (local + timedelta(days=7 - local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            start = monday.astimezone(timezone.utc)
            end = (monday + timedelta(days=7)).astimezone(timezone.utc)
        slots = [slot for slot in await self.slots(start, end) if start <= instant(slot["start"]) < end][:3]
        if not slots:
            period = "next week" if next_week else "the next 10 days"
            return handoff(f"Cal.com returned no available slots for {period}.")
        return offer_reply(slots)

    async def propose(self, start):
        now = datetime.now(timezone.utc)
        if start < now:
            return await self.offer()
        slots = await self.slots(max(now, start - timedelta(days=2)), start + timedelta(days=3))
        exact = next((slot for slot in slots if instant(slot["start"]) == start), None)
        if exact:
            slot = {**exact, "option": 1}
            return reply(f"{slot_label(slot)} is available. Please reply with Option 1 to confirm if you want to book it.",
                         "Cal.com verified the requested slot. It has not been booked.", [slot])
        nearby = sorted(slots, key=lambda slot: abs(instant(slot["start"]) - start))[:3]
        if not nearby:
            return handoff("Cal.com returned no nearby slots. A reviewer can follow up with the prospect.")
        choices = [{**slot, "option": n} for n, slot in enumerate(nearby, 1)]
        lines = "\n".join(f"Option {slot['option']}: {slot_label(slot)}" for slot in choices)
        return reply(f"The requested time is unavailable. Here are a few alternatives:\n{lines}\n\n"
                     "Please reply with the option number you confirm.",
                     "Cal.com checked the requested time and returned nearby alternatives.", choices)

    async def book(self, slot, email, name, attempt):
        if os.getenv("CALCOM_BOOKING_ENABLED", "").lower() != "true":
            raise ValueError("Cal.com booking is disabled until notifications are configured.")
        # Keep the phone internal. The test event must have no SMS workflow.
        booking = await self.request("POST", "bookings", "2024-08-13", json={
            "eventTypeId": self.event_type, "start": slot["start"],
            "attendee": {"name": name, "email": email, "timeZone": slot["zone"]},
            "metadata": {"ledgerwiseAttempt": str(attempt)}, "leadEnrichmentOptOut": True,
        })
        if (not isinstance(booking.get("uid"), str) or not booking["uid"]
                or booking.get("status") != "accepted" or instant(booking["start"]) != instant(slot["start"])):
            raise ValueError("Cal.com did not confirm the requested time.")
        return booking


def acceptance(text, offered):
    """Accept a single explicit option, with optional contact information."""
    text = current_reply_text(text)
    if clear_opt_out(text) or HUMAN_REQUEST.search(text) or INJECTION.search(text) or DISCLOSURE.search(text):
        return None, None
    text = re.sub(r"^(?:hi|hello),?\s*\n+", "", text, flags=re.I)
    text = re.sub(r"\n\s*(?:best|thanks),\s*\n[A-Za-z]+(?:[ '-][A-Za-z]+){0,2}$", "", text, flags=re.I)
    text = " ".join(text.split())
    text = re.sub(r"[.!]\s+(?:please book (?:it|that (?:time|slot))|go ahead and book it)[.!]?$",
                  "", text, flags=re.I)
    match = re.fullmatch(
        r"(?:(?:i (?:confirm|choose|accept) |let.s book )?option\s+([1-3])"
        r"(?:\s+(?:works(?: for me)?|please))?[.!]?)?"
        r"(?:[\s,.]*(?:(?:my )?phone(?: number)?(?: is|:)?\s*)?(\+?[0-9(][0-9() .-]{7,23}))?",
        text.strip(), re.I,
    )
    if not match:
        confirmed = re.fullmatch(r"(?:i (?:confirm|accept) |let's book )(.+)", text.strip(), re.I)
        if not confirmed:
            return None, None
        start = proposed_time(confirmed.group(1))
        matching = [slot for slot in offered if instant(slot["start"]) == start]
        return (matching[0], None) if len(matching) == 1 else (None, None)
    option, raw_phone = match.groups()
    phone = None
    if raw_phone:
        try:
            number = phonenumbers.parse(raw_phone.rstrip(" ."), "US")
            if phonenumbers.is_valid_number(number):
                phone = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            pass
    slot = next((item for item in offered if str(item["option"]) == option), None)
    return (slot, phone) if slot else (None, None)


PROPOSAL = re.compile(
    r"(?:(?:can|could|shall) we (?:meet|talk|schedule(?: a call)?)(?: on)?\s+|how about\s+|"
    r"does\s+)?(?P<date>\d{4}-\d{2}-\d{2}|[A-Za-z]+ \d{1,2},? \d{4})"
    r"(?: at|,)?\s+(?P<time>\d{1,2}(?::\d{2})?\s*(?:am|pm)?)[ ]+"
    r"(?P<zone>UTC|GMT|[ECPM]T|[ECPM][DS]T|[A-Za-z_]+(?:/[A-Za-z_+-]+)+)"
    r"(?: work(?: for you)?)?[.!?]?", re.I)
TIMING_HINT = re.compile(
    r"\b(?:option\s+\d+|\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|next (?:week|month)|at \d{1,2}|noon|morning|afternoon|evening)\b", re.I)
ZONES = {"ET": "America/New_York", "CT": "America/Chicago", "MT": "America/Denver",
         "PT": "America/Los_Angeles", "GMT": "UTC"}


def proposed_time(text):
    match = PROPOSAL.fullmatch(text.strip())
    if not match:
        return None
    date, time, zone = match.group("date", "time", "zone")
    time = re.sub(r"\s+", "", time).upper()
    try:
        if date[0].isdigit():
            day = datetime.strptime(date, "%Y-%m-%d")
        else:
            date = date.replace(",", "")
            date_format = "%b %d %Y" if len(date.split()[0]) == 3 else "%B %d %Y"
            day = datetime.strptime(date, date_format)
        if time.endswith(("AM", "PM")):
            clock = datetime.strptime(time, "%I:%M%p" if ":" in time else "%I%p")
        elif re.fullmatch(r"\d{2}:\d{2}", time):
            clock = datetime.strptime(time, "%H:%M")
        else:
            return None
        shorthand = zone.upper()
        if shorthand in {"EST", "EDT", "CST", "CDT", "MST", "MDT", "PST", "PDT"}:
            zone_name = ZONES[shorthand[0] + "T"]
        else:
            zone_name = ZONES.get(shorthand, "UTC" if shorthand == "UTC" else zone)
        local = day.replace(hour=clock.hour, minute=clock.minute, tzinfo=ZoneInfo(zone_name))
        if local.utcoffset() != local.replace(fold=1).utcoffset():
            return None
        if len(shorthand) == 3 and shorthand.endswith(("ST", "DT")) and local.tzname() != shorthand:
            return None
        return local.astimezone(timezone.utc)
    except (ValueError, KeyError):
        return None


def scheduling_request(text, has_offer):
    """Identify timing proposals that require a lookup or a clarification draft."""
    text = current_reply_text(text)
    if clear_opt_out(text) or HUMAN_REQUEST.search(text) or INJECTION.search(text) or DISCLOSURE.search(text):
        return None
    text = re.sub(r"^(?:hi|hello),?\s*\n+", "", text, flags=re.I)
    text = re.sub(r"\n\s*(?:best|thanks),\s*\n[A-Za-z]+(?:[ '-][A-Za-z]+){0,2}$", "", text, flags=re.I)
    # Classify the Gmail text without treating line wrapping as sentence structure.
    text = " ".join(text.split())
    start = proposed_time(text)
    if start:
        return start
    if re.search(r"(?:^|[.!?]\s+)(?:(?:could|can|shall) we |(?:i.d|i would) like to )"
                 r"(?:schedule (?:a|the) call|set up a call|meet|talk)(?: with ledgerwise)? next week"
                 r"(?:[.!?] (?:could|can|would) you share (?:a few |some )?available (?:time )?slots)?[.!?]?$",
                 text, re.I):
        return "next_week"
    if (has_offer and (re.fullmatch(r"(?:yes|sure|that works)[.!]?", text.strip(), re.I) or
                       TIMING_HINT.search(text))) or (TIMING_HINT.search(text) and re.search(
                           r"\b(?:meet|meeting|call|schedule|how about|available)\b", text, re.I)):
        return "clarify"
    return None
