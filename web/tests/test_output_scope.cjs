const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadOutputs() {
  const source = fs.readFileSync(path.join(__dirname, "../static/outputs.js"), "utf8");
  const elements = new Map();
  const storage = new Map();
  const localStorage = {
    getItem(key) { return storage.has(key) ? storage.get(key) : null; },
    setItem(key, value) { storage.set(key, String(value)); },
    removeItem(key) { storage.delete(key); },
  };
  const context = vm.createContext({
    URLSearchParams,
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, {
          innerHTML: "", textContent: "", disabled: false,
          classList: { add() {}, remove() {}, toggle() {} },
          setAttribute() {}, removeAttribute() {},
        });
        return elements.get(id);
      },
      querySelectorAll() { return []; },
    },
    window: { confirm() { return true; }, location: {}, localStorage, setTimeout() {} },
  });
  const startup = "  if (!outputUrlHasContext()) restoreOutputViewState();\n  readOutputContext();\n  outputStatePersistenceReady = true;\n  bindEvents();\n  loadOutputs(false);";
  assert.ok(source.includes(startup));
  vm.runInContext(source.replace(startup, `
    render = function () {};
    refreshOutputCollection = function () {};
    updateFilterSlider = function () {};
    this.api = { state, caseMediaOutputs, oneStarOutputs, scopedOutputSummary, renderSummary,
      exportCaseOutputs, deleteOneStarOutputs, restoreOutputViewState, saveOutputViewState,
      outputViewStateKey, readOutputContext, formatVideoDuration, artifactDurationMarkup };
  `.replace("this.api", "globalThis.api")), context);
  context.api.state.outputs = [
    { id: "a", project_id: "项目 A", kind: "file", display_type: "video", rating: 1, tags: ["案例"] },
    { id: "b", project_id: "b", kind: "file", display_type: "image", rating: 1, tags: ["案例"] },
    { id: "u", project_id: "", kind: "file", display_type: "audio", rating: 1, tags: ["案例"] },
    { id: "keep", project_id: "项目 A", kind: "file", display_type: "video", rating: 2 },
  ];
  return { context, api: context.api, elements, storage };
}

test("output view state restores folder, filters, search and selection", () => {
  const { api, elements, storage } = loadOutputs();
  storage.set(api.outputViewStateKey, JSON.stringify({
    version: 1,
    projectId: "项目 A",
    type: "video",
    rating: 4,
    tagFilters: { "案例": "include", H: "exclude" },
    workflowFilter: "流程甲",
    search: "目标片段",
    sort: "oldest",
    page: 3,
    selectedArtifactId: "a",
  }));
  api.restoreOutputViewState();
  assert.equal(api.state.projectId, "项目 A");
  assert.equal(api.state.type, "video");
  assert.equal(api.state.rating, 4);
  assert.deepEqual(JSON.parse(JSON.stringify(api.state.tagFilters)), { "案例": "include", H: "exclude" });
  assert.equal(api.state.workflowFilter, "流程甲");
  assert.equal(api.state.search, "目标片段");
  assert.equal(api.state.sort, "oldest");
  assert.equal(api.state.page, 3);
  assert.equal(api.state.selectedArtifactId, "a");
  assert.equal(elements.get("outputSearch").value, "目标片段");
  assert.equal(elements.get("outputSort").value, "oldest");

  api.saveOutputViewState();
  assert.equal(JSON.parse(storage.get(api.outputViewStateKey)).projectId, "项目 A");
  assert.equal(JSON.parse(storage.get(api.outputViewStateKey)).tagFilters.H, "exclude");
});

test("video duration is formatted beside video resolution only", () => {
  const { api } = loadOutputs();
  assert.equal(api.formatVideoDuration(0), "0s");
  assert.equal(api.formatVideoDuration(15), "15s");
  assert.equal(api.formatVideoDuration(61.4), "61s");
  assert.equal(api.formatVideoDuration(3661), "3661s");
  assert.equal(api.formatVideoDuration("invalid"), "");
  assert.match(api.artifactDurationMarkup({ display_type: "video" }), /artifact-duration/);
  assert.equal(api.artifactDurationMarkup({ display_type: "image" }), "");
});

test("explicit dashboard context overrides saved output view state without overwriting it", () => {
  const { api, context, storage } = loadOutputs();
  storage.set(api.outputViewStateKey, JSON.stringify({ version: 1, projectId: "项目 A", search: "旧关键词", type: "video", page: 4 }));
  api.restoreOutputViewState();
  context.window.location.search = "?workflow_name=当前工作流&range_days=7";
  api.readOutputContext();
  assert.equal(api.state.projectId, "");
  assert.equal(api.state.type, "all");
  assert.equal(api.state.search, "当前工作流");
  assert.equal(api.state.contextWorkflowName, "当前工作流");
  assert.equal(api.state.contextRangeDays, 7);
  api.saveOutputViewState();
  assert.equal(JSON.parse(storage.get(api.outputViewStateKey)).projectId, "项目 A");
  assert.equal(JSON.parse(storage.get(api.outputViewStateKey)).search, "旧关键词");
});

test("toolbar counts and disabled state follow the opened folder", () => {
  const { api, elements } = loadOutputs();
  for (const [projectId, count] of [["项目 A", 1], ["b", 1], ["__unclassified__", 1], ["missing", 0], ["", 3]]) {
    api.state.projectId = projectId;
    api.renderSummary();
    assert.equal(elements.get("oneStarOutputCount").textContent, String(count));
    assert.equal(elements.get("caseOutputCount").textContent, String(count));
    assert.equal(elements.get("deleteOneStarOutputs").disabled, count === 0);
    assert.equal(elements.get("exportCaseOutputs").disabled, count === 0);
  }
});

test("media and rating summaries follow the opened folder", () => {
  const { api } = loadOutputs();
  api.state.outputs = [
    { id: "a1", task_id: "task-a", project_id: "项目 A", kind: "file", display_type: "video", rating: 1 },
    { id: "a2", task_id: "task-a", project_id: "项目 A", kind: "file", display_type: "image", rating: 2 },
    { id: "b1", task_id: "task-b", project_id: "项目 B", kind: "file", display_type: "audio", rating: 1 },
    { id: "u1", task_id: "task-u", project_id: "", kind: "text", display_type: "text", rating: 3 },
  ];

  api.state.projectId = "项目 A";
  assert.deepEqual(JSON.parse(JSON.stringify(api.scopedOutputSummary())), {
    total: 2,
    tasks: 1,
    image: 1,
    video: 1,
    audio: 0,
    other: 0,
    text: 0,
    rating_counts: { unrated: 0, "1": 1, "2": 1, "3": 0, "4": 0, "5": 0 },
    tag_counts: { "案例": 0, "H": 0 },
  });

  api.state.projectId = "项目 B";
  assert.equal(api.scopedOutputSummary().total, 1);
  assert.equal(api.scopedOutputSummary().audio, 1);
  assert.equal(api.scopedOutputSummary().rating_counts["1"], 1);

  api.state.projectId = "";
  assert.equal(api.scopedOutputSummary().total, 4);
  assert.equal(api.scopedOutputSummary().rating_counts["3"], 1);
});

test("bulk action counts follow the active folder and filters", () => {
  const { api, elements } = loadOutputs();
  api.state.projectId = "项目 A";
  api.state.type = "video";
  api.state.rating = 1;
  api.state.tagFilters["案例"] = "include";
  api.state.outputs[0].task_name = "流程甲";
  api.state.workflowFilter = "流程甲";
  api.renderSummary();
  assert.equal(elements.get("oneStarOutputCount").textContent, "1");
  assert.equal(elements.get("caseOutputCount").textContent, "1");
  assert.equal(elements.get("deleteOneStarOutputs").disabled, false);
  assert.equal(elements.get("exportCaseOutputs").disabled, false);
});

test("export sends the selected folder and every active filter", () => {
  const { api, context } = loadOutputs();
  api.state.projectId = "项目 A";
  api.state.outputs[0].name = "目标片段";
  api.state.outputs[0].task_name = "长流程";
  api.state.search = "目标";
  api.state.type = "video";
  api.state.rating = 1;
  api.state.tagFilters["案例"] = "include";
  api.state.tagFilters.H = "exclude";
  api.state.workflowFilter = "长流程";
  api.exportCaseOutputs();
  assert.equal(context.window.location.href, "/api/outputs/export/case?project_id=" + encodeURIComponent("项目 A") +
    "&search=" + encodeURIComponent("目标") + "&type=video&rating=1&workflow=" + encodeURIComponent("长流程") +
    "&tag_case=include&tag_h=exclude");
});

test("deletion keeps other folders when navigation changes during the request", async () => {
  const { api, context } = loadOutputs();
  let finish;
  context.fetch = (url, options) => {
    assert.equal(url, "/api/outputs/rating/1?project_id=" + encodeURIComponent("项目 A"));
    assert.equal(options.method, "DELETE");
    return new Promise(resolve => { finish = resolve; });
  };
  api.state.projectId = "项目 A";
  api.deleteOneStarOutputs();
  api.state.projectId = "b";
  finish({ ok: true, json: async () => ({ deleted: 1 }) });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(Array.from(api.state.outputs, item => item.id), ["b", "u", "keep"]);
});
