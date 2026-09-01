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
  }
});
