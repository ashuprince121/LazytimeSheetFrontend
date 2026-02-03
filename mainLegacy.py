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
# ICS Loader (File OR URL)
# -------------------------------

ALLOWED_ICS_HOSTS = [
    "outlook.office365.com",
    "outlook.live.com",
    "office365.com",
]

async def load_ics_data(file: UploadFile | None, ics_url: str | None) -> bytes:
    if not file and not ics_url:
        raise HTTPException(400, "Provide ICS file or ICS link")

    # ---- Server-side ICS fetch (NO disk, NO CORS)
    if ics_url:
        parsed = urlparse(ics_url)

        if parsed.scheme != "https":
            raise HTTPException(400, "Only HTTPS ICS links allowed")

        if not parsed.path.endswith(".ics"):
            raise HTTPException(400, "Only .ics calendar links are supported")

        if not any(h in parsed.netloc for h in ALLOWED_ICS_HOSTS):
            raise HTTPException(400, "Only Outlook ICS links are supported")

        resp = requests.get(ics_url, timeout=20)
        if resp.status_code != 200:
            raise HTTPException(400, "Failed to download ICS file")

        if "text/calendar" not in resp.headers.get("content-type", ""):
            raise HTTPException(400, "Invalid ICS content")

        return resp.content

    # ---- Upload path (existing behavior)
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
# V1 ENDPOINT (UNCHANGED / LEGACY)
# ============================================================

@app.post("/generate-timesheet")
async def generate_timesheet(
    week_sunday: str = Form(...),
    file: UploadFile = Form(...),
    include_recurring: str = Form(default=""),
    finalize: str = Form(default="false")
):
    sunday_date = datetime.strptime(week_sunday, "%Y-%m-%d").date()
    week_start, week_end = get_week_range_from_sunday(sunday_date)

    raw = await file.read()
    cal = Calendar.from_ical(raw)

    events_by_day = {
        "Monday": [],
        "Tuesday": [],
        "Wednesday": [],
        "Thursday": [],
        "Friday": []
    }

    recurring_candidates = {}

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        summary = str(component.get("SUMMARY", "")).strip()
        if summary.lower().startswith("canceled"):
            continue

        rrule = component.get("RRULE")

        if rrule:
            dtstart_raw = component.get("DTSTART")
            if not dtstart_raw:
                continue

            dtstart_val = dtstart_raw.dt
            start_date = dtstart_val.date() if isinstance(dtstart_val, datetime) else dtstart_val

            until_vals = rrule.get("UNTIL", [])
            until_date = (
                until_vals[0].date()
                if until_vals and isinstance(until_vals[0], datetime)
                else until_vals[0] if until_vals else None
            )

            if start_date <= week_end and (not until_date or until_date >= week_start):
                recurring_candidates[summary] = {
                    "summary": summary,
                    "byday": rrule.get("BYDAY", [])
                }

        dtstart = component.get("DTSTART").dt
        if isinstance(dtstart, datetime):
            dtstart = dtstart.astimezone(LOCAL_TZ) if dtstart.tzinfo else LOCAL_TZ.localize(dtstart)
        else:
            dtstart = LOCAL_TZ.localize(datetime.combine(dtstart, datetime.min.time()))

        event_date = dtstart.date()
        if week_start <= event_date <= week_end:
            day_name = event_date.strftime("%A")
            if day_name in events_by_day:
                events_by_day[day_name].append(summary)

    used = {m for v in events_by_day.values() for m in v}
    for s in list(recurring_candidates.keys()):
        if s in used:
            recurring_candidates.pop(s)

    if include_recurring:
        approved = [s.strip() for s in include_recurring.split("||")]
        for meeting in approved:
            rec = recurring_candidates.get(meeting)
            if rec and rec["byday"]:
                for d in rec["byday"]:
                    day = DAY_MAP.get(d)
                    if day:
                        events_by_day[day].append(meeting)

    result = {
        day: ("Attended " + ", ".join(m) if m else "")
        for day, m in events_by_day.items()
    }

    response = {
        "week_range": f"{week_start} to {week_end}",
        "timesheet_summary": result
    }

    if finalize.lower() != "true":
        response["recurring_candidates"] = [{"summary": v["summary"]} for v in recurring_candidates.values()]

    return response


# ============================================================
# V2 ENDPOINT (CORRECT / SMART / FUTURE-PROOF)
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

    events_by_day = {d: [] for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]}
    recurring_candidates = {}

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        summary = str(component.get("SUMMARY", "")).strip()
        if summary.lower().startswith("canceled"):
            continue

        uid = str(component.get("UID", "")).strip()
        rrule = component.get("RRULE")

        dtstart_raw = component.get("DTSTART")
        if not dtstart_raw:
            continue

        dtstart_val = dtstart_raw.dt
        if isinstance(dtstart_val, datetime):
            dt = dtstart_val.astimezone(LOCAL_TZ) if dtstart_val.tzinfo else LOCAL_TZ.localize(dtstart_val)
            event_date = dt.date()
        else:
            event_date = dtstart_val

        if week_start <= event_date <= week_end:
            day_name = event_date.strftime("%A")
            if day_name in events_by_day:
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

    used = {m for v in events_by_day.values() for m in v}
    for uid in list(recurring_candidates.keys()):
        if recurring_candidates[uid]["summary"] in used:
            recurring_candidates.pop(uid)

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
                        events_by_day[weekday].append(rec["summary"])

    result = {
        day: ("Attended " + ", ".join(m) if m else "")
        for day, m in events_by_day.items()
    }

    response = {
        "week_range": f"{week_start} to {week_end}",
        "timesheet_summary": result
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

