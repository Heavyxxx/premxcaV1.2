const STAGES = [
  { name: "Cotización Web", probability: 15 },
  { name: "Promo 15% Enviada", probability: 30 },
  { name: "En Negociación", probability: 55 },
  { name: "Apartado", probability: 80 },
  { name: "Boleto Confirmado", probability: 100 }
];
const DEPARTURES = [
  "Salida 1 • Febrero", "Salida 2 • Abril", "Salida 3 • Junio", "Salida 4 • Septiembre", "Salida 5 • Noviembre"
];
const TICKET_PRICE = 120000;
const THEME_STORAGE_KEY = "premxca-crm-theme";
let currentLeads = [];
let selectedLeadId = null;
let toastTimer;

const $ = (selector, root = document) => root.querySelector(selector);
const fmtMXN = (amount) => new Intl.NumberFormat("es-MX", {
  style: "currency", currency: "MXN", maximumFractionDigits: 0
}).format(Number(amount || 0));
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[char]));

async function readJsonResponse(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Error HTTP ${response.status}`);
  return data;
}

async function api(url, options) {
  return readJsonResponse(await fetch(url, options));
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 3600);
}

function quoteFor(tickets) {
  const count = Math.max(1, Math.min(20, Number(tickets) || 1));
  const subtotal = count * TICKET_PRICE;
  const discountedTickets = Math.floor(count / 2);
  const discount = Math.round(discountedTickets * TICKET_PRICE * 0.15);
  return { tickets: count, subtotal, discountedTickets, discount, total: subtotal - discount };
}

function quoteMarkup(tickets) {
  const quote = quoteFor(tickets);
  return `<span>Subtotal · ${quote.tickets} ${quote.tickets === 1 ? "boleto" : "boletos"}</span><strong>${fmtMXN(quote.subtotal)}</strong>
    <span>Boletos con 15% de descuento</span><strong>${quote.discountedTickets}</strong>
    <span>Descuento total</span><strong>−${fmtMXN(quote.discount)}</strong>
    <span class="quote-total">Total cotizado</span><strong class="quote-total">${fmtMXN(quote.total)}</strong>`;
}

function getVisibleLeads() {
  const query = $("#searchInput").value.trim().toLocaleLowerCase("es-MX");
  const stage = $("#stageFilter").value;
  const departure = $("#departureFilter").value;
  const promo = $("#promoFilter").value;
  const pendingOnly = $("#pendingTasksFilter").checked;
  return currentLeads.filter((lead) => {
    const searchText = [lead.name, lead.email, lead.phone, lead.source, lead.departure].join(" ").toLocaleLowerCase("es-MX");
    if (query && !searchText.includes(query)) return false;
    if (stage !== "ALL" && lead.stage !== stage) return false;
    if (departure !== "ALL" && lead.departure !== departure) return false;
    if (promo === "yes" && !lead.promo_second_ticket_15) return false;
    if (promo === "no" && lead.promo_second_ticket_15) return false;
    if (pendingOnly && !(lead.tasks || []).some((task) => !task.completed)) return false;
    return true;
  });
}

function updateStats(stats) {
  $("#kpiPipeline").textContent = fmtMXN(stats.pipeline_amount);
  $("#kpiWeighted").textContent = fmtMXN(stats.weighted_pipeline_amount);
  $("#kpiTotalLeads").textContent = `${stats.open_leads} oportunidades abiertas`;
  $("#kpiConfirmedAmount").textContent = fmtMXN(stats.confirmed_amount);
  $("#kpiConfirmedTickets").textContent = `${stats.confirmed_tickets} boletos confirmados`;
  $("#kpiTasks").textContent = String(stats.pending_tasks);
  $("#kpiConversion").textContent = `${stats.conversion_rate}% conversión · ${stats.promo_count} promos`;
  $("#storageBadge").textContent = stats.storage === "mongodb" ? `MongoDB · ${stats.database || "crm_premska"}` : "JSON local";
}

function renderTable(leads) {
  const body = $("#leadsTableBody");
  body.innerHTML = "";
  $("#recordsCount").textContent = `${leads.length} ${leads.length === 1 ? "registro" : "registros"}`;
  $("#emptyState").hidden = leads.length > 0;
  leads.forEach((lead) => {
    const probability = Number(lead.probability ?? 15);
    const pending = (lead.tasks || []).filter((task) => !task.completed).sort((a, b) => String(a.due_date).localeCompare(String(b.due_date)));
    const nextTask = pending[0];
    const row = document.createElement("tr");
    row.dataset.id = lead.id;
    row.classList.toggle("selected", lead.id === selectedLeadId);
    const initials = String(lead.name || "?").trim().split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase();
    row.innerHTML = `
      <td><div class="person-cell"><span class="avatar">${escapeHtml(initials)}</span><span><strong>${escapeHtml(lead.name)}</strong><small>${escapeHtml(lead.email || lead.phone || "Sin contacto")}</small></span></div></td>
      <td><span class="stage-pill">${escapeHtml(lead.stage || STAGES[0].name)}</span></td>
      <td class="probability-cell">${probability}%<div class="progress-track"><span style="width:${Math.max(0, Math.min(100, probability))}%"></span></div></td>
      <td class="money-cell">${fmtMXN(lead.total)}</td><td class="money-cell">${fmtMXN(Number(lead.total || 0) * probability / 100)}</td>
      <td class="secondary-cell">${escapeHtml(lead.departure || "—")}<br>${escapeHtml(lead.source || "Web")}</td>
      <td class="next-action">${nextTask ? `${escapeHtml(nextTask.type)} · ${escapeHtml(nextTask.due_date || "Sin fecha")}` : "Sin tarea pendiente"}</td>`;
    row.addEventListener("click", () => selectLead(lead.id));
    body.appendChild(row);
  });
}

function renderBoard(leads) {
  const board = $("#boardView");
  board.innerHTML = "";
  STAGES.forEach(({ name, probability }) => {
    const column = document.createElement("section");
    column.className = "kanban-column";
    column.dataset.stage = name;
    const stageLeads = leads.filter((lead) => lead.stage === name);
    column.innerHTML = `<div class="kanban-header"><span>${escapeHtml(name)}<small> · ${probability}%</small></span><small>${stageLeads.length}</small></div>`;
    column.addEventListener("dragover", (event) => {
      event.preventDefault();
      column.classList.add("drag-over");
    });
    column.addEventListener("dragleave", () => column.classList.remove("drag-over"));
    column.addEventListener("drop", async (event) => {
      event.preventDefault();
      column.classList.remove("drag-over");
      const id = event.dataTransfer.getData("text/plain");
      if (!id) return;
      try {
        await api(`/api/leads/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ stage: name }) });
        await refreshAll();
        showToast(`Etapa actualizada · ${name} (${probability}%)`);
      } catch (error) {
        showToast(`No se pudo cambiar la etapa: ${error.message}`);
      }
    });
    stageLeads.forEach((lead) => {
      const card = document.createElement("article");
      card.className = "kanban-card";
      card.draggable = true;
      card.innerHTML = `<strong>${escapeHtml(lead.name)}</strong><small>${escapeHtml(lead.departure || "Salida por confirmar")} · ${Number(lead.probability ?? probability)}%</small><span class="kanban-value">${fmtMXN(lead.total)}</span>`;
      card.addEventListener("dragstart", (event) => event.dataTransfer.setData("text/plain", lead.id));
      card.addEventListener("click", () => selectLead(lead.id));
      column.appendChild(card);
    });
    board.appendChild(column);
  });
}

function renderLeads(preserveDetail = false) {
  const leads = getVisibleLeads();
  renderTable(leads);
  renderBoard(leads);
  if (selectedLeadId && !currentLeads.some((lead) => lead.id === selectedLeadId)) showDetail(null);
  else if (selectedLeadId && !preserveDetail) {
    const selected = currentLeads.find((lead) => lead.id === selectedLeadId);
    if (selected) renderDetail(selected);
  }
}

function renderDetail(lead) {
  $("#detailEmpty").hidden = true;
  $("#leadForm").hidden = true;
  $("#leadDetail").hidden = !lead;
  if (!lead) return;
  $("#detailName").textContent = lead.name;
  $("#detailCreated").textContent = `Actualizado ${formatDateTime(lead.updated_at || lead.created_at)}`;
  const form = $("#editLeadForm");
  form.elements.id.value = lead.id;
  form.elements.name.value = lead.name || "";
  form.elements.email.value = lead.email || "";
  form.elements.phone.value = lead.phone || "";
  form.elements.source.value = lead.source || "";
  form.elements.departure.value = lead.departure || DEPARTURES[0];
  form.elements.tickets.value = String(lead.tickets || 1);
  form.elements.stage.innerHTML = STAGES.map(({ name }) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
  form.elements.stage.value = lead.stage || STAGES[0].name;
  form.elements.probability.value = Number(lead.probability ?? 15);
  $("#detailQuote").innerHTML = quoteMarkup(lead.tickets);
  renderTasks(lead);
  renderNotes(lead);
}

function formatDateTime(value) {
  if (!value) return "Fecha no disponible";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

function renderTasks(lead) {
  const tasks = [...(lead.tasks || [])].sort((a, b) => Number(a.completed) - Number(b.completed) || String(a.due_date).localeCompare(String(b.due_date)));
  const pendingCount = tasks.filter((task) => !task.completed).length;
  $("#taskCount").textContent = String(pendingCount);
  const list = $("#taskList");
  list.innerHTML = "";
  if (!tasks.length) {
    list.innerHTML = '<li class="no-items">Aún no hay acciones programadas.</li>';
    return;
  }
  const today = new Date().toISOString().slice(0, 10);
  tasks.forEach((task) => {
    const item = document.createElement("li");
    item.className = `task-item${task.completed ? " completed" : ""}${!task.completed && task.due_date < today ? " overdue" : ""}`;
    item.innerHTML = `<input type="checkbox" aria-label="Marcar tarea como completada" ${task.completed ? "checked" : ""}><span class="task-copy"><strong>${escapeHtml(task.type)}</strong><small>${task.due_date ? `Vence ${escapeHtml(task.due_date)}` : "Sin fecha límite"}</small></span>`;
    $("input", item).addEventListener("change", () => toggleTask(lead.id, task.id, item.querySelector("input").checked));
    list.appendChild(item);
  });
}

function renderNotes(lead) {
  const list = $("#notesList");
  const notes = [...(lead.notes_log || [])].reverse();
  list.innerHTML = notes.length ? notes.map((note) => `<li>${escapeHtml(note.text)}<small>${escapeHtml(formatDateTime(note.created_at))}</small></li>`).join("") : '<li class="no-items">Sin notas registradas.</li>';
}

function showDetail(lead) {
  selectedLeadId = lead ? lead.id : null;
  if (!lead) {
    $("#leadDetail").hidden = true;
    $("#leadForm").hidden = true;
    $("#detailEmpty").hidden = false;
  } else renderDetail(lead);
  renderTable(getVisibleLeads());
}

function selectLead(id) {
  const lead = currentLeads.find((item) => item.id === id);
  if (!lead) return;
  showDetail(lead);
}

function showNewLeadForm() {
  selectedLeadId = null;
  $("#detailEmpty").hidden = true;
  $("#leadDetail").hidden = true;
  $("#leadForm").hidden = false;
  $("#leadForm").reset();
  $("#newQuotePreview").innerHTML = quoteMarkup(1);
  renderTable(getVisibleLeads());
  $("#leadForm").elements.name.focus();
}

async function toggleTask(leadId, taskId, completed) {
  try {
    await api(`/api/leads/${encodeURIComponent(leadId)}/tasks/${encodeURIComponent(taskId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ completed })
    });
    await refreshAll();
  } catch (error) {
    showToast(`No se pudo actualizar la tarea: ${error.message}`);
    await refreshAll();
  }
}

async function refreshAll({ preserveDetail = false } = {}) {
  const [stats, data] = await Promise.all([
    api("/api/stats"),
    api("/api/leads")
  ]);
  currentLeads = data.leads || [];
  updateStats(stats);
  renderLeads(preserveDetail);
}

function setView(view) {
  const table = view === "table";
  $("#tableView").hidden = !table;
  $("#boardView").hidden = table;
  $("#btnTableView").classList.toggle("active", table);
  $("#btnBoardView").classList.toggle("active", !table);
  $("#btnTableView").setAttribute("aria-pressed", String(table));
  $("#btnBoardView").setAttribute("aria-pressed", String(!table));
}

function exportData(format) {
  window.location.assign(`/api/export?format=${encodeURIComponent(format)}`);
}

function setDefaultTaskDate() {
  const due = new Date();
  due.setDate(due.getDate() + 1);
  $("#taskForm").elements.due_date.value = due.toISOString().slice(0, 10);
}

function applyTheme(theme) {
  const navyTheme = theme === "navy";
  document.body.classList.toggle("theme-navy", navyTheme);
  const button = $("#btnThemeToggle");
  button.setAttribute("aria-pressed", String(navyTheme));
  button.setAttribute("aria-label", navyTheme ? "Cambiar a tema claro" : "Cambiar a tema azul marino");
  button.innerHTML = navyTheme
    ? '<i class="ph ph-sun"></i><span>Tema claro</span>'
    : '<i class="ph ph-moon"></i><span>Tema azul</span>';
  document.querySelector('meta[name="theme-color"]').content = navyTheme ? "#061224" : "#ffffff";
}

function initializeTheme() {
  let savedTheme = "light";
  try {
    savedTheme = localStorage.getItem(THEME_STORAGE_KEY) || "light";
  } catch (error) {
    showToast(`No se pudo leer la preferencia de tema: ${error.message}`);
  }
  applyTheme(savedTheme);
}

$("#leadForm").addEventListener("input", (event) => {
  if (event.target.name === "tickets") $("#newQuotePreview").innerHTML = quoteMarkup(event.target.value);
});
$("#editLeadForm").addEventListener("input", (event) => {
  if (event.target.name === "tickets") $("#detailQuote").innerHTML = quoteMarkup(event.target.value);
});
$("#editLeadForm").elements.stage.addEventListener("change", (event) => {
  const stage = STAGES.find((item) => item.name === event.target.value);
  if (stage) $("#editLeadForm").elements.probability.value = stage.probability;
});
$("#leadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.tickets = Number(payload.tickets);
  try {
    const result = await api("/api/leads", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    await refreshAll();
    selectLead(result.lead.id);
    showToast("Prospecto registrado correctamente.");
  } catch (error) {
    showToast(`No se pudo crear el prospecto: ${error.message}`);
  }
});
$("#editLeadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form).entries());
  const id = payload.id;
  delete payload.id;
  payload.tickets = Number(payload.tickets);
  payload.probability = Number(payload.probability);
  try {
    await api(`/api/leads/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    await refreshAll();
    showToast("Cambios guardados.");
  } catch (error) {
    showToast(`No se pudieron guardar los cambios: ${error.message}`);
  }
});
$("#taskForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedLeadId) return;
  const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
  try {
    await api(`/api/leads/${encodeURIComponent(selectedLeadId)}/tasks`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    event.currentTarget.reset();
    setDefaultTaskDate();
    await refreshAll();
    showToast("Acción agregada.");
  } catch (error) {
    showToast(`No se pudo agregar la acción: ${error.message}`);
  }
});
$("#noteForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedLeadId) return;
  const form = event.currentTarget;
  const note = form.elements.note.value.trim();
  try {
    await api(`/api/leads/${encodeURIComponent(selectedLeadId)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ new_note: note }) });
    form.reset();
    await refreshAll();
    showToast("Nota agregada a la bitácora.");
  } catch (error) {
    showToast(`No se pudo guardar la nota: ${error.message}`);
  }
});
$("#btnDeleteLead").addEventListener("click", async () => {
  if (!selectedLeadId || !window.confirm("¿Eliminar este prospecto y su historial?")) return;
  try {
    await api(`/api/leads/${encodeURIComponent(selectedLeadId)}`, { method: "DELETE" });
    selectedLeadId = null;
    await refreshAll();
    showToast("Prospecto eliminado.");
  } catch (error) {
    showToast(`No se pudo eliminar: ${error.message}`);
  }
});
$("#btnNewLead").addEventListener("click", showNewLeadForm);
$("#btnThemeToggle").addEventListener("click", () => {
  const theme = document.body.classList.contains("theme-navy") ? "light" : "navy";
  applyTheme(theme);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch (error) {
    showToast(`No se pudo guardar la preferencia de tema: ${error.message}`);
  }
});
$("#emptyCreateLead").addEventListener("click", showNewLeadForm);
$("#btnCloseNewLead").addEventListener("click", () => showDetail(null));
$("#btnCancelLead").addEventListener("click", () => showDetail(null));
$("#btnCloseDetail").addEventListener("click", () => showDetail(null));
$("#btnRefresh").addEventListener("click", () => refreshAll().catch((error) => showToast(`No se pudo actualizar: ${error.message}`)));
$("#btnTableView").addEventListener("click", () => setView("table"));
$("#btnBoardView").addEventListener("click", () => setView("board"));
$("#btnExportJson").addEventListener("click", () => exportData("json"));
$("#btnExportCsv").addEventListener("click", () => exportData("csv"));
["searchInput", "stageFilter", "departureFilter", "promoFilter", "pendingTasksFilter"].forEach((id) => {
  $(`#${id}`).addEventListener(id === "searchInput" ? "input" : "change", () => renderLeads(true));
});
$("#leadForm").elements.tickets.addEventListener("input", (event) => { $("#newQuotePreview").innerHTML = quoteMarkup(event.target.value); });
$("#editLeadForm").elements.tickets.addEventListener("input", (event) => { $("#detailQuote").innerHTML = quoteMarkup(event.target.value); });
STAGES.forEach(({ name }) => {
  const option = document.createElement("option");
  option.value = name;
  option.textContent = name;
  $("#stageFilter").appendChild(option);
});
setDefaultTaskDate();
initializeTheme();
refreshAll().catch((error) => showToast(`No se pudo cargar el CRM: ${error.message}`));
window.setInterval(() => {
  if (!document.hidden) {
    refreshAll({ preserveDetail: true }).catch((error) => showToast(`No se pudo sincronizar el CRM: ${error.message}`));
  }
}, 5000);
