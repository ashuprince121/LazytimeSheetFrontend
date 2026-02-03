// content.js — UPDATED FIELD IDS (FULL COPY-PASTE)

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type !== "PUSH_TIMESHEET") return;

  const FIELD_MAP = {
    "Monday": "sp_formfield_u_mon_notes",
    "Tuesday": "sp_formfield_u_tue_notes",
    "Wednesday": "sp_formfield_u_wed_notes",
    "Thursday": "sp_formfield_u_thu_notes",
    "Friday": "sp_formfield_u_fri_notes"
  };

  const payload = request.payload;

  for (const day in payload) {
    const fieldId = FIELD_MAP[day];
    if (!fieldId) continue;

    const el = document.getElementById(fieldId);
    if (!el) continue;

    el.focus();
    el.value = payload[day];

    // Trigger ServiceNow / Angular change detection
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.blur();
  }

  sendResponse({ status: "ok" });
});
