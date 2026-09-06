const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadPromptTranslation() {
  const source = fs.readFileSync(path.join(__dirname, "../static/prompt.js"), "utf8");
  const bootMarker = "  restoreLibraryViewState();";
  const bootIndex = source.indexOf(bootMarker);
  assert.notEqual(bootIndex, -1);
  const context = vm.createContext({});
  vm.runInContext(source.slice(0, bootIndex) + `
    globalThis.translationApi = { chineseTextSegments, translateChineseText, translateTextSegments };
  })();`, context);
  const calls = [];
  context.fetch = async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push({ url, body });
    return { ok: true, json: async () => ({ translated_text: "EN[" + body.text + "]" }) };
  };
  return { api: context.translationApi, calls };
}

test("free-text translation sends only Chinese fragments and writes them back in place", async () => {
  const { api, calls } = loadPromptTranslation();
  const source = "A long English description. 中文人物缓慢转身，随后看向镜头。 Keep this English.";
  const result = await api.translateTextSegments({
    segments: [
      { type: "text", text: source },
      { type: "text", text: "Already English." },
      { type: "reference", sourceId: "character-1" },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [{
    url: "/api/prompt/translate",
    body: { text: "中文人物缓慢转身，随后看向镜头" },
  }]);
  assert.equal(result, "A long English description. EN[中文人物缓慢转身，随后看向镜头]。 Keep this English.Already English.__RH_REF_2__");
});

test("English-only free text stays unchanged without a translation request", async () => {
  const { api, calls } = loadPromptTranslation();
  const source = "Only English instructions stay exactly as written.";

  assert.equal(await api.translateTextSegments({ text: source }), source);
  assert.deepEqual(calls, []);
  assert.deepEqual(JSON.parse(JSON.stringify(api.chineseTextSegments(source))), []);
});
