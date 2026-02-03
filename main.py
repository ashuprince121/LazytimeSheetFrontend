from fastapi import FastAPI, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from icalendar import Calendar
from datetime import datetime, timedelta, date
from urllib.parse import urlparse
import pytz
import requests

# -------------------------------
# Helpers
# -------------------------------

def describe_recurrence(rrule):
    freq = rrule.get("FREQ", [])
    byday = rrule.get("BYDAY", [])

    day_map = {
        "MO": "Monday",
        "TU": "Tuesday",
        "WE": "Wednesday",
        "TH": "Thursday",
        "FR": "Friday",
        "SA": "Saturday",
        "SU": "Sunday",
    }

    if freq and freq[0] == "DAILY":
        return "Occurs every day"

    if freq and freq[0] == "WEEKLY" and byday:
        days = [day_map[d] for d in byday if d in day_map]

        if days == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            return "Occurs every weekday"

        if len(days) == 1:
            return f"Occurs every {days[0]}"

        return "Occurs every " + ", ".join(days)

    return "Recurring meeting"


def get_week_range_from_sunday(sunday: date):
    monday = sunday + timedelta(days=1)
    friday = monday + timedelta(days=4)
    return monday, friday


# -------------------------------
# ICS Loader (PROD-SAFE)
# -------------------------------

ALLOWED_ICS_HOSTS = [
    "outlook.office365.com",
    "outlook.live.com",
    "office365.com",
]

async def load_ics_data(file: UploadFile | None, ics_url: str | None) -> bytes:
    if not file and not ics_url:
        raise HTTPException(400, "Provide ICS file or ICS link")

    if ics_url:
        parsed = urlparse(ics_url)

        if parsed.scheme != "https":
            raise HTTPException(400, "Only HTTPS ICS links allowed")

        if not parsed.path.endswith(".ics"):
            raise HTTPException(400, "Only .ics calendar links are supported")

        if not any(h in parsed.netloc for h in ALLOWED_ICS_HOSTS):
            raise HTTPException(400, "Only Outlook ICS links are supported")

        resp = requests.get(
            ics_url,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/calendar,*/*"
            }
        )

        if resp.status_code != 200:
            raise HTTPException(400, "Failed to download ICS file")

        content = resp.content
        if b"BEGIN:VCALENDAR" not in content.upper():
            raise HTTPException(400, "Invalid ICS content")

        return content

    return await file.read()


# -------------------------------
# App setup
# -------------------------------

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

LOCAL_TZ = pytz.timezone("Asia/Kolkata")

DAY_MAP = {
    "MO": "Monday",
    "TU": "Tuesday",
    "WE": "Wednesday",
    "TH": "Thursday",
    "FR": "Friday",
}

# ============================================================
# V2 ENDPOINT (PROD LOGIC, FIXED LOADER)
# ============================================================

@app.post("/generate-timesheet-v2")
async def generate_timesheet_v2(
    week_sunday: str = Form(...),
    file: UploadFile = Form(None),
    ics_url: str = Form(None),
    include_recurring_uids: str = Form(default=""),
    finalize: str = Form(default="false")
):
    sunday_date = datetime.strptime(week_sunday, "%Y-%m-%d").date()
    week_start, week_end = get_week_range_from_sunday(sunday_date)

    raw = await load_ics_data(file, ics_url)
    cal = Calendar.from_ical(raw)

    events_by_day = {
        "Monday": [],
        "Tuesday": [],
        "Wednesday": [],
        "Thursday": [],
        "Friday": []
    }

    recurring_candidates = {}

    for component in cal.walk("VEVENT"):
        summary = str(component.get("SUMMARY", "")).strip()
        if not summary or summary.lower().startswith("canceled"):
            continue

        uid = str(component.get("UID", "")).strip()
        rrule = component.get("RRULE")

        dtstart_raw = component.get("DTSTART")
        if not dtstart_raw:
            continue

        dt_val = dtstart_raw.dt
        if isinstance(dt_val, datetime):
            dt = dt_val.astimezone(LOCAL_TZ) if dt_val.tzinfo else LOCAL_TZ.localize(dt_val)
            event_date = dt.date()
        else:
            event_date = dt_val
            dt = LOCAL_TZ.localize(datetime.combine(dt_val, datetime.min.time()))

        if week_start <= event_date <= week_end:
            day_name = dt.strftime("%A")
            if day_name in events_by_day:
                if summary not in events_by_day[day_name]:
                    events_by_day[day_name].append(summary)

        if rrule and uid:
            until_vals = rrule.get("UNTIL", [])
            until_date = (
                until_vals[0].date()
                if until_vals and isinstance(until_vals[0], datetime)
                else until_vals[0] if until_vals else None
            )

            if event_date > week_end or (until_date and until_date < week_start):
                continue

            exdates = set()
            ex_prop = component.get("EXDATE")
            if ex_prop:
                for d in ex_prop.dts:
                    ex_dt = d.dt
                    exdates.add(ex_dt.date() if isinstance(ex_dt, datetime) else ex_dt)

            recurring_candidates[uid] = {
                "uid": uid,
                "summary": summary,
                "byday": rrule.get("BYDAY", []),
                "exdates": exdates,
                "recurrence_text": describe_recurrence(rrule)
            }

    # Remove recurring only if fully covered
    def fully_covered(summary, byday):
        covered = set()
        for day, meetings in events_by_day.items():
            if summary in meetings:
                covered.add(day[:2].upper())
        return set(byday) <= covered

    for uid in list(recurring_candidates.keys()):
        rec = recurring_candidates[uid]
        if rec["byday"] and fully_covered(rec["summary"], rec["byday"]):
            recurring_candidates.pop(uid)

    # Apply selected recurring
    if include_recurring_uids:
        for uid in include_recurring_uids.split("||"):
            rec = recurring_candidates.get(uid)
            if not rec:
                continue

            for offset in range(5):
                current_date = week_start + timedelta(days=offset)
                weekday = current_date.strftime("%A")
                if weekday[:2].upper() in rec["byday"]:
                    if current_date not in rec["exdates"]:
                        if rec["summary"] not in events_by_day[weekday]:
                            events_by_day[weekday].append(rec["summary"])

    response = {
        "week_range": f"{week_start} to {week_end}",
        "timesheet_summary": {
            day: ("Attended " + ", ".join(meetings) if meetings else "")
            for day, meetings in events_by_day.items()
        }
    }

    if finalize.lower() != "true":
        response["recurring_candidates"] = [
            {
                "uid": v["uid"],
                "summary": v["summary"],
                "recurrence_text": v["recurrence_text"],
                "byday": v["byday"]
            }
            for v in recurring_candidates.values()
        ]

    return response
