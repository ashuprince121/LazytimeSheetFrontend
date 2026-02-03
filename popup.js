// popup.js — FINAL CANONICAL (WITH WAKE-UP, FRONTEND RECURRING INSERT)

const BACKEND_URL = "https://lazy-timesheet-backend.onrender.com";

// ---------------- WAKE BACKEND (LIGHT GET) ----------------
async function wakeBackend() {
  try {
    await fetch(`${BACKEND_URL}/docs`, { method: "GET" });
  } catch (e) {
    // ignore — backend may already be awake
  }
}

// ---------------- DOM ----------------
const loader = document.getElementById("loader");
const output = document.getElementById("output");

const recurringBox = document.getElementById("recurringBox");
const recurringList = document.getElementById("recurringList");
const applyRecurringBtn = document.getElementById("applyRecurring");
const pushAllBox = document.getElementById("pushAllBox");

const greeting = document.getElementById("greeting");
const nameCard = document.getElementById("nameCard");
const inputCard = document.getElementById("inputCard");
const changeCalendarBtn = document.getElementById("changeCalendar");

const icsSection = document.getElementById("icsSection");

// ---------------- STATE ----------------
let cachedIcsUrl = null;
let currentSummary = {};      // day -> text
let recurringCache = {};      // uid -> recurring object

// ---------------- NAME ----------------
const storedName = localStorage.getItem("lt_name");
if (storedName) {
  greeting.textContent = "Hello " + storedName;
  nameCard.style.display = "none";
  inputCard.style.display = "flex";
}

document.getElementById("saveName").onclick = () => {
  const name = document.getElementById("userName").value.trim();
  if (!name) return;

  localStorage.setItem("lt_name", name);
  greeting.textContent = "Hello " + name;
  nameCard.style.display = "none";
  inputCard.style.display = "flex";
};

// ---------------- RESTORE ICS ----------------
chrome.storage.local.get(["lt_ics_url"], res => {
  if (res.lt_ics_url) {
    cachedIcsUrl = res.lt_ics_url;
    if (icsSection) icsSection.style.display = "none";
    if (changeCalendarBtn) changeCalendarBtn.style.display = "block";
  }
});

// ---------------- CHANGE CALENDAR ----------------
changeCalendarBtn.onclick = () => {
  if (!confirm("Disconnect current calendar?")) return;
  chrome.storage.local.remove("lt_ics_url", () => location.reload());
};

// ---------------- HELPERS ----------------
function isSunday(dateStr) {
  return new Date(dateStr).getDay() === 0;
}

function normalizeDay(day) {
  return day.slice(0, 2).toUpperCase();
}

function toBullets(text) {
  if (!text) return "";
  const parts = text.replace(/^Attended\s*/i, "").split(",");
  let out = "Meetings:\n";
  parts.forEach(p => {
    if (p.trim()) out += "- " + p.trim() + "\n";
  });
  return out.trim();
}

// ---------------- GENERATE ----------------
document.getElementById("generate").onclick = async () => {
  try {
    loader.style.display = "block";
    loader.innerText = "Waking up server… (first time may take ~30s)";
    output.innerHTML = "";
    recurringBox.style.display = "none";
    pushAllBox.style.display = "none";

    // 👇 force UI paint so text is visible
    await new Promise(resolve => setTimeout(resolve, 0));

    // 👇 wake Render free tier
    await wakeBackend();

    loader.innerText = "Processing…";

    const sunday = document.getElementById("weekSunday").value;
    if (!sunday) throw new Error("Please select Sunday.");
    if (!isSunday(sunday) && !confirm("Selected date is not Sunday. Continue?")) {
      loader.style.display = "none";
      return;
    }

    const fd = new FormData();
    fd.append("week_sunday", sunday);
    fd.append("finalize", "false");

    if (cachedIcsUrl) {
      fd.append("ics_url", cachedIcsUrl);
    } else {
      const url = document.getElementById("icsUrl").value.trim();
      const file = document.getElementById("icsFile").files[0];

      if (!url && !file) throw new Error("Provide ICS link or upload file.");
      if (url && file) throw new Error("Use only one option.");

      if (url) {
        fd.append("ics_url", url);
        cachedIcsUrl = url;
        chrome.storage.local.set({ lt_ics_url: url });
        if (icsSection) icsSection.style.display = "none";
        changeCalendarBtn.style.display = "block";
      } else {
        fd.append("file", file);
      }
    }

    const res = await fetch(`${BACKEND_URL}/generate-timesheet-v2`, {
      method: "POST",
      body: fd
    });

    if (!res.ok) {
      const t = await res.text();
      throw new Error("Backend error: " + t);
    }

    const data = await res.json();

    currentSummary = structuredClone(data.timesheet_summary);
    renderDays(currentSummary);

    if (data.recurring_candidates?.length) {
      recurringCache = {};
      data.recurring_candidates.forEach(r => {
        recurringCache[r.uid] = r;
      });
      renderRecurring(data.recurring_candidates);
    }

  } catch (e) {
    alert(e.message);
  } finally {
    loader.style.display = "none";
    loader.innerText = "Processing…";
  }
};

// ---------------- RENDER RECURRING ----------------
function renderRecurring(list) {
  recurringBox.style.display = "block";
  recurringList.innerHTML = "";

  list.forEach(item => {
    const row = document.createElement("div");
    row.className = "recurring-item";
    row.innerHTML = `
      <input type="checkbox" data-uid="${item.uid}">
      <div class="recurring-text">
        <div class="summary">${item.summary}</div>
        <div class="recurrence">${item.recurrence_text}</div>
      </div>
    `;
    recurringList.appendChild(row);
  });
}

// ---------------- APPLY RECURRING (FRONTEND ONLY) ----------------
applyRecurringBtn.onclick = () => {
  const selected = Array.from(
    recurringList.querySelectorAll("input:checked")
  ).map(c => c.dataset.uid);

  if (!selected.length) return;

  selected.forEach(uid => {
    const rec = recurringCache[uid];
    if (!rec || !Array.isArray(rec.byday)) {
      alert("Recurring data missing. Please regenerate timesheet.");
      return;
    }

    Object.keys(currentSummary).forEach(day => {
      const code = normalizeDay(day);
      if (rec.byday.includes(code)) {
        const existing = currentSummary[day] || "";
        if (!existing.includes(rec.summary)) {
          currentSummary[day] =
            (existing ? existing + ", " : "Attended ") + rec.summary;
        }
      }
    });

    const checkbox = recurringList.querySelector(`input[data-uid="${uid}"]`);
    if (checkbox) checkbox.disabled = true;
  });

  renderDays(currentSummary);
};

// ---------------- RENDER DAYS ----------------
function renderDays(summary) {
  output.innerHTML = "";
  let payload = {};

  for (const day in summary) {
    if (!summary[day]) continue;

    const text = toBullets(summary[day]);
    payload[day] = text;

    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <strong>${day}</strong>
      <pre>${text}</pre>
      <div class="button-row">
        <button class="secondary">📋 Copy</button>
        <button class="primary">🚀 Push</button>
      </div>
    `;

    const [copyBtn, pushBtn] = card.querySelectorAll("button");

    copyBtn.onclick = () => navigator.clipboard.writeText(text);
    pushBtn.onclick = () => {
      chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
        chrome.tabs.sendMessage(tabs[0].id, {
          type: "PUSH_TIMESHEET",
          payload: { [day]: text }
        });
      });
    };

    output.appendChild(card);
  }

  if (Object.keys(payload).length) {
    pushAllBox.style.display = "block";
    document.getElementById("pushAll").onclick = () => {
      chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
        chrome.tabs.sendMessage(tabs[0].id, {
          type: "PUSH_TIMESHEET",
          payload
        });
      });
    };
  }
}
