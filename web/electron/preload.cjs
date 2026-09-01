const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("rhElectron", {
  isElectron: true,
  getPathForFile(file) {
    return webUtils.getPathForFile(file);
  },
  selectDirectory() {
    return ipcRenderer.invoke("select-directory");
  }
});
