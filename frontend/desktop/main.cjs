const { app, BrowserWindow, dialog, session } = require('electron');

// This shell never starts trading engines or exposes Node APIs to the renderer.
const terminalURL = new URL(process.env.AMAT_TERMINAL_URL || 'http://127.0.0.1:5173');
if (terminalURL.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(terminalURL.hostname)
    || terminalURL.username || terminalURL.password) {
  throw new Error('AMAT_TERMINAL_URL must be a local HTTP address without credentials');
}

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  const window = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 1000,
    minHeight: 700,
    backgroundColor: '#0b0e13',
    title: 'AMAT Terminal',
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, destination) => {
    if (new URL(destination).origin !== terminalURL.origin) event.preventDefault();
  });
  window.webContents.on('will-attach-webview', (event) => event.preventDefault());
  window.loadURL(terminalURL.href).catch(() => {
    dialog.showErrorBox('Terminal unavailable', `Start the local terminal service first, then reopen this window.\n${terminalURL.origin}`);
  });
});

app.on('window-all-closed', () => app.quit());
