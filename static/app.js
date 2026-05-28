const logoutButton = document.querySelector("#logoutButton");
const userEmail = document.querySelector("#userEmail");
const authStatus = document.querySelector("#authStatus");
const libraryList = document.querySelector("#libraryList");

const separationForm = document.querySelector("#uploadForm");
const analysisForm = document.querySelector("#analysisForm");
const separationButton = document.querySelector("#submitButton");
const analysisButton = document.querySelector("#analyzeButton");

const tabSeparation = document.querySelector("#tabSeparation");
const tabAnalysis = document.querySelector("#tabAnalysis");
const panelSeparation = document.querySelector("#panelSeparation");
const panelAnalysis = document.querySelector("#panelAnalysis");

const statusLabel = document.querySelector("#status");
const stageLabel = document.querySelector("#stage");
const progressBar = document.querySelector("#progressBar");
const logsBox = document.querySelector("#logs");
const resultsBox = document.querySelector("#results");
const currentTrackLabel = document.querySelector("#currentTrackLabel");

const analysisMeta = document.querySelector("#analysisMeta");
const analysisKeyValue = document.querySelector("#analysisKeyValue");
const analysisTempoValue = document.querySelector("#analysisTempoValue");
const measureMeta = document.querySelector("#measureMeta");
const measureSheetBox = document.querySelector("#measureSheet");
const analysisPlayerDock = document.querySelector("#analysisPlayerDock");
const analysisPlayerPlaceholder = document.querySelector("#analysisPlayerPlaceholder");

let pollHandle = null;
let currentUser = null;
let currentTrackId = null;
let currentJobId = null;
let currentTab = "separation";
let libraryTracks = [];
let analysisAudio = null;

function formatNumber(value, digits, fallback = "--") {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : fallback;
}

function formatTempoHint(context) {
  if (!context) {
    return "";
  }
  const meter = Number(context.detected_meter);
  if (Number.isFinite(meter)) {
    return `Detected meter: ${meter}/4.`;
  }
  return "";
}

function setStatus(message) {
  statusLabel.textContent = message;
}

function setAuthStatus(message) {
  authStatus.textContent = message;
}

function setActiveTab(tab) {
  currentTab = tab;
  tabSeparation.classList.toggle("active", tab === "separation");
  tabAnalysis.classList.toggle("active", tab === "analysis");
  panelSeparation.classList.toggle("active", tab === "separation");
  panelAnalysis.classList.toggle("active", tab === "analysis");
}

function resetSeparationView() {
  currentTrackLabel.textContent = "No track selected";
  stageLabel.textContent = "Idle";
  progressBar.style.width = "0%";
  logsBox.textContent = "Open or create a saved track.";
  resultsBox.className = "results empty";
  resultsBox.textContent = "No output yet.";
  setStatus("Ready.");
}

function resetAnalysisView() {
  analysisMeta.textContent = "Available after analysis.";
  analysisKeyValue.textContent = "--";
  analysisTempoValue.textContent = "--";
  measureMeta.textContent = "Grouped by measure";
  analysisPlayerDock.className = "analysis-player-dock empty";
  analysisPlayerDock.textContent = "Load an analysis track to audition it bar by bar.";
  analysisPlayerPlaceholder.textContent = "No Chord data yet.";
  measureSheetBox.className = "measure-sheet empty";
  measureSheetBox.textContent = "";
  analysisAudio = null;
}

function updateAuthUi() {
  if (currentUser) {
    userEmail.textContent = currentUser.email;
    setAuthStatus("");
    separationButton.disabled = false;
    analysisButton.disabled = false;
  } else {
    window.location.href = "/auth";
  }
}

function renderLibrary() {
  if (!currentUser) {
    libraryList.className = "library-list empty";
    libraryList.textContent = "Sign in to see saved tracks.";
    return;
  }
  if (!libraryTracks.length) {
    libraryList.className = "library-list empty";
    libraryList.textContent = "No saved tracks yet.";
    return;
  }

  libraryList.className = "library-list";
  libraryList.innerHTML = "";
  libraryTracks.forEach((track) => {
    const card = document.createElement("article");
    card.className = `library-item library-kind-${track.kind}${track.id === currentTrackId ? " active" : ""}`;
    const progressPercent = Math.max(0, Math.min(100, Math.round((track.progress || 0) * 100)));
    const kindLabel = track.kind === "analysis" ? "Chord Finder" : "Stem Separation";
    card.innerHTML = `
      <button type="button" class="library-open">
        <span class="library-chip">${kindLabel}</span>
        <span class="library-title">${track.title}</span>
        <span class="library-meta">${track.status} | ${new Date(track.updated_at).toLocaleString()}</span>
      </button>
      <div class="library-progress-track">
        <div class="library-progress-bar" style="width: ${progressPercent}%"></div>
      </div>
      <div class="library-footer">
        <span class="library-stage">${track.stage} | ${progressPercent}%</span>
        <button type="button" class="library-delete">Delete</button>
      </div>
    `;
    card.querySelector(".library-open").addEventListener("click", () => loadTrack(track.id));
    card.querySelector(".library-delete").addEventListener("click", (event) => {
      event.stopPropagation();
      deleteTrack(track.id);
    });
    libraryList.append(card);
  });
}

async function deleteTrack(trackId) {
  if (!window.confirm("Delete this saved track and all generated files?")) {
    return;
  }
  try {
    const response = await fetch(`/api/tracks/${trackId}`, { method: "DELETE" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Delete failed.");
    }
    if (currentTrackId === trackId) {
      currentTrackId = null;
      currentJobId = null;
      if (pollHandle) {
        clearInterval(pollHandle);
        pollHandle = null;
      }
      resetSeparationView();
      resetAnalysisView();
    }
    await refreshLibrary();
    setStatus("Saved track deleted.");
  } catch (error) {
    console.error(error);
    setStatus(error.message || "Delete failed.");
  }
}

function renderResults(track) {
  if (track.kind === "analysis") {
    return;
  }
  const entries = Object.entries(track.result_files || {});
  if (!entries.length) {
    resultsBox.className = "results empty";
    resultsBox.textContent = "No output yet.";
    return;
  }
  resultsBox.className = "results";
  resultsBox.innerHTML = "";
  entries.forEach(([label, filename]) => {
    const card = document.createElement("article");
    card.className = "result-card";
    card.innerHTML = `
      <p class="result-title">${label.replaceAll("_", " ")}</p>
      <audio controls preload="none" src="/downloads/tracks/${track.id}/${filename}"></audio>
      <a class="download-link" href="/downloads/tracks/${track.id}/${filename}">Download ${filename}</a>
    `;
    resultsBox.append(card);
  });
}

function clearMeasureHighlight() {
  measureSheetBox.querySelectorAll(".measure-row.active").forEach((row) => row.classList.remove("active"));
}

function updateMeasureHighlight(currentTime) {
  if (!Number.isFinite(currentTime)) {
    clearMeasureHighlight();
    return;
  }
  const rows = measureSheetBox.querySelectorAll(".measure-row");
  let activeFound = false;
  rows.forEach((row) => {
    const start = Number(row.dataset.start);
    const end = Number(row.dataset.end);
    const active = Number.isFinite(start) && Number.isFinite(end) && currentTime >= start && currentTime < end;
    row.classList.toggle("active", active);
    if (active) {
      activeFound = true;
    }
  });
  if (!activeFound && rows.length) {
    rows[rows.length - 1].classList.toggle("active", currentTime >= Number(rows[rows.length - 1].dataset.start || 0));
  }
}

function renderAnalysisPlayer(track, analysis) {
  const sourceFilename = track.result_files?.analysis_source;
  if (!sourceFilename || !analysis) {
    analysisPlayerDock.className = "analysis-player-dock empty";
    analysisPlayerDock.textContent = "No audio source available for this analysis track.";
    analysisPlayerPlaceholder.textContent = "No audio source available for this analysis track.";
    analysisAudio = null;
    return;
  }
  analysisPlayerDock.className = "analysis-player-dock";
  analysisPlayerDock.innerHTML = `
    <div class="analysis-player-head">
      <p class="label">Audio Player</p>
    </div>
    <audio id="analysisAudio" controls preload="metadata" src="/downloads/tracks/${track.id}/${sourceFilename}"></audio>
  `;
  analysisPlayerPlaceholder.textContent = "The player is pinned to the bottom of the screen while you scroll the chord sheet.";
  analysisAudio = analysisPlayerDock.querySelector("#analysisAudio");
  analysisAudio.addEventListener("timeupdate", () => updateMeasureHighlight(analysisAudio.currentTime));
  analysisAudio.addEventListener("seeked", () => updateMeasureHighlight(analysisAudio.currentTime));
  analysisAudio.addEventListener("ended", clearMeasureHighlight);
  analysisAudio.addEventListener("pause", () => updateMeasureHighlight(analysisAudio.currentTime));
}

function renderAnalysis(track) {
  const analysis = track?.analysis;
  if (!analysis || analysis.error) {
    analysisMeta.textContent = analysis?.error || "Available after analysis.";
    analysisKeyValue.textContent = "--";
    analysisTempoValue.textContent = "--";
    measureMeta.textContent = "Grouped by measure";
    analysisPlayerDock.className = "analysis-player-dock empty";
    analysisPlayerDock.textContent = "Load an analysis track to audition it bar by bar.";
    analysisPlayerPlaceholder.textContent = "No chord data yet.";
    measureSheetBox.className = "measure-sheet empty";
    measureSheetBox.textContent = "";
    return;
  }

  const keyObject = analysis.key || {};
  const context = analysis.analysis_context || {};
  const metaParts = [`Confidence: ${formatNumber(keyObject.confidence, 3)}`];
  const tempoHint = formatTempoHint(context);
  if (tempoHint) {
    metaParts.push(tempoHint);
  }
  analysisMeta.textContent = metaParts.join(" ");
  analysisKeyValue.textContent = keyObject.label || "--";
  analysisTempoValue.textContent = `${analysis.tempo_bpm ?? "--"} BPM`;
  renderAnalysisPlayer(track, analysis);

  measureMeta.textContent = `${analysis.beats_per_bar || 4} beats per bar detected`;
  const measures = analysis.measure_sheet || [];
  if (!measures.length) {
    measureSheetBox.className = "measure-sheet empty";
    measureSheetBox.textContent = "";
  } else {
    measureSheetBox.className = "measure-sheet";
    const beatHeaders = Array.from({ length: analysis.beats_per_bar || 4 }, (_, index) => `<th>Beat ${index + 1}</th>`).join("");
    const rowsHtml = measures
      .map((measure) => {
        const beatCells = (measure.display_slots || measure.slots || [])
          .map((slot) => `<td class="measure-cell">${slot || ""}</td>`)
          .join("");
        return `<tr class="measure-row" data-start="${measure.start}" data-end="${measure.end}"><td class="bar-index">Bar ${measure.measure}</td>${beatCells}</tr>`;
      })
      .join("");
    measureSheetBox.innerHTML = `<table class="measure-table"><thead><tr><th>Bar</th>${beatHeaders}</tr></thead><tbody>${rowsHtml}</tbody></table>`;
    measureSheetBox.querySelectorAll(".measure-row").forEach((row) => {
      row.addEventListener("click", () => {
        if (!analysisAudio) {
          return;
        }
        const start = Number(row.dataset.start);
        if (Number.isFinite(start)) {
          analysisAudio.currentTime = start;
          updateMeasureHighlight(start);
        }
      });
    });
    updateMeasureHighlight(analysisAudio?.currentTime ?? Number.NaN);
  }

}

function renderTrack(track) {
  currentTrackId = track.id;
  currentJobId = track.job_id;
  setActiveTab(track.kind === "analysis" ? "analysis" : "separation");
  currentTrackLabel.textContent = track.title;
  stageLabel.textContent = track.stage;
  progressBar.style.width = `${Math.max(3, Math.round((track.progress || 0) * 100))}%`;
  logsBox.textContent = (track.logs || []).join("\n\n") || "No logs yet.";
  renderResults(track);
  renderAnalysis(track.kind === "analysis" ? track : null);
  renderLibrary();
}

async function refreshLibrary() {
  if (!currentUser) {
    renderLibrary();
    return;
  }
  const response = await fetch("/api/library");
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "Failed to load library.");
  }
  libraryTracks = payload.tracks || [];
  renderLibrary();
}

async function fetchCurrentUser() {
  const response = await fetch("/api/auth/me");
  const payload = await response.json();
  currentUser = payload.user;
  updateAuthUi();
  if (currentUser) {
    await refreshLibrary();
  }
}

async function loadTrack(trackId) {
  const response = await fetch(`/api/tracks/${trackId}`);
  const track = await response.json();
  if (!response.ok) {
    throw new Error(track.detail || "Failed to load track.");
  }
  renderTrack(track);
  if (track.status !== "done" && track.status !== "error") {
    startPolling(track.job_id);
  } else if (pollHandle) {
    clearInterval(pollHandle);
    pollHandle = null;
  }
}

async function pollJob(jobId) {
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    const track = await response.json();
    if (!response.ok) {
      throw new Error(track.detail || "Status polling failed.");
    }
    renderTrack(track);
    if (track.status === "done") {
      setStatus(track.kind === "analysis" ? "Analysis complete." : "Separation complete.");
      await refreshLibrary();
      separationButton.disabled = false;
      analysisButton.disabled = false;
      clearInterval(pollHandle);
      pollHandle = null;
      return;
    }
    if (track.status === "error") {
      setStatus(track.error || "Job failed.");
      await refreshLibrary();
      separationButton.disabled = false;
      analysisButton.disabled = false;
      clearInterval(pollHandle);
      pollHandle = null;
      return;
    }
    setStatus(`Processing: ${track.stage}`);
  } catch (error) {
    console.error(error);
    setStatus(error.message || "Status polling failed.");
    separationButton.disabled = false;
    analysisButton.disabled = false;
    if (pollHandle) {
      clearInterval(pollHandle);
      pollHandle = null;
    }
  }
}

function startPolling(jobId) {
  if (pollHandle) {
    clearInterval(pollHandle);
  }
  pollHandle = setInterval(() => pollJob(jobId), 1600);
  pollJob(jobId);
}

async function submitSeparation(event) {
  event.preventDefault();
  if (!currentUser) {
    setStatus("Sign in before uploading.");
    return;
  }
  const payload = new FormData(separationForm);
  if (!(payload.get("aggressive_refine") instanceof File) && !payload.has("aggressive_refine")) {
    payload.set("aggressive_refine", "false");
  }
  separationButton.disabled = true;
  analysisButton.disabled = true;
  setActiveTab("separation");
  resetSeparationView();
  setStatus("Uploading track for stem separation.");
  try {
    const response = await fetch("/api/jobs", { method: "POST", body: payload });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || "Upload failed.");
    }
    currentTrackId = result.track_id;
    currentJobId = result.job_id;
    await refreshLibrary();
    startPolling(result.job_id);
  } catch (error) {
    console.error(error);
    setStatus(error.message || "Upload failed.");
    separationButton.disabled = false;
    analysisButton.disabled = false;
  }
}

async function submitAnalysis(event) {
  event.preventDefault();
  if (!currentUser) {
    setStatus("Sign in before uploading.");
    return;
  }
  const payload = new FormData();
  const fileInput = analysisForm.querySelector('input[name="file"]');
  if (!fileInput?.files?.length) {
    setStatus("Choose an audio file first.");
    return;
  }
  payload.set("file", fileInput.files[0]);
  separationButton.disabled = true;
  analysisButton.disabled = true;
  setActiveTab("analysis");
  resetAnalysisView();
  setStatus("Uploading audio file for analysis.");
  try {
    const response = await fetch("/api/analysis-jobs", { method: "POST", body: payload });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || "Upload failed.");
    }
    currentTrackId = result.track_id;
    currentJobId = result.job_id;
    await refreshLibrary();
    startPolling(result.job_id);
  } catch (error) {
    console.error(error);
    setStatus(error.message || "Upload failed.");
    separationButton.disabled = false;
    analysisButton.disabled = false;
  }
}

logoutButton.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  currentUser = null;
  currentTrackId = null;
  currentJobId = null;
  libraryTracks = [];
  if (pollHandle) {
    clearInterval(pollHandle);
    pollHandle = null;
  }
  window.location.href = "/auth";
});

tabSeparation.addEventListener("click", () => setActiveTab("separation"));
tabAnalysis.addEventListener("click", () => setActiveTab("analysis"));
separationForm.addEventListener("submit", submitSeparation);
analysisForm.addEventListener("submit", submitAnalysis);

resetSeparationView();
resetAnalysisView();
fetchCurrentUser().catch((error) => {
  console.error(error);
  window.location.href = "/auth";
});
