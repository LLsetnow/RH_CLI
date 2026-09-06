const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function setup(count, page = 1) {
  const elements = new Map();
  const document = {
    activeElement: null,
    querySelector() { return this.modal || null; },
    getElementById(id) { return elements.get(id) || null; },
    querySelectorAll() { return grid.children; },
    createElement() {
      return {
        content: { querySelectorAll() { return []; } },
        set innerHTML(markup) {
          this.content.firstElementChild = card(markup.match(/data-artifact-id="([^"]+)"/)[1]);
        },
      };
    },
  };
  const grid = {
    children: [],
    querySelectorAll() { return this.children.slice(); },
    querySelector() { return null; },
    insertBefore(node, next) {
      node.remove();
      const index = next ? this.children.indexOf(next) : this.children.length;
      this.children.splice(index, 0, node);
    },
    set innerHTML(markup) { this.children = []; this.emptyMarkup = markup; },
  };
  function card(id) {
    return {
      dataset: { artifactId: id },
      media: {},
      classList: { toggle() {} },
      setAttribute() {},
      contains(node) { return node === this; },
      focus() { document.activeElement = this; },
      closest() { return null; },
      getBoundingClientRect() { return { top: 100, bottom: 200 }; },
      querySelector() { return this.head || (this.head = { outerHTML: "" }); },
      remove() {
        const index = grid.children.indexOf(this);
        if (index >= 0) grid.children.splice(index, 1);
      },
    };
  }
  elements.set("outputGrid", grid);
  elements.set("outputPagination", { innerHTML: "", hidden: false });
  elements.set("outputWorkflowFilters", { innerHTML: "", hidden: true });
  elements.set("deleteOneStarOutputs", { innerHTML: "删除一星", classList: { add() {}, remove() {} } });
  elements.set("confirmOutputProjectMove", { disabled: false, textContent: "移动任务" });
  const context = vm.createContext({ document, window: {
    confirm() { return true; },
    innerHeight: 900,
    getComputedStyle() { return { gridTemplateColumns: "200px 200px" }; },
    RHMotion: { bindVideoLoopControls() {}, closeModal() {}, showToast() {} },
  } });
  const source = fs.readFileSync(path.join(__dirname, "../static/outputs.js"), "utf8");
  const startup = "  if (!outputUrlHasContext()) restoreOutputViewState();\n  readOutputContext();\n  outputStatePersistenceReady = true;\n  bindEvents();\n  loadOutputs(false);";
  assert.ok(source.includes(startup));
  vm.runInContext(source.replace(startup, `
    render = function () { throw new Error("Must not render the whole page"); };
    renderSummary = function () {};
    renderProjectBrowser = function () {};
    globalThis.api = { state, refreshTaggedArtifact, refreshRatedArtifact,
      handleOutputArrowNavigation, restoreArtifactFocusAfterClick, focusSelectedArtifact,
      deleteArtifactTask, deleteOneStarOutputs, outputWorkflowNames,
      outputWorkflowFilterLabel, outputWorkflowFilterMatches, renderWorkflowFilters,
      filteredOutputs, confirmOutputProjectMove, projectMove, updateOutputTaskProjectState };
  `), context);
  const { api } = context;
  api.state.outputs = Array.from({ length: count }, (_, index) => ({
    id: String(index), task_id: "task-" + index, kind: "text", display_type: "text", tags: [], rating: 0,
  }));
  api.state.page = page;
  api.state.tagFilters.H = "exclude";
  grid.children = api.state.outputs.slice((page - 1) * 64, page * 64).map(item => card(item.id));
  return { api, grid, document, context, elements, pagination: elements.get("outputPagination") };
}

test("tag exclusion removes only the selected card, fills the page and preserves media and focus", () => {
  const { api, grid, document, pagination } = setup(66);
  const before = grid.children.slice();
  api.state.selectedArtifactId = "10";
  before[10].focus();
  api.state.outputs[10].tags = ["H"];
  api.refreshTaggedArtifact(api.state.outputs[10]);
  assert.equal(grid.children.length, 64);
  assert.deepEqual(grid.children.slice(0, 63), before.filter((_, index) => index !== 10));
  assert.equal(grid.children[63].dataset.artifactId, "64");
  assert.equal(api.state.selectedArtifactId, "11");
  assert.equal(document.activeElement, before[11]);
  assert.equal(grid.children[10].media, before[11].media);
  assert.match(pagination.innerHTML, /第 1 \/ 2 页/);
});

test("removing the final filtered card shows the empty state", () => {
  const { api, grid, pagination } = setup(1);
  api.state.selectedArtifactId = "0";
  api.state.outputs[0].tags = ["H"];
  api.refreshTaggedArtifact(api.state.outputs[0]);
  assert.equal(grid.children.length, 0);
  assert.match(grid.emptyMarkup, /没有符合筛选条件/);
  assert.equal(api.state.selectedArtifactId, "");
  assert.equal(api.state.page, 1);
  assert.equal(pagination.hidden, true);
});

test("removing the last card on the last page clamps pagination and fills the preceding page", () => {
  const { api, grid, pagination } = setup(65, 2);
  api.state.selectedArtifactId = "64";
  api.state.outputs[64].tags = ["H"];
  api.refreshTaggedArtifact(api.state.outputs[64]);
  assert.equal(api.state.page, 1);
  assert.equal(grid.children.length, 64);
  assert.equal(grid.children[0].dataset.artifactId, "0");
  assert.equal(api.state.selectedArtifactId, "0");
  assert.equal(pagination.hidden, true);
});

test("removing an included tag respects combined filters and retains other cards", () => {
  const { api, grid } = setup(3);
  api.state.tagFilters["案例"] = "include";
  api.state.outputs.forEach(item => { item.tags = ["案例"]; });
  const before = grid.children.slice();
  api.state.outputs[1].tags = [];
  api.refreshTaggedArtifact(api.state.outputs[1]);
  assert.deepEqual(grid.children, [before[0], before[2]]);
});

test("a tag change that still matches only updates the card heading", () => {
  const { api, grid } = setup(3);
  const before = grid.children.slice();
  api.state.outputs[1].tags = ["案例"];
  api.refreshTaggedArtifact(api.state.outputs[1]);
  assert.deepEqual(grid.children, before);
  assert.match(before[1].head.outerHTML, /artifact-tag-case/);
});

test("rating exclusion uses the same incremental update", () => {
  const { api, grid } = setup(3);
  api.state.rating = "unrated";
  const before = grid.children.slice();
  api.state.outputs[1].rating = 5;
  api.refreshRatedArtifact(api.state.outputs[1]);
  assert.deepEqual(grid.children, [before[0], before[2]]);
});

function arrowEvent(target, key = "ArrowDown") {
  return {
    key, target, defaultPrevented: false, stopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; },
  };
}

for (const control of ["video", "audio", "tag-filter", "rating-button"]) {
  test(`arrows navigate cards when ${control} owns focus`, () => {
    const { api, grid, document } = setup(4);
    const target = { closest() { return null; }, control };
    document.activeElement = target;
    api.state.selectedArtifactId = "0";
    const event = arrowEvent(target);
    api.handleOutputArrowNavigation(event);
    assert.equal(api.state.selectedArtifactId, "2");
    assert.equal(document.activeElement, grid.children[2]);
    assert.equal(event.defaultPrevented, true);
    assert.equal(event.stopped, true);
  });
}

test("click restoration runs after controls and returns focus without changing selection", async () => {
  const { api, grid, document } = setup(4);
  api.state.selectedArtifactId = "1";
  const target = { closest() { return null; } };
  api.restoreArtifactFocusAfterClick({ target });
  document.activeElement = target;
  assert.equal(document.activeElement, target);
  await Promise.resolve();
  assert.equal(document.activeElement, grid.children[1]);
  assert.equal(api.state.selectedArtifactId, "1");
});

test("text editing and modal actions retain their focus and arrow keys", async () => {
  const { api, document } = setup(4);
  api.state.selectedArtifactId = "0";
  const input = { closest() { return this; } };
  document.activeElement = input;
  const editing = arrowEvent(input);
  api.handleOutputArrowNavigation(editing);
  api.restoreArtifactFocusAfterClick(editing);
  await Promise.resolve();
  assert.equal(editing.defaultPrevented, false);
  assert.equal(document.activeElement, input);
  const button = { closest() { return null; } };
  document.activeElement = button;
  api.restoreArtifactFocusAfterClick({ target: button });
  document.modal = {};
  const inModal = arrowEvent(button);
  api.handleOutputArrowNavigation(inModal);
  await Promise.resolve();
  assert.equal(inModal.defaultPrevented, false);
  assert.equal(document.activeElement, button);
  assert.equal(api.state.selectedArtifactId, "0");
});

test("an arrow at the grid boundary still restores card focus", () => {
  const { api, grid, document } = setup(4);
  api.state.selectedArtifactId = "0";
  const target = { closest() { return null; } };
  document.activeElement = target;
  const event = arrowEvent(target, "ArrowLeft");
  api.handleOutputArrowNavigation(event);
  assert.equal(api.state.selectedArtifactId, "0");
  assert.equal(document.activeElement, grid.children[0]);
  assert.equal(event.defaultPrevented, true);
});

function successfulDeletion(context, expectedUrl, deleted = 1) {
  context.fetch = async (url, options) => {
    assert.equal(url, expectedUrl);
    assert.equal(options.method, "DELETE");
    return { ok: true, json: async () => ({ deleted }) };
  };
}

test("task deletion removes all sibling outputs, preserves other cards and fills the page", async () => {
  const { api, grid, document, context } = setup(66);
  api.state.outputs[11].task_id = "task-10";
  api.state.selectedArtifactId = "10";
  const before = grid.children.slice();
  before[10].focus();
  successfulDeletion(context, "/api/tasks/task-10");
  await api.deleteArtifactTask(api.state.outputs[10]);
  assert.equal(api.state.outputs.length, 64);
  assert.equal(grid.children.length, 64);
  assert.deepEqual(grid.children.slice(0, 62), before.filter((_, i) => i !== 10 && i !== 11));
  assert.deepEqual(grid.children.slice(62).map(card => card.dataset.artifactId), ["64", "65"]);
  assert.equal(api.state.selectedArtifactId, "12");
  assert.equal(document.activeElement, before[12]);
  assert.equal(api.state.summary.total, 64);
});

test("deleting the last card on the final page falls back to the preceding page", async () => {
  const { api, grid, context, pagination, document } = setup(65, 2);
  api.state.selectedArtifactId = "64";
  successfulDeletion(context, "/api/tasks/task-64");
  await api.deleteArtifactTask(api.state.outputs[64]);
  assert.equal(api.state.page, 1);
  assert.equal(grid.children.length, 64);
  assert.equal(pagination.hidden, true);
  assert.equal(document.activeElement, grid.children[0]);
});

test("deleting the last matching output shows an empty filter result without removing other data", async () => {
  const { api, grid, context } = setup(2);
  api.state.outputs[1].tags = ["H"];
  grid.children.pop();
  api.state.selectedArtifactId = "0";
  successfulDeletion(context, "/api/tasks/task-0");
  await api.deleteArtifactTask(api.state.outputs[0]);
  assert.equal(api.state.outputs.length, 1);
  assert.equal(grid.children.length, 0);
  assert.match(grid.emptyMarkup, /没有符合筛选条件/);
  assert.equal(api.state.selectedArtifactId, "");
});

test("a failed deletion leaves cards, media, selection and action availability intact", async () => {
  const { api, grid, context } = setup(3);
  const before = grid.children.slice();
  const button = { disabled: false };
  api.state.selectedArtifactId = "1";
  context.fetch = async () => ({ ok: false, json: async () => ({ message: "删除失败" }) });
  await api.deleteArtifactTask(api.state.outputs[1], button);
  assert.deepEqual(grid.children, before);
  assert.equal(api.state.outputs.length, 3);
  assert.equal(api.state.selectedArtifactId, "1");
  assert.equal(button.disabled, false);
});

test("selection changed during deletion remains selected when the response arrives", async () => {
  const { api, grid, context, document } = setup(4);
  let finish;
  context.fetch = () => new Promise(resolve => { finish = resolve; });
  api.state.selectedArtifactId = "0";
  const pending = api.deleteArtifactTask(api.state.outputs[0]);
  api.state.selectedArtifactId = "3";
  const selected = grid.children[3];
  selected.focus();
  finish({ ok: true, json: async () => ({}) });
  await pending;
  assert.equal(api.state.selectedArtifactId, "3");
  assert.equal(document.activeElement, selected);
  assert.equal(grid.children[2], selected);
});

test("bulk one-star deletion preserves surviving cards and other projects", async () => {
  const { api, grid, context } = setup(4);
  api.state.outputs.forEach(item => { item.project_id = "a"; });
  api.state.outputs[0].rating = 1;
  api.state.outputs[1].rating = 1;
  api.state.outputs[1].project_id = "b";
  api.state.projectId = "a";
  const before = grid.children.slice();
  grid.children.splice(1, 1);
  successfulDeletion(context, "/api/outputs/rating/1?project_id=a&tag_h=exclude");
  await api.deleteOneStarOutputs();
  assert.deepEqual(Array.from(api.state.outputs, item => item.id), ["1", "2", "3"]);
  assert.deepEqual(grid.children, [before[2], before[3]]);
  assert.equal(api.state.selectedArtifactId, "2");
});

test("moving a task from the card context menu refreshes matching cards locally", async () => {
  const { api, grid, context, document, elements } = setup(3);
  api.state.outputs[0].task_id = "task-move";
  api.state.outputs[0].project_id = "project-a";
  api.state.outputs[1].task_id = "task-move";
  api.state.outputs[1].project_id = "project-a";
  api.state.outputs[2].task_id = "task-keep";
  api.state.outputs[2].project_id = "project-a";
  api.state.projects = [
    { id: "project-a", name: "项目 A", task_count: 2 },
    { id: "project-b", name: "项目 B", task_count: 1 },
  ];
  api.state.projectId = "project-a";
  api.state.selectedArtifactId = "0";
  grid.children[0].focus();
  const before = grid.children.slice();
  api.projectMove.item = api.state.outputs[0];
  api.projectMove.projectId = "project-b";
  context.fetch = async (url, options) => {
    assert.equal(url, "/api/tasks/task-move/project");
    assert.equal(options.method, "PATCH");
    return { ok: true, json: async () => ({ task: { project_id: "project-b", project_name: "项目 B", project_path: "" } }) };
  };
  await api.confirmOutputProjectMove();
  assert.deepEqual(grid.children, [before[2]]);
  assert.equal(api.state.selectedArtifactId, "2");
  assert.equal(document.activeElement, before[2]);
  assert.equal(api.state.projects[0].task_count, 1);
  assert.equal(api.state.projects[1].task_count, 2);
  assert.equal(elements.get("confirmOutputProjectMove").disabled, false);
});

test("workflow filters are generated only for the opened project and use unique names", () => {
  const { api, elements } = setup(4);
  api.state.outputs[0].project_id = "project-a";
  api.state.outputs[1].project_id = "project-a";
  api.state.outputs[2].project_id = "project-b";
  api.state.outputs[0].task_name = "同一个工作流";
  api.state.outputs[1].task_name = "同一个工作流";
  api.state.outputs[2].task_name = "另一个工作流";
  api.state.projectId = "project-a";
  assert.deepEqual(JSON.parse(JSON.stringify(api.outputWorkflowNames())), ["同一个工作流"]);
  api.renderWorkflowFilters();
  const workflowFilters = elements.get("outputWorkflowFilters");
  assert.equal(workflowFilters.hidden, false);
  assert.match(workflowFilters.innerHTML, /同一个工作流/);
  assert.equal((workflowFilters.innerHTML.match(/data-output-workflow=/g) || []).length, 2);
  api.state.workflowFilter = "同一个工作流";
  assert.equal(api.filteredOutputs().length, 2);
  api.state.projectId = "";
  api.renderWorkflowFilters();
  assert.equal(workflowFilters.hidden, true);
  assert.equal(api.state.workflowFilter, "");
});

test("long workflow names have a bounded label while retaining the full title", () => {
  const { api } = setup(1);
  const name = "这是一个非常非常非常非常非常长的工作流文件名称.json";
  const label = api.outputWorkflowFilterLabel(name);
  assert.ok(Array.from(label).length <= 18);
  assert.equal(label.endsWith("…"), true);
});

test("workflow filter excludes outputs from other workflows and clears when no longer available", () => {
  const { api, elements } = setup(3);
  api.state.projectId = "project-a";
  api.state.outputs.forEach(item => { item.project_id = "project-a"; });
  api.state.outputs[0].task_name = "工作流 A";
  api.state.outputs[1].task_name = "工作流 B";
  api.state.outputs[2].task_name = "工作流 A";
  api.state.workflowFilter = "工作流 B";
  api.renderWorkflowFilters();
  assert.equal(api.filteredOutputs().length, 1);
  api.state.outputs[1].project_id = "project-b";
  api.renderWorkflowFilters();
  assert.equal(api.state.workflowFilter, "");
  assert.equal(elements.get("outputWorkflowFilters").hidden, false);
});
