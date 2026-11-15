"use strict";

const byId = (id) => document.getElementById(id);
const themeSelect = byId("theme");
const themes = ["system", "light", "dark"];
let savedTheme = "system";
try { savedTheme = localStorage.getItem("multi-agent-theme") || savedTheme; } catch {}
themeSelect.value = themes.includes(savedTheme) ? savedTheme : "system";
document.documentElement.dataset.theme = themeSelect.value;
themeSelect.addEventListener("change", () => {
  document.documentElement.dataset.theme = themeSelect.value;
  try { localStorage.setItem("multi-agent-theme", themeSelect.value); } catch {}
});
const form = byId("job-form");
const status = byId("status");
const progress = byId("progress");
const nav = byId("output-nav");
const content = byId("content");
const download = byId("download");
const stages = {
  starting: ["Başlıyor", 0], plan: ["Planlanıyor", 0],
  board_ready: ["Pano görevi alındı", 1],
  code: ["Kodlanıyor", 1], repair: ["Düzeltiliyor", 1],
  test: ["Test ediliyor", 1], review: ["Denetleniyor", 2], observe: ["Ek ajanlar inceliyor", 2],
  plan_review: ["Plan denetleniyor", 2], plan_revision: ["Plan gözden geçiriliyor", 0],
  plan_ready: ["Plan hazır", 1], HUMAN_REVIEW: ["İncelemeye hazır", 3],
  CHANGES_REQUESTED: ["Düzeltme gerekli", 3], CHECK_REQUIRED: ["Test gerekli", 3],
  applying: ["Uygulanıyor", 3], applied: ["Uygulandı", 3], reverted: ["Geri alındı", 3],
  error: ["Akış durdu", -1],
};
const outputNames = {plan: "Plan", code: "Geliştirme günlüğü", diff: "Git farkı", test: "Test",
                     review: "Denetim", observers: "Ek ajan gözlemleri"};
const decisions = {
  HUMAN_REVIEW: "Test geçti. Yerel denetçi bir engel bildirmedi.",
  CHANGES_REQUESTED: "Denetçi bulgu bildirdi veya test başarısız oldu.",
  CHECK_REQUIRED: "Bağımsız test sonucu gerekli.",
};
let submitting = false;
let polling = false;
let job = null;
let selected = "";
let navKey = "";
let outputRequest = 0;
let liveKey = "";
let applyJobId = null;
let applying = false;
const applyDialog = byId("apply-dialog");
const confirmApply = byId("confirm-apply");
const cancelApply = byId("cancel-apply");
const applyError = byId("apply-error");
const revertDialog = byId("revert-dialog");
const openRevert = byId("open-revert");
const openReapply = byId("open-reapply");
const confirmRevert = byId("confirm-revert");
const cancelRevert = byId("cancel-revert");
const revertError = byId("revert-error");
let application = null;
let revertId = null;
let applicationAction = "revert";
let reverting = false;
let availableModelList = [];
let editingAgent = null;
let projectTeam = null;
let teamLoad = 0;
let savingAgent = false;
let fetchingModels = false;
let preparingProject = false;
let sendingPlan = false;
let planKey = "";
const modelRoles = ["planner", "reviewer"];
const liveCards = {};
const agentSources = {Mimar: "plan", Codex: "code", "Denetçi": "review"};
const timeFormat = new Intl.DateTimeFormat("tr-TR", {hour: "2-digit", minute: "2-digit", second: "2-digit"});

const baseAgents = {plan: "Planlama", code: "Geliştirme", review: "Denetim"};
function addLiveCard(key, label) {
  const card = byId("agent-template").content.firstElementChild.cloneNode(true);
  card.dataset.agent = baseAgents[key] ? key : "custom";
  const fields = Object.fromEntries(Array.from(card.querySelectorAll("[data-label]"), (node) => [node.dataset.label, node]));
  fields.name.textContent = label;
  fields.output.setAttribute("aria-label", `${label} canlı çıktısı`);
  byId("agent-cards").append(card);
  liveCards[key] = {card, ...fields};
}
for (const [key, label] of Object.entries(baseAgents)) addLiveCard(key, label);

function renderLive(current, phase) {
  byId("live-stage").textContent = phase;
  for (const [key, agent] of Object.entries(current.agents)) {
    if (!liveCards[key]) addLiveCard(key, agent.label);
  }
  for (const [key, fields] of Object.entries(liveCards)) {
    const agent = current.agents[key];
    fields.card.hidden = !agent;
    if (!agent) continue;
    fields.name.textContent = agent.label || baseAgents[key];
    fields.card.classList.toggle("is-working", agent.state === "Çalışıyor");
    const detail = [agent.branch_label, agent.role_label, agent.model].filter(Boolean).join(" · ");
    fields.model.hidden = !detail;
    fields.model.textContent = detail;
    fields.state.textContent = agent.state;
    fields.state.dataset.state = agent.state;
    fields.activity.textContent = agent.activity;
    fields.decision.textContent = agent.decision || "Henüz karar yok.";
    const output = agent.output || "Henüz çıktı yok.";
    if (fields.output.textContent !== output) {
      const atBottom = fields.output.scrollHeight - fields.output.scrollTop - fields.output.clientHeight < 24;
      fields.output.textContent = output;
      if (atBottom) fields.output.scrollTop = fields.output.scrollHeight;
    }
  }
  const timeline = byId("timeline");
  const key = current.id + (current.timeline.at(-1)?.time || "");
  if (key === liveKey) return;
  liveKey = key;
  const entries = current.timeline.map((event) => {
    const item = document.createElement("li");
    item.dataset.agent = agentSources[event.source] || (event.source === "Panel" ? "panel" : "custom");
    const time = document.createElement("time");
    time.dateTime = event.time;
    time.textContent = timeFormat.format(new Date(event.time));
    const source = document.createElement("strong");
    source.className = "timeline-source";
    source.textContent = event.source;
    item.append(time, source, ` ${event.message}`);
    return item;
  });
  timeline.replaceChildren(...entries);
  timeline.scrollTop = timeline.scrollHeight;
}

function toggleButtons() {
  const assigned = new Set((projectTeam?.members || []).filter(agent => agent.enabled).map(agent => agent.slot));
  for (const button of form.querySelectorAll('button[type="submit"]')) {
    const coding = assigned.has("code") && assigned.has("review");
    const ready = button.value === "direct" ? coding : assigned.has("plan") && (button.value !== "execute" || coding);
    button.disabled = !ready || preparingProject || submitting || Boolean(job?.running);
  }
}

function updateModelLabels() {
  for (const role of modelRoles) {
    const slot = role === "planner" ? "plan" : "review";
    const member = job?.running ? job.slots?.[slot] : projectTeam?.members.find(agent => agent.enabled && agent.slot === slot);
    byId(`${role}-name`).textContent = member ? `${member.name} · ${member.model || member.provider}`
      : "Atama yok";
  }
  const code = job?.running ? job.slots?.code : projectTeam?.members.find(agent => agent.enabled && agent.slot === "code");
  byId("step-code").querySelector("small").textContent = code ? `${code.name} · ${code.model || code.provider}`
    : "Atama yok";
}

async function loadModels() {
  if (fetchingModels) return;
  fetchingModels = true;
  toggleButtons();
  const refreshButton = byId("refresh-models");
  const modelStatus = byId("models-status");
  refreshButton.disabled = true;
  modelStatus.textContent = "Ollama modelleri yükleniyor…";
  try {
    const response = await fetch("/api/models");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    availableModelList = data.models;
    refreshAgentModels();
    modelStatus.textContent = data.models.length
      ? `${data.models.length} yerel metin modeli`
      : "Ollama’da kullanılabilir yerel metin modeli bulunamadı.";
  } catch (error) {
    modelStatus.textContent = error.message || "Model listesi yüklenemedi.";
  } finally {
    fetchingModels = false;
    refreshButton.disabled = false;
    toggleButtons();
    updateModelLabels();
  }
}

function refreshAgentModels(chosen = byId("agent-model").value) {
  const names = new Set(availableModelList);
  if (chosen) names.add(chosen);
  const model = byId("agent-model");
  model.replaceChildren(new Option("Model seç", ""), ...Array.from(names, name => new Option(name, name)));
  model.value = chosen;
}

function openAgent(agent = null, branch = agent?.branch || "management") {
  editingAgent = agent;
  byId("project-agent-fields").hidden = false;
  byId("remove-project-agent").hidden = !agent;
  byId("agent-title").textContent = agent ? `@${agent.handle} · Ajanı düzenle` : "Ajan oluştur";
  byId("agent-name").value = agent?.name || "";
  byId("agent-duty").value = agent?.duty || "";
  byId("agent-watch").checked = agent?.watch || false;
  byId("agent-enabled").checked = agent?.enabled ?? true;
  byId("agent-provider").value = agent?.provider || "ollama";
  byId("agent-slot").value = agent?.slot || "member";
  byId("agent-handle").value = agent?.handle || "";
  const branchField = byId("agent-branch");
  branchField.replaceChildren(...projectTeam.catalog.branches.map(item => new Option(item.label, item.id)));
  branchField.value = branch;
  updateAgentRoles(agent?.role || "");
  byId("agent-api-url").value = agent?.endpoint || "";
  byId("agent-key-env").value = agent?.key_env || "";
  byId("agent-model-id").value = agent?.model || "";
  refreshAgentModels(agent?.provider === "ollama" ? agent.model : "");
  byId("agent-error").hidden = true;
  updateAgentProvider();
  byId("agent-dialog").showModal();
}

byId("add-project-member").addEventListener("click", () => { if (projectTeam) openAgent(); });
function updateAgentRoles(selected = "") {
  const branch = byId("agent-branch").value;
  const field = byId("agent-template-role");
  const roles = projectTeam.catalog.roles.filter(role => role.branch === branch);
  field.replaceChildren(new Option("Elle yaz", ""), ...roles.map(role => new Option(role.label, role.id)));
  field.value = roles.some(role => role.id === selected) ? selected : "";
}
byId("agent-branch").addEventListener("change", () => updateAgentRoles());
byId("agent-template-role").addEventListener("change", event => {
  const preset = projectTeam.catalog.roles.find(role => role.id === event.target.value);
  if (!preset) return;
  const occupied = projectTeam.members.some(agent => agent.enabled && agent.id !== editingAgent?.id
    && agent.slot === preset.slot);
  byId("agent-slot").value = occupied ? "member" : preset.slot;
  byId("agent-duty").value = preset.duty;
  if (!byId("agent-name").value.trim()) byId("agent-name").value = preset.label;
});

function updateAgentProvider() {
  const provider = byId("agent-provider").value;
  const external = ["openai", "anthropic"].includes(provider);
  const local = provider === "ollama";
  byId("agent-api-fields").hidden = !external;
  byId("agent-model-id-row").hidden = local;
  byId("agent-ollama-row").hidden = !local;
  byId("agent-api-url").placeholder = provider === "anthropic" ? "https://api.anthropic.com/v1" : "https://api.openai.com/v1";
  byId("agent-key-env").placeholder = provider === "anthropic" ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY";
}
byId("agent-provider").addEventListener("change", updateAgentProvider);

function renderProjectTeam(data) {
  projectTeam = data;
  const list = byId("project-team-list");
  byId("project-team-status").textContent = data ? (data.repo
    ? (data.inherited ? "Bu proje genel ekibi kullanıyor. Burada yaptığın düzenlemeler bu projeye kaydedilir."
      : `Proje ekibi: ${data.repo}`)
    : "Genel ekip · Projeye özel ekip tanımlanmadığında kullanılır.") + (data.members.length ? "" : " İlk ajanı ekle.") : "";
  byId("add-project-member").disabled = !data;
  const labels = {plan: "Planlama", code: "Geliştirme", review: "Denetim", member: "Ekip üyesi"};
  const sections = (data?.catalog.branches || []).map(branch => {
    const section = document.createElement("section");
    section.className = "team-branch";
    const header = document.createElement("div");
    header.className = "result-head";
    const title = document.createElement("h3");
    title.textContent = branch.label;
    const add = document.createElement("button");
    add.type = "button";
    add.className = "secondary";
    add.textContent = "+ Ajan";
    add.setAttribute("aria-label", branch.label + " dalına ajan ekle");
    add.addEventListener("click", () => openAgent(null, branch.id));
    header.append(title, add);
    const description = document.createElement("p");
    description.className = "hint";
    description.textContent = branch.description;
    section.append(header, description);
    const agents = data.members.filter(agent => agent.branch === branch.id);
    for (const agent of agents) {
      const row = document.createElement("div");
      row.className = "extra-agent-row";
      const line = document.createElement("p");
      line.textContent = `${agent.name} · @${agent.handle} · ${agent.role_label || labels[agent.slot]} · ${agent.model || agent.provider}${agent.enabled ? "" : " · Devre dışı"}`;
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "secondary";
      edit.textContent = "Düzenle";
      edit.setAttribute("aria-label", `${agent.name} proje ajanını düzenle`);
      edit.addEventListener("click", () => openAgent(agent));
      row.append(line, edit);
      section.append(row);
    }
    return section;
  });
  list.replaceChildren(...sections);
  toggleButtons();
  updateModelLabels();
}

async function loadProjectTeam(path = byId("repo").value) {
  const request = ++teamLoad;
  byId("add-project-member").disabled = true;
  try {
    const response = await fetch(`/api/project-team?repo=${encodeURIComponent(path)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    if (request === teamLoad) renderProjectTeam(data);
  } catch (error) {
    if (request !== teamLoad) return;
    renderProjectTeam(null);
    byId("project-team-status").textContent = error.message;
  }
}
byId("open-project-team").addEventListener("click", () => loadProjectTeam());
byId("open-default-team").addEventListener("click", () => loadProjectTeam(""));
byId("repo").addEventListener("change", () => {
  if (byId("repo").value !== projectTeam?.repo) loadProjectTeam();
});
byId("cancel-agent").addEventListener("click", () => byId("agent-dialog").close());
byId("agent-dialog").addEventListener("cancel", event => { if (savingAgent) event.preventDefault(); });
byId("remove-project-agent").addEventListener("click", async () => {
  if (!editingAgent || savingAgent
      || !window.confirm(`${editingAgent.name} ekipten çıkarılsın mı? Eski mesajları ve kararları saklanır.`)) return;
  savingAgent = true;
  try {
    const response = await fetch("/api/project-team", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: "remove", repo: projectTeam.repo, id: editingAgent.id})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    renderProjectTeam(data);
    byId("agent-dialog").close();
  } catch (error) {
    byId("agent-error").textContent = error.message;
    byId("agent-error").hidden = false;
  } finally {
    savingAgent = false;
  }
});
byId("save-agent").addEventListener("click", async () => {
  if (savingAgent) return;
  savingAgent = true;
  const button = byId("save-agent");
  const cancel = byId("cancel-agent");
  const errorBox = byId("agent-error");
  button.disabled = cancel.disabled = true;
  errorBox.hidden = true;
  try {
    const provider = byId("agent-provider").value;
    const response = await fetch("/api/project-team", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id: editingAgent?.id, name: byId("agent-name").value,
        model: provider === "ollama" ? byId("agent-model").value : byId("agent-model-id").value,
        duty: byId("agent-duty").value, watch: byId("agent-watch").checked, enabled: byId("agent-enabled").checked,
        repo: projectTeam.repo, provider, handle: byId("agent-handle").value,
        slot: byId("agent-slot").value, branch: byId("agent-branch").value,
        role: byId("agent-template-role").value, endpoint: byId("agent-api-url").value, key_env: byId("agent-key-env").value}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    renderProjectTeam(data);
    byId("agent-dialog").close();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    savingAgent = false;
    button.disabled = cancel.disabled = false;
  }
});

async function selectOutput(key) {
  const request = ++outputRequest;
  selected = key;
  for (const button of nav.children) {
    button.setAttribute("aria-pressed", String(button.dataset.output === key));
  }
  const url = `/api/runs/${job.id}/${key}`;
  download.href = `${url}?download=1`;
  content.textContent = "Yükleniyor…";
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error("Çıktı yüklenemedi.");
    let value = await response.text();
    if (key.startsWith("review-")) {
      const result = JSON.parse(value);
      value = [decisions[result.status], result.review.reason, ...result.review.findings].filter(Boolean).join("\n\n");
    }
    if (request === outputRequest) content.textContent = value || "Dosya boş.";
  } catch (error) {
    if (request === outputRequest) content.textContent = error.message;
  }
}

function render(current) {
  job = current;
  toggleButtons();
  updateModelLabels();
  renderPlan(current);
  if (!job) return;
  byId("apply-actions").hidden = job.stage !== "HUMAN_REVIEW" || job.running || !job.target;
  const [label, position] = stages[job.stage] || ["Çalışıyor", 0];
  renderLive(job, label);
  status.textContent = label;
  status.dataset.kind = ["error", "CHANGES_REQUESTED", "CHECK_REQUIRED"].includes(job.stage) ? "error" : "";
  const message = job.stage === "plan" ? "Yerel mimar görev planını hazırlıyor." : job.message;
  if (progress.textContent !== message) progress.textContent = message;
  const displayPosition = position < 0 ? stages[job.failed_stage]?.[1] ?? 0 : position;
  ["plan", "code", "review"].forEach((name, index) => {
    const step = byId(`step-${name}`);
    step.classList.toggle("active", job.agents[name].state === "Çalışıyor");
    step.classList.toggle("done", index < displayPosition);
  });
  const folder = byId("folder");
  folder.hidden = false;
  folder.textContent = job.folder;
  byId("outputs").hidden = job.artifacts.length === 0;
  const updatedKey = [job.id, job.running, ...job.artifacts].join("/");
  if (updatedKey === navKey) return;
  const sameJob = navKey.startsWith(`${job.id}/`);
  navKey = updatedKey;
  ++outputRequest;
  nav.replaceChildren();
  if (!job.artifacts.length) return;
  for (const key of job.artifacts) {
    const button = document.createElement("button");
    button.type = "button";
    const [kind, round] = key.split("-");
    button.textContent = round ? `Tur ${round} · ${outputNames[kind]}` : outputNames[kind];
    button.dataset.output = key;
    button.addEventListener("click", () => selectOutput(key));
    nav.append(button);
  }
  const latest = job.artifacts.at(-1);
  selectOutput(sameJob && job.artifacts.includes(selected) && job.running ? selected : latest);
}

function renderPlan(current) {
  const editor = byId("plan-editor");
  const visible = Boolean(current?.plan && ["plan_ready", "plan_review", "plan_revision"].includes(current.stage));
  editor.hidden = !visible;
  if (!visible) return;
  const key = `${current.id}/${current.plan_version}`;
  const field = byId("plan-text");
  if (key !== planKey) {
    field.value = current.plan;
    planKey = key;
  }
  const busy = current.running || sendingPlan;
  field.readOnly = busy;
  byId("review-plan").disabled = busy;
  byId("execute-plan").disabled = busy;
  byId("execute-plan").textContent = current.slots?.code ? `${current.slots.code.name} ajanına gönder` : "Codex’e gönder";
  byId("plan-review-box").hidden = !current.plan_review;
  const review = byId("plan-review-text");
  if (review.textContent !== (current.plan_review || "")) {
    review.textContent = current.plan_review || "";
    if (current.plan_review) byId("plan-review-box").open = true;
  }
}

async function sendPlan(action) {
  if (!job || job.stage !== "plan_ready" || job.running || sendingPlan) return;
  const plan = byId("plan-text").value;
  const errorBox = byId("plan-error");
  errorBox.hidden = true;
  if (!plan.trim()) {
    errorBox.textContent = "Gönderilecek planı yaz.";
    errorBox.hidden = false;
    return;
  }
  sendingPlan = true;
  renderPlan(job);
  try {
    const response = await fetch("/api/plan", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id: job.id, version: job.plan_version, action, plan, check: byId("check").value}),
    });
    if (!response.ok) throw new Error((await response.json()).error);
    job.running = true;
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    sendingPlan = false;
    renderPlan(job);
    toggleButtons();
    refresh();
  }
}

byId("review-plan").addEventListener("click", () => sendPlan("review"));
byId("execute-plan").addEventListener("click", () => sendPlan("execute"));

byId("open-apply").addEventListener("click", async () => {
  if (!job?.target || job.running || job.stage !== "HUMAN_REVIEW") return;
  const id = job.id;
  applyJobId = id;
  const target = job.target;
  byId("apply-target").textContent = `${target.repo} · ${target.branch} · ${target.base.slice(0, 12)}`;
  const preview = byId("apply-preview");
  preview.textContent = "Git farkı yükleniyor…";
  applyError.hidden = true;
  confirmApply.disabled = true;
  applyDialog.showModal();
  cancelApply.focus();
  try {
    const response = await fetch(`/api/runs/${id}/approved-diff`);
    if (!response.ok) throw new Error("Onaylanabilir Git farkı artık mevcut değil.");
    const diff = await response.text();
    if (applyJobId !== id || !applyDialog.open) return;
    preview.textContent = diff;
    confirmApply.disabled = false;
  } catch (error) {
    if (applyJobId === id && applyDialog.open) preview.textContent = error.message;
  }
});

cancelApply.addEventListener("click", () => applyDialog.close());
applyDialog.addEventListener("cancel", (event) => {
  if (applying) event.preventDefault();
});
confirmApply.addEventListener("click", async () => {
  if (applying || !applyJobId) return;
  applying = true;
  confirmApply.disabled = true;
  cancelApply.disabled = true;
  applyError.hidden = true;
  try {
    const response = await fetch("/api/apply", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id: applyJobId}),
    });
    if (!response.ok) throw new Error((await response.json()).error);
    applyDialog.close();
  } catch (error) {
    applyError.textContent = error.message;
    applyError.hidden = false;
    confirmApply.disabled = false;
  } finally {
    applying = false;
    cancelApply.disabled = false;
    refresh();
  }
});

function renderApplication(current) {
  application = current;
  byId("last-application").hidden = !current;
  if (!current) return;
  const reverted = current.state === "reverted";
  const applied = current.state === "applied";
  const labels = {
    reverted: "Geri alındı", applied: "Uygulandı",
    revert_blocked: "Geri alma doğrulanamadı", reapply_blocked: "Yeniden uygulama doğrulanamadı",
  };
  byId("application-target").textContent =
    `${labels[current.state]} · ${current.repo} · ${current.branch}`;
  openRevert.hidden = !applied;
  openRevert.disabled = current.busy || reverting;
  openReapply.hidden = !reverted;
  openReapply.disabled = current.busy || reverting;
}

function openApplicationDialog(action) {
  const redo = action === "reapply";
  if (!application || application.state !== (redo ? "reverted" : "applied") || application.busy) return;
  applicationAction = action;
  byId("revert-title").textContent = redo ? "Son değişikliği yeniden uygula" : "Son değişikliği geri al";
  byId("revert-description").textContent = redo
    ? "Geri alınan Git farkı kaynak projeye yeniden yazılacak."
    : "Panelin uyguladığı Git farkı kaynak projede tersine çevrilecek.";
  confirmRevert.textContent = redo ? "Onayla ve yeniden uygula" : "Onayla ve geri al";
  revertId = application.id;
  byId("revert-target").textContent = `${application.repo} · ${application.branch}`;
  revertError.hidden = true;
  revertDialog.showModal();
  cancelRevert.focus();
}

openRevert.addEventListener("click", () => openApplicationDialog("revert"));
openReapply.addEventListener("click", () => openApplicationDialog("reapply"));

cancelRevert.addEventListener("click", () => revertDialog.close());
revertDialog.addEventListener("cancel", (event) => {
  if (reverting) event.preventDefault();
});
confirmRevert.addEventListener("click", async () => {
  if (!revertId || reverting) return;
  reverting = true;
  confirmRevert.disabled = true;
  cancelRevert.disabled = true;
  revertError.hidden = true;
  try {
    const response = await fetch(`/api/${applicationAction}`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id: revertId}),
    });
    if (!response.ok) throw new Error((await response.json()).error);
    revertDialog.close();
  } catch (error) {
    revertError.textContent = error.message;
    revertError.hidden = false;
  } finally {
    reverting = false;
    confirmRevert.disabled = false;
    cancelRevert.disabled = false;
    refresh();
  }
});

async function refresh() {
  if (polling || submitting || sendingPlan) return;
  polling = true;
  try {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error();
    const state = await response.json();
    if (!job && state.job && !repo.value.trim()) {
      repo.value = state.job.project;
      preparedMemoryProject = state.job.project;
      if (state.job.board_id) selectTopic(state.job.board_id);
      byId("check").value = (state.job.check || []).join("\n");
      loadProjectTeam(state.job.project);
      refreshBoard(true);
    }
    renderApplication(state.application);
    render(state.job);
  } catch {
    progress.textContent = "Yerel sunucuya ulaşılamıyor. Başlatıcı penceresini kontrol et.";
  } finally {
    polling = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (event.submitter?.disabled || preparingProject || submitting || job?.running) return;
  const errorBox = byId("form-error");
  errorBox.hidden = true;
  const fields = Object.fromEntries(new FormData(form));
  const mode = event.submitter?.value || "plan";
  fields.execute = mode !== "plan";
  fields.direct = mode === "direct";
  fields.board_id = taskTopicFor(fields.repo);
  submitting = true;
  toggleButtons();
  try {
    const response = await fetch("/api/run", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(fields),
    });
    if (!response.ok) throw new Error((await response.json()).error);
    const result = await response.json();
    if (result.topic && result.topic !== topicId) {
      const submitted = byId("task").value;
      selectTopic(result.topic);
      byId("task").value = submitted;
    }
    refreshBoard(true);
    progress.textContent = "Görev başlatıldı.";
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    submitting = false;
    toggleButtons();
    refresh();
  }
});

byId("refresh-models").addEventListener("click", loadModels);
byId("prepare-project").addEventListener("click", async () => {
  const repo = byId("repo");
  const button = byId("prepare-project");
  const projectStatus = byId("project-status");
  if (preparingProject || !repo.reportValidity()) return;
  preparingProject = true;
  button.disabled = true;
  toggleButtons();
  projectStatus.hidden = false;
  projectStatus.classList.remove("error");
  projectStatus.textContent = "Boş proje hazırlanıyor…";
  try {
    const response = await fetch("/api/project", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({repo: repo.value}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    repo.value = data.repo;
    await openWorkspaceProject(data.repo);
    projectStatus.textContent = "Proje hazır. Görevini yazıp akışı başlatabilirsin.";
  } catch (error) {
    projectStatus.classList.add("error");
    projectStatus.textContent = error.message;
  } finally {
    preparingProject = false;
    button.disabled = false;
    toggleButtons();
  }
});
byId("repo").addEventListener("input", () => { byId("project-status").hidden = true; });
loadModels();
loadProjectTeam("");
refresh();
setInterval(refresh, 1200);
