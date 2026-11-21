const selectAll = document.getElementById("select-all-companies");
const companies = [...document.querySelectorAll('input[name="company_ids"]:enabled')];
const lookupForm = document.getElementById("bulk-contact-lookup");
const lookupButton = lookupForm.querySelector("button");
const selectionCount = document.getElementById("company-selection-count");

function updateSelection() {
  const count = companies.filter((checkbox) => checkbox.checked).length;
  lookupButton.disabled = count === 0;
  selectAll.disabled = companies.length === 0;
  selectAll.checked = companies.length > 0 && count === companies.length;
  selectAll.indeterminate = count > 0 && count < companies.length;
  selectionCount.textContent = count + " selected";
}

selectAll.addEventListener("change", () => {
  companies.forEach((checkbox) => { checkbox.checked = selectAll.checked; });
  updateSelection();
});
companies.forEach((checkbox) => checkbox.addEventListener("change", updateSelection));
updateSelection();
lookupForm.addEventListener("submit", (event) => {
  if (lookupButton.disabled) {
    event.preventDefault();
    return;
  }
  lookupButton.disabled = true;
  lookupButton.textContent = "Looking up contacts...";
});

const draftFilter = document.getElementById("draft-status-filter");
function filterDrafts() {
  const draftRows = [...document.querySelectorAll("[data-draft-status]")];
  draftRows.forEach((row) => {
    row.hidden = Boolean(draftFilter.value && row.dataset.draftStatus !== draftFilter.value);
  });
  document.getElementById("draft-filter-empty").hidden = draftRows.some((row) => !row.hidden);
}
draftFilter.addEventListener("change", filterDrafts);
filterDrafts();

function showDraftVersion(dialog, index) {
  const panels = [...dialog.querySelectorAll("[data-draft-version]")];
  if (index < 0 || index >= panels.length) return;
  panels.forEach((panel, position) => { panel.hidden = position !== index; });
  dialog.querySelector('[data-version-step="-1"]').disabled = index === 0;
  dialog.querySelector('[data-version-step="1"]').disabled = index === panels.length - 1;
}

// Delegation also works for rows and controls refreshed from the server.
document.addEventListener("click", (event) => {
  const edit = event.target.closest("[data-edit-draft]");
  if (edit) {
    const form = edit.closest("article").querySelector("[data-email-edit]");
    form.hidden = !form.hidden;
    if (!form.hidden) form.querySelector('[name="subject"]').focus();
    return;
  }
  const step = event.target.closest("[data-version-step]");
  if (step) {
    const dialog = step.closest("dialog");
    const panels = [...dialog.querySelectorAll("[data-draft-version]")];
    showDraftVersion(dialog, panels.findIndex((panel) => !panel.hidden) + Number(step.dataset.versionStep));
    return;
  }
  const button = event.target.closest("[data-review-dialog]");
  if (!button) return;
  const dialog = document.getElementById(button.dataset.reviewDialog);
  showDraftVersion(dialog, dialog.querySelectorAll('[data-draft-version]').length - 1);
  dialog.showModal();
  dialog.addEventListener("close", () => {
    document.querySelector('[data-review-dialog="' + dialog.id + '"]')?.focus();
  }, {once: true});
});

let refreshTimer;
function scheduleDraftRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refreshDrafts, 2500);
}

function draftGenerationActive() {
  return document.querySelector('[data-draft-status="Generating"], [data-generation-progress]:not([hidden])');
}

async function refreshDrafts() {
  try {
    const response = await fetch("/dashboard/drafts", {cache: "no-store"});
    if (!response.ok) throw Error("Could not refresh draft status.");
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const incomingRows = page.getElementById("email-draft-rows");
    const incomingDialogs = page.getElementById("email-draft-dialogs");
    if (!incomingRows || !incomingDialogs) throw Error("Could not refresh draft status.");

    const dialogs = document.getElementById("email-draft-dialogs");
    const active = dialogs.querySelector("dialog[open]");
    if (active) {
      // Keep the same native dialog open, even when its draft advances to a new version.
      const replacement = incomingDialogs.querySelector("#" + active.id);
      if (replacement) {
        const previousLatest = active.querySelector("[data-draft-version]:last-child")?.dataset.draftVersion;
        const selected = active.querySelector("[data-draft-version]:not([hidden])")?.dataset.draftVersion;
        active.replaceChildren(...replacement.childNodes);
        const panels = [...active.querySelectorAll("[data-draft-version]")];
        // Keep historical browsing during polling, but open a newly generated version.
        if (panels.at(-1)?.dataset.draftVersion === previousLatest) {
          showDraftVersion(active, panels.findIndex((panel) => panel.dataset.draftVersion === selected));
        }
        replacement.remove();
      }
    }
    dialogs.querySelectorAll("dialog:not([open])").forEach((dialog) => dialog.remove());
    dialogs.append(...incomingDialogs.childNodes);
    document.getElementById("email-draft-rows").replaceChildren(...incomingRows.childNodes);
    filterDrafts();
  } catch {
    scheduleDraftRefresh();
    return;
  }
  if (draftGenerationActive()) scheduleDraftRefresh();
}

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!form.matches("[data-draft-generation]")) return;
  event.preventDefault();
  const button = form.querySelector("button");
  if (button.disabled) return;
  button.disabled = true;
  const progress = form.querySelector("[data-generation-progress]");
  const error = form.querySelector("[data-generation-error]");
  if (progress) progress.hidden = false;
  error.hidden = true;
  clearTimeout(refreshTimer);
  try {
    const jsonRequest = Boolean(progress);
    const response = await fetch(form.action, {
      method: "POST", body: new FormData(form),
      headers: jsonRequest ? {"Accept": "application/json"} : {},
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw Error(data.detail || "Could not start generation.");
    }
    await refreshDrafts();
  } catch (failure) {
    if (progress) progress.hidden = true;
    error.textContent = failure.message;
    error.hidden = false;
    button.disabled = false;
    if (draftGenerationActive()) scheduleDraftRefresh();
  }
});

if (draftGenerationActive()) scheduleDraftRefresh();

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!form.matches("[data-email-review]")) return;
  event.preventDefault();
  const dialog = form.closest("dialog");
  if (dialog.dataset.reviewBusy) return;
  dialog.dataset.reviewBusy = "true";
  const panel = form.closest("article");
  const buttons = [...panel.querySelectorAll("button:not(:disabled)")];
  buttons.forEach((button) => { button.disabled = true; });
  const progress = panel.querySelector("[data-review-progress]");
  const error = panel.querySelector("[data-review-error]");
  const approving = form.action.endsWith("/approve");
  progress.textContent = approving ? "Sending..." : "Saving...";
  progress.hidden = false;
  error.hidden = true;
  try {
    const response = await fetch(form.action, {
      method: "POST", body: new FormData(form), headers: {"Accept": "application/json"},
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || "The review action could not be completed.");
    if (["sent", "rejected"].includes(result.status)) dialog.close();
    await refreshDrafts();
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
    if (approving) {
      const retryNote = document.createElement("p");
      retryNote.textContent = "Refresh the draft status before attempting another send.";
      error.append(retryNote);
    }
  } finally {
    delete dialog.dataset.reviewBusy;
    progress.hidden = true;
    buttons.forEach((button) => {
      if (!approving || !button.closest('form[action$="/approve"]') || error.hidden) button.disabled = false;
    });
  }
});
