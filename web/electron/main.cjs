const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

const webRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(webRoot, "..");
const serverScript = path.join(webRoot, "start.sh");
const packagedBackend = path.join(process.resourcesPath, "rh-workflow-desk-server");
const healthTimeoutMs = 15000;

let mainWindow = null;
let backendProcess = null;

ipcMain.handle("select-directory", async () => {
  const result = await dialog.showOpenDialog(mainWindow || undefined, {
    title: "选择默认产物目录",
    properties: ["openDirectory", "createDirectory"]
  });
  return result.canceled ? "" : (result.filePaths[0] || "");
});

function findFreePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      const port = typeof address === "object" && address ? address.port : 0;
      probe.close(() => resolve(port));
    });
  });
}

function checkBackend(port) {
  return new Promise((resolve) => {
    const request = http.get(
      { host: "127.0.0.1", port, path: "/api/health", timeout: 800 },
      (response) => {
        response.resume();
        resolve(response.statusCode === 200);
      }
    );
    request.on("error", () => resolve(false));
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function waitForBackend(port) {
  const deadline = Date.now() + healthTimeoutMs;
  while (Date.now() < deadline) {
    if (await checkBackend(port)) return;
    await new Promise((resolve) => setTimeout(resolve, 120));
  }
  throw new Error(`Python 本地服务未能在 ${healthTimeoutMs / 1000} 秒内启动`);
}

function stopBackend() {
  if (!backendProcess || backendProcess.killed) return;
  backendProcess.kill("SIGTERM");
  backendProcess = null;
}

async function startBackend() {
  const port = await findFreePort();
  const args = ["--no-browser", "--host", "127.0.0.1", "--port", String(port)];
  const packaged = app.isPackaged;
  if (packaged && !fs.existsSync(packagedBackend)) {
    throw new Error(`安装包缺少本地服务：${packagedBackend}`);
  }
  backendProcess = packaged
    ? spawn(packagedBackend, args, {
      cwd: app.getPath("userData"),
      env: {
        ...process.env,
        RH_ELECTRON: "1",
        RH_WORKFLOW_DESK_DATA_ROOT: path.join(app.getPath("userData"), "data")
      },
      stdio: ["ignore", "pipe", "pipe"]
    })
    : spawn("/bin/sh", [serverScript, ...args], {
      cwd: repoRoot,
      env: { ...process.env, RH_ELECTRON: "1" },
      stdio: ["ignore", "pipe", "pipe"]
    });
  backendProcess.stdout.on("data", (chunk) => process.stdout.write(`[rh-web] ${chunk}`));
  backendProcess.stderr.on("data", (chunk) => process.stderr.write(`[rh-web] ${chunk}`));
  backendProcess.once("exit", (code, signal) => {
    if (backendProcess && (code !== 0 || signal)) {
      console.error(`[rh-web] 本地服务已退出：code=${code} signal=${signal || "-"}`);
    }
  });
  await waitForBackend(port);
  return port;
}

function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1040,
    minHeight: 720,
    title: "RH Workflow Desk",
    backgroundColor: "#090e1b",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.on("closed", () => {
    mainWindow = null;
    app.quit();
  });
  return mainWindow.loadURL(`http://127.0.0.1:${port}/`);
}

async function boot() {
  try {
    const port = await startBackend();
    await createWindow(port);
  } catch (error) {
    stopBackend();
    dialog.showErrorBox("RH Workflow Desk 启动失败", error instanceof Error ? error.message : String(error));
    app.quit();
  }
}

app.whenReady().then(boot);

app.on("before-quit", () => {
  stopBackend();
});

app.on("window-all-closed", () => {
  stopBackend();
  app.quit();
});
