"use strict";

const form = document.querySelector("#scan-form");
const scanButton = document.querySelector("#scan-button");
const results = document.querySelector("#results");
const resultTitle = document.querySelector("#result-title");
const resultMessage = document.querySelector("#result-message");
const resultCounts = document.querySelector("#result-counts");
const resultFiles = document.querySelector("#result-files");
const resultOutput = document.querySelector("#result-output");
const progressTrack = document.querySelector("#progress-track");

let pollingTimer = null;

function renderStatus(state) {
  const labels = {
    idle: ["Ready to scan", "Enter a source and a separate destination to begin."],
    running: ["Scanning in progress", "The source is being scanned in read-only mode. Large devices may take a while."],
    completed: ["Scan complete", state.total ? `Found ${state.total} recoverable file${state.total === 1 ? "" : "s"}.` : "No supported contiguous files were found."],
    failed: ["Scan could not finish", state.error || "An error occurred while scanning."],
  };
  const [title, message] = labels[state.status] || labels.failed;

  results.hidden = false;
  results.dataset.status = state.status;
  resultTitle.textContent = title;
  resultMessage.textContent = message;
  resultOutput.textContent = state.output ? `Saved to ${state.output}` : "";
  progressTrack.hidden = state.status !== "running";
  scanButton.disabled = state.status === "running";

  resultCounts.replaceChildren();
  for (const [kind, count] of Object.entries(state.counts || {})) {
    const chip = document.createElement("span");
    chip.className = "count-chip";
    chip.textContent = `${kind.toUpperCase()} · ${count}`;
    resultCounts.append(chip);
  }

  resultFiles.replaceChildren();
  for (const filename of state.files || []) {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = `/api/files/${encodeURIComponent(filename)}`;
    link.download = filename;
    const name = document.createElement("span");
    name.textContent = filename;
    const action = document.createElement("span");
    action.textContent = "Download ↓";
    link.append(name, action);
    item.append(link);
    resultFiles.append(item);
  }
}

async function refreshStatus() {
  const response = await fetch("/api/status", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Could not read scan status from the local service.");
  }
  const state = await response.json();
  renderStatus(state);
  if (state.status === "running") {
    pollingTimer = window.setTimeout(refreshStatus, 1500);
  } else {
    pollingTimer = null;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pollingTimer !== null) {
    window.clearTimeout(pollingTimer);
    pollingTimer = null;
  }

  const formData = new FormData(form);
  const payload = {
    source: formData.get("source"),
    output: formData.get("output"),
    max_file_size: Number(formData.get("max-file-size")),
    confirmed_separate_device: formData.get("confirm-device") === "on",
  };

  scanButton.disabled = true;
  renderStatus({ status: "running", counts: {}, files: [] });
  try {
    const response = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) {
      renderStatus({
        status: "failed",
        error: body.error || "The scan could not be started.",
        counts: {},
        files: [],
      });
      return;
    }
    await refreshStatus();
  } catch (error) {
    renderStatus({
      status: "failed",
      error: error instanceof Error ? error.message : "The local recovery service could not be reached.",
      counts: {},
      files: [],
    });
  } finally {
    scanButton.disabled = results.dataset.status === "running";
  }
});

refreshStatus().catch((error) => {
  renderStatus({
    status: "failed",
    error: error instanceof Error ? error.message : "The local recovery service could not be reached.",
    counts: {},
    files: [],
  });
});
