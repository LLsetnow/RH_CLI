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
let backendPort = null;
const accountWindows = new Map();

function normaliseAccount(value) {
  const account = value && typeof value === "object" ? value : {};
  const id = String(account.id || "").trim();
  if (!id) throw new Error("托管账号缺少本地 ID");
  return {
    id,
    name: String(account.name || "RunningHub 账号"),
    site: account.site === "cn" ? "cn" : "ai"
  };
}

function accountSiteUrl(site) {
  return site === "cn" ? "https://www.runninghub.cn/" : "https://www.runninghub.ai/";
}

function accountPartition(accountId) {
  const safeId = String(accountId).replace(/[^a-zA-Z0-9_-]/g, "_");
  return `persist:rh-account-${safeId}`;
}

function patchLocalAccount(accountId, changes) {
  if (!backendPort) return Promise.resolve(null);
  const body = JSON.stringify(changes || {});
  return new Promise((resolve, reject) => {
    const request = http.request({
      host: "127.0.0.1",
      port: backendPort,
      path: `/api/accounts/${encodeURIComponent(accountId)}`,
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body)
      },
      timeout: 5000
    }, (response) => {
      let raw = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { raw += chunk; });
      response.on("end", () => {
        if (response.statusCode && response.statusCode >= 400) {
          reject(new Error(`本地账号状态保存失败 (${response.statusCode})`));
          return;
        }
        try { resolve(raw ? JSON.parse(raw) : null); } catch (_) { resolve(null); }
      });
    });
    request.on("error", reject);
    request.on("timeout", () => request.destroy(new Error("本地账号状态保存超时")));
    request.write(body);
    request.end();
  });
}

async function readAccountSession(accountWindow) {
  const script = `
    (async () => {
      const parse = (value) => {
        try { return value ? JSON.parse(value) : null; } catch (_) { return null; }
      };
      const isUser = (value) => Boolean(value && typeof value === "object" && (
        value.id || value.userId || value.uid || value.nickName || value.nickname || value.username || value.email
      ));
      const unwrap = (value) => {
        if (!value || typeof value !== "object") return null;
        if (isUser(value)) return value;
        if (isUser(value.data)) return value.data;
        if (isUser(value.user)) return value.user;
        return null;
      };
      let user = null;
      for (const key of ["userInfo", "user_info", "user", "User"]) {
        user = unwrap(parse(localStorage.getItem(key)));
        if (user) break;
      }
      const token = localStorage.getItem("Rh-Accesstoken") || localStorage.getItem("accessToken") || "";
      let refreshed = null;
      try {
        const headers = { "Content-Type": "application/json" };
        if (token) headers.Authorization = "Bearer " + token;
        const userId = user && (user.id || user.userId || user.uid);
        const response = await fetch("/uc/getUserInfo", {
          method: "POST",
          credentials: "include",
          headers,
          body: JSON.stringify(userId ? { userId } : {})
        });
        const payload = await response.json();
        refreshed = unwrap(payload);
        if (refreshed) {
          user = refreshed;
          localStorage.setItem("userInfo", JSON.stringify(refreshed));
        }
      } catch (_) {}
      const userId = user && (user.id || user.userId || user.uid);
      const loggedIn = Boolean(token || userId || isUser(user));
      const value = (...keys) => {
        for (const key of keys) {
          if (user && user[key] !== undefined && user[key] !== null && String(user[key]) !== "") return user[key];
        }
        return "";
      };
      return {
        loggedIn,
        loginCoinTriggered: Boolean(value("loginCoinTriggered")),
        loginDailyCoin: value("loginDailyCoin", "dailyGold", "DailyGold"),
        balance: value("remainCoins", "coins", "totalCoin", "virtualCoin", "gold", "rhCoins"),
        userId: userId ? String(userId) : "",
        href: location.href
      };
    })()
  `;
  return accountWindow.webContents.executeJavaScript(script, true);
}

function accountStateFromSession(session, isCheckin) {
  const loggedIn = Boolean(session && session.loggedIn);
  const reward = session && session.loginDailyCoin != null ? String(session.loginDailyCoin) : "";
  const rewarded = Boolean(session && session.loginCoinTriggered) || Boolean(reward && reward !== "0");
  let status = "login_required";
  let message = "请在打开的 RunningHub 窗口完成登录";
  if (loggedIn && rewarded) {
    status = "checked_in";
    message = reward ? `网站已返回今日登录奖励：${reward} RH 币` : "网站已返回今日登录奖励";
  } else if (loggedIn && isCheckin) {
    status = "not_checked_in";
    message = "已登录，但网站未返回今日登录奖励；如需重新认证，请在账号窗口完成登录后再试";
  } else if (loggedIn) {
    status = "ready";
    message = "已登录，可点击签到读取今日登录奖励";
  }
  return {
    status,
    status_message: message,
    daily_coin: reward,
    balance: session && session.balance != null ? String(session.balance) : "",
    session
  };
}

async function syncAccountWindow(account, accountWindow, isCheckin) {
  const session = await readAccountSession(accountWindow);
  const state = accountStateFromSession(session, isCheckin);
  const changes = {
    status: state.status,
    status_message: state.status_message,
    daily_coin: state.daily_coin,
    balance: state.balance,
    checked_at: Date.now()
  };
  if (session.loggedIn && !isCheckin) changes.last_login_at = Date.now();
  if (isCheckin) changes.last_checkin_at = Date.now();
  await patchLocalAccount(account.id, changes);
  return state;
}

function scheduleAccountSync(account, accountWindow) {
  if (accountWindow.__rhSyncTimer) clearTimeout(accountWindow.__rhSyncTimer);
  accountWindow.__rhSyncTimer = setTimeout(() => {
    if (accountWindow.isDestroyed()) return;
    syncAccountWindow(account, accountWindow, false).catch(() => {});
  }, 450);
}

function createAccountWindow(account) {
  const accountWindow = new BrowserWindow({
    width: 1180,
    height: 820,
    minWidth: 820,
    minHeight: 620,
    title: `${account.name} · ${account.site === "cn" ? "runninghub.cn" : "runninghub.ai"}`,
    backgroundColor: "#090e1b",
    webPreferences: {
      partition: accountPartition(account.id),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });
  accountWindow.webContents.on("did-finish-load", () => scheduleAccountSync(account, accountWindow));
  accountWindow.webContents.on("did-navigate", () => scheduleAccountSync(account, accountWindow));
  accountWindow.on("closed", () => {
    if (accountWindow.__rhSyncTimer) clearTimeout(accountWindow.__rhSyncTimer);
    accountWindows.delete(account.id);
  });
  accountWindows.set(account.id, accountWindow);
  return accountWindow;
}

async function ensureAccountWindow(account) {
  const existing = accountWindows.get(account.id);
  if (existing && !existing.isDestroyed()) {
    existing.show();
    existing.focus();
    return existing;
  }
  const accountWindow = createAccountWindow(account);
  try {
    await accountWindow.loadURL(accountSiteUrl(account.site));
  } catch (error) {
    accountWindows.delete(account.id);
    if (!accountWindow.isDestroyed()) accountWindow.destroy();
    throw error;
  }
  return accountWindow;
}

function reloadAccountWindow(accountWindow) {
  return new Promise((resolve, reject) => {
    let finished = false;
    const timeout = setTimeout(() => {
      if (finished) return;
      finished = true;
      accountWindow.webContents.removeListener("did-finish-load", onLoad);
      reject(new Error("RunningHub 页面刷新超时，请检查网络后重试"));
    }, 20000);
    const onLoad = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timeout);
      resolve();
    };
    accountWindow.webContents.once("did-finish-load", onLoad);
    try {
      accountWindow.webContents.reload();
    } catch (error) {
      if (finished) return;
      finished = true;
      clearTimeout(timeout);
      accountWindow.webContents.removeListener("did-finish-load", onLoad);
      reject(error);
    }
  });
}

ipcMain.handle("account-login", async (_event, rawAccount) => {
  const account = normaliseAccount(rawAccount);
  const accountWindow = await ensureAccountWindow(account);
  accountWindow.show();
  accountWindow.focus();
  let state = null;
  try { state = await syncAccountWindow(account, accountWindow, false); } catch (_) {}
  return { ok: true, status: state ? state.status : "login_window_opened" };
});

ipcMain.handle("account-checkin", async (_event, rawAccount) => {
  const account = normaliseAccount(rawAccount);
  await patchLocalAccount(account.id, {
    status: "checking",
    status_message: "正在刷新 RunningHub 会话并读取今日登录奖励…",
    checked_at: Date.now()
  });
  const accountWindow = await ensureAccountWindow(account);
  accountWindow.show();
  accountWindow.focus();
  await reloadAccountWindow(accountWindow);
  const state = await syncAccountWindow(account, accountWindow, true);
  return { ok: true, status: state.status, message: state.status_message, daily_coin: state.daily_coin };
});

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
  backendPort = port;
  return port;
}

function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1040,
    minHeight: 720,
    title: "RH Workflow Desk",
    icon: path.join(webRoot, "static", "assets", "rh-workflow-desk-icon.png"),
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
    if (process.platform === "darwin" && app.dock) {
      try { app.dock.setIcon(path.join(webRoot, "static", "assets", "rh-workflow-desk-icon.png")); } catch (_) {}
    }
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
