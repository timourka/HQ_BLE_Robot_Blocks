/* global Blockly */
"use strict";

let workspace;
let programs = [];
let activeProgramId = null;
let loadedProgramId = null;
let connected = false;
let running = false;
let apiReady = false;
let bridgeInitialising = false;
const activeBlockStyles = new Map();

const $ = (id) => document.getElementById(id);
const uid = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
const api = (method, ...args) => {
  const bridge = window.pywebview?.api;
  if (!bridge || typeof bridge[method] !== "function") {
    return Promise.reject(new Error(
      "Python API недоступен. Запустите приложение через robot_studio.py или run_windows.bat."
    ));
  }
  return bridge[method](...args);
};

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.style.display = "none"; }, 4200);
}

function appendLog(message) {
  const now = new Date().toLocaleTimeString("ru-RU", {hour12: false});
  $("log").textContent += `[${now}] ${message}\n`;
  $("log").scrollTop = $("log").scrollHeight;
}

function activeProgram() {
  return programs.find((p) => p.id === activeProgramId);
}

function saveWorkspaceToProgram() {
  const program = programs.find((p) => p.id === loadedProgramId);
  if (program && workspace) {
    program.workspace = Blockly.serialization.workspaces.save(workspace);
  }
}

function loadProgram(programId) {
  saveWorkspaceToProgram();
  const program = programs.find((p) => p.id === programId);
  if (!program) return;
  activeProgramId = program.id;
  workspace.clear();
  if (program.workspace) {
    Blockly.serialization.workspaces.load(program.workspace, workspace);
  }
  loadedProgramId = program.id;
  renderProgramTabs();
  Blockly.svgResize(workspace);
}

function renderProgramTabs() {
  const host = $("programTabs");
  host.replaceChildren();
  programs.forEach((program) => {
    const button = document.createElement("button");
    button.textContent = program.name;
    button.className = program.id === activeProgramId ? "active" : "";
    button.onclick = () => loadProgram(program.id);
    host.appendChild(button);
  });
}

function createDefaultProgram(name = `Программа ${programs.length + 1}`) {
  const temp = new Blockly.Workspace();
  const first = temp.newBlock("hq_forward");
  first.setFieldValue("0.5", "DURATION");
  const stop = temp.newBlock("hq_stop");
  first.nextConnection.connect(stop.previousConnection);
  const program = {
    id: uid(),
    name,
    workspace: Blockly.serialization.workspaces.save(temp)
  };
  temp.dispose();
  return program;
}

function updateToolbar() {
  $("connectBtn").disabled = !apiReady || connected;
  $("disconnectBtn").disabled = !apiReady || !connected;
  $("runBtn").disabled = !apiReady || !connected || running;
  $("stopBtn").disabled = !apiReady || !connected;
  $("openBtn").disabled = !apiReady;
  $("saveBtn").disabled = !apiReady;
  $("saveAsBtn").disabled = !apiReady;
}

async function initialiseBridge() {
  if (apiReady || bridgeInitialising || !window.pywebview?.api) return;
  bridgeInitialising = true;
  try {
    const state = await api("initial_state");
    apiReady = true;
    connected = state.connected;
    running = state.running;
    setStatus(
      connected ? `Подключено: ${state.device}` : "Не подключено",
      connected ? "connected" : "disconnected"
    );
    renderVariables(state.variables || {});
    renderControls(state.controls || {});
  } catch (error) {
    setStatus("Python API недоступен", "error");
    toast(String(error));
  } finally {
    bridgeInitialising = false;
    updateToolbar();
  }
}

function setStatus(text, kind) {
  $("statusText").textContent = text;
  const colours = {
    connected: "#12B76A", searching: "#F79009",
    error: "#F04438", disconnected: "#98A2B3"
  };
  $("statusDot").style.background = colours[kind] || "#98A2B3";
}

const simpleConverters = {
  hq_forward: (b) => ({type: "forward", duration_expr: b.getFieldValue("DURATION")}),
  hq_backward: (b) => ({type: "backward", duration_expr: b.getFieldValue("DURATION")}),
  hq_left: (b) => ({type: "left", duration_expr: b.getFieldValue("DURATION")}),
  hq_right: (b) => ({type: "right", duration_expr: b.getFieldValue("DURATION")}),
  hq_stop: () => ({type: "stop"}),
  hq_wait: (b) => ({type: "wait", duration_expr: b.getFieldValue("DURATION")}),
  hq_var_set: (b) => ({type: "var_set", name: b.getFieldValue("NAME"), expr: b.getFieldValue("EXPR")}),
  hq_var_change: (b) => ({type: "var_change", name: b.getFieldValue("NAME"), expr: b.getFieldValue("EXPR")}),
  hq_var_math: (b) => ({type: "var_math", name: b.getFieldValue("NAME"), op: b.getFieldValue("OP"), expr: b.getFieldValue("EXPR")}),
  hq_array_set: (b) => ({type: "array_set", name: b.getFieldValue("NAME"), index_expr: b.getFieldValue("INDEX"), value_expr: b.getFieldValue("VALUE")}),
  hq_array_append: (b) => ({type: "array_append", name: b.getFieldValue("NAME"), value_expr: b.getFieldValue("VALUE")}),
  hq_array_insert: (b) => ({type: "array_insert", name: b.getFieldValue("NAME"), index_expr: b.getFieldValue("INDEX"), value_expr: b.getFieldValue("VALUE")}),
  hq_array_pop: (b) => ({type: "array_pop", name: b.getFieldValue("NAME"), index_expr: b.getFieldValue("INDEX"), target: b.getFieldValue("TARGET")}),
  hq_log: (b) => ({type: "log_expr", expr: b.getFieldValue("EXPR")}),
  hq_control_define: (b) => ({
    type: "control_define", control_name: b.getFieldValue("NAME"),
    label: b.getFieldValue("LABEL"), control_kind: b.getFieldValue("KIND"),
    min_expr: b.getFieldValue("MIN"), max_expr: b.getFieldValue("MAX"),
    step_expr: b.getFieldValue("STEP"), default_expr: b.getFieldValue("DEFAULT"),
    bind_var: b.getFieldValue("BIND")
  }),
  hq_control_read: (b) => ({type: "control_read", control_name: b.getFieldValue("NAME"), target: b.getFieldValue("TARGET")}),
  hq_control_write: (b) => ({type: "control_write", control_name: b.getFieldValue("NAME"), value_expr: b.getFieldValue("VALUE")}),
  hq_matrix: (b) => ({
    type: "matrix", source: b.getFieldValue("SOURCE"),
    matrix_expr: b.getFieldValue("EXPR"),
    rows: b.getFieldValue("ROWS").split(",").map((x) => Number(x.trim()) & 31).slice(0, 5)
  }),
  hq_marquee: (b) => ({type: "marquee", text_expr: b.getFieldValue("TEXT"), frame_time_expr: b.getFieldValue("FRAME"), duration_expr: b.getFieldValue("DURATION")}),
  hq_clear_matrix: () => ({type: "clear_matrix"}),
  hq_melody: (b) => ({type: "melody", number_expr: b.getFieldValue("NUMBER")}),
  hq_file_read: (b) => ({type: "file_read", path_expr: b.getFieldValue("PATH"), target: b.getFieldValue("TARGET"), mode: b.getFieldValue("MODE")}),
  hq_file_write: (b) => ({type: "file_write", path_expr: b.getFieldValue("PATH"), value_expr: b.getFieldValue("VALUE"), mode: b.getFieldValue("MODE"), append: b.getFieldValue("APPEND") === "TRUE"}),
  hq_file_list: (b) => ({type: "file_list", path_expr: b.getFieldValue("PATH"), target: b.getFieldValue("TARGET")}),
  hq_raw: (b) => ({type: "raw", hex: b.getFieldValue("HEX")})
};

function chainToRunner(first, output = []) {
  let block = first;
  while (block) {
    if (block.type === "hq_repeat") {
      output.push({id: block.id, type: "repeat_start", count_expr: block.getFieldValue("COUNT")});
      chainToRunner(block.getInputTargetBlock("DO"), output);
      output.push({id: `${block.id}:end`, type: "repeat_end"});
    } else if (block.type === "hq_parallel") {
      output.push({id: block.id, type: "parallel_start"});
      const branches = ["BRANCH1", "BRANCH2", "BRANCH3"]
        .map((name) => block.getInputTargetBlock(name))
        .filter(Boolean);
      branches.forEach((branch, index) => {
        if (index) output.push({id: `${block.id}:branch${index + 1}`, type: "parallel_branch"});
        chainToRunner(branch, output);
      });
      output.push({id: `${block.id}:end`, type: "parallel_end"});
    } else {
      const converter = simpleConverters[block.type];
      if (!converter) throw new Error(`Неподдерживаемый блок: ${block.type}`);
      output.push({id: block.id, ...converter(block)});
    }
    block = block.getNextBlock();
  }
  return output;
}

function workspaceToRunner() {
  const top = workspace.getTopBlocks(true);
  const output = [];
  top.forEach((block) => chainToRunner(block, output));
  return output;
}

const legacyTypes = {
  forward: "hq_forward", backward: "hq_backward", left: "hq_left", right: "hq_right",
  stop: "hq_stop", wait: "hq_wait", var_set: "hq_var_set", var_change: "hq_var_change",
  var_math: "hq_var_math", array_set: "hq_array_set", array_append: "hq_array_append",
  array_insert: "hq_array_insert", array_pop: "hq_array_pop", log_expr: "hq_log",
  control_define: "hq_control_define", control_read: "hq_control_read",
  control_write: "hq_control_write", matrix: "hq_matrix", marquee: "hq_marquee",
  clear_matrix: "hq_clear_matrix", melody: "hq_melody", file_read: "hq_file_read",
  file_write: "hq_file_write", file_list: "hq_file_list", raw: "hq_raw"
};

const legacyFields = {
  duration_expr: "DURATION", count_expr: "COUNT", name: "NAME", expr: "EXPR",
  op: "OP", index_expr: "INDEX", value_expr: "VALUE", target: "TARGET",
  control_name: "NAME", label: "LABEL", control_kind: "KIND", min_expr: "MIN",
  max_expr: "MAX", step_expr: "STEP", default_expr: "DEFAULT", bind_var: "BIND",
  source: "SOURCE", matrix_expr: "EXPR", text_expr: "TEXT",
  frame_time_expr: "FRAME", number_expr: "NUMBER", path_expr: "PATH",
  mode: "MODE", hex: "HEX"
};

function configureLegacyBlock(block, data) {
  Object.entries(legacyFields).forEach(([oldKey, field]) => {
    if (data[oldKey] !== undefined && block.getField(field)) {
      block.setFieldValue(String(data[oldKey]), field);
    }
  });
  if (data.duration_expr !== undefined && block.getField("DURATION")) {
    block.setFieldValue(String(data.duration_expr), "DURATION");
  }
  if (data.duration_expr !== undefined && block.type === "hq_marquee") {
    block.setFieldValue(String(data.duration_expr), "DURATION");
  }
  if (data.rows && block.getField("ROWS")) block.setFieldValue(data.rows.join(","), "ROWS");
  if (data.append !== undefined && block.getField("APPEND")) {
    block.setFieldValue(data.append ? "TRUE" : "FALSE", "APPEND");
  }
}

function connectChain(blocks) {
  for (let i = 0; i < blocks.length - 1; i += 1) {
    blocks[i].nextConnection.connect(blocks[i + 1].previousConnection);
  }
  return blocks[0] || null;
}

function legacySequence(ws, data, start = 0, stops = new Set()) {
  const made = [];
  let index = start;
  while (index < data.length) {
    const item = data[index];
    if (stops.has(item.type)) return {first: connectChain(made), index, token: item.type};
    if (item.type === "repeat_start") {
      const repeat = ws.newBlock("hq_repeat", item.id);
      repeat.setFieldValue(String(item.count_expr ?? item.count ?? 2), "COUNT");
      const inner = legacySequence(ws, data, index + 1, new Set(["repeat_end"]));
      if (inner.first) repeat.getInput("DO").connection.connect(inner.first.previousConnection);
      made.push(repeat);
      index = inner.index + 1;
      continue;
    }
    if (item.type === "parallel_start") {
      const parallel = ws.newBlock("hq_parallel", item.id);
      let position = index + 1;
      for (let branch = 1; branch <= 3; branch += 1) {
        const result = legacySequence(ws, data, position, new Set(["parallel_branch", "parallel_end"]));
        if (result.first) parallel.getInput(`BRANCH${branch}`).connection.connect(result.first.previousConnection);
        position = result.index + 1;
        if (result.token === "parallel_end") break;
      }
      made.push(parallel);
      index = position;
      continue;
    }
    const blockType = legacyTypes[item.type];
    if (blockType) {
      const block = ws.newBlock(blockType, item.id);
      configureLegacyBlock(block, item);
      made.push(block);
    }
    index += 1;
  }
  return {first: connectChain(made), index, token: null};
}

function legacyProgramToWorkspace(blocks) {
  const temp = new Blockly.Workspace();
  legacySequence(temp, blocks || []);
  const state = Blockly.serialization.workspaces.save(temp);
  temp.dispose();
  return state;
}

function normaliseProject(payload) {
  if (Array.isArray(payload.programs)) {
    return payload.programs.map((program, index) => ({
      id: program.id || uid(),
      name: program.name || `Программа ${index + 1}`,
      workspace: program.workspace || legacyProgramToWorkspace(program.blocks || [])
    }));
  }
  if (Array.isArray(payload.blocks)) {
    return [{id: uid(), name: "Импортированная программа", workspace: legacyProgramToWorkspace(payload.blocks)}];
  }
  throw new Error("Неизвестный формат проекта");
}

async function refreshRuntime() {
  if (!apiReady) return;
  const state = await api("get_runtime_state");
  renderVariables(state.variables || {});
  renderControls(state.controls || {});
}

function renderVariables(variables) {
  const host = $("variablesList");
  host.replaceChildren();
  const entries = Object.entries(variables).sort(([a], [b]) => a.localeCompare(b));
  if (!entries.length) {
    host.innerHTML = '<div class="empty">Переменных пока нет</div>';
    return;
  }
  entries.forEach(([name, value]) => {
    const card = document.createElement("article");
    card.className = "data-card";
    card.innerHTML = `<header><h3></h3><button class="icon-btn">Удалить</button></header><small></small><div class="value"></div>`;
    card.querySelector("h3").textContent = name;
    card.querySelector("small").textContent = Array.isArray(value) ? "array" : typeof value;
    card.querySelector(".value").textContent = JSON.stringify(value);
    card.querySelector(".value").ondblclick = async () => {
      const expression = prompt(`${name} =`, JSON.stringify(value));
      if (expression === null) return;
      const result = await api("set_variable", name, expression);
      if (!result.ok) toast(result.error); else refreshRuntime();
    };
    card.querySelector("button").onclick = async () => {
      await api("delete_variable", name);
      refreshRuntime();
    };
    host.appendChild(card);
  });
}

function renderControls(controls) {
  const host = $("controlsList");
  host.replaceChildren();
  const entries = Object.entries(controls);
  if (!entries.length) {
    host.innerHTML = '<div class="empty">Элементов управления пока нет</div>';
    return;
  }
  entries.forEach(([name, spec]) => {
    const card = document.createElement("article");
    card.className = "data-card";
    const header = document.createElement("header");
    header.innerHTML = "<h3></h3><button class='icon-btn'>Удалить</button>";
    header.querySelector("h3").textContent = `${spec.label || name} [${name}]`;
    header.querySelector("button").onclick = async () => {
      await api("delete_control", name); refreshRuntime();
    };
    card.appendChild(header);
    const row = document.createElement("div");
    row.className = "control-row";
    let input;
    if (spec.kind === "button") {
      input = document.createElement("button");
      input.textContent = `Нажать (${Number(spec.value || 0)})`;
      input.onclick = async () => { await api("set_control", name, Number(spec.value || 0) + 1); refreshRuntime(); };
    } else if (spec.kind === "checkbox") {
      input = document.createElement("input");
      input.type = "checkbox"; input.checked = Boolean(spec.value);
      input.onchange = () => api("set_control", name, input.checked);
    } else {
      input = document.createElement("input");
      input.type = spec.kind === "slider" ? "range" : spec.kind === "number" ? "number" : "text";
      input.value = spec.value;
      if (spec.kind === "slider" || spec.kind === "number") {
        input.min = spec.min; input.max = spec.max; input.step = spec.step;
      }
      const commit = () => api("set_control", name, spec.kind === "text" ? input.value : Number(input.value));
      input.onchange = commit;
      if (spec.kind === "slider") input.oninput = commit;
    }
    row.appendChild(input);
    card.appendChild(row);
    host.appendChild(card);
  });
}

function highlightBlock(id, active) {
  const baseId = String(id).split(":")[0];
  const block = workspace.getBlockById(baseId);
  if (!block?.pathObject?.svgPath) return;
  const path = block.pathObject.svgPath;
  if (active) {
    activeBlockStyles.set(baseId, {stroke: path.style.stroke, width: path.style.strokeWidth});
    path.style.stroke = "#FFD000";
    path.style.strokeWidth = "5px";
  } else {
    const old = activeBlockStyles.get(baseId) || {};
    path.style.stroke = old.stroke || "";
    path.style.strokeWidth = old.width || "";
    activeBlockStyles.delete(baseId);
  }
}

window.handleBackendEvent = async ({event, args}) => {
  if (event === "log") appendLog(String(args[0]));
  if (event === "status") setStatus(String(args[0]), String(args[1]));
  if (event === "connected") { connected = Boolean(args[0]); updateToolbar(); }
  if (event === "running") { running = Boolean(args[0]); updateToolbar(); }
  if (event === "tx") $("txValue").textContent = String(args[0]);
  if (event === "rx") $("rxValue").textContent = String(args[0]);
  if (event === "state_changed") refreshRuntime();
  if (event === "highlight" && args[0] === activeProgramId) highlightBlock(args[1], args[2]);
  if (event === "clear_highlights" && args[0] === activeProgramId) {
    [...activeBlockStyles.keys()].forEach((id) => highlightBlock(id, false));
  }
};

async function initialise() {
  workspace = Blockly.inject("blocklyDiv", {
    toolbox: $("toolbox"),
    trashcan: true,
    zoom: {controls: true, wheel: true, startScale: 0.9, maxScale: 1.6, minScale: 0.35},
    move: {scrollbars: true, drag: true, wheel: true},
    grid: {spacing: 22, length: 3, colour: "#d7dde5", snap: true},
    renderer: "zelos",
    theme: Blockly.Themes.Modern
  });
  workspace.addChangeListener((event) => {
    if (!event.isUiEvent) saveWorkspaceToProgram();
  });
  programs = [createDefaultProgram("Программа 1")];
  activeProgramId = programs[0].id;
  loadProgram(activeProgramId);
  bindUi();
  updateToolbar();

  // pywebview may inject its API before or after DOMContentLoaded.
  window.addEventListener("pywebviewready", initialiseBridge);
  initialiseBridge();
  let bridgeChecks = 0;
  const bridgeTimer = setInterval(() => {
    bridgeChecks += 1;
    initialiseBridge();
    if (apiReady || bridgeChecks >= 100) clearInterval(bridgeTimer);
  }, 100);
}

function bindUi() {
  $("connectBtn").onclick = () => apiReady && api("connect");
  $("disconnectBtn").onclick = () => apiReady && api("disconnect");
  $("stopBtn").onclick = () => apiReady && api("stop");
  $("runBtn").onclick = async () => {
    try {
      const result = await api("run_program", workspaceToRunner(), activeProgramId);
      if (!result.ok) toast(result.error);
    } catch (error) { toast(String(error)); }
  };
  $("saveBtn").onclick = () => saveProject(false);
  $("saveAsBtn").onclick = () => saveProject(true);
  $("openBtn").onclick = async () => {
    const result = await api("open_project");
    if (result.ok) {
      try {
        programs = normaliseProject(result.payload);
        activeProgramId = programs[0].id;
        loadProgram(activeProgramId);
        refreshRuntime();
        appendLog(`Проект открыт: ${result.path}`);
      } catch (error) { toast(String(error)); }
    } else if (!result.cancelled) toast(result.error);
  };
  $("addProgramBtn").onclick = () => {
    saveWorkspaceToProgram();
    const program = createDefaultProgram();
    programs.push(program);
    loadProgram(program.id);
  };
  $("renameProgramBtn").onclick = () => {
    const program = activeProgram();
    const name = prompt("Название программы:", program.name);
    if (name?.trim()) { program.name = name.trim(); renderProgramTabs(); }
  };
  $("deleteProgramBtn").onclick = () => {
    if (programs.length === 1) return toast("Должна остаться хотя бы одна программа");
    const current = activeProgram();
    if (!confirm(`Закрыть «${current.name}»?`)) return;
    programs = programs.filter((p) => p.id !== current.id);
    activeProgramId = programs[0].id;
    loadProgram(activeProgramId);
  };
  document.querySelectorAll(".panel-tabs button").forEach((button) => {
    button.onclick = () => {
      document.querySelectorAll(".panel-tabs button, .panel").forEach((node) => node.classList.remove("active"));
      button.classList.add("active");
      $(button.dataset.panel).classList.add("active");
    };
  });
  $("addVariableBtn").onclick = async () => {
    const name = prompt("Имя переменной:", "x");
    if (!name) return;
    const expression = prompt("Начальное значение / выражение:", "0");
    if (expression === null) return;
    const result = await api("set_variable", name, expression);
    if (!result.ok) toast(result.error); else refreshRuntime();
  };
  $("addControlBtn").onclick = async () => {
    const name = prompt("Имя элемента управления:", "speed");
    if (!name) return;
    const kind = prompt("Тип: slider / number / checkbox / text / button", "slider");
    if (!kind) return;
    const result = await api("define_control", {
      name, label: name, kind: kind.trim(), min: 0, max: 100, step: 1,
      value: kind.trim() === "text" ? "" : 0, bind_var: name
    });
    if (!result.ok) toast(result.error); else refreshRuntime();
  };
  window.addEventListener("resize", () => Blockly.svgResize(workspace));
}

async function saveProject(saveAs) {
  saveWorkspaceToProgram();
  const result = await api("save_project", {programs}, saveAs);
  if (!result.ok && !result.cancelled) toast(result.error);
}

document.addEventListener("DOMContentLoaded", initialise);
