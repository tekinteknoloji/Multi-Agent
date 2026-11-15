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
let projectDecisions = [];
let decisionKey = "";
let decisionTarget = "";
const boardDate = new Intl.DateTimeFormat("tr-TR", {dateStyle: "medium", timeZone: "Europe/Istanbul"});
const boardTime = new Intl.DateTimeFormat("tr-TR", {timeStyle: "short", timeZone: "Europe/Istanbul"});
const draftByTopic = new Map();
const boardField = byId("task");
const boardStatus = byId("board-status");
const boardError = byId("board-error");

function selectTopic(id) {
  draftByTopic.set(topicId, boardField.value);
  topicId = id;
  byId("board-topic").value = id;
  boardField.value = draftByTopic.get(id) || "";
  lastMessageKey = "";
  byId("board-search").value = "";
  byId("board-attachments").value = "";
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
  for (const id of ["board-team", "board-synthesis", "board-directed"]) {
    byId(id).disabled = !topicId || boardBusy || boardRequest || !boardConfigured || !boardRoster.length;
  }
  byId("board-topic").disabled = boardRequest;
  byId("board-new").disabled = boardRequest;
  for (const button of byId("board-form").querySelectorAll("button")) {
    button.disabled = boardRequest;
  }
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

function renderMessages(topic, decisions = projectDecisions) {
  displayedTopic = topic;
  const messages = topic?.messages || [];
  const term = byId("board-search").value.trim().toLocaleLowerCase("tr");
  const key = (topic?.id || "") + "/" + (topic?.project || "") + "/" + (messages.at(-1)?.id || "") + "/" + term +
    "/" + decisions.map(decision => decision.id).join(",");
  if (key === lastMessageKey) return;
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
    article.append(actions);
    elements.push(article);
  }
  list.replaceChildren(...elements);
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
  const select = byId("board-mention");
  select.replaceChildren(new Option("@ ile birini etiketle…", ""));
  if (agents.length) select.add(new Option("@Herkes", "@Herkes"));
  for (const agent of agents) {
    const option = new Option(`${agent.name} · @${agent.handle} · ${agent.branch_label}`, `@${agent.handle}`);
    option.dataset.custom = "true";
    select.add(option);
  }
  byId("board-team").hidden = agents.length === 0;
  byId("board-synthesis").hidden = !agents.some(agent => agent.slot === "plan" || agent.role === "supervisor");
}

function projectKey(path) {
  return (path || "").trim().replaceAll("/", "\\").replace(/[\\]+$/, "").toLocaleLowerCase("en-US");
}

function taskTopicFor(path) {
  return topicId && (!displayedTopic?.project || projectKey(displayedTopic.project) === projectKey(path)) ? topicId : null;
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
    else if (preparedMemoryProject) query.set("repo", preparedMemoryProject);
    if (loadList) query.set("list", "1");
    const response = await fetch(`/api/board?${query}`);
    if (!response.ok) throw new Error("Pano okunamadı.");
    const data = await response.json();
    if (data.topics) {
      const list = byId("board-topic");
      const selected = projectKey(preparedMemoryProject);
      const topics = data.topics.filter(topic => !selected || !topic.project || projectKey(topic.project) === selected);
      list.replaceChildren(new Option("Yeni konu", ""), ...topics.map(topic => new Option(topic.title, topic.id)));
      list.value = topicId;
    }
    if (requestedTopic !== topicId) return;
    boardBusy = data.busy;
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
      : (topicId ? "Bu konu yerel panoda kayıtlı. Proje klasörüne taşımak için panoyu projeye bağla." : "");
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
byId("board-search").addEventListener("input", () => renderMessages(displayedTopic));
byId("board-cancel-reply").addEventListener("click", () => setReply(null));
byId("board-mention").addEventListener("change", event => {
  const field = byId("board-message");
  if (!event.target.value) return;
  const start = field.selectionStart;
  const prefix = start && !/\s/.test(field.value[start - 1]) ? " " : "";
  field.setRangeText(prefix + event.target.value + " ", start, field.selectionEnd, "end");
  event.target.value = "";
  field.focus();
});
byId("board-form").addEventListener("submit", event => {
  event.preventDefault();
  const directed = event.submitter?.value !== "post";
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
    if (topicId && displayedTopic?.project && projectKey(displayedTopic.project) !== projectKey(path)) {
      throw new Error("Bu sohbet başka bir projeye ait. Üstten projeyi aç veya yeni konu seç.");
    }
    const result = await sendBoard({action: "post", text, reply_to: replyMessage?.id, attachments, repo: path});
    if (field.value === text) field.value = "";
    byId("board-attachments").value = "";
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
