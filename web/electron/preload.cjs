const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("rhElectron", {
  isElectron: true,
  getPathForFile(file) {
    return webUtils.getPathForFile(file);
  },
  selectDirectory() {
    return ipcRenderer.invoke("select-directory");
  },
  openAccountLogin(account) {
    return ipcRenderer.invoke("account-login", account);
  },
  accountCheckin(account) {
    return ipcRenderer.invoke("account-checkin", account);
  },
  onGlobalPageNavigation(callback) {
    if (typeof callback !== "function") return () => {};
    const listener = (_event, direction) => callback(direction);
    ipcRenderer.on("rh-global-page-navigation", listener);
    return () => ipcRenderer.removeListener("rh-global-page-navigation", listener);
  }
});
