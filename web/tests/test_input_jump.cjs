const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadJumpRuntime({ card, stickyTabs, scrollHeight = 1800, scrollY = 120, innerHeight = 800 }) {
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  const startup = source.indexOf("  bindEvents();");
  assert.notEqual(startup, -1);
  const scrollCalls = [];
  const document = {
    documentElement: { scrollHeight, clientHeight: innerHeight },
    querySelectorAll(selector) {
      return selector === ".input-card[data-input-id]" ? [card] : [];
    },
    querySelector(selector) {
      return selector === ".submit-workspace-tabs-sticky" ? stickyTabs : null;
    },
  };
  const window = {
    innerHeight,
    scrollY,
    scrollTo(options) { scrollCalls.push(options); },
    setTimeout() {},
  };
  const context = vm.createContext({ document, window });
  vm.runInContext(source.slice(0, startup) + "  globalThis.api = { jumpToInput };\n})();", context);
  return { api: context.api, scrollCalls };
}

test("input jump scrolls a colon-containing node id into view below sticky tabs", () => {
  const classNames = [];
  const card = {
    dataset: { inputId: "37:image" },
    offsetWidth: 500,
    getBoundingClientRect() { return { top: 920, bottom: 1100, height: 180 }; },
    classList: {
      add(name) { classNames.push("add:" + name); },
      remove(name) { classNames.push("remove:" + name); },
    },
  };
  const stickyTabs = { getBoundingClientRect() { return { top: 0, bottom: 112 }; } };
  const { api, scrollCalls } = loadJumpRuntime({ card, stickyTabs });

  api.jumpToInput("37:image");

  assert.equal(scrollCalls.length, 1);
  assert.equal(scrollCalls[0].behavior, "smooth");
  assert.ok(scrollCalls[0].top > 120);
  assert.deepEqual(classNames, ["remove:input-target", "add:input-target"]);
});

test("input jump ignores an unknown node id", () => {
  const card = {
    dataset: { inputId: "37:image" },
    getBoundingClientRect() { return { top: 100, bottom: 200, height: 100 }; },
    classList: { add() {}, remove() {} },
  };
  const { api, scrollCalls } = loadJumpRuntime({ card, stickyTabs: null });

  api.jumpToInput("52:audio");

  assert.equal(scrollCalls.length, 0);
});
