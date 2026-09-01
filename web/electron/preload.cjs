const { contextBridge, webUtils } = require("electron");

contextBridge.exposeInMainWorld("rhElectron", {
  isElectron: true,
  getPathForFile(file) {
    return webUtils.getPathForFile(file);
  }
});
