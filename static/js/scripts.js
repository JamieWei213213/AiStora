// static/js/scripts.js

// Wait for the entire HTML document to be loaded before running any script
document.addEventListener("DOMContentLoaded", () => {
  const pipelineEnabled = document.getElementById("app")?.dataset.pipelineEnabled !== "false";
  if (!pipelineEnabled) {
    document.getElementById("load-options")?.classList.add("hidden");
    document.getElementById("loads-section")?.classList.add("hidden");
  }
  // --- Screen Elements ---
  const screens = {
    auth: document.getElementById("auth-screen"),
    db: document.getElementById("db-screen"),
    upload: document.getElementById("upload-screen"),
    chat: document.getElementById("chat-screen"),
  };

  const loadingOverlay = document.getElementById("loading-overlay");
  const loadingText = document.getElementById("loading-text");
  const errorToast = document.getElementById("error-toast");
  const errorMessage = document.getElementById("error-message");

  // --- Auth Elements ---
  const authError = document.getElementById("auth-error");

  // --- DB Screen Elements ---
  const logoutBtn = document.getElementById("logout-btn");
  const createDbBtn = document.getElementById("create-db-btn");
  const dbList = document.getElementById("db-list");
  const createDbModal = document.getElementById("create-db-modal");
  const cancelDbBtn = document.getElementById("cancel-db-btn");
  const confirmDbBtn = document.getElementById("confirm-db-btn");
  const newDbName = document.getElementById("new-db-name");

  // --- Upload Elements ---
  const uploadLogout = document.getElementById("upload-logout");
  const goChat = document.getElementById("go-chat");
  const backToDbScreen = document.getElementById("back-to-db-screen");
  const dropZone = document.getElementById("drop-zone");
  const fileUpload = document.getElementById("file-upload");
  const fileList = document.getElementById("file-list");
  const detectRelationshipsBtn = document.getElementById(
    "detect-relationships-btn"
  );
  const relationshipList = document.getElementById("relationship-list");
  const cleanDataModal = document.getElementById("clean-data-modal");
  const cleanDataReport = document.getElementById("clean-data-report");
  const cleanDataClose = document.getElementById("clean-data-close");
  const cleanDataCancel = document.getElementById("clean-data-cancel");
  const cleanDataApply = document.getElementById("clean-data-apply");
  let pendingCleaning = null;

  // --- Load Options / Data Loads Elements ---
  const loadMode = document.getElementById("load-mode");
  const loadKeyColumnsWrap = document.getElementById("load-key-columns-wrap");
  const loadKeyColumns = document.getElementById("load-key-columns");
  const loadDataset = document.getElementById("load-dataset");
  const loadDatasetHint = document.getElementById("load-dataset-hint");
  const loadKeepHistoryWrap = document.getElementById("load-keep-history-wrap");
  const loadKeepHistory = document.getElementById("load-keep-history");
  const loadsList = document.getElementById("loads-list");
  const loadsRefresh = document.getElementById("loads-refresh");
  const pipelineHealthToggle = document.getElementById("pipeline-health-toggle");
  const pipelineHealthPanel = document.getElementById("pipeline-health-panel");

  // --- Chat Elements ---
  let chatThread = document.getElementById("chat-thread");
  let chatInput = document.getElementById("chat-input");
  let chatSend = document.getElementById("chat-send");
  const chatCancel = document.getElementById("chat-cancel");
  const autoAnalyze = document.getElementById("auto-analyze");
  const edaReportButton = document.getElementById("eda-report");
  const edaReportModal = document.getElementById("eda-report-modal");
  const edaReportContent = document.getElementById("eda-report-content");
  const edaReportSubtitle = document.getElementById("eda-report-subtitle");
  const edaReportClose = document.getElementById("eda-report-close");
  const edaReportDownload = document.getElementById("eda-report-download");
  const clearAgentMemory = document.getElementById("clear-agent-memory");
  const clearAgentLearning = document.getElementById("clear-agent-learning");
  const agentSuggestions = document.getElementById("agent-suggestions");
  const agentActivity = document.getElementById("agent-activity");
  const agentBudget = document.getElementById("agent-budget");
  const agentMetrics = document.getElementById("agent-metrics");
  let activeRequestId = null;
  let activeController = null;
  let lastEdaReport = null;

  const backToUpload = document.getElementById("back-to-upload");

  // --- Schema Elements ---
  const schemaToggle = document.getElementById("schema-toggle");
  const schemaDrawer = document.getElementById("schema-drawer");
  const schemaClose = document.getElementById("schema-close");
  const schemaContent = document.getElementById("schema-content");

  // --- Create Icons ---
  lucide.createIcons();

  // === Utility Functions ===
  function showScreen(target) {
    Object.values(screens).forEach((s) => {
      if (s) s.classList.add("hidden");
    });
    if (target) target.classList.remove("hidden");
  }
  function showLoading(show, text = "Please wait...") {
    if (loadingText) loadingText.textContent = text;
    if (loadingOverlay) loadingOverlay.classList.toggle("hidden", !show);
  }
  function showError(message, element = errorToast) {
    if (element === authError) { authUI.message(message || "Please try again."); return; }
    const msgEl = element === errorToast ? errorMessage : element;
    if (msgEl) msgEl.textContent = message || "Something went wrong. Please try again.";
    if (element) element.classList.remove("hidden");
  }
  document.getElementById("error-dismiss")?.addEventListener("click", () => errorToast.classList.add("hidden"));
  function setButtonLoading(button, isLoading) {
    if (!button) return;
    if (isLoading) {
      button.disabled = true;
      button.innerHTML = '<div class="spinner"></div>';
    } else {
      button.disabled = false;
      button.innerHTML = button.dataset.originalContent;
    }
  }
  function createRequestId() {
    const cryptoApi = window.crypto;
    if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
      return cryptoApi.randomUUID();
    }

    // randomUUID() requires HTTPS in browsers. Keep cancellation and feedback
    // working on the HTTP development endpoint with an RFC 4122 UUID v4.
    const bytes = new Uint8Array(16);
    if (cryptoApi && typeof cryptoApi.getRandomValues === "function") {
      cryptoApi.getRandomValues(bytes);
    } else {
      for (let index = 0; index < bytes.length; index += 1) {
        bytes[index] = Math.floor(Math.random() * 256);
      }
      let timestamp = Date.now();
      for (let index = 0; index < 6; index += 1) {
        bytes[index] ^= timestamp & 0xff;
        timestamp = Math.floor(timestamp / 256);
      }
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) =>
      byte.toString(16).padStart(2, "0")
    ).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(
      12,
      16
    )}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  async function apiFetch(url, options = {}) {
    const response = await fetch(url, options);
    if (response.status === 401) {
      sessionExpired = true;
      showLoading(false);
      showScreen(screens.auth);
      authUI.message("Your session has expired. Sign in again to continue. Your uploaded files are still saved.", "info", "Please sign in again");
      return null;
    }
    if (!response.ok) {
      try {
        const body = await response.clone().json();
        if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("Invalid response");
      } catch (_) {
        const message = response.status === 413 ? "This upload is too large. Try a smaller CSV file."
          : response.status === 429 ? "Too many requests. Please wait and try again."
          : "AIStora is temporarily unavailable. Please try again shortly.";
        return new Response(JSON.stringify({success:false, type:"error", error:message, data:message}), {
          status:response.status, headers:{"Content-Type":"application/json"},
        });
      }
    }
    return response;
  }
  [
    uploadLogout,
    detectRelationshipsBtn,
    goChat,
    chatSend,
    edaReportButton,
    logoutBtn,
    createDbBtn,
    confirmDbBtn,
    cleanDataApply,
  ].forEach((btn) => {
    if (btn) btn.dataset.originalContent = btn.innerHTML;
  });

  // === Auth Logic ===
  let sessionExpired = false;
  const authUI = window.AIStoraAuth.init(async () => {
    // Clear old account-specific UI and state after reauthentication.
    if (sessionExpired) { window.location.reload(); return; }
    errorToast?.classList.add("hidden");
    await loadDatabases();
  });

  // === DB Selection Logic ===
  async function loadDatabases() {
    showLoading(true, "Loading your workspaces...");
    showScreen(screens.db);
    try {
      const res = await apiFetch("/api/databases");
      if (!res) return;
      const data = await res.json();
      if (data.success) {
        showScreen(screens.db);
        dbList.innerHTML = "";
        if (data.databases.length === 0) {
          dbList.innerHTML = `<p class="text-slate-500 text-sm">No databases found. Click "New Database" to create one.</p>`;
        }
        data.databases.forEach((db) => {
          dbList.innerHTML += `
            <div class="bg-white p-5 rounded-xl border border-slate-200 hover:shadow-md transition cursor-pointer group relative db-item-btn" data-db-id="${escapeHtml(db.id)}">
              <div class="flex justify-between items-start mb-2">
                <div class="p-2 bg-sky-50 rounded-lg text-sky-600"><i data-lucide="database"></i></div>
                <div class="flex gap-1">
                  <button class="text-slate-300 hover:text-slate-700 p-1 rename-db-btn" data-db-id="${escapeHtml(db.id)}" data-db-name="${escapeHtml(db.name)}"><i data-lucide="edit-2" class="w-3 h-3"></i></button>
                  <button class="text-slate-300 hover:text-red-500 p-1 delete-db-btn" data-db-id="${escapeHtml(db.id)}" data-db-name="${escapeHtml(db.name)}"><i data-lucide="trash-2" class="w-3 h-3"></i></button>
                </div>
              </div>
              <h3 class="font-semibold text-slate-800">${escapeHtml(db.name)}</h3>
              <p class="text-xs text-slate-500">${escapeHtml(db.table_count)} tables</p>
            </div>
          `;
        });
        lucide.createIcons();
      } else {
        showError(data.error || "We couldn't load your workspaces. Please try again.");
      }
    } catch (e) {
      showError("We couldn't load your workspaces. Refresh the page to try again.");
    } finally {
      showLoading(false);
    }
  }

  if (dbList) {
    dbList.addEventListener("click", (e) => {
      const openBtn = e.target.closest(".db-item-btn");
      const renameBtn = e.target.closest(".rename-db-btn");
      const deleteBtn = e.target.closest(".delete-db-btn");

      if (renameBtn) {
        e.stopPropagation();
        const id = renameBtn.dataset.dbId;
        const name = renameBtn.dataset.dbName;
        renameDatabase(id, name);
      } else if (deleteBtn) {
        e.stopPropagation();
        const id = deleteBtn.dataset.dbId;
        const name = deleteBtn.dataset.dbName;
        deleteDatabase(id, name);
      } else if (openBtn) {
        const id = openBtn.dataset.dbId;
        openDatabase(id);
      }
    });
  }

  async function openDatabase(id) {
    showLoading(true, "Opening database...");
    try {
      const res = await apiFetch("/api/databases/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      if (!res) return;
      const data = await res.json();
      if (data.success) {
        renderSchema(data.schema);
        const schemaSize = data.schema ? Object.keys(data.schema).length : 0;
        if (goChat) goChat.disabled = schemaSize === 0;
        // Relationships are per database; switching must not carry the
        // previous database's suggestions across.
        if (detectRelationshipsBtn) detectRelationshipsBtn.disabled = schemaSize < 2;
        if (relationshipList) relationshipList.innerHTML = "";
        // Loads are per database too: drop the previous list and its
        // pollers, then show this database's history (incl. connector loads).
        resetLoadsState();
        showScreen(screens.upload);
        fetchLoads();
      } else {
        showError(data.error);
      }
    } catch (e) {
      showError("Error opening database.");
    } finally {
      showLoading(false);
    }
  }

  // Mutations used to have no error handling: a network failure was an
  // unhandled rejection and a double click sent the request twice.
  async function mutate(url, options, onSuccess, label) {
    if (mutate.inFlight) return;
    mutate.inFlight = true;
    showLoading(true, label || "Working...");
    try {
      const res = await apiFetch(url, options);
      if (!res) return;
      const data = await res.json();
      if (data.success) await onSuccess(data);
      else showError(data.error || "The request failed.");
    } catch (error) {
      showError("Connection error. Please try again.");
    } finally {
      mutate.inFlight = false;
      showLoading(false);
    }
  }

  async function renameDatabase(id, currentName) {
    const newName = prompt("Enter a new database name:", currentName);
    if (newName && newName !== currentName) {
      await mutate(
        `/api/databases/${id}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: newName }),
        },
        loadDatabases,
        "Renaming database..."
      );
    }
  }

  async function deleteDatabase(id, name) {
    if (
      !confirm(
        `Are you sure you want to delete "${name}"?\nAll tables inside it will be lost.`
      )
    )
      return;

    await mutate(
      `/api/databases/${id}`,
      { method: "DELETE" },
      loadDatabases,
      "Deleting database..."
    );
  }

  if (createDbBtn) {
    createDbBtn.addEventListener("click", () =>
      createDbModal.classList.remove("hidden")
    );
    cancelDbBtn.addEventListener("click", () =>
      createDbModal.classList.add("hidden")
    );
    confirmDbBtn.addEventListener("click", async () => {
      const name = newDbName.value;
      if (!name) return;
      setButtonLoading(confirmDbBtn, true);
      try {
        const res = await apiFetch("/api/databases", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        });
        const data = await res.json();
        if (data.success) {
          newDbName.value = "";
          createDbModal.classList.add("hidden");
          loadDatabases();
        } else {
          showError(data.error);
        }
      } catch (e) {
        showError("Could not create database.");
      } finally {
        setButtonLoading(confirmDbBtn, false);
      }
    });
  }

  if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
      showLoading(true, "Logging out...");
      await apiFetch("/api/logout", { method: "POST" });
      window.location.href = "/app";
    });
  }

  // === Upload Logic ===
  if (uploadLogout) {
    uploadLogout.addEventListener("click", async () => {
      showLoading(true, "Logging out...");
      await apiFetch("/api/logout", { method: "POST" });
      window.location.href = "/app";
    });
  }
  if (backToDbScreen) {
    backToDbScreen.addEventListener("click", () => {
      // Nobody is watching the loads list from the database screen.
      stopAllLoadPolling();
      loadDatabases();
    });
  }

  if (dropZone) {
    ["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
      dropZone.addEventListener(
        eventName,
        (e) => {
          e.preventDefault();
          e.stopPropagation();
        },
        false
      );
    });
    ["dragenter", "dragover"].forEach((eventName) => {
      dropZone.addEventListener(
        eventName,
        () => dropZone.classList.add("bg-slate-100"),
        false
      );
    });
    ["dragleave", "drop"].forEach((eventName) => {
      dropZone.addEventListener(
        eventName,
        () => dropZone.classList.remove("bg-slate-100"),
        false
      );
    });
    dropZone.addEventListener("click", () => fileUpload.click());
    dropZone.addEventListener("drop", (e) => {
      fileUpload.files = e.dataTransfer.files;
      handleFiles(fileUpload.files);
    });
    fileUpload.addEventListener("change", () => {
      handleFiles(fileUpload.files);
    });
  }

  async function handleFiles(files) {
    if (files.length === 0) return;
    const formData = new FormData();
    for (const file of files) {
      if (file.type !== "text/csv" && !file.name.endsWith(".csv")) {
        showError(`File "${file.name}" is not a CSV.`);
        continue;
      }
      formData.append("files", file);
    }
    const fileCount = formData.getAll("files").length;
    if (fileCount === 0) return;

    // Load options travel as plain form fields next to the files. The
    // pipeline ignores them when disabled, so the legacy path is unaffected.
    const options = readLoadOptions(fileCount);
    if (options === null) {
      // Clear the picker so choosing the same file again fires "change".
      fileUpload.value = null;
      return;
    }
    Object.entries(options).forEach(([field, value]) => {
      if (value !== "") formData.append(field, value);
    });

    showLoading(true, "Uploading, parsing, and inferring types...");
    try {
      const response = await apiFetch("/api/upload", {
        method: "POST",
        body: formData,
      });
      if (!response) return;
      const data = await response.json();
      if (Array.isArray(data.loads)) {
        // Pipeline response: the loads are the source of truth. Rows appear
        // right away and pending ones are polled to completion; the schema
        // in the answer is the current state, so render it either way.
        ingestLoads(data.loads);
        if (data.schema) applySchema(data.schema, { detect: false });
        if (!data.success) {
          showError(data.error || "The upload was rejected by the pipeline.");
        } else if (
          data.loads.some((load) => load.status === "succeeded") &&
          data.schema
        ) {
          await applySchema(data.schema, { detect: true });
        }
        return;
      }
      if (data.success) {
        await applySchema(data.schema, { detect: true });
      } else {
        showError(data.error);
      }
    } catch (error) {
      showError("File upload failed.");
    } finally {
      showLoading(false);
      fileUpload.value = null;
    }
  }

  // Reads the "Load options" block. Returns null (after showing the reason)
  // when the upload must not start; otherwise the form fields to send.
  function readLoadOptions(fileCount) {
    const mode = loadMode ? loadMode.value : "replace";
    const keyColumns = loadKeyColumns ? loadKeyColumns.value.trim() : "";
    if (mode === "merge" && !keyColumns) {
      showError("Merge needs at least one key column.");
      if (loadKeyColumns) loadKeyColumns.focus();
      return null;
    }
    // The backend rejects a dataset name with several files, and the files
    // are sent as soon as they are picked, so decide here rather than fail.
    const multi = fileCount > 1;
    if (loadDataset) loadDataset.disabled = multi;
    if (loadDatasetHint) {
      loadDatasetHint.classList.toggle(
        "hidden",
        !(multi && loadDataset && loadDataset.value.trim())
      );
    }
    return {
      mode,
      key_columns: mode === "merge" ? keyColumns : "",
      dataset: multi || !loadDataset ? "" : loadDataset.value.trim(),
      keep_history: mode === "merge" && loadKeepHistory && loadKeepHistory.checked
        ? "true"
        : "",
    };
  }

  // Shared tail of a successful upload / load: show the tables and, when
  // asked, re-run relationship detection like the legacy upload did.
  async function applySchema(schema, { detect } = { detect: false }) {
    renderSchema(schema);
    const schemaSize = schema ? Object.keys(schema).length : 0;
    if (goChat) goChat.disabled = schemaSize === 0;
    if (detectRelationshipsBtn) detectRelationshipsBtn.disabled = schemaSize < 2;
    if (detect && schemaSize >= 2) await detectRelationships(true);
  }

  if (loadMode) {
    const syncLoadOptions = () => {
      const merge = loadMode.value === "merge";
      if (loadKeyColumnsWrap) loadKeyColumnsWrap.classList.toggle("hidden", !merge);
      if (loadKeepHistoryWrap) loadKeepHistoryWrap.classList.toggle("hidden", !merge);
      if (loadKeyColumns) loadKeyColumns.required = merge;
    };
    loadMode.addEventListener("change", syncLoadOptions);
    syncLoadOptions();
  }

  if (fileList) {
    fileList.addEventListener("click", async (e) => {
      const renameBtn = e.target.closest(".rename-btn");
      const deleteBtn = e.target.closest(".delete-btn");
      const cleanBtn = e.target.closest(".clean-btn");

      if (cleanBtn) {
        handleCleaningPreview(cleanBtn.dataset.tableId, cleanBtn.dataset.tableName);
      } else if (renameBtn) {
        const tableId = renameBtn.dataset.tableId;
        handleRenameTable(tableId);
      }
      if (deleteBtn) {
        const tableId = deleteBtn.dataset.tableId;
        handleDeleteTable(tableId);
      }
    });
  }

  async function handleRenameTable(tableId) {
    const input = fileList.querySelector(`input[data-table-id="${tableId}"]`);
    const newName = input.value.trim();
    if (!newName) {
      showError("Table name cannot be empty.");
      return;
    }

    await mutate(
      `/api/tables/${tableId}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName }),
      },
      updateSchemaFromServer,
      "Renaming table..."
    );
  }

  async function handleDeleteTable(tableId) {
    const input = fileList.querySelector(`input[data-table-id="${tableId}"]`);
    if (!confirm(`Are you sure you want to delete the table "${input.value}"?`))
      return;

    await mutate(
      `/api/tables/${tableId}`,
      { method: "DELETE" },
      updateSchemaFromServer,
      "Deleting table..."
    );
  }

  function closeCleaningModal() {
    pendingCleaning = null;
    cleanDataModal.classList.add("hidden");
    cleanDataReport.innerHTML = "";
    cleanDataApply.disabled = true;
  }

  async function handleCleaningPreview(tableId, tableName) {
    showLoading(true, "Profiling data quality locallyâ€¦");
    try {
      const response = await apiFetch(`/api/tables/${tableId}/clean/preview`, {
        method: "POST",
      });
      if (!response) return;
      const result = await response.json();
      if (!result.success) {
        showError(result.error || "Could not profile this table.");
        return;
      }

      const report = result.report;
      const missing = Object.entries(report.missing_by_column || {})
        .filter(([, count]) => count > 0)
        .slice(0, 8);
      let html = `
        <div class="mb-4">
          <p class="font-semibold text-slate-700">${escapeHtml(tableName)}</p>
          <p class="text-[11px] text-slate-500 mt-1">${escapeHtml(report.total_rows)} source rows â€¢ ${escapeHtml(report.estimated_output_rows)} estimated cleaned rows</p>
        </div>
        <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-4">
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Duplicates</p><p class="font-semibold text-slate-700">${escapeHtml(report.duplicate_rows)}</p></div>
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Empty rows</p><p class="font-semibold text-slate-700">${escapeHtml(report.empty_rows)}</p></div>
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Whitespace cells</p><p class="font-semibold text-slate-700">${escapeHtml(report.trimmed_cells)}</p></div>
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Null markers</p><p class="font-semibold text-slate-700">${escapeHtml(report.standardized_nulls)}</p></div>
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Malformed rows</p><p class="font-semibold text-slate-700">${escapeHtml(report.malformed_rows)}</p></div>
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Header changes</p><p class="font-semibold text-slate-700">${escapeHtml(report.header_changes.length)}</p></div>
        </div>`;

      if (result.requires_cleaning) {
        html += '<p class="font-medium text-slate-700 mb-2">Recommended plan</p><div class="space-y-2">';
        report.actions.forEach((action) => {
          html += `<div class="flex gap-2 p-2 rounded-xl border border-emerald-100 bg-emerald-50/50"><i data-lucide="check-circle-2" class="w-3.5 h-3.5 text-emerald-500 mt-0.5 shrink-0"></i><div><p class="text-slate-700">${escapeHtml(action.description)}</p><p class="text-[10px] text-slate-400">${escapeHtml(action.affected)} affected</p></div></div>`;
        });
        html += "</div>";
      } else {
        html += '<div class="p-3 rounded-xl bg-emerald-50 text-emerald-700">No automatically fixable issues were detected.</div>';
      }

      if (missing.length) {
        html += '<details class="mt-4"><summary class="cursor-pointer text-slate-600 font-medium">Missing values by column</summary><div class="mt-2 space-y-1">';
        missing.forEach(([column, count]) => {
          html += `<div class="flex justify-between text-[11px]"><span>${escapeHtml(column)}</span><span class="text-slate-400">${escapeHtml(count)}</span></div>`;
        });
        html += '<p class="text-[10px] text-amber-600 mt-2">Missing values are reported, not guessed or filled.</p></div></details>';
      }

      cleanDataReport.innerHTML = html;
      pendingCleaning = result.requires_cleaning
        ? { tableId, token: result.approval_token }
        : null;
      cleanDataApply.disabled = !pendingCleaning;
      cleanDataModal.classList.remove("hidden");
      lucide.createIcons();
    } catch (error) {
      showError("Could not profile this table.");
    } finally {
      showLoading(false);
    }
  }

  async function applyCleaningPlan() {
    if (!pendingCleaning) return;
    setButtonLoading(cleanDataApply, true);
    try {
      const response = await apiFetch(
        `/api/tables/${pendingCleaning.tableId}/clean/apply`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            approval_token: pendingCleaning.token,
            approved: true,
          }),
        }
      );
      if (!response) return;
      const result = await response.json();
      if (!result.success) {
        showError(result.error || "Cleaning failed.");
        return;
      }
      renderSchema(result.schema);
      closeCleaningModal();
      alert(
        `Created "${result.cleaned_table}" with ${result.output_rows} rows. The original table was not changed.`
      );
    } catch (error) {
      showError("Cleaning failed.");
    } finally {
      setButtonLoading(cleanDataApply, false);
    }
  }

  if (cleanDataClose) cleanDataClose.addEventListener("click", closeCleaningModal);
  if (cleanDataCancel) cleanDataCancel.addEventListener("click", closeCleaningModal);
  if (cleanDataApply) cleanDataApply.addEventListener("click", applyCleaningPlan);

  async function updateSchemaFromServer() {
    try {
      const response = await apiFetch("/api/schema");
      if (!response) return;
      const data = await response.json();
      if (data.success) {
        renderSchema(data.schema);
        goChat.disabled = !data.schema || Object.keys(data.schema).length === 0;
        detectRelationshipsBtn.disabled =
          !data.schema || Object.keys(data.schema).length < 2;
      } else {
        renderSchema({});
      }
    } catch (error) {
      console.error("Error fetching schema:", error);
    }
  }

  function renderSchema(schema) {
    fileList.innerHTML = "";
    if (!schema || Object.keys(schema).length === 0) {
      fileList.innerHTML =
        '<p class="text-[11px] text-slate-400 text-center py-4">Upload files using the panel on the left.</p>';
      return;
    }

    Object.entries(schema).forEach(([tableName, details]) => {
      fileList.innerHTML += `
        <div class="flex flex-wrap items-center justify-between gap-3 border border-slate-100 rounded-2xl px-3 py-2.5 bg-slate-50">
          <div class="flex items-center gap-2">
            <i data-lucide="file-text" class="text-sky-500 w-4 h-4"></i>
            <div>
              <p class="text-slate-700 text-xs font-medium">${escapeHtml(
                details.filename
              )}</p>
              <p class="text-[11px] text-slate-400">${escapeHtml(
                details.row_count || 0
              )} rows</p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <input type="text" value="${escapeHtml(tableName)}" data-table-id="${escapeHtml(
        details.id
      )}"
                   class="w-28 p-1.5 border border-slate-200 rounded-lg text-[11px] focus:ring-1 focus:ring-sky-500">
            <button data-table-id="${escapeHtml(details.id)}" data-original-content="Rename"
                    class="rename-btn text-[10px] bg-slate-200 text-slate-600 px-2 py-1 rounded-md hover:bg-slate-300">
              Rename
            </button>
            <button data-table-id="${escapeHtml(details.id)}" data-table-name="${escapeHtml(tableName)}"
                    class="clean-btn text-[10px] bg-emerald-50 text-emerald-700 px-2 py-1 rounded-md hover:bg-emerald-100">
              Auto clean
            </button>
            <button data-table-id="${escapeHtml(details.id)}"
                    class="delete-btn text-slate-400 hover:text-red-500 p-1">
              <i data-lucide="trash-2" class="w-3 h-3"></i>
            </button>
          </div>
        </div>
      `;
    });

    schemaContent.innerHTML = "";
    let tablesHTML = '<ul class="space-y-1">';
    let columnsHTML = '<div class="space-y-2">';

    Object.entries(schema).forEach(([tableName, details]) => {
      tablesHTML += `<li class="flex items-center justify-between"><span>${escapeHtml(
        tableName
      )}</span><span class="text-slate-400">${escapeHtml(
        details.row_count || 0
      )} rows</span></li>`;
      let colList = "";
      if (details.types) {
        Object.entries(details.types).forEach(([colName, colType]) => {
          let typeColor =
            colType === "int" || colType === "float"
              ? "text-amber-600"
              : "text-emerald-600";
          colList += `<li class="flex justify-between"><span>${escapeHtml(
            colName
          )}</span><span class="${typeColor} font-medium">${escapeHtml(
            colType
          )}</span></li>`;
        });
      }
      columnsHTML += `<div><p class="font-semibold text-slate-600 mb-1">${escapeHtml(
        tableName
      )}</p><ul class="pl-2 space-y-0.5 text-slate-500">${colList}</ul></div>`;
    });
    tablesHTML += "</ul>";
    columnsHTML += "</div>";

    schemaContent.innerHTML = `
      <div><p class="font-semibold text-slate-700 mb-1 flex items-center gap-1"><i data-lucide="table" class="w-3.5 h-3.5 text-sky-500"></i> Tables</p>${tablesHTML}</div>
      <div id="schema-relationships"></div>
      <details><summary class="font-semibold text-slate-700 mb-1 flex items-center gap-1 mt-3 cursor-pointer"><i data-lucide="columns" class="w-3.5 h-3.5 text-violet-500"></i> Columns</summary>${columnsHTML}</details>
    `;
    lucide.createIcons();
  }

  async function detectRelationships(silent = false) {
    if (!silent) setButtonLoading(detectRelationshipsBtn, true);
    relationshipList.innerHTML = "";
    try {
      const response = await apiFetch("/api/detect-relationships", {
        method: "POST",
      });
      if (!response) return;
      const data = await response.json();
      if (data.success) {
        if (data.relationships.length === 0) {
          relationshipList.innerHTML =
            '<p class="text-xs text-slate-500">No relationships detected.</p>';
        } else {
          renderRelationships(data.relationships);
        }
      } else {
        if (!silent) showError(data.error);
      }
    } catch (error) {
      if (!silent) showError("Could not detect relationships.");
    } finally {
      if (!silent) setButtonLoading(detectRelationshipsBtn, false);
    }
  }
  if (detectRelationshipsBtn)
    detectRelationshipsBtn.addEventListener("click", () =>
      detectRelationships(false)
    );

  function renderRelationships(relationships) {
    let listHTML = "";
    let schemaHTML = `<p class="font-semibold text-slate-700 mb-1 flex items-center gap-1"><i data-lucide="git-branch" class="w-3.5 h-3.5 text-emerald-500"></i> Relationships</p><ul class="space-y-1">`;
    relationships.forEach((rel) => {
      const relText = `<span class="font-semibold">${escapeHtml(
        rel.from_table
      )}.${escapeHtml(
        rel.from_column
      )}</span> â†’ <span class="font-semibold">${escapeHtml(
        rel.to_table
      )}.${escapeHtml(rel.to_column)}</span>`;
      listHTML += `<div class="rounded-2xl border border-dashed border-emerald-200 bg-emerald-50/70 px-3 py-2.5 flex items-start gap-2"><i data-lucide="link-2" class="text-emerald-500 w-4 h-4 mt-[2px]"></i><div><p class="text-[11px] text-emerald-800 font-medium">Suggested relationship</p><p class="text-[11px] text-emerald-700">${relText}</p></div></div>`;
      schemaHTML += `<li class="text-emerald-700">${relText}</li>`;
    });
    schemaHTML += "</ul>";
    relationshipList.innerHTML = listHTML;
    const schemaRelElement = document.getElementById("schema-relationships");
    if (schemaRelElement) schemaRelElement.innerHTML = schemaHTML;
    lucide.createIcons();
  }

  // === Data Loads (pipeline) Logic ===
  // The list is rendered from this state, never patched by hand, so a poll
  // update cannot wipe an open details area, a snapshot list or a pending
  // rollback confirmation.
  const LOAD_STAGES = ["validate", "profile", "gate", "transform", "curate", "register"];
  const LOAD_POLL_INTERVAL_MS = 1500;
  const LOAD_POLL_MAX_MS = 5 * 60 * 1000;
  const loadsState = {
    order: [], // load ids, newest first
    byId: new Map(),
    expanded: new Set(),
    busy: new Set(), // load ids with a request in flight
    pollers: new Map(), // load id -> { timer, startedAt }
    timedOut: new Set(), // pollers stopped by the 5 minute cap
    snapshots: new Map(), // dataset -> snapshot list (shown when present)
    confirmRollback: null, // load id awaiting in-DOM confirmation
  };

  // Full class strings so the Tailwind scanner sees them.
  const LOAD_STATUS_CLASSES = {
    succeeded: "bg-emerald-50 text-emerald-700 border-emerald-100",
    quarantined: "bg-amber-50 text-amber-700 border-amber-100",
    failed: "bg-rose-50 text-rose-700 border-rose-100",
    running: "bg-sky-50 text-sky-700 border-sky-100 animate-pulse",
    received: "bg-sky-50 text-sky-700 border-sky-100 animate-pulse",
    rolled_back: "bg-slate-100 text-slate-600 border-slate-200",
  };
  const LOAD_MODE_CLASSES = {
    replace: "bg-slate-100 text-slate-600",
    append: "bg-sky-50 text-sky-700",
    merge: "bg-violet-50 text-violet-700",
  };
  const QUALITY_CLASSES = {
    pass: "bg-emerald-50 text-emerald-700",
    warn: "bg-amber-50 text-amber-700",
    fail: "bg-rose-50 text-rose-700",
  };

  function formatCount(value) {
    if (value === null || value === undefined || value === "") return "â€”";
    const number = Number(value);
    if (!Number.isFinite(number)) return escapeHtml(value);
    return escapeHtml(number.toLocaleString());
  }

  function formatDuration(ms) {
    const number = Number(ms);
    if (!Number.isFinite(number) || ms === null || ms === undefined) return "â€”";
    if (number < 1000) return `${Math.round(number)} ms`;
    if (number < 60000) return `${(number / 1000).toFixed(1)} s`;
    const minutes = Math.floor(number / 60000);
    const seconds = Math.round((number % 60000) / 1000);
    return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  }

  function formatBytes(bytes) {
    const number = Number(bytes);
    if (!Number.isFinite(number) || bytes === null || bytes === undefined) return "â€”";
    if (number < 1024) return `${number} B`;
    if (number < 1024 * 1024) return `${(number / 1024).toFixed(1)} KB`;
    return `${(number / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatLoadTime(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return escapeHtml(iso);
    return escapeHtml(date.toLocaleString());
  }

  function shortId(value) {
    return escapeHtml(String(value || "").slice(0, 8));
  }

  function resetLoadsState() {
    stopAllLoadPolling();
    loadsState.order = [];
    loadsState.byId.clear();
    loadsState.expanded.clear();
    loadsState.busy.clear();
    loadsState.timedOut.clear();
    loadsState.snapshots.clear();
    loadsState.confirmRollback = null;
    renderLoads();
    if (pipelineHealthPanel) {
      pipelineHealthPanel.classList.add("hidden");
      pipelineHealthPanel.innerHTML = "";
    }
    if (pipelineHealthToggle) pipelineHealthToggle.setAttribute("aria-expanded", "false");
  }

  function stopLoadPolling(loadId) {
    const poller = loadsState.pollers.get(loadId);
    if (poller) clearTimeout(poller.timer);
    loadsState.pollers.delete(loadId);
  }

  function stopAllLoadPolling() {
    Array.from(loadsState.pollers.keys()).forEach(stopLoadPolling);
  }

  // Merges loads into the state (new ones on top) and polls the unfinished
  // ones. `replace` drops loads the server no longer returns.
  function ingestLoads(loads, { replace } = { replace: false }) {
    if (replace) {
      loadsState.order = [];
      loadsState.byId.clear();
    }
    const fresh = [];
    (loads || []).forEach((load) => {
      if (!load || !load.load_id) return;
      if (!loadsState.byId.has(load.load_id)) fresh.push(load.load_id);
      loadsState.byId.set(load.load_id, load);
    });
    // GET /api/loads is already newest first; an upload answer lists loads
    // in file order, so those go on top in reverse to keep the list sorted.
    loadsState.order = replace ? fresh : fresh.reverse().concat(loadsState.order);
    loadsState.order.forEach((loadId) => {
      const load = loadsState.byId.get(loadId);
      if (load && !load.is_terminal) startLoadPolling(loadId);
      else stopLoadPolling(loadId);
    });
    renderLoads();
  }

  function startLoadPolling(loadId) {
    if (loadsState.pollers.has(loadId) || loadsState.timedOut.has(loadId)) return;
    const poller = { timer: null, startedAt: Date.now() };
    loadsState.pollers.set(loadId, poller);
    const tick = async () => {
      if (!loadsState.pollers.has(loadId)) return;
      if (Date.now() - poller.startedAt > LOAD_POLL_MAX_MS) {
        // Give up quietly; Refresh picks the load up again if it finished.
        loadsState.timedOut.add(loadId);
        stopLoadPolling(loadId);
        renderLoads();
        return;
      }
      try {
        const response = await apiFetch(`/api/loads/${encodeURIComponent(loadId)}`);
        if (!response) return;
        const data = await response.json();
        if (data.success && data.load) {
          const previous = loadsState.byId.get(loadId) || {};
          loadsState.byId.set(loadId, data.load);
          if (data.load.is_terminal) {
            stopLoadPolling(loadId);
            if (data.load.status === "succeeded" && previous.status !== "succeeded") {
              await applySchema(data.schema || {}, { detect: true });
            }
          }
          renderLoads();
        }
      } catch (error) {
        // Transient; the next tick retries until the cap.
      }
      if (loadsState.pollers.has(loadId)) {
        poller.timer = setTimeout(tick, LOAD_POLL_INTERVAL_MS);
      }
    };
    poller.timer = setTimeout(tick, LOAD_POLL_INTERVAL_MS);
  }

  async function fetchLoads() {
    if (!loadsList || !pipelineEnabled) return;
    if (loadsRefresh) loadsRefresh.disabled = true;
    try {
      const response = await apiFetch("/api/loads?limit=50");
      if (!response) return;
      const data = await response.json();
      if (data.success) {
        loadsState.timedOut.clear();
        ingestLoads(data.loads, { replace: true });
      } else {
        showError(data.error || "Could not load the pipeline history.");
      }
    } catch (error) {
      showError("Could not load the pipeline history.");
    } finally {
      if (loadsRefresh) loadsRefresh.disabled = false;
    }
  }
  if (loadsRefresh) loadsRefresh.addEventListener("click", () => fetchLoads());

  function stageStripHtml(load) {
    const stages = load.stages || {};
    const stopped = load.status === "quarantined" || load.status === "failed";
    const segments = LOAD_STAGES.map((stage) => {
      const entry = stages[stage] || {};
      let color = "bg-slate-200";
      if (stopped && stage === load.stage) color = "bg-rose-500";
      else if (entry.status === "succeeded") color = "bg-emerald-500";
      else if (entry.status === "failed" || entry.status === "quarantined") color = "bg-rose-500";
      else if (entry.status === "running" || (!load.is_terminal && stage === load.stage)) {
        color = "bg-sky-500 animate-pulse";
      }
      return `<span class="h-1.5 flex-1 rounded-full ${color}" title="${stage}"></span>`;
    });
    const label = load.is_terminal
      ? escapeHtml(load.status === "succeeded" ? "all stages complete" : load.stage || "")
      : `${escapeHtml(load.stage || "queued")}â€¦`;
    return `<div class="flex items-center gap-2"><div class="flex flex-1 gap-0.5">${segments.join(
      ""
    )}</div><span class="text-[10px] text-slate-400 shrink-0">${label}</span></div>`;
  }

  function badgeHtml(text, classes) {
    return `<span class="inline-flex items-center px-1.5 py-0.5 rounded-md border border-transparent text-[10px] font-medium ${classes}">${escapeHtml(
      text
    )}</span>`;
  }

  function loadRowHtml(load) {
    const loadId = load.load_id;
    const expanded = loadsState.expanded.has(loadId);
    const statusClasses = LOAD_STATUS_CLASSES[load.status] || LOAD_STATUS_CLASSES.rolled_back;
    const modeClasses = LOAD_MODE_CLASSES[load.mode] || LOAD_MODE_CLASSES.replace;
    const quality = load.quality_status || (load.quality && load.quality.status);
    const qualityHtml = quality
      ? badgeHtml(`quality ${quality}`, QUALITY_CLASSES[quality] || QUALITY_CLASSES.warn)
      : "";
    const rejected = Number(load.rows_rejected || 0);
    const meta = [
      `${formatCount(load.rows_in)} in â†’ ${formatCount(load.rows_out)} out`,
      rejected > 0 ? `<span class="text-rose-600">${formatCount(rejected)} rejected</span>` : "",
      formatDuration(load.duration_ms),
      load.original_filename ? escapeHtml(load.original_filename) : "",
      load.source && load.source !== "upload" ? `via ${escapeHtml(load.source)}` : "",
    ].filter(Boolean);
    const errorHtml =
      load.error && load.error.message
        ? `<p class="text-[11px] ${
            load.status === "failed" ? "text-rose-600" : "text-amber-700"
          } break-words">${escapeHtml(load.error.message)}</p>`
        : "";
    const timedOutHtml = loadsState.timedOut.has(loadId)
      ? '<p class="text-[10px] text-slate-400">Stopped watching after 5 minutes. Refresh to check again.</p>'
      : "";
    return `<div class="rounded-2xl border border-slate-100 bg-slate-50 overflow-hidden" data-load-id="${escapeHtml(
      loadId
    )}">
      <button type="button" class="load-row-toggle w-full text-left p-3 space-y-2 hover:bg-slate-100 transition" aria-expanded="${
        expanded ? "true" : "false"
      }">
        <div class="flex flex-wrap items-center gap-1.5">
          <span class="text-xs font-medium text-slate-700 break-all">${escapeHtml(
            load.dataset
          )}</span>
          ${badgeHtml(load.mode, modeClasses)}
          ${badgeHtml(String(load.status || "").replace("_", " "), statusClasses)}
          ${qualityHtml}
          <span class="ml-auto text-[10px] text-slate-400">${formatLoadTime(
            load.finished_at || load.created_at
          )}</span>
        </div>
        ${stageStripHtml(load)}
        <div class="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-slate-500">${meta
          .map((item) => `<span>${item}</span>`)
          .join("")}</div>
        ${errorHtml}${timedOutHtml}
      </button>
      <div class="load-details ${
        expanded ? "" : "hidden"
      } border-t border-slate-100 bg-white p-3 space-y-3">${
      expanded ? loadDetailsHtml(load) : ""
    }</div>
    </div>`;
  }

  function loadDetailsHtml(load) {
    const sections = [];
    const quality = load.quality || {};
    const checks = quality.checks || [];
    if (checks.length) {
      sections.push(`<div><p class="text-[10px] uppercase tracking-wide text-slate-400 mb-1">Quality checks</p><ul class="space-y-1">${checks
        .map(
          (check) =>
            `<li class="flex gap-2 text-[11px]"><i data-lucide="${
              check.passed ? "check-circle-2" : "x-circle"
            }" class="w-3.5 h-3.5 mt-0.5 shrink-0 ${
              check.passed
                ? "text-emerald-500"
                : check.severity === "warn"
                ? "text-amber-500"
                : "text-rose-500"
            }"></i><span class="text-slate-600 break-words"><span class="font-medium">${escapeHtml(
              check.name
            )}</span>${check.message ? ` â€” ${escapeHtml(check.message)}` : ""}</span></li>`
        )
        .join("")}</ul></div>`);
    }
    const castFailures = Object.entries(quality.cast_failures || {}).filter(
      ([, count]) => Number(count) > 0
    );
    if (castFailures.length || Number(quality.duplicate_rows) > 0) {
      const items = castFailures.map(
        ([column, count]) => `${escapeHtml(column)}: ${formatCount(count)} values could not be cast`
      );
      if (Number(quality.duplicate_rows) > 0) {
        items.push(`${formatCount(quality.duplicate_rows)} duplicate rows`);
      }
      sections.push(`<div><p class="text-[10px] uppercase tracking-wide text-slate-400 mb-1">Profile</p><ul class="space-y-0.5 text-[11px] text-slate-600">${items
        .map((item) => `<li>${item}</li>`)
        .join("")}</ul></div>`);
    }
    const renames = Object.entries(load.header_renames || {});
    if (renames.length) {
      sections.push(`<div><p class="text-[10px] uppercase tracking-wide text-slate-400 mb-1">Header renames</p><ul class="space-y-0.5 text-[11px] text-slate-600">${renames
        .map(
          ([from, to]) =>
            `<li class="break-all"><span class="text-slate-400">${escapeHtml(
              from
            )}</span> â†’ ${escapeHtml(to)}</li>`
        )
        .join("")}</ul></div>`);
    }
    const changes = load.schema_changes || {};
    const changeItems = [];
    if (changes.reset) changeItems.push('<li class="text-slate-500">schema reset by replace load</li>');
    if (changes.initial) changeItems.push('<li class="text-slate-500">initial schema</li>');
    const describe = (entry) =>
      typeof entry === "string"
        ? escapeHtml(entry)
        : `${escapeHtml(entry.column)}${entry.type ? ` <span class="text-slate-400">${escapeHtml(entry.type)}</span>` : ""}`;
    (changes.added || []).forEach((entry) =>
      changeItems.push(`<li><span class="text-emerald-600">added</span> ${describe(entry)}</li>`)
    );
    (changes.removed || []).forEach((entry) =>
      changeItems.push(`<li><span class="text-rose-600">removed</span> ${describe(entry)}</li>`)
    );
    (changes.narrowed || []).forEach((entry) =>
      changeItems.push(`<li><span class="text-amber-600">narrowed</span> ${describe(entry)}</li>`)
    );
    (changes.blocked || []).forEach((entry) =>
      changeItems.push(`<li><span class="text-rose-600">blocked</span> ${describe(entry)}</li>`)
    );
    if (changes.blocked_reason) {
      changeItems.push(`<li class="text-rose-600">${escapeHtml(changes.blocked_reason)}</li>`);
    }
    if (changeItems.length) {
      sections.push(`<div><p class="text-[10px] uppercase tracking-wide text-slate-400 mb-1">Schema changes</p><ul class="space-y-0.5 text-[11px] text-slate-600">${changeItems.join(
        ""
      )}</ul></div>`);
    }
    const counts = load.counts || {};
    if (load.mode === "merge" || load.mode === "append") {
      const tiles = [
        ["Inserted", counts.rows_inserted],
        ["Updated", counts.rows_updated],
        ["Skipped", counts.rows_skipped],
        ["Current", counts.rows_current],
        ["History", counts.history_rows],
      ].filter(([, value]) => value !== undefined && value !== null);
      if (tiles.length) {
        sections.push(`<div><p class="text-[10px] uppercase tracking-wide text-slate-400 mb-1">Rows</p><div class="grid grid-cols-3 sm:grid-cols-5 gap-1.5">${tiles
          .map(
            ([label, value]) =>
              `<div class="rounded-lg bg-slate-50 p-2"><p class="text-[9px] text-slate-400">${label}</p><p class="text-[11px] font-semibold text-slate-700">${formatCount(
                value
              )}</p></div>`
          )
          .join("")}</div></div>`);
      }
      if ((load.key_columns || []).length) {
        sections.push(`<p class="text-[10px] text-slate-400">Key: ${load.key_columns
          .map(escapeHtml)
          .join(", ")}${load.keep_history ? " â€¢ row history kept" : ""}</p>`);
      }
    }
    sections.push(`<p class="text-[10px] text-slate-400 break-all">Load ${escapeHtml(
      load.load_id
    )}${load.snapshot_id ? ` â€¢ snapshot ${shortId(load.snapshot_id)}` : ""}${
      load.raw_bytes ? ` â€¢ ${formatBytes(load.raw_bytes)}` : ""
    }</p>`);

    const busy = loadsState.busy.has(load.load_id);
    const buttons = [];
    if (load.can_rollback) {
      buttons.push(
        `<button type="button" class="load-rollback-btn inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11px] bg-amber-50 text-amber-700 hover:bg-amber-100 disabled:opacity-50" ${
          busy ? "disabled" : ""
        }><i data-lucide="undo-2" class="w-3.5 h-3.5"></i> Roll back</button>`
      );
    }
    const showingSnapshots = loadsState.snapshots.has(load.dataset);
    buttons.push(
      `<button type="button" class="load-snapshots-btn inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11px] bg-slate-100 text-slate-600 hover:bg-slate-200 disabled:opacity-50" ${
        busy ? "disabled" : ""
      }><i data-lucide="history" class="w-3.5 h-3.5"></i> ${
        showingSnapshots ? "Hide snapshots" : "Snapshots"
      }</button>`
    );
    let actions = `<div class="flex flex-wrap items-center gap-2">${buttons.join("")}${
      busy ? '<span class="text-[10px] text-slate-400">Workingâ€¦</span>' : ""
    }</div>`;
    if (loadsState.confirmRollback === load.load_id) {
      actions += `<div class="rounded-xl border border-amber-200 bg-amber-50 p-3 space-y-2"><p class="text-[11px] text-amber-800">Roll back <span class="font-medium">${escapeHtml(
        load.dataset
      )}</span> to the snapshot before this load? The table returns to its previous rows.</p><div class="flex flex-wrap gap-2"><button type="button" class="load-rollback-confirm-btn px-3 py-1.5 rounded-lg text-[11px] bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50" ${
        busy ? "disabled" : ""
      }>Confirm roll back</button><button type="button" class="load-rollback-cancel-btn px-3 py-1.5 rounded-lg text-[11px] bg-white text-slate-600 border border-slate-200 hover:bg-slate-50" ${
        busy ? "disabled" : ""
      }>Cancel</button></div></div>`;
    }
    sections.push(actions);
    if (showingSnapshots) sections.push(snapshotsHtml(loadsState.snapshots.get(load.dataset)));
    return sections.join("");
  }

  function snapshotsHtml(snapshots) {
    if (!snapshots || !snapshots.length) {
      return '<p class="text-[11px] text-slate-400">No snapshots recorded for this dataset.</p>';
    }
    return `<div><p class="text-[10px] uppercase tracking-wide text-slate-400 mb-1">Snapshots</p><ul class="divide-y divide-slate-100 rounded-xl border border-slate-100">${snapshots
      .map((snapshot) => {
        const time = snapshot.timestamp_ms
          ? escapeHtml(new Date(Number(snapshot.timestamp_ms)).toLocaleString())
          : "â€”";
        return `<li class="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-2.5 py-1.5 text-[11px] ${
          snapshot.is_current ? "bg-emerald-50/60" : ""
        }"><span class="text-slate-600">${time}</span><span class="text-slate-500">${escapeHtml(
          snapshot.operation || ""
        )}${snapshot.mode ? ` (${escapeHtml(snapshot.mode)})` : ""}</span><span class="text-slate-400">load ${shortId(
          snapshot.load_id
        )}</span><span class="text-slate-600">${formatCount(
          snapshot.total_records
        )} rows</span>${
          snapshot.is_current
            ? '<span class="ml-auto text-[10px] font-medium text-emerald-700">current</span>'
            : ""
        }</li>`;
      })
      .join("")}</ul></div>`;
  }

  function renderLoads() {
    if (!loadsList) return;
    if (!loadsState.order.length) {
      loadsList.innerHTML =
        '<p class="text-[11px] text-slate-400 text-center py-3">No loads yet. Upload a CSV to run it through the pipeline.</p>';
      return;
    }
    loadsList.innerHTML = loadsState.order
      .map((loadId) => loadsState.byId.get(loadId))
      .filter(Boolean)
      .map(loadRowHtml)
      .join("");
    lucide.createIcons();
  }

  if (loadsList) {
    loadsList.addEventListener("click", (event) => {
      const row = event.target.closest("[data-load-id]");
      if (!row) return;
      const loadId = row.dataset.loadId;
      const load = loadsState.byId.get(loadId);
      if (!load) return;
      if (event.target.closest(".load-row-toggle")) {
        if (loadsState.expanded.has(loadId)) loadsState.expanded.delete(loadId);
        else loadsState.expanded.add(loadId);
        renderLoads();
      } else if (event.target.closest(".load-rollback-btn")) {
        loadsState.confirmRollback = loadId;
        renderLoads();
      } else if (event.target.closest(".load-rollback-cancel-btn")) {
        loadsState.confirmRollback = null;
        renderLoads();
      } else if (event.target.closest(".load-rollback-confirm-btn")) {
        rollbackLoad(load);
      } else if (event.target.closest(".load-snapshots-btn")) {
        if (loadsState.snapshots.has(load.dataset)) {
          loadsState.snapshots.delete(load.dataset);
          renderLoads();
        } else {
          fetchSnapshots(load);
        }
      }
    });
  }

  async function rollbackLoad(load) {
    if (loadsState.busy.has(load.load_id)) return;
    loadsState.busy.add(load.load_id);
    renderLoads();
    try {
      const response = await apiFetch(
        `/api/loads/${encodeURIComponent(load.load_id)}/rollback`,
        { method: "POST" }
      );
      if (!response) return;
      const data = await response.json();
      if (!data.success) {
        showError(data.error || "The rollback failed.");
        return;
      }
      loadsState.confirmRollback = null;
      if (data.load) loadsState.byId.set(load.load_id, data.load);
      // The snapshot list is stale after a rollback; refetch if it is open.
      if (loadsState.snapshots.has(load.dataset)) loadsState.snapshots.delete(load.dataset);
      if (data.schema) await applySchema(data.schema, { detect: false });
      await fetchLoads();
    } catch (error) {
      showError("The rollback failed.");
    } finally {
      loadsState.busy.delete(load.load_id);
      renderLoads();
    }
  }

  async function fetchSnapshots(load) {
    if (loadsState.busy.has(load.load_id)) return;
    loadsState.busy.add(load.load_id);
    renderLoads();
    try {
      const response = await apiFetch(
        `/api/datasets/${encodeURIComponent(load.dataset)}/snapshots`
      );
      if (!response) return;
      const data = await response.json();
      if (!data.success) {
        showError(data.error || "Could not list snapshots.");
        return;
      }
      loadsState.snapshots.set(load.dataset, data.snapshots || []);
    } catch (error) {
      showError("Could not list snapshots.");
    } finally {
      loadsState.busy.delete(load.load_id);
      renderLoads();
    }
  }

  // --- Pipeline health panel ---
  if (pipelineHealthToggle && pipelineHealthPanel) {
    pipelineHealthToggle.addEventListener("click", () => {
      const open = pipelineHealthPanel.classList.contains("hidden");
      pipelineHealthPanel.classList.toggle("hidden", !open);
      pipelineHealthToggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) loadPipelineMetrics();
    });
    pipelineHealthPanel.addEventListener("click", (event) => {
      const button = event.target.closest("[data-pipeline-job]");
      if (button) runPipelineJob(button.dataset.pipelineJob, button);
    });
  }

  async function loadPipelineMetrics() {
    if (!pipelineHealthPanel) return;
    pipelineHealthToggle.disabled = true;
    pipelineHealthPanel.innerHTML =
      '<p class="text-[11px] text-slate-400">Loading pipeline metricsâ€¦</p>';
    try {
      const response = await apiFetch("/api/pipeline/metrics");
      if (!response) return;
      const data = await response.json();
      if (!data.success) {
        pipelineHealthPanel.innerHTML = `<p class="text-[11px] text-rose-600">${escapeHtml(
          data.error || "Metrics are unavailable."
        )}</p>`;
        return;
      }
      renderPipelineHealth(data.metrics || {});
    } catch (error) {
      pipelineHealthPanel.innerHTML =
        '<p class="text-[11px] text-rose-600">Metrics are unavailable.</p>';
    } finally {
      pipelineHealthToggle.disabled = false;
    }
  }

  async function runPipelineJob(job, button) {
    if (!["telemetry", "compaction"].includes(job) || button.disabled) return;
    button.disabled = true;
    button.textContent = "Runningâ€¦";
    try {
      const response = await apiFetch(`/api/pipeline/jobs/${job}`, { method: "POST" });
      if (!response) return;
      const data = await response.json();
      if (!response.ok || !data.success) {
        showError(data.error || `The ${job} job could not be started.`);
        button.disabled = false;
        button.textContent = "Run telemetry now";
        return;
      }
      if (data.status === "queued") {
        showError("The job was queued; refresh this panel in a minute.");
      }
      await loadPipelineMetrics();
    } catch (error) {
      showError(`The ${job} job could not be started.`);
      button.disabled = false;
      button.textContent = "Run telemetry now";
    }
  }

  function statTileHtml(label, value, hint) {
    return `<div class="rounded-xl bg-white border border-slate-100 p-2.5"><p class="text-[9px] uppercase tracking-wide text-slate-400">${label}</p><p class="text-sm font-semibold text-slate-700">${value}</p>${
      hint ? `<p class="text-[10px] text-slate-400">${hint}</p>` : ""
    }</div>`;
  }

  function renderPipelineHealth(metrics) {
    const live = metrics.live || {};
    const byStatus = live.by_status || {};
    const duration = live.duration_ms || {};
    const percent = (value) =>
      value === null || value === undefined ? "â€”" : `${(Number(value) * 100).toFixed(1)}%`;
    const tiles = [
      statTileHtml("Loads", formatCount(live.loads_total), `${formatCount(live.loads_24h)} in 24h`),
      statTileHtml("Succeeded", formatCount(byStatus.succeeded)),
      statTileHtml("Quarantined", formatCount(byStatus.quarantined), percent(live.quarantine_rate)),
      statTileHtml("Failed", formatCount(byStatus.failed), percent(live.failure_rate)),
      statTileHtml("Rows loaded", formatCount(live.rows_loaded), `${formatCount(live.rows_rejected)} rejected`),
      statTileHtml("p95 duration", formatDuration(duration.p95), `avg ${formatDuration(duration.avg)}`),
      statTileHtml(
        "Throughput",
        live.throughput_rows_per_second === null || live.throughput_rows_per_second === undefined
          ? "â€”"
          : `${formatCount(Math.round(Number(live.throughput_rows_per_second)))} rows/s`,
        formatBytes(live.raw_bytes)
      ),
    ].join("");

    const datasets = live.datasets || [];
    const datasetRows = datasets
      .map(
        (item) =>
          `<tr class="border-t border-slate-100"><td class="p-1.5 break-all">${escapeHtml(
            item.dataset
          )}</td><td class="p-1.5 text-right">${formatCount(item.loads)}</td><td class="p-1.5 text-right">${formatCount(
            item.rows_current
          )}</td><td class="p-1.5">${badgeHtml(
            String(item.last_status || "â€”").replace("_", " "),
            LOAD_STATUS_CLASSES[item.last_status]
              ? LOAD_STATUS_CLASSES[item.last_status].replace(" animate-pulse", "")
              : LOAD_STATUS_CLASSES.rolled_back
          )}</td><td class="p-1.5">${escapeHtml(item.last_mode || "â€”")}</td><td class="p-1.5">${
            item.quality_status
              ? badgeHtml(item.quality_status, QUALITY_CLASSES[item.quality_status] || QUALITY_CLASSES.warn)
              : "â€”"
          }</td></tr>`
      )
      .join("");
    const datasetTable = datasets.length
      ? `<div class="overflow-x-auto rounded-xl border border-slate-100 bg-white"><table class="w-full text-[11px] min-w-[28rem]"><thead class="bg-slate-100 text-slate-500"><tr><th class="p-1.5 text-left font-medium">Dataset</th><th class="p-1.5 text-right font-medium">Loads</th><th class="p-1.5 text-right font-medium">Rows</th><th class="p-1.5 text-left font-medium">Last status</th><th class="p-1.5 text-left font-medium">Mode</th><th class="p-1.5 text-left font-medium">Quality</th></tr></thead><tbody>${datasetRows}</tbody></table></div>`
      : '<p class="text-[11px] text-slate-400">No datasets loaded yet.</p>';

    pipelineHealthPanel.innerHTML = `<div class="flex flex-wrap items-center justify-between gap-2"><p class="text-[11px] font-semibold text-slate-600">Live metrics</p><span class="text-[10px] px-1.5 py-0.5 rounded-md bg-slate-200 text-slate-600">backend: ${escapeHtml(
      metrics.backend || "local"
    )}</span></div><div class="grid grid-cols-2 sm:grid-cols-4 gap-1.5">${tiles}</div>${datasetTable}<div id="pipeline-gold" class="space-y-2"></div>`;

    const gold = metrics.gold || {};
    const goldEl = pipelineHealthPanel.querySelector("#pipeline-gold");
    if (gold.available) {
      const pipelineSeries = (gold.daily_pipeline || []).map((row) => ({
        dt: row.dt,
        value: Number(row.loads || 0),
      }));
      const agentSeries = (gold.daily_agent || []).map((row) => ({
        dt: row.dt,
        value: Number(row.estimated_cost_usd || 0),
      }));
      goldEl.appendChild(
        sparklineCard("Loads per day (30 days)", pipelineSeries, "fill-sky-500", (v) =>
          v.toLocaleString()
        )
      );
      goldEl.appendChild(
        sparklineCard("Agent cost per day (USD)", agentSeries, "fill-violet-500", (v) =>
          `$${v.toFixed(2)}`
        )
      );
    } else {
      const note = document.createElement("div");
      note.className =
        "rounded-xl border border-dashed border-slate-200 bg-white p-3 flex flex-wrap items-center justify-between gap-2";
      const text = document.createElement("p");
      text.className = "text-[11px] text-slate-500";
      text.textContent = gold.reason || "Daily aggregates are not available yet.";
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.pipelineJob = "telemetry";
      button.className =
        "px-2.5 py-1.5 rounded-lg text-[11px] bg-slate-900 text-slate-50 hover:bg-slate-800 disabled:opacity-50";
      button.textContent = "Run telemetry now";
      note.appendChild(text);
      note.appendChild(button);
      goldEl.appendChild(note);
    }
    lucide.createIcons();
  }

  // A tiny bar sparkline built with DOM APIs (no chart library, no inline
  // styles): the CSP forbids style attributes, so sizing goes through
  // SVG attributes and colour through Tailwind fill utilities.
  function sparklineCard(title, series, fillClass, formatValue) {
    const SVG_NS = "http://www.w3.org/2000/svg";
    const card = document.createElement("div");
    card.className = "rounded-xl bg-white border border-slate-100 p-2.5 space-y-1";
    const heading = document.createElement("div");
    heading.className = "flex items-center justify-between gap-2";
    const label = document.createElement("p");
    label.className = "text-[10px] uppercase tracking-wide text-slate-400";
    label.textContent = title;
    const last = document.createElement("p");
    last.className = "text-[11px] font-semibold text-slate-700";
    const latest = series.length ? series[series.length - 1] : null;
    last.textContent = latest ? `${formatValue(latest.value)} on ${latest.dt}` : "no data";
    heading.appendChild(label);
    heading.appendChild(last);
    card.appendChild(heading);
    if (!series.length) return card;

    const width = 240;
    const height = 36;
    const gap = 1;
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", title);
    svg.setAttribute("class", "w-full h-9");
    const max = Math.max(...series.map((point) => point.value), 0) || 1;
    const barWidth = Math.max((width - gap * (series.length - 1)) / series.length, 1);
    series.forEach((point, index) => {
      const barHeight = Math.max((point.value / max) * height, point.value > 0 ? 1 : 0);
      const rect = document.createElementNS(SVG_NS, "rect");
      rect.setAttribute("x", (index * (barWidth + gap)).toFixed(2));
      rect.setAttribute("y", (height - barHeight).toFixed(2));
      rect.setAttribute("width", barWidth.toFixed(2));
      rect.setAttribute("height", barHeight.toFixed(2));
      rect.setAttribute("rx", "1");
      rect.setAttribute("class", point.value > 0 ? fillClass : "fill-slate-200");
      const tooltip = document.createElementNS(SVG_NS, "title");
      tooltip.textContent = `${point.dt}: ${formatValue(point.value)}`;
      rect.appendChild(tooltip);
      svg.appendChild(rect);
    });
    card.appendChild(svg);
    return card;
  }

  // === Agentic Chat Logic ===
  if (goChat) {
    goChat.addEventListener("click", () => {
      showScreen(screens.chat);
      loadAgentSuggestions();
      loadAgentMetrics();
    });
  }
  if (backToUpload) {
    backToUpload.addEventListener("click", () => showScreen(screens.upload));
  }
  if (schemaToggle) {
    schemaToggle.addEventListener("click", () =>
      schemaDrawer.classList.toggle("-translate-x-full")
    );
    schemaClose.addEventListener("click", () =>
      schemaDrawer.classList.add("-translate-x-full")
    );
  }

  // Escapes for text nodes AND double/single quoted attribute values. The
  // previous textContent -> innerHTML round-trip did not escape quotes, so a
  // value containing a double quote could break out of an attribute.
  const HTML_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
    "`": "&#96;",
  };

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>"'`]/g, (character) => HTML_ESCAPES[character]);
  }

  function appendBubble(html, side) {
    const wrap = document.createElement("div");
    wrap.className = side === "user" ? "flex justify-end" : "flex justify-start";
    const bubble = document.createElement("div");
    bubble.className =
      "max-w-[90%] px-3 py-2.5 text-xs rounded-2xl shadow-sm " +
      (side === "user" ? "bg-sky-50" : "bg-white border border-slate-100");
    bubble.innerHTML = html;
    wrap.appendChild(bubble);
    if (chatThread) {
      chatThread.appendChild(wrap);
      chatThread.scrollTop = chatThread.scrollHeight;
    }
  }

  function renderTable(data) {
    if (!data || data.length === 0) {
      return '<p class="text-[11px] text-slate-500">The analysis returned no rows.</p>';
    }
    const headers = Object.keys(data[0]);
    let table =
      '<table class="text-[11px] border border-slate-200 rounded-lg overflow-hidden w-full">';
    table += '<thead class="bg-slate-100"><tr>';
    headers.forEach(
      (header) =>
        (table += `<th class="p-1.5 text-left">${escapeHtml(header)}</th>`)
    );
    table += "</tr></thead><tbody>";
    data.forEach((row) => {
      table += '<tr class="hover:bg-slate-50">';
      headers.forEach(
        (header) =>
          (table += `<td class="p-1.5 border-t">${escapeHtml(row[header])}</td>`)
      );
      table += "</tr>";
    });
    return `<div class="overflow-x-auto">${table}</tbody></table></div>`;
  }

  function formatEdaNumber(value) {
    if (value === null || value === undefined) return "â€”";
    const number = Number(value);
    if (!Number.isFinite(number)) return escapeHtml(value);
    return number.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }

  function formatEdaLabel(value) {
    if (!value) return "â€”";
    const text = String(value).replaceAll("_", " ");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function edaList(items, emptyMessage) {
    if (!items || !items.length) {
      return `<p class="text-[11px] text-slate-400">${escapeHtml(emptyMessage)}</p>`;
    }
    return `<ul class="space-y-1 text-[11px] text-slate-600">${items
      .map(
        (item) =>
          `<li class="flex gap-2"><span class="text-sky-500">â€¢</span><span>${escapeHtml(
            item
          )}</span></li>`
      )
      .join("")}</ul>`;
  }

  function renderEdaTableReport(table) {
    const missingRows = (table.missingness || [])
      .filter((item) => Number(item.missing_count) > 0)
      .slice(0, 12)
      .map((item) => ({
        Column: item.column,
        Missing: formatEdaNumber(item.missing_count),
        Rate: `${(Number(item.missing_rate || 0) * 100).toFixed(1)}%`,
        Privacy: item.classification,
      }));
    const numericRows = (table.numeric_summary || []).slice(0, 20).map((item) => ({
      Column: item.column,
      Count: formatEdaNumber(item.count),
      Mean: formatEdaNumber(item.mean),
      Median: formatEdaNumber(item.median),
      Min: formatEdaNumber(item.min),
      Max: formatEdaNumber(item.max),
      "Std dev": formatEdaNumber(item.std_dev),
      "IQR tails": item.outlier_status === "assessed"
        ? `${formatEdaNumber(item.iqr_outlier_count)} (${(
            Number(item.iqr_outlier_rate || 0) * 100
          ).toFixed(1)}%)`
        : "Not assessed (small sample)",
      Shape: formatEdaLabel(item.distribution_shape),
    }));
    const categoryRows = (table.categorical_summary || []).slice(0, 20).map((item) => ({
      Column: item.column,
      Distinct: `${formatEdaNumber(item.distinct_count)}${
        item.distinct_count_is_lower_bound ? "+" : ""
      }`,
      "Top values": item.values_suppressed_reason
        ? `[SUPPRESSED: ${String(item.values_suppressed_reason).replaceAll("_", " ")}]`
        : (item.top_values || [])
            .map((entry) => `${entry.value} (${entry.count})`)
            .join(", ") || "â€”",
      Privacy: item.classification,
    }));
    const timeRows = (table.time_summary || []).map((item) => ({
      Column: item.column,
      Earliest: item.min,
      Latest: item.max,
      Parsed: formatEdaNumber(item.parsed_count),
      Invalid: formatEdaNumber(item.invalid_date_count),
    }));
    const correlationRows = (table.correlations || []).map((item) => ({
      Columns: `${item.left} â†” ${item.right}`,
      "Pearson r": formatEdaNumber(item.pearson_r),
      Pairs: formatEdaNumber(item.pair_count),
    }));
    const missingPatternRows = (table.missingness_patterns || []).map((item) => ({
      Column: item.column,
      "Explained by": item.group_by,
      Interpretation: item.summary,
    }));
    const completeness = table.complete_scan ? "Complete scan" : "Bounded sample";
    const statusClass = table.complete_scan
      ? "bg-emerald-50 text-emerald-700"
      : "bg-amber-50 text-amber-700";
    return `<details open class="rounded-xl border border-slate-200 bg-white overflow-hidden">
      <summary class="cursor-pointer px-4 py-3 flex flex-wrap items-center justify-between gap-2">
        <span class="font-semibold text-sm text-slate-800">${escapeHtml(table.name)}</span>
        <span class="flex items-center gap-2 text-[10px]"><span class="${statusClass} px-2 py-1 rounded-full">${completeness}</span><span class="text-slate-400">${formatEdaNumber(
      table.rows_scanned
    )} rows â€¢ ${formatEdaNumber(table.column_count)} columns â€¢ ${formatEdaNumber(
      table.duration_ms
    )} ms</span></span>
      </summary>
      <div class="border-t border-slate-100 p-4 space-y-5">
        <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
          <div class="rounded-lg bg-slate-50 p-2"><p class="text-[9px] uppercase text-slate-400">Declared rows</p><p class="text-sm font-semibold">${formatEdaNumber(
            table.declared_row_count
          )}</p></div>
          <div class="rounded-lg bg-slate-50 p-2"><p class="text-[9px] uppercase text-slate-400">Duplicates</p><p class="text-sm font-semibold">${formatEdaNumber(
            table.duplicate_rows
          )}</p></div>
          <div class="rounded-lg bg-slate-50 p-2"><p class="text-[9px] uppercase text-slate-400">Numeric columns</p><p class="text-sm font-semibold">${formatEdaNumber(
            table.numeric_column_count
          )}</p></div>
          <div class="rounded-lg bg-slate-50 p-2"><p class="text-[9px] uppercase text-slate-400">Categorical columns</p><p class="text-sm font-semibold">${formatEdaNumber(
            table.categorical_column_count
          )}</p></div>
        </div>
        <section><h4 class="text-xs font-semibold text-slate-700 mb-2">Verified findings</h4>${edaList(
          table.findings,
          "No high-priority deterministic flags in this table."
        )}</section>
        <section><h4 class="text-xs font-semibold text-slate-700 mb-2">Missing values</h4>${renderTable(
          missingRows
        )}</section>
        <section><h4 class="text-xs font-semibold text-slate-700 mb-2">Missingness context</h4>${renderTable(
          missingPatternRows
        )}</section>
        <section><h4 class="text-xs font-semibold text-slate-700 mb-2">Numeric distributions</h4>${renderTable(
          numericRows
        )}</section>
        <section><h4 class="text-xs font-semibold text-slate-700 mb-2">Categorical distributions</h4>${renderTable(
          categoryRows
        )}</section>
        <section><h4 class="text-xs font-semibold text-slate-700 mb-2">Time coverage</h4>${renderTable(
          timeRows
        )}</section>
        <section><h4 class="text-xs font-semibold text-slate-700 mb-2">Strongest correlations</h4>${renderTable(
          correlationRows
        )}</section>
        ${
          table.warnings && table.warnings.length
            ? `<section class="rounded-lg bg-amber-50 p-3"><h4 class="text-xs font-semibold text-amber-700 mb-2">Scope warnings</h4>${edaList(
                table.warnings,
                ""
              )}</section>`
            : ""
        }
      </div>
    </details>`;
  }

  function renderEdaReport(report) {
    const overview = report.overview || {};
    const statusClass = report.status === "complete"
      ? "bg-emerald-50 text-emerald-700"
      : "bg-amber-50 text-amber-700";
    const relationships = (report.relationships || []).map((item) => {
      const coverage = item.status === "data_verified"
        ? ` â€¢ ${(Number(item.from_match_rate || 0) * 100).toFixed(
            1
          )}% of child rows reference a valid parent; ${(
            Number(item.to_match_rate || 0) * 100
          ).toFixed(1)}% of parent rows participate${
            item.scope_complete ? "" : " (bounded scope)"
          }`
        : " â€¢ schema validated; data coverage unavailable";
      return `${item.from_table}.${item.from_column} â†’ ${item.to_table}.${item.to_column}${coverage}`;
    });
    const sensitiveCount = ((report.privacy || {}).sensitive_columns || []).length;
    const traceRows = (report.trace || []).map((item) => ({
      Step: item.step,
      Table: item.table,
      Status: item.status,
      Duration: `${formatEdaNumber(item.duration_ms)} ms`,
      Summary: item.summary,
    }));
    return `<div class="space-y-5">
      <section class="rounded-xl border border-slate-200 bg-white p-4">
        <div class="flex flex-wrap items-center justify-between gap-2 mb-4">
          <div><p class="text-sm font-semibold text-slate-800">Database overview</p><p class="text-[11px] text-slate-400">Generated ${escapeHtml(
            new Date(report.generated_at).toLocaleString()
          )} â€¢ ${formatEdaNumber(report.duration_ms)} ms</p></div>
          <span class="${statusClass} px-2.5 py-1 rounded-full text-[10px] font-medium">${escapeHtml(
            report.status
          )}</span>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-5 gap-2">
          ${[
            ["Tables", overview.profiled_table_count],
            ["Rows scanned", overview.scanned_rows],
            ["Declared rows", overview.declared_rows],
            ["Columns", overview.column_count],
            ["Relationships", overview.relationship_count],
          ]
            .map(
              ([label, value]) =>
                `<div class="rounded-lg bg-slate-50 p-3"><p class="text-[9px] uppercase text-slate-400">${label}</p><p class="text-base font-semibold text-slate-700">${formatEdaNumber(
                  value
                )}</p></div>`
            )
            .join("")}
        </div>
      </section>
      <section class="grid md:grid-cols-2 gap-3">
        <div class="rounded-xl border border-sky-100 bg-sky-50/60 p-4"><h3 class="text-xs font-semibold text-sky-800 mb-2">Verified findings</h3>${edaList(
          report.findings,
          "No high-priority deterministic flags were found."
        )}</div>
        <div class="rounded-xl border border-violet-100 bg-violet-50/60 p-4"><h3 class="text-xs font-semibold text-violet-800 mb-2">Privacy controls</h3><p class="text-[11px] text-slate-600">All calculations ran locally. No raw rows are returned in this report. Category values are hidden for ${formatEdaNumber(
          sensitiveCount
        )} name-classified columns; identifier and free-text values are also suppressed.</p></div>
      </section>
      <section class="space-y-3">${(report.tables || [])
        .map(renderEdaTableReport)
        .join("")}</section>
      <section class="rounded-xl border border-slate-200 bg-white p-4"><h3 class="text-xs font-semibold text-slate-700 mb-2">Schema relationships</h3>${edaList(
        relationships,
        "No validated relationship hints are available."
      )}</section>
      <section class="rounded-xl border border-amber-100 bg-amber-50/60 p-4"><h3 class="text-xs font-semibold text-amber-800 mb-2">Limitations</h3>${edaList(
        report.limitations,
        "No additional limitations were recorded."
      )}</section>
      <details class="rounded-xl border border-slate-200 bg-white p-4"><summary class="cursor-pointer text-xs font-semibold text-slate-700">Validated plan and execution trace</summary><div class="mt-3 space-y-3"><p class="text-[11px] text-emerald-700">Plan version ${formatEdaNumber(
        (report.plan || {}).version
      )} passed structural validation before execution.</p>${renderTable(traceRows)}<p class="text-[10px] text-slate-400">Limits: ${formatEdaNumber(
        (report.limits || {}).tables
      )} tables, ${formatEdaNumber(
        (report.limits || {}).rows_per_table
      )} rows per table, ${formatEdaNumber(
        (report.limits || {}).timeout_seconds
      )} seconds.</p></div></details>
    </div>`;
  }

  async function runEdaReport() {
    if (!edaReportModal || !edaReportContent || !edaReportButton) return;
    if (activeRequestId) {
      showError("Finish or cancel the current agent task first.");
      return;
    }
    edaReportModal.classList.remove("hidden");
    edaReportDownload.classList.add("hidden");
    edaReportContent.innerHTML =
      '<div class="h-full flex items-center justify-center"><div class="text-center"><div class="spinner mx-auto mb-3"></div><p class="text-xs text-slate-600">Profiling tables locallyâ€¦</p><p class="text-[10px] text-slate-400 mt-1">Large datasets may use bounded samples.</p></div></div>';
    setButtonLoading(edaReportButton, true);
    try {
      const response = await apiFetch("/api/eda-report", { method: "POST" });
      if (!response) return;
      const result = await response.json();
      if (!response.ok || !result.success) {
        throw new Error(result.error || "The EDA report could not be completed.");
      }
      lastEdaReport = result.report;
      edaReportSubtitle.textContent = `${lastEdaReport.status} â€¢ ${formatEdaNumber(
        lastEdaReport.overview.scanned_rows
      )} rows scanned locally`;
      edaReportContent.innerHTML = renderEdaReport(lastEdaReport);
      edaReportDownload.classList.remove("hidden");
    } catch (error) {
      lastEdaReport = null;
      edaReportSubtitle.textContent = "The report was not generated";
      edaReportContent.innerHTML = `<div class="rounded-xl border border-red-100 bg-red-50 p-4"><p class="text-sm font-semibold text-red-700">EDA report error</p><p class="text-xs text-red-600 mt-1">${escapeHtml(
        error.message
      )}</p></div>`;
    } finally {
      setButtonLoading(edaReportButton, false);
    }
  }

  function traceHtml(result) {
    const plan = result.plan || [];
    const trace = result.trace || [];
    const verification = result.verification;
    const agent = result.agent;
    if (!plan.length && !trace.length && !verification && !agent) return "";
    let html =
      '<details class="mt-3 border-t border-slate-100 pt-2"><summary class="text-[10px] text-violet-600 cursor-pointer font-medium">Agent activity</summary><div class="mt-2 space-y-2">';
    if (agent) {
      html += `<p class="text-[10px] text-slate-400">${escapeHtml(
        agent.model
      )} â€¢ ${escapeHtml(agent.routing_tier)} routing â€¢ ${Number(
        agent.examples_used || 0
      )} learned examples</p>`;
    }
    if (verification) {
      const verifyColor = verification.passed
        ? "text-emerald-600"
        : "text-amber-600";
      const verifyLabel = verification.passed
        ? "Deterministic checks passed"
        : "Result needs review";
      html += `<p class="text-[10px] ${verifyColor} font-medium">${verifyLabel} â€¢ ${Math.round(
        Number(verification.score || 0) * 100
      )}%</p>`;
      (verification.warnings || []).forEach(
        (warning) =>
          (html += `<p class="text-[10px] text-amber-600">${escapeHtml(
            warning
          )}</p>`)
      );
    }
    if (plan.length) {
      html += '<ol class="list-decimal pl-4 text-[10px] text-slate-500">';
      plan.forEach((step) => (html += `<li>${escapeHtml(step)}</li>`));
      html += "</ol>";
    }
    trace.forEach((item) => {
      const color = item.status === "ok" ? "text-emerald-600" : "text-red-600";
      html += `<div class="text-[10px]"><span class="${color} font-medium">${escapeHtml(
        item.tool
      )}</span><span class="text-slate-400"> â€¢ ${item.duration_ms || 0}ms</span><p class="text-slate-500">${escapeHtml(
        item.summary
      )}</p></div>`;
    });
    return `${html}</div></details>`;
  }

  function renderAgentActivity(result) {
    if (!agentActivity || !agentBudget) return;
    const budget = result.budget || {};
    agentBudget.textContent =
      budget.turns_used !== undefined
        ? `${budget.turns_used} turns â€¢ ${budget.tool_calls_used} tool calls`
        : "Task completed";
    const plan = result.plan || [];
    const trace = result.trace || [];
    let html = "";
    if (result.agent) {
      html += `<div class="rounded-lg bg-violet-50 p-2 mb-3"><p class="text-[10px] font-medium text-violet-700">${escapeHtml(
        result.agent.model
      )}</p><p class="text-[9px] text-violet-500 mt-0.5">${escapeHtml(
        result.agent.routing_reason
      )} â€¢ ${Number(result.agent.examples_used || 0)} learned examples</p></div>`;
    }
    if (result.verification) {
      const verified = result.verification.passed;
      html += `<div class="rounded-lg ${
        verified ? "bg-emerald-50" : "bg-amber-50"
      } p-2 mb-3"><p class="text-[10px] font-medium ${
        verified ? "text-emerald-700" : "text-amber-700"
      }">${verified ? "Result verified" : "Verification warning"} â€¢ ${Math.round(
        Number(result.verification.score || 0) * 100
      )}%</p></div>`;
    }
    if (plan.length) {
      html += '<p class="text-[10px] uppercase tracking-wide text-slate-400 mb-2">Plan</p><ol class="list-decimal pl-4 text-[11px] text-slate-600 space-y-1 mb-4">';
      plan.forEach((step) => (html += `<li>${escapeHtml(step)}</li>`));
      html += "</ol>";
    }
    html += '<p class="text-[10px] uppercase tracking-wide text-slate-400 mb-2">Local actions</p><div class="space-y-3">';
    trace.forEach((item) => {
      const dot = item.status === "ok" ? "bg-emerald-500" : "bg-red-500";
      html += `<div class="flex gap-2"><span class="mt-1 w-2 h-2 rounded-full ${dot} shrink-0"></span><div><p class="text-[11px] font-medium text-slate-700">${escapeHtml(
        item.tool
      )}</p><p class="text-[10px] text-slate-500">${escapeHtml(
        item.summary
      )}</p><p class="text-[9px] text-slate-300">${item.duration_ms || 0}ms</p></div></div>`;
    });
    agentActivity.innerHTML =
      html || '<p class="text-[11px] text-slate-400">No local tools were needed.</p>';
  }

  async function loadAgentMetrics() {
    if (!agentMetrics) return;
    try {
      const response = await apiFetch("/api/chat/metrics");
      if (!response || !response.ok) return;
      const result = await response.json();
      const metrics = result.metrics || {};
      const percent = (value) =>
        value === null || value === undefined
          ? "â€”"
          : `${Math.round(Number(value) * 100)}%`;
      agentMetrics.innerHTML = [
        ["Runs", metrics.total_runs || 0],
        ["Success", percent(metrics.success_rate)],
        ["Helpful", percent(metrics.positive_feedback_rate)],
      ]
        .map(
          ([label, value]) =>
            `<div class="rounded-md bg-slate-50 px-1.5 py-1 text-center"><p class="text-[9px] text-slate-400">${label}</p><p class="text-[10px] font-semibold text-slate-600">${value}</p></div>`
        )
        .join("");
    } catch (error) {
      agentMetrics.innerHTML = "";
    }
  }

  function feedbackHtml(result) {
    if (
      !result.request_id ||
      !["table", "count", "chart", "text"].includes(result.type)
    ) {
      return "";
    }
    return `<div data-agent-feedback="${escapeHtml(
      result.request_id
    )}" class="mt-3 border-t border-slate-100 pt-2 flex items-center gap-2 text-[10px] text-slate-400"><span>Was this useful?</span><button data-feedback-rating="up" class="px-2 py-1 rounded-md hover:bg-emerald-50 hover:text-emerald-600" title="Helpful">Yes</button><button data-feedback-rating="down" class="px-2 py-1 rounded-md hover:bg-red-50 hover:text-red-600" title="Not helpful">No</button></div>`;
  }

  async function loadAgentSuggestions() {
    if (!agentSuggestions) return;
    agentSuggestions.innerHTML =
      '<span class="text-[10px] text-slate-400">Building suggestions from columnsâ€¦</span>';
    try {
      const response = await apiFetch("/api/chat/suggestions");
      if (!response) return;
      const result = await response.json();
      agentSuggestions.innerHTML = "";
      (result.suggestions || []).forEach((suggestion) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className =
          "px-2.5 py-1.5 rounded-full border border-violet-100 bg-white text-[10px] text-violet-700 hover:bg-violet-50 transition";
        button.textContent = suggestion.label;
        button.title = `${suggestion.question} â€” ${suggestion.reason}`;
        button.addEventListener("click", () =>
          sendMessage({ query: suggestion.question })
        );
        agentSuggestions.appendChild(button);
      });
      if (!agentSuggestions.children.length) {
        agentSuggestions.innerHTML =
          '<span class="text-[10px] text-slate-400">Upload a table to get column-based suggestions.</span>';
      }
    } catch (error) {
      agentSuggestions.innerHTML =
        '<span class="text-[10px] text-slate-400">Suggestions are temporarily unavailable.</span>';
    }
  }

  async function sendMessage(options = {}) {
    if (activeRequestId) return;
    const value = (options.query || chatInput.value || "").trim();
    if (!value) return;
    if (options.displayUser !== false) appendBubble(escapeHtml(value), "user");
    chatInput.value = "";
    setButtonLoading(chatSend, true);
    chatCancel.classList.remove("hidden");
    activeRequestId = createRequestId();
    activeController = new AbortController();
    if (agentActivity) {
      agentActivity.innerHTML =
        '<div class="text-[11px] text-violet-600 animate-pulse">Planning and running local toolsâ€¦</div>';
    }
    if (agentBudget) agentBudget.textContent = "Agent running";

    const typingEl = document.createElement("div");
    typingEl.className = "flex justify-start";
    typingEl.innerHTML =
      '<div class="bg-white border border-slate-100 px-3 py-2.5 text-xs rounded-2xl shadow-sm text-violet-600">Agent is workingâ€¦</div>';
    chatThread.appendChild(typingEl);
    chatThread.scrollTop = chatThread.scrollHeight;

    try {
      const response = await apiFetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: value,
          request_id: activeRequestId,
          approvals: options.approvals || [],
          auto_analyze: options.autoAnalyze === true,
        }),
        signal: activeController.signal,
      });
      if (!response) return;
      const result = await response.json();
      renderAgentActivity(result);

      let htmlResponse = "";
      if (result.message && result.type !== "text") {
        htmlResponse += `<p class="text-slate-600 mb-2">${escapeHtml(
          result.message
        )}</p>`;
      }
      switch (result.type) {
        case "chart":
          htmlResponse += `<img src="${escapeHtml(
            result.data
          )}" alt="Generated chart" class="rounded-lg border border-slate-200" />`;
          break;
        case "table":
          htmlResponse += renderTable(result.data);
          break;
        case "count":
          htmlResponse += `The result is <b class="text-sky-600">${escapeHtml(
            result.data
          )}</b>.`;
          break;
        case "text":
          htmlResponse = `<p>${escapeHtml(result.data)}</p>`;
          break;
        case "clarification":
          htmlResponse = `<p class="font-medium text-violet-700 mb-1">I need one detail:</p><p>${escapeHtml(
            result.data
          )}</p>`;
          break;
        case "approval": {
          const approvalId = `approve-${Date.now()}`;
          htmlResponse = `<p class="font-medium text-amber-700 mb-1">${escapeHtml(
            result.approval && result.approval.title
          )}</p><p class="text-slate-600 mb-3">${escapeHtml(
            result.data
          )}</p><div class="flex gap-2"><button id="${approvalId}" class="px-3 py-1.5 rounded-lg bg-amber-500 text-white text-[11px]">Approve once</button><button id="${approvalId}-deny" class="px-3 py-1.5 rounded-lg bg-slate-100 text-slate-600 text-[11px]">Not now</button></div>`;
          appendBubble(htmlResponse + traceHtml(result), "ai");
          document.getElementById(approvalId).addEventListener("click", () =>
            sendMessage({
              query: result.original_query,
              approvals: [result.approval.scope],
              displayUser: false,
              autoAnalyze: result.auto_analyze === true,
            })
          );
          document
            .getElementById(`${approvalId}-deny`)
            .addEventListener("click", (event) => {
              event.target.parentElement.innerHTML =
                '<span class="text-slate-400">Chart sharing was not approved.</span>';
            });
          return;
        }
        case "cancelled":
          htmlResponse = `<p class="text-slate-500">${escapeHtml(result.data)}</p>`;
          break;
        case "quota":
          htmlResponse = `<p class="font-medium text-amber-700">Gemini quota reached</p><p class="text-amber-700 text-[11px] mt-1">${escapeHtml(
            result.data
          )}</p><div class="flex gap-3 mt-2 text-[11px]"><a class="text-violet-600 hover:underline" href="https://aistudio.google.com/" target="_blank" rel="noopener noreferrer">Open Google AI Studio</a><a class="text-violet-600 hover:underline" href="https://ai.google.dev/gemini-api/docs/rate-limits" target="_blank" rel="noopener noreferrer">Quota help</a></div>`;
          break;
        default:
          htmlResponse = `<p class="font-medium text-red-600">Agent error</p><p class="text-red-500 text-[11px]">${escapeHtml(
            result.data || "Unknown error"
          )}</p>`;
      }
      appendBubble(
        htmlResponse + traceHtml(result) + feedbackHtml(result),
        "ai"
      );
      loadAgentMetrics();
    } catch (error) {
      if (error.name === "AbortError") {
        appendBubble('<p class="text-slate-500">Cancellation requested.</p>', "ai");
      } else {
        appendBubble(
          '<p class="font-medium text-red-600">Connection error</p><p class="text-red-500">The agent could not reach the server.</p>',
          "ai"
        );
      }
    } finally {
      if (typingEl.parentNode === chatThread) chatThread.removeChild(typingEl);
      setButtonLoading(chatSend, false);
      chatCancel.classList.add("hidden");
      activeRequestId = null;
      activeController = null;
    }
  }

  if (chatSend) chatSend.addEventListener("click", () => sendMessage());
  if (chatThread) {
    chatThread.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-feedback-rating]");
      if (!button) return;
      const container = button.closest("[data-agent-feedback]");
      if (!container) return;
      const response = await apiFetch("/api/chat/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_id: container.dataset.agentFeedback,
          rating: button.dataset.feedbackRating,
        }),
      });
      if (response && response.ok) {
        container.innerHTML =
          '<span class="text-emerald-600">Feedback saved for future evaluations.</span>';
        loadAgentMetrics();
      } else {
        showError("Feedback could not be saved.");
      }
    });
  }
  if (autoAnalyze) {
    autoAnalyze.addEventListener("click", () =>
      sendMessage({
        query: "Auto-analyze this database",
        autoAnalyze: true,
      })
    );
  }
  if (edaReportButton) {
    edaReportButton.addEventListener("click", runEdaReport);
  }
  if (edaReportClose) {
    edaReportClose.addEventListener("click", () =>
      edaReportModal.classList.add("hidden")
    );
  }
  if (edaReportDownload) {
    edaReportDownload.addEventListener("click", () => {
      if (!lastEdaReport) return;
      const blob = new Blob([JSON.stringify(lastEdaReport, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `aistora-eda-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
    });
  }
  if (chatInput) {
    chatInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
      }
    });
  }
  if (chatCancel) {
    chatCancel.addEventListener("click", async () => {
      if (!activeRequestId) return;
      const requestId = activeRequestId;
      try {
        await apiFetch("/api/chat/cancel", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request_id: requestId }),
        });
      } finally {
        if (activeController) activeController.abort();
      }
    });
  }
  if (clearAgentMemory) {
    clearAgentMemory.addEventListener("click", async () => {
      const response = await apiFetch("/api/chat/memory", { method: "DELETE" });
      if (response && response.ok) {
        appendBubble(
          '<p class="text-slate-500">Conversation memory for this database was cleared.</p>',
          "ai"
        );
      }
    });
  }
  if (clearAgentLearning) {
    clearAgentLearning.addEventListener("click", async () => {
      const response = await apiFetch("/api/chat/history", { method: "DELETE" });
      if (response && response.ok) {
        appendBubble(
          '<p class="text-slate-500">Saved evaluations and learned examples for this database were cleared.</p>',
          "ai"
        );
        loadAgentMetrics();
      }
    });
  }

  // === Page Load Logic ===
  async function checkAuthStatus() {
    showLoading(true, "Checking session...");
    try {
      const data = await authUI.requestJSON("/api/auth/status");
      authUI.setRecoveryAvailable(data.passwordResetAvailable);
      if (authUI.isReset()) { showScreen(screens.auth); return; }
      if (data.isLoggedIn) {
        await loadDatabases(); // User is logged in, show DB screen
      } else {
        showScreen(screens.auth); // User is not logged in
      }
    } catch (error) {
      showScreen(screens.auth);
      authUI.message(error.message, "error", "We couldn't check your session", false);
    } finally {
      showLoading(false);
    }
  }

  // Start the app
  checkAuthStatus();
}); // --- End of DOMContentLoaded wrapper
