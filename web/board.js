"use strict";

let topicId = "";
let boardBusy = false;
let boardRequest = false;
let boardPoll = false;
let lastMessageKey = "";
let boardNeedsList = false;
let displayedTopic = null;
let replyMessage = null;
let reportKey = "";
let preparedMemoryProject = "";
let extraTeamKey = "";
let boardConfigured = false;
let boardRoster = [];
let activeBoardTurn = null;
let projectDecisions = [];
let decisionKey = "";
let decisionTarget = "";
const boardDate = new Intl.DateTimeFormat("tr-TR", {dateStyle: "medium", timeZone: "Europe/Istanbul"});
const boardTime = new Intl.DateTimeFormat("tr-TR", {timeStyle: "short", timeZone: "Europe/Istanbul"});
const draftByTopic = new Map();
const generatedDraftTopics = new Set();
const boardField = byId("task");
const boardStatus = byId("board-status");
const boardError = byId("board-error");

function selectTopic(id) {
  draftByTopic.set(topicId, boardField.value);
  topicId = id;
  byId("board-topic").value = id;
  boardField.value = draftByTopic.get(id) || "";
  byId("task-draft-source").hidden = !generatedDraftTopics.has(id);
  lastMessageKey = "";
  byId("board-search").value = "";
  byId("board-attachments").value = "";
  renderPendingAttachments();
  closeMentionOptions();
  setReply(null);
  renderMessages(null);
  renderMemory(null, []);
  boardError.hidden = true;
  updateBoardControls();
}

function setReply(message) {
  replyMessage = message;
  byId("board-reply").hidden = !message;
  byId("board-reply-author").textContent = message ? `${message.author} mesajına yanıt` : "";
  byId("board-reply-text").textContent = message?.text.slice(0, 200) || "";
}

function appendMessageText(node, text) {
  const segments = text.split(/((?<![\p{L}\p{N}_@])@[\p{L}\p{N}_]+(?:-[\p{L}\p{N}_]+)*)/gu);
  for (let index = 0; index < segments.length; index++) {
    if (index % 2 === 0) node.append(segments[index]);
    else {
      const tag = document.createElement("mark");
      tag.className = "board-mention";
      tag.textContent = segments[index];
      node.append(tag);
    }
  }
}

function updateBoardControls() {
  const projectOpen = Boolean(preparedMemoryProject);
  for (const id of ["board-team", "board-synthesis", "board-directed"]) {
    byId(id).disabled = !topicId || boardBusy || boardRequest || !boardConfigured || !boardRoster.length;
  }
  byId("board-topic").disabled = !projectOpen || boardRequest;
  byId("board-new").disabled = !projectOpen || boardRequest;
  byId("board-draft-task").disabled = !projectOpen || !taskTopicFor(byId("repo").value)
    || boardBusy || boardRequest || !boardRoster.length;
  for (const field of document.querySelectorAll("#board-form button, #board-form textarea, #board-form input, [form='board-form']")) {
    field.disabled = !projectOpen || boardRequest;
  }
  byId("board-mention").disabled ||= !boardRoster.length;
  byId("board-models").textContent = boardConfigured ? "Ekip: " + (boardRoster.map(agent => agent.name).join(" · ") || "Etkin ajan yok.")
    : "Ajanlara iletmek için Ekip ekranından model ve görev seç.";
}

function boardSourceLink(topic, id, label) {
  const link = document.createElement("a");
  link.href = `#message-${id}`;
  link.textContent = label;
  link.addEventListener("click", event => {
    event.preventDefault();
    if (topicId !== topic) selectTopic(topic);
    byId("board-search").value = "";
    lastMessageKey = "";
    decisionTarget = id;
    refreshBoard();
  });
  return link;
}

function renderAgentTrace(message, topic) {
  const {routing, sources} = message.trace;
  const fragment = document.createDocumentFragment();
  const group = (label) => {
    const detail = document.createElement("details");
    detail.className = "agent-trace";
    const summary = document.createElement("summary");
    summary.textContent = label;
    detail.append(summary);
    fragment.append(detail);
    return detail;
  };
  const why = group("Neden bu ajan?");
  const reason = document.createElement("p");
  const routes = {
    mention: "İstekteki @etiket ile çağrıldı.",
    reply: "Yanıtlanan mesajın göndereni olduğu için çağrıldı.",
    team: "Ekip görüşü isteğiyle çağrıldı.",
    synthesis: `${routing.router}, aynı isteğe verilen ekip yanıtlarını özetlemek için çağrıldı.`,
    routing: routing.branch_fallback ? "Panel bu ajanı açıkça istenen dalın yanıt vermesi için ekledi."
      : `${routing.router}${routing.supervisor ? " (Supervisor / Orchestrator)" : ""} etiketsiz isteği bu ajana yönlendirdi.`,
  };
  const role = message.agent_profile?.role_label;
  reason.textContent = [routes[routing.mode], message.agent_profile?.branch_label, role,
    routing.total > 1 ? `Yanıt sırası ${routing.position}/${routing.total}.` : "",
    routing.ordered ? "İstekte belirtilen dal sırası uygulandı." : "",
    routing.scoped ? "Yönlendirme istekte belirtilen dallarla sınırlandı." : ""].filter(Boolean).join(" · ");
  why.append(reason);
  const records = group("Hangi kayıtları gördü?");
  const note = document.createElement("p");
  note.className = "hint";
  note.textContent = "Yanıt hazırlanırken ajana iletilen kaynaklar.";
  records.append(note);
  const lists = [
    ["Proje kararları", sources.decisions.map(item => boardSourceLink(item.topic, item.id,
      `${item.active ? "Güncel" : "Değiştirildi"} · ${item.text.slice(0, 250)}`))],
    ["Kabul kriterleri", (sources.criteria || []).map(item => {
      const link = document.createElement("a");
      link.href = "#history";
      link.textContent = `${item.status} · ${item.title}${item.report_available ? " · " + item.report_run + "/" + item.report : ""}`;
      link.addEventListener("click", () => {
        const picker = byId("history-run");
        picker.value = item.task_run;
        loadHistory(topic.project, item.task_run);
      });
      return link;
    })],
    ["Test ve denetim raporları", sources.reports.map(item => {
      const link = document.createElement("a");
      link.href = topic.project ? historyUrl(topic.project, item.run, item.path)
        : `/api/board-memory/${topic.id}/${item.run}/${item.path}`;
      const label = item.path.endsWith("05-integration.txt") ? "Entegrasyon test raporu"
        : item.path.endsWith("05-check.txt") ? "Test raporu" : "Denetim raporu";
      link.textContent = `${label} · ${item.run}/${item.path}`;
      return link;
    })],
    ["Pano mesajları", sources.messages.map(item => boardSourceLink(item.topic, item.id,
      `${item.author} · ${item.excerpt}`))],
    ["Ekler", sources.attachments.map(item => {
      const link = document.createElement("a");
      link.href = `/api/board-attachment/${topic.id}/${item.id}`;
      link.textContent = item.name;
      return link;
    })],
  ];
  for (const [name, links] of lists) {
    if (!links.length) continue;
    const title = document.createElement("h4");
    title.textContent = name;
    const list = document.createElement("ul");
    for (const link of links) {
      const item = document.createElement("li");
      item.append(link);
      list.append(item);
    }
    records.append(title, list);
  }
  return fragment;
}

const deliveryLabels = {
  queued: "Sırada", preparing: "Bağlam hazırlanıyor", responding: "Yanıt bekleniyor",
  answered: "Yanıtladı", error: "Hata", interrupted: "Kesildi",
};

function renderDeliveries(topic) {
  const messages = topic?.messages || [];
  const historicalReplies = new Map();
  for (const message of messages) {
    if (message.agent_id && message.reply_to) {
      const authors = historicalReplies.get(message.reply_to) || new Set();
      authors.add(message.author);
      historicalReplies.set(message.reply_to, authors);
    }
  }
  for (const message of messages) {
    const box = byId(`delivery-${message.id}`);
    if (!box) continue;
    const record = message.delivery;
    const active = activeBoardTurn?.id === topic.id && activeBoardTurn?.message === message.id;
    const interrupted = !active && ["routing", "running"].includes(record?.state);
    const receiptKey = JSON.stringify([record, interrupted, [...(historicalReplies.get(message.id) || [])]]);
    if (box.dataset.receipt === receiptKey) continue;
    box.dataset.receipt = receiptKey;
    const summary = document.createElement("span");
    const labels = {
      saved: "Panoya kaydedildi · Ajanlara iletilmedi",
      routing: record?.router ? `${record.router} alıcıları belirliyor` : "Alıcılar belirleniyor",
      running: "Ajan turu sürüyor",
      finished: "Ajan turu tamamlandı",
      blocked: record?.reason, error: record?.reason,
    };
    const oldReplies = [...(historicalReplies.get(message.id) || [])];
    summary.textContent = interrupted ? "Ajan turu kesildi" : labels[record?.state]
      || (oldReplies.length ? `Yanıtlayanlar: ${oldReplies.join(", ")}` : "Pano kaydı");
    box.replaceChildren(summary);
    for (const agent of record?.agents || []) {
      const state = interrupted && ["queued", "preparing", "responding"].includes(agent.state)
        ? "interrupted" : agent.state;
      const pill = document.createElement(agent.response ? "a" : "span");
      pill.className = "board-delivery-agent";
      pill.dataset.state = state;
      pill.textContent = `${agent.name} · ${deliveryLabels[state] || state}`;
      if (agent.reason) pill.title = agent.reason;
      if (agent.response) pill.href = `#message-${agent.response}`;
      box.append(pill);
    }
  }
}

function renderMessages(topic, decisions = projectDecisions) {
  displayedTopic = topic;
  const messages = topic?.messages || [];
  const term = byId("board-search").value.trim().toLocaleLowerCase("tr");
  const key = (topic?.id || "") + "/" + (topic?.project || "") + "/" + (messages.at(-1)?.id || "") + "/" + term +
    "/" + decisions.map(decision => decision.id).join(",");
  if (key === lastMessageKey) {
    renderDeliveries(topic);
    return;
  }
  lastMessageKey = key;
  const list = byId("board-messages");
  const filtered = messages.filter(message => !term ||
    `${message.author} ${message.text} ${message.run_id || ""} ${(message.attachments || []).map(item => item.name).join(" ")}`.toLocaleLowerCase("tr").includes(term));
  if (!filtered.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = term ? "Eşleşen mesaj bulunamadı." : "İlk fikrini aşağıya yaz.";
    list.replaceChildren(empty);
    return;
  }
  const nearEnd = list.scrollHeight - list.clientHeight - list.scrollTop < 45;
  const byMessage = new Map(messages.map(message => [message.id, message]));
  const approvals = new Map(decisions.filter(decision => decision.topic === topic?.id)
    .map(decision => [decision.proposal, decision]));
  const elements = [];
  let previousDay = "";
  for (const message of filtered) {
    const day = boardDate.format(new Date(message.time));
    if (day !== previousDay) {
      const separator = document.createElement("p");
      separator.className = "board-day";
      separator.textContent = day;
      elements.push(separator);
      previousDay = day;
    }
    const article = document.createElement("article");
    article.className = "board-message";
    article.id = `message-${message.id}`;
    article.dataset.agent = message.agent_id?.startsWith("custom-") ? "custom" : (agentSources[message.author] || "panel");
    article.dataset.own = String(message.author === "Kullanıcı");
    const heading = document.createElement("header");
    heading.textContent = `${message.author}${message.model && message.model !== message.author ? " · " + message.model : ""} · `;
    const time = document.createElement("time");
    time.dateTime = message.time;
    time.textContent = boardTime.format(new Date(message.time));
    heading.append(time);
    article.append(heading);
    const original = byMessage.get(message.reply_to);
    if (original) {
      const quote = document.createElement("a");
      quote.className = "board-quote";
      quote.href = `#message-${original.id}`;
      quote.textContent = `${original.author} · ${original.text.slice(0, 150)}`;
      quote.addEventListener("click", event => {
        event.preventDefault();
        if (!byId(`message-${original.id}`)) {
          byId("board-search").value = "";
          renderMessages(topic);
        }
        byId(`message-${original.id}`)?.scrollIntoView({block: "nearest"});
      });
      article.append(quote);
    }
    const text = document.createElement("p");
    appendMessageText(text, message.text);
    article.append(text);
    if (message.trace?.sources) article.append(renderAgentTrace(message, topic));
    if (message.proposal) {
      const approved = approvals.get(message.id);
      const detail = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = approved ? (approved.active ? "Karar onaylandı" : "Karar değiştirildi")
        : (topic.project ? "Karar önerisini incele" : "Onaylamak için projeye bağla");
      detail.append(summary);
      const description = document.createElement("p");
      description.textContent = message.proposal;
      detail.append(description);
      if (topic.project && !approved) {
        const label = document.createElement("label");
        label.textContent = "Hangi kararın yerine geçecek?";
        const replacement = document.createElement("select");
        replacement.append(new Option("Yeni karar", ""), ...decisions.filter(decision => decision.active)
          .map(decision => new Option(decision.text.slice(0, 120), decision.id)));
        label.append(replacement);
        const approval = document.createElement("button");
        approval.type = "button";
        approval.textContent = "Kararı onayla";
        approval.addEventListener("click", () => boardOperation(() => sendBoard({
          action: "approve_decision", id: topic.id, proposal: message.id, replaces: replacement.value || null,
        })));
        detail.append(label, approval);
      }
      article.append(detail);
    }
    for (const entry of message.attachments || []) {
      const link = document.createElement("a");
      link.className = "download board-attachment";
      link.href = `/api/board-attachment/${topic.id}/${entry.id}`;
      link.download = entry.name;
      link.textContent = `Ek: ${entry.name}`;
      article.append(link);
    }
    const actions = document.createElement("div");
    actions.className = "actions";
    const reply = document.createElement("button");
    reply.type = "button";
    reply.className = "secondary";
    reply.textContent = "Yanıtla";
    reply.addEventListener("click", () => {
      setReply(message);
      byId("board-message").focus();
    });
    actions.append(reply);
    if (message.run_id) {
      const link = document.createElement("a");
      link.className = "download";
      link.href = "#board-reports";
      link.textContent = `Raporlar · ${message.run_id}`;
      link.addEventListener("click", () => { byId("board-reports").open = true; });
      actions.append(link);
    }
    if (message.author !== "Panel") {
      const transfer = document.createElement("button");
      transfer.type = "button";
      transfer.className = "secondary";
      transfer.textContent = "Taslağa al";
      transfer.addEventListener("click", () => {
        // Append to a draft the user is already editing.
        boardField.value = boardField.value.trim() ? boardField.value + "\n\n" + message.text : message.text;
        boardField.focus();
      });
      actions.append(transfer);
    }
    if (message.author === "Kullanıcı" && !message.run_id) {
      const delivery = document.createElement("div");
      delivery.className = "board-delivery";
      delivery.id = `delivery-${message.id}`;
      delivery.setAttribute("role", "status");
      article.append(delivery);
    }
    article.append(actions);
    elements.push(article);
  }
  list.replaceChildren(...elements);
  renderDeliveries(topic);
  if (nearEnd) list.scrollTop = list.scrollHeight;
  if (decisionTarget) {
    byId(`message-${decisionTarget}`)?.scrollIntoView({block: "nearest"});
    decisionTarget = "";
  }
}

function renderDecisions(decisions) {
  const list = byId("board-decision-list");
  const key = decisions.map(decision => decision.id).join(",") || "empty";
  if (key === decisionKey) return;
  decisionKey = key;
  const items = decisions.map(decision => {
    const item = document.createElement("li");
    const source = document.createElement("a");
    source.href = `#message-${decision.proposal}`;
    source.textContent = decision.text;
    source.addEventListener("click", event => {
      event.preventDefault();
      if (topicId !== decision.topic) selectTopic(decision.topic);
      byId("board-search").value = "";
      lastMessageKey = "";
      decisionTarget = decision.proposal;
      refreshBoard();
    });
    item.append(decision.active ? "Güncel · " : "Değiştirildi · ", source,
      " · " + boardDate.format(new Date(decision.time)));
    return item;
  });
  if (!items.length) {
    const empty = document.createElement("li");
    empty.textContent = "Henüz onaylanan karar yok.";
    items.push(empty);
  }
  list.replaceChildren(...items);
}

function renderMemory(topic, reports, project = topic?.project) {
  const panel = byId("board-memory");
  panel.hidden = !topic && !project;
  byId("board-export").hidden = !topic;
  byId("board-reports").hidden = !topic;
  if (!topic) {
    reportKey = "";
    return;
  }
  const prefix = `/api/board-memory/${topic.id}`;
  byId("board-export").href = prefix;
  const key = `${topic.id}/${reports.map(item => item.run + item.path).join(",")}`;
  if (key === reportKey) return;
  reportKey = key;
  const list = byId("board-report-list");
  if (!reports.length) {
    list.textContent = "Bu konuda henüz çalışma raporu yok.";
    return;
  }
  const groups = new Map();
  for (const report of reports) {
    if (!groups.has(report.run)) groups.set(report.run, []);
    groups.get(report.run).push(report);
  }
  list.replaceChildren(...Array.from(groups, ([run, files]) => {
    const group = document.createElement("section");
    const label = document.createElement("p");
    label.textContent = `Çalışma ${run}`;
    const items = document.createElement("ul");
    for (const file of files) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = `${prefix}/${run}/${file.path}`;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = file.title;
      item.append(link);
      items.append(item);
    }
    group.append(label, items);
    return group;
  }).reverse());
}

function renderExtraTeam(agents, configured) {
  boardConfigured = configured;
  boardRoster = agents;
  const key = JSON.stringify([configured, agents.map(({id, name, handle, slot, role, branch}) =>
    [id, name, handle, slot, role, branch])]);
  if (key === extraTeamKey) return;
  extraTeamKey = key;
  closeMentionOptions();
  byId("board-team").hidden = agents.length === 0;
  byId("board-synthesis").hidden = !agents.some(agent => agent.slot === "plan" || agent.role === "supervisor");
}

function projectKey(path) {
  return (path || "").trim().replaceAll("/", "\\").replace(/[\\]+$/, "").toLocaleLowerCase("en-US");
}

function taskTopicFor(path) {
  return topicId && displayedTopic?.project && projectKey(displayedTopic.project) === projectKey(path) ? topicId : null;
}

async function refreshBoard(includeList = false) {
  boardNeedsList ||= includeList;
  if (boardPoll) return;
  boardPoll = true;
  const loadList = boardNeedsList;
  boardNeedsList = false;
  const requestedTopic = topicId;
  try {
    const query = new URLSearchParams();
    if (requestedTopic) query.set("topic", requestedTopic);
    if (preparedMemoryProject) query.set("repo", preparedMemoryProject);
    if (loadList) query.set("list", "1");
    const response = await fetch(`/api/board?${query}`);
    if (!response.ok) throw new Error("Pano okunamadı.");
    const data = await response.json();
    if (data.topics) {
      const list = byId("board-topic");
      const selected = projectKey(preparedMemoryProject);
      const topics = data.topics.filter(topic => selected && projectKey(topic.project) === selected);
      list.replaceChildren(new Option(selected ? "Yeni konu" : "Önce proje aç", ""),
        ...topics.map(topic => new Option(topic.title, topic.id)));
      list.value = topicId;
    }
    if (requestedTopic !== topicId) return;
    boardBusy = data.busy;
    activeBoardTurn = data.turn;
    if (data.topic?.project && !preparedMemoryProject && !byId("repo").value.trim()) {
      preparedMemoryProject = data.topic.project;
      byId("repo").value = data.topic.project;
      loadProjectTeam(data.topic.project);
      loadHistory(data.topic.project);
      boardNeedsList = true;
    }
    const memoryProject = data.topic?.project || (topicId ? "" : preparedMemoryProject);
    byId("board-storage").textContent = memoryProject
      ? `Mesajlar, ekler ve raporlar: ${memoryProject}\\Proje-Hafizasi`
      : "Pano konularını görmek ve mesaj yazmak için önce bir proje aç.";
    renderExtraTeam(data.extra_agents || [], Boolean(data.configured));
    projectDecisions = data.decisions || [];
    renderMessages(data.topic);
    renderDecisions(projectDecisions);
    renderMemory(data.topic, data.reports || [], memoryProject);
    boardStatus.textContent = data.turn?.id === topicId
      ? `${data.turn.role} · Pano turu sürüyor.`
      : (data.busy ? "Başka bir ajan çalışması sürüyor." : "");
  } catch (error) {
    boardStatus.textContent = error.message;
  } finally {
    boardPoll = false;
    updateBoardControls();
    if (boardNeedsList) refreshBoard();
  }
}

async function sendBoard(payload) {
  const response = await fetch("/api/board", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id: topicId || null, ...payload}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}

async function boardOperation(operation) {
  if (boardRequest) return;
  boardRequest = true;
  boardError.hidden = true;
  updateBoardControls();
  try {
    await operation();
  } catch (error) {
    boardError.textContent = error.message;
    boardError.hidden = false;
  } finally {
    boardRequest = false;
    updateBoardControls();
    refreshBoard(true);
  }
}

byId("board-new").addEventListener("click", () => {
  selectTopic("");
  byId("board-message").value = "";
  byId("board-message").focus();
});
byId("board-topic").addEventListener("change", event => {
  selectTopic(event.target.value);
  refreshBoard();
});
byId("board-draft-task").addEventListener("click", () => {
  const repo = byId("repo").value;
  const source = taskTopicFor(repo);
  if (!source) return;
  const previous = boardField.value;
  if (previous.trim() && !window.confirm("Görev alanındaki metni yeni taslakla değiştirmek istiyor musun?")) return;
  boardOperation(async () => {
    boardStatus.textContent = "Konuşmadan görev taslağı hazırlanıyor…";
    const result = await sendBoard({action: "draft_task", id: source, repo});
    if (taskTopicFor(byId("repo").value) !== result.id || boardField.value !== previous) {
      throw new Error("Proje, konu veya görev metni değişti. Taslağı yeniden oluştur.");
    }
    generatedDraftTopics.add(source);
    boardField.value = result.draft;
    byId("task-draft-source").hidden = false;
    boardField.focus();
  });
});
byId("board-search").addEventListener("input", () => renderMessages(displayedTopic));
byId("board-cancel-reply").addEventListener("click", () => setReply(null));
function renderPendingAttachments() {
  const files = Array.from(byId("board-attachments").files);
  byId("board-pending-attachments").hidden = files.length === 0;
  byId("board-attachment-names").textContent = files.map(file => file.name).join(" · ");
}

function closeMentionOptions() {
  byId("board-mention-options").hidden = true;
  byId("board-mention").setAttribute("aria-expanded", "false");
}

function showMentionOptions(fromButton = false) {
  const field = byId("board-message");
  const cursor = field.selectionStart;
  const match = field.value.slice(0, cursor).match(/(?<![\p{L}\p{N}_@])@([\p{L}\p{N}_-]*)$/u);
  if ((!match && !fromButton) || !boardRoster.length || field.disabled) {
    closeMentionOptions();
    return;
  }
  const search = (match?.[1] || "").toLocaleLowerCase("tr-TR");
  const choices = [{label: "@Herkes", tag: "@Herkes"}, ...boardRoster.map(agent => ({
    label: `${agent.name} · @${agent.handle}`, tag: `@${agent.handle}`,
  }))].filter(item => item.label.toLocaleLowerCase("tr-TR").includes(search));
  const menu = byId("board-mention-options");
  menu.replaceChildren(...choices.map(item => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = item.label;
    button.addEventListener("click", () => {
      const start = match?.index ?? cursor;
      const prefix = !match && start && !/\s/.test(field.value[start - 1]) ? " " : "";
      field.setRangeText(prefix + item.tag + " ", start, field.selectionEnd, "end");
      closeMentionOptions();
      field.focus();
    });
    return button;
  }));
  menu.hidden = !choices.length;
  byId("board-mention").setAttribute("aria-expanded", String(!menu.hidden));
}

byId("board-add-attachment").addEventListener("click", () => byId("board-attachments").click());
byId("board-attachments").addEventListener("change", renderPendingAttachments);
byId("board-clear-attachments").addEventListener("click", () => {
  byId("board-attachments").value = "";
  renderPendingAttachments();
  byId("board-message").focus();
});
byId("board-mention").addEventListener("click", () => {
  if (!byId("board-mention-options").hidden) closeMentionOptions();
  else {
    showMentionOptions(true);
    byId("board-mention-options").querySelector("button")?.focus();
  }
});
byId("board-message").addEventListener("input", () => showMentionOptions());
byId("board-message").addEventListener("keydown", event => {
  if (event.key !== "Enter" || event.isComposing) return;
  event.preventDefault();
  if (event.repeat) return;
  const field = event.currentTarget;
  if (event.altKey) {
    if (field.value.length - (field.selectionEnd - field.selectionStart) < field.maxLength) {
      field.setRangeText("\n", field.selectionStart, field.selectionEnd, "end");
    }
    closeMentionOptions();
  } else if (field.value.trim() || byId("board-attachments").files.length) {
    byId("board-form").requestSubmit(byId("board-send"));
  }
});
byId("board-form").addEventListener("keydown", event => {
  if (byId("board-mention-options").hidden) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeMentionOptions();
    byId("board-message").focus();
  } else if (event.key === "ArrowDown" && event.target.id === "board-message") {
    event.preventDefault();
    byId("board-mention-options").querySelector("button")?.focus();
  }
});
document.addEventListener("click", event => {
  if (!event.target.closest(".board-composer")) closeMentionOptions();
});
byId("board-form").addEventListener("submit", event => {
  event.preventDefault();
  const directed = event.submitter?.value !== "post" && boardConfigured && boardRoster.length;
  closeMentionOptions();
  boardOperation(async () => {
    const field = byId("board-message");
    const text = field.value;
    const files = Array.from(byId("board-attachments").files);
    if (files.length > 4 || files.reduce((size, file) => size + file.size, 0) > 2_000_000) {
      throw new Error("Bir mesaja en fazla dört dosya ve toplam 2 MB ekle.");
    }
    const attachments = await Promise.all(files.map(async file => {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let bytestring = "";
      for (let i = 0; i < bytes.length; i += 16384) {
        bytestring += String.fromCharCode(...bytes.subarray(i, i + 16384));
      }
      return {name: file.name, data: btoa(bytestring)};
    }));
    const path = byId("repo").value;
    if (topicId && projectKey(path) && projectKey(displayedTopic?.project) !== projectKey(path)) {
      throw new Error("Bu sohbet seçili projeye bağlı değil. Yeni konu aç veya panoyu projeye bağla.");
    }
    const result = await sendBoard({action: "post", text, reply_to: replyMessage?.id, attachments, repo: path});
    if (field.value === text) field.value = "";
    byId("board-attachments").value = "";
    renderPendingAttachments();
    byId("board-more").open = false;
    setReply(null);
    if (result.id !== topicId) selectTopic(result.id);
    if (directed) {
      try {
        await sendBoard({action: "directed", id: result.id, message: result.message});
      } catch (error) {
        throw new Error(`Mesaj panoya yazıldı. ${error.message}`);
      }
    }
  });
});
async function openWorkspaceProject(path, action = "open_project") {
  const data = await sendBoard({action, repo: path});
  byId("repo").value = data.project;
  preparedMemoryProject = data.project;
  if ((data.id || "") !== topicId) selectTopic(data.id || "");
  await loadProjectTeam(data.project);
  loadHistory(data.project);
  const status = byId("project-status");
  status.hidden = false;
  status.classList.remove("error");
  status.textContent = data.id ? "Proje ve pano geçmişi açık." : "Proje açık. İlk pano mesajını veya görevi yaz.";
  refreshBoard(true);
}

for (const [button, action] of [["board-bind", "bind_project"], ["board-open-project", "open_project"]]) {
  byId(button).addEventListener("click", async () => {
    const control = byId(button);
    const status = byId("project-status");
    control.disabled = true;
    try {
      await openWorkspaceProject(byId("repo").value, action);
    } catch (error) {
      status.hidden = false;
      status.classList.add("error");
      status.textContent = error.message;
    } finally {
      control.disabled = false;
    }
  });
}
for (const [id, action] of [["board-team", "team"], ["board-synthesis", "synthesis"], ["board-directed", "directed"]]) {
  byId(id).addEventListener("click", () => boardOperation(() => sendBoard({action})));
}
refreshBoard(true);
setInterval(refreshBoard, 1500);
