const form = document.querySelector('#decodeForm');
const imageInput = document.querySelector('#imageInput');
const dropZone = document.querySelector('#dropZone');
const selectedFile = document.querySelector('#selectedFile');
const selectedName = document.querySelector('#selectedName');
const selectedPath = document.querySelector('#selectedPath');
const inputPath = document.querySelector('#inputPath');
const outputPath = document.querySelector('#outputPath');
const password = document.querySelector('#password');
const decodeButton = document.querySelector('#decodeButton');
const statusChip = document.querySelector('#statusChip');
const resultState = document.querySelector('#resultState');
const resultDetail = document.querySelector('#resultDetail');
const resultData = document.querySelector('#resultData');
const previewStage = document.querySelector('#previewStage');
const previewImage = document.querySelector('#previewImage');
const previewVideo = document.querySelector('#previewVideo');
const previewType = document.querySelector('#previewType');
const resultRadar = document.querySelector('.radar');
const resultInput = document.querySelector('#resultInput');
const resultOutput = document.querySelector('#resultOutput');
const resultSize = document.querySelector('#resultSize');
const resultElapsed = document.querySelector('#resultElapsed');
const serviceStatus = document.querySelector('#serviceStatus');
const toast = document.querySelector('#toast');
const nativePickButton = document.querySelector('#nativePickButton');

let selectedUpload = null;
let toastTimer = null;
let hasNativePicker = false;

function showToast(message, isError = false) {
  toast.textContent = message;
  toast.classList.toggle('error', isError);
  toast.classList.add('visible');
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove('visible'), 3600);
}

function setStatus(kind, label) {
  statusChip.className = `status-chip ${kind}`;
  statusChip.innerHTML = `<span></span>${label}`;
  document.body.classList.toggle('is-working', kind === 'working');
  document.body.classList.toggle('is-success', kind === 'success');
  document.body.classList.toggle('is-error', kind === 'error');
}

function setSelected(name, pathLabel) {
  selectedName.textContent = name;
  selectedPath.textContent = pathLabel;
  selectedFile.hidden = false;
}

function clearSelection() {
  selectedUpload = null;
  imageInput.value = '';
  inputPath.value = '';
  selectedFile.hidden = true;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function resetPreview() {
  previewStage.hidden = true;
  previewImage.hidden = true;
  previewVideo.hidden = true;
  previewImage.removeAttribute('src');
  previewVideo.pause();
  previewVideo.removeAttribute('src');
  previewVideo.load();
  resultRadar.hidden = false;
}

function setResultCopy(state, detail) {
  resultState.hidden = false;
  resultDetail.hidden = false;
  resultState.textContent = state;
  resultDetail.textContent = detail;
}

function hideResultCopy() {
  resultState.hidden = true;
  resultDetail.hidden = true;
  resultRadar.hidden = true;
}

function setReadyState() {
  setStatus('idle', 'READY');
  setResultCopy('准备好接收一张鸭子图', '解码完成后，结果会出现在右侧。');
  resultData.hidden = true;
  resetPreview();
}

function showPreview(payload) {
  resetPreview();
  if (!payload.preview_url || !payload.media_kind) {
    hideResultCopy();
    return;
  }

  previewType.textContent = payload.media_kind === 'video' ? 'VIDEO / PLAYABLE' : 'IMAGE / VIEWABLE';
  if (payload.media_kind === 'video') {
    previewVideo.src = payload.preview_url;
    previewVideo.hidden = false;
  } else {
    previewImage.src = payload.preview_url;
    previewImage.hidden = false;
  }
  previewStage.hidden = false;
  hideResultCopy();
}

imageInput.addEventListener('change', () => {
  const file = imageInput.files?.[0];
  if (!file) return;
  selectedUpload = file;
  inputPath.value = '';
  setSelected(file.name, `${formatBytes(file.size)} · 浏览器上传`);
});

dropZone.addEventListener('click', (event) => {
  if (event.target.closest('button')) return;
  imageInput.click();
});

dropZone.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    imageInput.click();
  }
});

for (const eventName of ['dragenter', 'dragover']) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.add('dragging');
  });
}

for (const eventName of ['dragleave', 'drop']) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove('dragging');
  });
}

dropZone.addEventListener('drop', (event) => {
  const file = event.dataTransfer.files?.[0];
  if (!file) return;
  selectedUpload = file;
  imageInput.files = event.dataTransfer.files;
  inputPath.value = '';
  setSelected(file.name, `${formatBytes(file.size)} · 拖入上传`);
});

document.querySelector('#clearFileButton').addEventListener('click', clearSelection);

nativePickButton.addEventListener('click', async () => {
  if (!hasNativePicker) {
    imageInput.click();
    return;
  }
  const button = nativePickButton;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '正在打开本地选择器…';
  try {
    const response = await fetch('/api/pick-image', { method: 'POST' });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || '选择图片失败');
    selectedUpload = null;
    imageInput.value = '';
    inputPath.value = payload.path;
    setSelected(payload.name, payload.path);
    showToast('已获取本地图片路径');
  } catch (error) {
    if (error.name !== 'AbortError') showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
});

inputPath.addEventListener('input', () => {
  if (!inputPath.value.trim()) return;
  selectedUpload = null;
  imageInput.value = '';
  setSelected(inputPath.value.split('/').pop() || '本地图片', inputPath.value);
});

document.querySelector('#revealButton').addEventListener('click', (event) => {
  const isPassword = password.type === 'password';
  password.type = isPassword ? 'text' : 'password';
  event.currentTarget.textContent = isPassword ? '隐藏' : '显示';
});

document.querySelector('#copyButton').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(resultOutput.textContent);
    showToast('输出路径已复制');
  } catch {
    showToast('复制失败，请手动选择路径', true);
  }
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!selectedUpload && !inputPath.value.trim()) {
    showToast('请先选择或上传一张鸭子图', true);
    return;
  }
  if (!outputPath.value.trim()) {
    showToast('请先设置导出路径', true);
    outputPath.focus();
    return;
  }

  const body = new FormData();
  body.append('output_path', outputPath.value.trim());
  body.append('password', password.value);
  if (selectedUpload) body.append('image', selectedUpload, selectedUpload.name);
  else body.append('input_path', inputPath.value.trim());

  decodeButton.disabled = true;
  decodeButton.querySelector('.button-label').textContent = '正在解码…';
  setStatus('working', 'WORKING');
  setResultCopy('正在读取隐藏内容', '本地解码器正在工作，图片较大时可能需要一些时间。');
  resultData.hidden = true;
  resetPreview();

  try {
    const response = await fetch('/api/decode', { method: 'POST', body });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || '解码失败');
    setStatus('success', 'DONE');
    resultInput.textContent = payload.input;
    resultOutput.textContent = payload.output_path;
    resultSize.textContent = formatBytes(payload.bytes);
    resultElapsed.textContent = `${payload.elapsed_seconds} s`;
    resultData.hidden = false;
    showPreview(payload);
    showToast('解码完成');
  } catch (error) {
    setStatus('error', 'ERROR');
    setResultCopy('这次解码没有完成', error.message || '请检查图片、密码和导出路径。');
    showToast(error.message || '解码失败', true);
  } finally {
    decodeButton.disabled = false;
    decodeButton.querySelector('.button-label').textContent = '开始解码';
  }
});

async function loadStatus() {
  try {
    const response = await fetch('/api/status');
    const payload = await response.json();
    hasNativePicker = Boolean(payload.native_picker);
    if (!hasNativePicker) nativePickButton.textContent = '使用浏览器选择文件 ↗';
    if (!payload.decoder_ready) {
      serviceStatus.textContent = `${payload.platform} 解码器不可用`;
      document.querySelector('.service-line').classList.add('error');
      return;
    }
    serviceStatus.textContent = `${payload.backend_label} 已就绪 · 数据不离开本机`;
    if (!outputPath.value) outputPath.value = payload.default_export_path;
  } catch {
    serviceStatus.textContent = '本地服务连接异常';
    document.querySelector('.service-line').classList.add('error');
  }
}

setReadyState();
loadStatus();
