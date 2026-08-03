// static/js/scripts.js

// Wait for the entire HTML document to be loaded before running any script
document.addEventListener("DOMContentLoaded", () => {
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
  const authSubmitBtn = document.getElementById("auth-submit-btn");
  const authToggleMode = document.getElementById("auth-toggle-mode");
  const authTitle = document.getElementById("auth-title");
  const authSubtitle = document.getElementById("auth-subtitle");
  const authBtnLabel = document.getElementById("auth-btn-label");
  const authEmail = document.getElementById("auth-email");
  const authPassword = document.getElementById("auth-password");
  const authError = document.getElementById("auth-error");
  let isLoginMode = true;

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

  // --- Chat Elements ---
  let chatThread = document.getElementById("chat-thread");
  let chatInput = document.getElementById("chat-input");
  let chatSend = document.getElementById("chat-send");
  const chatCancel = document.getElementById("chat-cancel");
  const autoAnalyze = document.getElementById("auto-analyze");
  const clearAgentMemory = document.getElementById("clear-agent-memory");
  const clearAgentLearning = document.getElementById("clear-agent-learning");
  const agentSuggestions = document.getElementById("agent-suggestions");
  const agentActivity = document.getElementById("agent-activity");
  const agentBudget = document.getElementById("agent-budget");
  const agentMetrics = document.getElementById("agent-metrics");
  let activeRequestId = null;
  let activeController = null;

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
    const msgEl = element === errorToast ? errorMessage : element;
    if (msgEl) msgEl.textContent = message;
    if (element) element.classList.remove("hidden");
    setTimeout(() => {
      if (element) element.classList.add("hidden");
    }, 3000);
  }
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
  async function apiFetch(url, options = {}) {
    const response = await fetch(url, options);
    if (response.status === 401) {
      showError("Session expired. Please log in again.");
      setTimeout(() => (window.location.href = "/app"), 1000);
      return null;
    }
    return response;
  }
  [
    authSubmitBtn,
    uploadLogout,
    detectRelationshipsBtn,
    goChat,
    chatSend,
    logoutBtn,
    createDbBtn,
    confirmDbBtn,
    cleanDataApply,
  ].forEach((btn) => {
    if (btn) btn.dataset.originalContent = btn.innerHTML;
  });

  // === Auth Logic ===
  if (authToggleMode) {
    authToggleMode.addEventListener("click", () => {
      isLoginMode = !isLoginMode;
      authError.classList.add("hidden");
      if (isLoginMode) {
        authTitle.textContent = "Login";
        authSubtitle.textContent = "Enter your credentials.";
        authBtnLabel.textContent = "Login";
        authToggleMode.innerHTML =
          "Don’t have an account? <span class='font-medium'>Register</span>";
      } else {
        authTitle.textContent = "Create Account";
        authSubtitle.textContent = "Start your data journey.";
        authBtnLabel.textContent = "Register";
        authToggleMode.innerHTML =
          "Already have an account? <span class='font-medium'>Login</span>";
      }
    });
  }
  if (authSubmitBtn) {
    authSubmitBtn.addEventListener("click", async () => {
      const email = authEmail.value;
      const password = authPassword.value;
      if (!email || !password) {
        showError("Please enter email and password.", authError);
        return;
      }
      const endpoint = isLoginMode ? "/api/login" : "/api/register";
      setButtonLoading(authSubmitBtn, true);
      try {
        const res = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });
        const data = await res.json();
        if (data.success) {
          if (!isLoginMode) {
            alert("Account created! Please login.");
            authToggleMode.click();
          } else {
            await loadDatabases();
          }
        } else {
          showError(data.error, authError);
        }
      } catch (e) {
        showError("Connection error.", authError);
      } finally {
        setButtonLoading(authSubmitBtn, false);
      }
    });
  }

  // === DB Selection Logic ===
  async function loadDatabases() {
    showLoading(true, "Loading databases...");
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
            <div class="bg-white p-5 rounded-xl border border-slate-200 hover:shadow-md transition cursor-pointer group relative db-item-btn" data-db-id="${db.id}">
              <div class="flex justify-between items-start mb-2">
                <div class="p-2 bg-sky-50 rounded-lg text-sky-600"><i data-lucide="database"></i></div>
                <div class="flex gap-1">
                  <button class="text-slate-300 hover:text-slate-700 p-1 rename-db-btn" data-db-id="${db.id}" data-db-name="${db.name}"><i data-lucide="edit-2" class="w-3 h-3"></i></button>
                  <button class="text-slate-300 hover:text-red-500 p-1 delete-db-btn" data-db-id="${db.id}" data-db-name="${db.name}"><i data-lucide="trash-2" class="w-3 h-3"></i></button>
                </div>
              </div>
              <h3 class="font-semibold text-slate-800">${db.name}</h3>
              <p class="text-xs text-slate-500">${db.table_count} tables</p>
            </div>
          `;
        });
        lucide.createIcons();
      }
    } catch (e) {
      showError("Could not load databases.");
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
        showScreen(screens.upload);
      } else {
        showError(data.error);
      }
    } catch (e) {
      showError("Error opening database.");
    } finally {
      showLoading(false);
    }
  }

  async function renameDatabase(id, currentName) {
    const newName = prompt("Enter a new database name:", currentName);
    if (newName && newName !== currentName) {
      const res = await apiFetch(`/api/databases/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName }),
      });
      if (!res) return;
      const data = await res.json();
      if (data.success) loadDatabases();
      else showError(data.error);
    }
  }

  async function deleteDatabase(id, name) {
    if (
      !confirm(
        `Are you sure you want to delete "${name}"?\nAll tables inside it will be lost.`
      )
    )
      return;

    const res = await apiFetch(`/api/databases/${id}`, { method: "DELETE" });
    if (!res) return;
    const data = await res.json();
    if (data.success) loadDatabases();
    else showError(data.error);
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
    backToDbScreen.addEventListener("click", () => loadDatabases());
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
    if (formData.getAll("files").length === 0) return;

    showLoading(true, "Uploading, parsing, and inferring types...");
    try {
      const response = await apiFetch("/api/upload", {
        method: "POST",
        body: formData,
      });
      if (!response) return;
      const data = await response.json();
      if (data.success) {
        renderSchema(data.schema);
        goChat.disabled = false;
        const schemaSize = Object.keys(data.schema).length;
        detectRelationshipsBtn.disabled = schemaSize < 2;
        if (schemaSize >= 2) await detectRelationships(true);
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

    const res = await apiFetch(`/api/tables/${tableId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    });
    if (!res) return;
    const data = await res.json();
    if (data.success) {
      alert("Table renamed successfully!");
      updateSchemaFromServer();
    } else showError(data.error);
  }

  async function handleDeleteTable(tableId) {
    const input = fileList.querySelector(`input[data-table-id="${tableId}"]`);
    if (!confirm(`Are you sure you want to delete the table "${input.value}"?`))
      return;

    const res = await apiFetch(`/api/tables/${tableId}`, { method: "DELETE" });
    if (!res) return;
    const data = await res.json();
    if (data.success) {
      alert("Table deleted.");
      updateSchemaFromServer();
    } else showError(data.error);
  }

  function closeCleaningModal() {
    pendingCleaning = null;
    cleanDataModal.classList.add("hidden");
    cleanDataReport.innerHTML = "";
    cleanDataApply.disabled = true;
  }

  async function handleCleaningPreview(tableId, tableName) {
    showLoading(true, "Profiling data quality locally…");
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
          <p class="text-[11px] text-slate-500 mt-1">${report.total_rows} source rows • ${report.estimated_output_rows} estimated cleaned rows</p>
        </div>
        <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-4">
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Duplicates</p><p class="font-semibold text-slate-700">${report.duplicate_rows}</p></div>
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Empty rows</p><p class="font-semibold text-slate-700">${report.empty_rows}</p></div>
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Whitespace cells</p><p class="font-semibold text-slate-700">${report.trimmed_cells}</p></div>
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Null markers</p><p class="font-semibold text-slate-700">${report.standardized_nulls}</p></div>
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Malformed rows</p><p class="font-semibold text-slate-700">${report.malformed_rows}</p></div>
          <div class="p-2 rounded-xl bg-slate-50"><p class="text-[10px] text-slate-400">Header changes</p><p class="font-semibold text-slate-700">${report.header_changes.length}</p></div>
        </div>`;

      if (result.requires_cleaning) {
        html += '<p class="font-medium text-slate-700 mb-2">Recommended plan</p><div class="space-y-2">';
        report.actions.forEach((action) => {
          html += `<div class="flex gap-2 p-2 rounded-xl border border-emerald-100 bg-emerald-50/50"><i data-lucide="check-circle-2" class="w-3.5 h-3.5 text-emerald-500 mt-0.5 shrink-0"></i><div><p class="text-slate-700">${escapeHtml(action.description)}</p><p class="text-[10px] text-slate-400">${action.affected} affected</p></div></div>`;
        });
        html += "</div>";
      } else {
        html += '<div class="p-3 rounded-xl bg-emerald-50 text-emerald-700">No automatically fixable issues were detected.</div>';
      }

      if (missing.length) {
        html += '<details class="mt-4"><summary class="cursor-pointer text-slate-600 font-medium">Missing values by column</summary><div class="mt-2 space-y-1">';
        missing.forEach(([column, count]) => {
          html += `<div class="flex justify-between text-[11px]"><span>${escapeHtml(column)}</span><span class="text-slate-400">${count}</span></div>`;
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
              <p class="text-slate-700 text-xs font-medium">${
                details.filename
              }</p>
              <p class="text-[11px] text-slate-400">${
                details.row_count || 0
              } rows</p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <input type="text" value="${tableName}" data-table-id="${
        details.id
      }"
                   class="w-28 p-1.5 border border-slate-200 rounded-lg text-[11px] focus:ring-1 focus:ring-sky-500">
            <button data-table-id="${details.id}" data-original-content="Rename"
                    class="rename-btn text-[10px] bg-slate-200 text-slate-600 px-2 py-1 rounded-md hover:bg-slate-300">
              Rename
            </button>
            <button data-table-id="${details.id}" data-table-name="${tableName}"
                    class="clean-btn text-[10px] bg-emerald-50 text-emerald-700 px-2 py-1 rounded-md hover:bg-emerald-100">
              Auto clean
            </button>
            <button data-table-id="${details.id}"
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
      tablesHTML += `<li class="flex items-center justify-between"><span>${tableName}</span><span class="text-slate-400">${
        details.row_count || 0
      } rows</span></li>`;
      let colList = "";
      if (details.types) {
        Object.entries(details.types).forEach(([colName, colType]) => {
          let typeColor =
            colType === "int" || colType === "float"
              ? "text-amber-600"
              : "text-emerald-600";
          colList += `<li class="flex justify-between"><span>${colName}</span><span class="${typeColor} font-medium">${colType}</span></li>`;
        });
      }
      columnsHTML += `<div><p class="font-semibold text-slate-600 mb-1">${tableName}</p><ul class="pl-2 space-y-0.5 text-slate-500">${colList}</ul></div>`;
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
      const relText = `<span class="font-semibold">${rel.from_table}.${rel.from_column}</span> → <span class="font-semibold">${rel.to_table}.${rel.to_column}</span>`;
      listHTML += `<div class="rounded-2xl border border-dashed border-emerald-200 bg-emerald-50/70 px-3 py-2.5 flex items-start gap-2"><i data-lucide="link-2" class="text-emerald-500 w-4 h-4 mt-[2px]"></i><div><p class="text-[11px] text-emerald-800 font-medium">Suggested relationship</p><p class="text-[11px] text-emerald-700">${relText}</p></div></div>`;
      schemaHTML += `<li class="text-emerald-700">${relText}</li>`;
    });
    schemaHTML += "</ul>";
    relationshipList.innerHTML = listHTML;
    const schemaRelElement = document.getElementById("schema-relationships");
    if (schemaRelElement) schemaRelElement.innerHTML = schemaHTML;
    lucide.createIcons();
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

  function escapeHtml(value) {
    const element = document.createElement("div");
    element.textContent = value === null || value === undefined ? "" : String(value);
    return element.innerHTML;
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
      )} • ${escapeHtml(agent.routing_tier)} routing • ${Number(
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
      html += `<p class="text-[10px] ${verifyColor} font-medium">${verifyLabel} • ${Math.round(
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
      )}</span><span class="text-slate-400"> • ${item.duration_ms || 0}ms</span><p class="text-slate-500">${escapeHtml(
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
        ? `${budget.turns_used} turns • ${budget.tool_calls_used} tool calls`
        : "Task completed";
    const plan = result.plan || [];
    const trace = result.trace || [];
    let html = "";
    if (result.agent) {
      html += `<div class="rounded-lg bg-violet-50 p-2 mb-3"><p class="text-[10px] font-medium text-violet-700">${escapeHtml(
        result.agent.model
      )}</p><p class="text-[9px] text-violet-500 mt-0.5">${escapeHtml(
        result.agent.routing_reason
      )} • ${Number(result.agent.examples_used || 0)} learned examples</p></div>`;
    }
    if (result.verification) {
      const verified = result.verification.passed;
      html += `<div class="rounded-lg ${
        verified ? "bg-emerald-50" : "bg-amber-50"
      } p-2 mb-3"><p class="text-[10px] font-medium ${
        verified ? "text-emerald-700" : "text-amber-700"
      }">${verified ? "Result verified" : "Verification warning"} • ${Math.round(
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
          ? "—"
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
      '<span class="text-[10px] text-slate-400">Building suggestions from columns…</span>';
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
        button.title = `${suggestion.question} — ${suggestion.reason}`;
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
    activeRequestId =
      window.crypto && window.crypto.randomUUID
        ? window.crypto.randomUUID()
        : `${Date.now()}-${Math.random()}`;
    activeController = new AbortController();
    if (agentActivity) {
      agentActivity.innerHTML =
        '<div class="text-[11px] text-violet-600 animate-pulse">Planning and running local tools…</div>';
    }
    if (agentBudget) agentBudget.textContent = "Agent running";

    const typingEl = document.createElement("div");
    typingEl.className = "flex justify-start";
    typingEl.innerHTML =
      '<div class="bg-white border border-slate-100 px-3 py-2.5 text-xs rounded-2xl shadow-sm text-violet-600">Agent is working…</div>';
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
      const response = await fetch("/api/auth/status");
      const data = await response.json();
      if (data.isLoggedIn) {
        await loadDatabases(); // User is logged in, show DB screen
      } else {
        showScreen(screens.auth); // User is not logged in
      }
    } catch (error) {
      showScreen(screens.auth);
    } finally {
      showLoading(false);
    }
  }

  // Start the app
  checkAuthStatus();
}); // --- End of DOMContentLoaded wrapper
