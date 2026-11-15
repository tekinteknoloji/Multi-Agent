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
                     integration: "Birleşik entegrasyon testi", coverage: "Dosya bazlı denetim",
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
    const response = await fetch(key.startsWith("coverage-") ? download.href : url);
    if (!response.ok) throw new Error("Çıktı yüklenemedi.");
    let value = await response.text();
    if (key.startsWith("review-")) {
      const result = JSON.parse(value);
      value = [decisions[result.status], result.review.reason, ...result.review.findings].filter(Boolean).join("\n\n");
    }
    if (key.startsWith("coverage-")) value = formatFileReview(JSON.parse(value));
    if (request === outputRequest) content.textContent = value || "Dosya boş.";
  } catch (error) {
    if (request === outputRequest) content.textContent = error.message;
  }
}

function formatFileReview(record) {
  const decisions = new Map(record.decisions.map(item => [item.part, item.decision]));
  return record.files.map(file => {
    const states = file.parts.map(part => decisions.get(part));
    const status = states.includes("CHANGES_REQUESTED") ? "Bölüm düzeltme istedi"
      : states.every(state => state === "PASS") ? "İncelendi" : "Bekliyor";
    return `${file.path} · ${status} · Bölüm ${file.parts.join(", ")}`;
  }).join("\n");
}

let findingKey = "";
function renderFindings(current) {
  const review = current?.finding_review;
  const section = byId("finding-review");
  section.hidden = !review;
  const resolutions = current?.finding_resolutions || [];
  const key = JSON.stringify([current?.id, review?.revision, resolutions]);
  if (key === findingKey) return;
  findingKey = key;
  const list = byId("finding-items");
  list.replaceChildren();
  if (!review) return;
  for (const item of review.findings) {
    const closed = resolutions.find(record => record.part === item.part && record.revision === review.revision);
    const details = document.createElement("details");
    const title = document.createElement("summary");
    title.textContent = `Bölüm ${item.part} · ${item.files.join(", ")} · ${closed ? "Kapatıldı" : "Açık"}`;
    const finding = document.createElement("pre");
    finding.textContent = [item.reason, ...item.findings].join("\n");
    details.append(title, finding);
    const evidenceLink = (phase) => `/api/runs/${current.id}/${phase === "05-integration" ? "integration" : "test"}-${review.round}`;
    if (closed) {
      const note = document.createElement("p");
      note.textContent = closed.explanation;
      const source = document.createElement("a");
      source.href = evidenceLink(closed.report.split("/").pop().replace(".txt", ""));
      source.textContent = "Bağlı test raporu";
      details.append(note, source);
    } else if (review.reports.length) {
      const form = document.createElement("form");
      const report = document.createElement("select");
      const reason = document.createElement("textarea");
      const source = document.createElement("a");
      const submit = document.createElement("button");
      const error = document.createElement("p");
      for (const phase of review.reports) {
        report.add(new Option(phase === "05-integration" ? "Birleşik entegrasyon testi" : "Panel testi", phase));
      }
      if (review.reports.includes("05-integration")) report.value = "05-integration";
      source.href = evidenceLink(report.value);
      source.target = "_blank";
      source.rel = "noopener";
      source.textContent = "Test raporunu aç";
      report.addEventListener("change", () => { source.href = evidenceLink(report.value); });
      reason.required = true;
      reason.maxLength = 4000;
      reason.rows = 4;
      submit.type = "submit";
      submit.textContent = "Bulguyu kapat";
      error.setAttribute("role", "alert");
      const reportLabel = document.createElement("label");
      reportLabel.append("Test kanıtı", report);
      const reasonLabel = document.createElement("label");
      reasonLabel.append("Kapatma gerekçesi", reason);
      form.append(reportLabel, source, reasonLabel, submit, error);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        submit.disabled = true;
        error.textContent = "";
        try {
          const response = await fetch("/api/review/close", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({id: current.id, part: item.part, revision: review.revision,
              report: report.value, reason: reason.value}),
          });
          if (!response.ok) throw new Error((await response.json()).error);
          await refresh();
        } catch (failure) {
          error.textContent = failure.message;
          submit.disabled = false;
        }
      });
      details.append(form);
    }
    list.append(details);
  }
}

function render(current) {
  job = current;
  toggleButtons();
  updateModelLabels();
  renderPlan(current);
  renderFindings(current);
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
      body: JSON.stringify({id: job.id, version: job.plan_version, action, plan, check: byId("check").value,
        integration: byId("integration").value}),
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

let historyProject = "";
let historyRequest = 0;
const historySelect = byId("history-run");
const historyStatus = byId("history-status");
const historyDetail = byId("history-detail");

function historyUrl(project, run = "", file = "") {
  const params = new URLSearchParams({repo: project});
  if (run) params.set("run", run);
  if (file) params.set("file", file);
  return `/api/history?${params}`;
}

function historySection(parent, title, content) {
  const section = document.createElement("details");
  const label = document.createElement("summary");
  label.textContent = title;
  const body = document.createElement("pre");
  body.textContent = content;
  section.append(label, body);
  parent.append(section);
  return section;
}

function renderCriteria(data) {
  const project = historyProject;
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = "Kabul kriterleri";
  section.append(heading);
  const error = document.createElement("p");
  error.className = "error";
  error.setAttribute("role", "alert");
  error.hidden = true;
  section.append(error);
  for (const criterion of [...data.criteria, null]) {
    const form = document.createElement("form");
    form.className = "criterion";
    const label = document.createElement("label");
    label.textContent = criterion ? "Kriter" : "Yeni kabul kriteri";
    const title = document.createElement("input");
    title.required = true;
    title.maxLength = 500;
    title.value = criterion?.title || "";
    label.append(title);
    const evidenceLabel = document.createElement("label");
    evidenceLabel.textContent = "Bağlı test sonucu";
    const select = document.createElement("select");
    select.append(new Option("Kanıt bağlama", ""), ...data.test_checks.map((check, index) =>
      new Option(`${check.run === data.id ? "" : check.run + " · "}${check.title} · ${check.name}`, String(index))));
    const existing = data.test_checks.findIndex(check => check.run === criterion?.report_run && check.report === criterion?.report
      && check.name === criterion?.check);
    if (existing >= 0) select.value = String(existing);
    evidenceLabel.append(select);
    const save = document.createElement("button");
    save.type = "submit";
    save.className = "secondary";
    save.textContent = criterion ? "Kriteri kaydet" : "Kriter ekle";
    form.append(label, evidenceLabel, save);
    if (criterion) {
      const badge = document.createElement("strong");
      badge.className = "status";
      badge.dataset.kind = criterion.status === "Başarısız" ? "error" : "";
      badge.textContent = criterion.status;
      form.append(badge);
      if (criterion.report_available) {
        const link = document.createElement("a");
        link.className = "download";
        link.href = historyUrl(project, criterion.report_run, criterion.report);
        link.textContent = "Bağlı raporu indir";
        form.append(link);
      }
    }
    form.addEventListener("submit", async event => {
      event.preventDefault();
      save.disabled = true;
      error.hidden = true;
      const check = select.value === "" ? null : data.test_checks[Number(select.value)];
      try {
        const response = await fetch("/api/history/criteria", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({repo: project, run: data.id, id: criterion?.id, title: title.value,
            report_run: check?.run ?? null, report: check?.report ?? null, check: check?.name ?? null}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        if (historyProject === project && historySelect.value === data.id) renderHistory(result);
      } catch (problem) {
        error.textContent = problem.message;
        error.hidden = false;
      } finally {
        save.disabled = false;
      }
    });
    section.append(form);
  }
  return section;
}

function renderHistory(data) {
  const fragment = document.createDocumentFragment();
  const heading = document.createElement("h3");
  heading.textContent = data.title;
  const info = document.createElement("p");
  info.className = "hint";
  info.textContent = [data.id, data.base && `Başlangıç commit’i ${data.base}`].filter(Boolean).join(" · ");
  fragment.append(heading, info);
  historySection(fragment, "Talep", data.task || "Talep kaydı bulunamadı.").open = true;
  fragment.append(renderCriteria(data));
  const members = data.team.map(agent =>
    [agent.name, agent.branch_label || agent.slot, agent.role_label, agent.model || agent.provider, agent.duty]
      .filter(Boolean).join(" · "));
  historySection(fragment, "Ajanlar", members.join("\n") || "Bu çalışmaya ait ekip kaydı bulunamadı.").open = true;
  if (data.plan) historySection(fragment, "Plan ve onaylanan kapsam", data.plan);
  if (data.notes.length) historySection(fragment, "Görev durumları", data.notes.map(item => item.text).join("\n"));
  const dates = new Intl.DateTimeFormat("tr-TR", {dateStyle: "short", timeStyle: "short"});
  const operations = data.operations.map(item => `${dates.format(new Date(item.time))} · ${item.text}`).join("\n");
  historySection(fragment, "Uygulama ve geri alma kayıtları",
    operations || "Bu görev için uygulama veya geri alma kaydı yok.").open = true;
  for (const review of data.plan_reviews) historySection(fragment, review.title, review.text);
  for (const round of data.rounds) {
    const title = document.createElement("h3");
    title.textContent = `Tur ${round.number}`;
    fragment.append(title);
    for (const [label, value] of [["Git farkı", round.diff], ["Test sonucu", round.test],
      ["Birleşik entegrasyon testi", round.integration], ["Dosya bazlı denetim", round.coverage && formatFileReview(round.coverage)],
      ["Denetim sonucu", round.review]]) {
      const section = historySection(fragment, label, value || "Kayıt yok.");
      section.open = Boolean(value);
    }
    const links = document.createElement("div");
    links.className = "actions";
    for (const report of round.files) {
      const link = document.createElement("a");
      link.className = "download";
      link.href = historyUrl(historyProject, data.id, report.path);
      link.textContent = report.title;
      links.append(link);
    }
    fragment.append(links);
  }
  historyDetail.replaceChildren(fragment);
}

async function loadHistory(project, run) {
  const changed = projectKey(historyProject) !== projectKey(project);
  historyProject = project;
  const requested = ++historyRequest;
  const selected = changed ? "" : historySelect.value;
  if (changed) {
    historyDetail.replaceChildren();
    byId("history-picker").hidden = true;
  }
  try {
    const response = await fetch(historyUrl(project, run));
    const data = await response.json();
    if (requested !== historyRequest) return;
    if (!response.ok) throw new Error(data.error);
    if (run) {
      renderHistory(data);
      historyStatus.textContent = "";
      return;
    }
    historyStatus.textContent = data.runs.length ? "" : "Bu projede henüz kayıtlı görev yok.";
    byId("history-picker").hidden = !data.runs.length;
    historySelect.replaceChildren(new Option("Görev seç", ""), ...data.runs.map(item =>
      new Option(`${item.title} · ${item.time.slice(0, 10)} · ${item.id}`, item.id)));
    if (data.runs.some(item => item.id === selected)) {
      historySelect.value = selected;
      loadHistory(project, selected);
    }
  } catch (error) {
    if (requested === historyRequest) historyStatus.textContent = error.message;
  }
}

historySelect.addEventListener("change", () => {
  historyDetail.replaceChildren();
  if (historySelect.value) loadHistory(historyProject, historySelect.value);
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
      const check = state.job.check;
      byId("check").value = (Array.isArray(check) ? check : check?.["05-check"] || []).join("\n");
      byId("integration").value = (check?.["05-integration"] || []).join("\n");
      loadProjectTeam(state.job.project);
      refreshBoard(true);
      loadHistory(state.job.project);
    } else if (job && state.job && (job.stage !== state.job.stage || job.running !== state.job.running)
        && !state.job.running && projectKey(historyProject) === projectKey(state.job.project)) {
      loadHistory(historyProject);
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
