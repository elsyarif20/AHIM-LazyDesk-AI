const $ = (id) => document.getElementById(id);
const state = { mode: "text", lastResult: "", history: [], sourceExtension: "" };
let initialConfigChecked = false;

function toast(message) {
  const el = $("toast"); el.textContent = message; el.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove("show"), 2400);
}
function switchView(view) {
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active-view"));
  const target = $("view-" + view); if (target) target.classList.add("active-view");
  document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  const labels = {studio:"AI Studio",history:"Recent activity",library:"Document library",settings:"AI settings"};
  $("crumb").textContent = labels[view] || "AI Studio";
  if (view === "history") renderHistory();
  if (view === "settings") checkConfig();
  if (window.innerWidth < 760) $("sidebar").classList.remove("open");
}
document.querySelectorAll(".nav-item").forEach(btn => btn.addEventListener("click", () => switchView(btn.dataset.view)));
$("goStudio").addEventListener("click", () => switchView("studio"));
$("mobileMenu").addEventListener("click", () => $("sidebar").classList.toggle("open"));
$("themeInfo").addEventListener("click", () => toast("AHIM LazyDesk AI · Keep human judgment in the loop."));
function updateCharCount() {
  const length = $("sourceText").value.length;
  $("charCount").textContent = length.toLocaleString("id-ID") + " / 60.000";
  $("charCount").classList.toggle("limit-near", length > 54000);
}
$("sourceText").addEventListener("input", updateCharCount);
$("clearInputButton").addEventListener("click", () => {
  $("sourceText").value = "";
  updateCharCount();
  $("sourceText").focus();
  toast("Teks input dibersihkan.");
});

document.querySelectorAll(".mode-tab").forEach(btn => btn.addEventListener("click", () => {
  state.mode = btn.dataset.mode;
  document.querySelectorAll(".mode-tab").forEach(b => b.classList.toggle("selected", b === btn));
  $("textMode").classList.toggle("hidden", state.mode !== "text");
  $("fileMode").classList.toggle("hidden", state.mode !== "file");
}));
const allowedExtensions = new Set(["pdf", "docx", "xlsx", "xlsm", "csv", "txt", "md", "json", "log", "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"]);
function setSelectedFile(file) {
  if (!file) {
    $("fileName").textContent = "Belum ada file dipilih";
    return;
  }
  const extension = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
  if (!allowedExtensions.has(extension)) {
    $("fileInput").value = "";
    $("fileName").textContent = "Format file tidak didukung";
    toast("Format tidak didukung. Gunakan PDF, DOCX, XLSX, CSV, TXT, gambar, atau screenshot.");
    return;
  }
  if (file.size > 100 * 1024 * 1024) {
    $("fileInput").value = "";
    $("fileName").textContent = "File melebihi 100 MB";
    toast("Pilih file berukuran maksimal 100 MB.");
    return;
  }
  $("fileName").textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
}
$("fileInput").addEventListener("change", () => setSelectedFile($("fileInput").files[0]));
$("resetUploadButton").addEventListener("click", () => {
  $("fileInput").value = "";
  $("fileName").textContent = "Belum ada file dipilih";
  state.lastResult = "";
  state.sourceExtension = "";
  $("resultContent").innerHTML = "";
  $("resultState").classList.add("hidden");
  $("errorState").classList.add("hidden");
  $("emptyState").classList.remove("hidden");
  toast("File dan hasil pemrosesan dibersihkan.");
});

const dropZone = $("dropZone");
["dragenter", "dragover"].forEach(eventName => dropZone.addEventListener(eventName, event => {
  event.preventDefault(); dropZone.classList.add("drag-over");
}));
["dragleave", "drop"].forEach(eventName => dropZone.addEventListener(eventName, event => {
  event.preventDefault(); dropZone.classList.remove("drag-over");
}));
dropZone.addEventListener("drop", event => {
  const file = event.dataTransfer.files && event.dataTransfer.files[0];
  if (!file) return;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  $("fileInput").files = transfer.files;
  setSelectedFile(file);
  state.mode = "file";
  document.querySelectorAll(".mode-tab").forEach(b => b.classList.toggle("selected", b.dataset.mode === "file"));
  $("textMode").classList.add("hidden"); $("fileMode").classList.remove("hidden");
});

$("backend").addEventListener("change", () => {
  const val = $("backend").value;
  const labels = {ollama_cloud:'GEMMA <small>/ CLOUD</small>',openai:'OPENAI <small>/ VISION</small>',gemini:'GEMINI <small>/ VISION</small>',demo:'DEMO <small>/ PREVIEW</small>'};
  $("metricEngine").innerHTML = labels[val] || labels.demo;
  $("engineStatus").textContent = ({ollama_cloud:"Ollama Cloud selected",openai:"OpenAI selected",gemini:"Google Gemini selected",demo:"Preview mode"})[val];
});
function showPanel(panel) {
  ["emptyState","loadingState","resultState","errorState"].forEach(id => $(id).classList.toggle("hidden", id !== panel));
}
function taskPrompt() {
  const type = $("taskType").value, extra = $("customPrompt").value.trim();
  let task = "";
  if (type === "meeting") task = "Format the output as meeting minutes with agenda, decisions, action items, and unresolved issues.";
  if (type === "action") task = "Focus on actionable tasks only. Do not infer owners or deadlines.";
  if (type === "rewrite") task = "Clean up and structure the content while preserving the original meaning.";
  return `You are AHIM LazyDesk AI, a careful productivity assistant.\nProvide a concise summary, key decisions/dates, action items, and open questions. Never invent facts. Reply in the language of the source.\n${task}\n${extra ? "Additional user instruction: " + extra : ""}`;
}
async function runTask() {
  const text = $("sourceText").value.trim();
  const file = $("fileInput").files[0];
  if (state.mode === "text" && !text) { toast("Add some text first."); $("sourceText").focus(); return; }
  if (state.mode === "file" && !file) { toast("Choose a document first."); return; }
  const backend = $("backend").value;
  state.sourceExtension = file && file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
  const body = new FormData();
  body.append("backend", backend); body.append("prompt", taskPrompt());
  if (state.mode === "text") body.append("text", text); else body.append("file", file);
  showPanel("loadingState"); $("runButton").disabled = true; $("runButton").innerHTML = '<span>◌</span> Working on it…';
  try {
    const res = await fetch("/api/summarize", {method:"POST", body});
    const data = await res.json();
    if (!res.ok) {
      const err = new Error(data.error || "Permintaan gagal.");
      err.needsApiKey = Boolean(data.needsApiKey);
      throw err;
    }
    state.lastResult = data.result;
    $("resultContent").textContent = data.result;
    $("resultMeta").textContent = `${(data.characters || 0).toLocaleString()} characters processed · ${(data.model || backend).toUpperCase()} · ${new Date().toLocaleTimeString()}`;
    showPanel("resultState");
    state.history.unshift({title: (text || (file && file.name) || "Untitled draft").replace(/\s+/g," ").slice(0,72), result:data.result, time:new Date().toLocaleString()});
    state.history = state.history.slice(0,20);
    toast("Your first draft is ready.");
  } catch (err) {
    $("errorText").textContent = err.message || "Terjadi kesalahan yang tidak diketahui.";
    showPanel("errorState");
    if (err.needsApiKey) $("openSettingsFromError").classList.remove("hidden");
    else $("openSettingsFromError").classList.add("hidden");
  } finally {
    $("runButton").disabled = false; $("runButton").innerHTML = '<span>✦</span> Generate clarity <span class="button-arrow">↗</span>';
  }
}
$("runButton").addEventListener("click", runTask);
$("retryButton").addEventListener("click", runTask);
$("openSettingsFromError").addEventListener("click", () => switchView("settings"));
$("newButton").addEventListener("click", () => {
  state.lastResult = "";
  state.sourceExtension = "";
  $("sourceText").value = "";
  $("fileInput").value = "";
  $("fileName").textContent = "Belum ada file dipilih";
  $("customPrompt").value = "";
  updateCharCount();
  state.mode = "text";
  document.querySelectorAll(".mode-tab").forEach(b => b.classList.toggle("selected", b.dataset.mode === "text"));
  $("textMode").classList.remove("hidden"); $("fileMode").classList.add("hidden");
  showPanel("emptyState");
  $("sourceText").focus();
  toast("Siap untuk tugas baru.");
});
$("copyButton").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(state.lastResult); toast("Copied to clipboard."); }
  catch { toast("Clipboard access unavailable in this browser."); }
});
async function exportResult(format) {
  if (!state.lastResult) { toast("Belum ada hasil untuk diekspor."); return; }
  try {
    const res = await fetch("/api/export", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({content:state.lastResult, format})
    });
    if (!res.ok) {
      let err = {}; try { err = await res.json(); } catch {}
      throw new Error(err.error || "Ekspor gagal.");
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `ahim_lazydesk_${new Date().toISOString().slice(0,16).replace(/[-:T]/g,"")}.${format === "word" ? "docx" : format === "excel" ? "xlsx" : format}`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    toast("Ekspor " + format.toUpperCase() + " selesai.");
  } catch (err) { toast(err.message || "Ekspor gagal."); }
}
$("downloadButton").addEventListener("click", () => exportResult(["xlsx","xlsm","csv"].includes(state.sourceExtension) ? "xlsx" : "md"));
$("exportWordButton").addEventListener("click", () => exportResult("docx"));
$("exportPdfButton").addEventListener("click", () => exportResult("pdf"));
$("exportExcelButton").addEventListener("click", () => exportResult("xlsx"));

function renderHistory() {
  $("historyCount").textContent = `${state.history.length} item${state.history.length === 1 ? "" : "s"}`;
  const list = $("historyList");
  if (!state.history.length) { list.innerHTML = '<div class="empty-history">Your generated drafts will appear here during this browser session.</div>'; return; }
  list.innerHTML = "";
  state.history.forEach((item, idx) => {
    const row = document.createElement("div"); row.className = "history-item";
    const left = document.createElement("div"); const title = document.createElement("strong"); title.textContent = item.title || "Untitled";
    const sub = document.createElement("div"); sub.style.marginTop = "5px"; sub.textContent = "Click to reopen this draft";
    left.append(title,sub); const time = document.createElement("span"); time.textContent = item.time;
    row.append(left,time); row.addEventListener("click", () => { state.lastResult=item.result; $("resultContent").textContent=item.result; $("resultMeta").textContent="Restored from this browser session"; showPanel("resultState"); switchView("studio"); });
    list.appendChild(row);
  });
}
async function checkConfig() {
  try {
    const res = await fetch("/api/config"); const data = await res.json();
    const status = $("cloudStatus");
    if (status) status.textContent = data.cloudConfigured
      ? "● API KEY TERSIMPAN · SIAP DIGUNAKAN"
      : "○ API KEY BELUM ADA · TAMBAHKAN UNTUK MEMULAI";
    const model = $("cloudModel");
    if (model) model.textContent = data.model || "gemma4:31b";
    $("engineStatus").textContent = data.cloudConfigured ? "Ollama Cloud connected" : "API key required";
    $("metricEngine").textContent = data.cloudConfigured ? "GEMMA / CLOUD" : "SETUP / KEY";
    if (!initialConfigChecked) {
      initialConfigChecked = true;
      if (!data.cloudConfigured) switchView("settings");
    }
  } catch {
    const status = $("cloudStatus");
    if (status) status.textContent = "SERVER CONFIGURATION UNAVAILABLE";
  }
}
function selectedSettingsProvider() {
  return $("settingsProvider") ? $("settingsProvider").value : "ollama_cloud";
}
const providerInfo = {
  ollama_cloud: {title:"Gemma 4 31B · Ollama Cloud", link:"https://ollama.com/settings/keys", model:"gemma4:31b"},
  openai: {title:"OpenAI Vision", link:"https://platform.openai.com/api-keys", model:"gpt-4.1-mini"},
  gemini: {title:"Google Gemini Vision", link:"https://aistudio.google.com/apikey", model:"gemini-2.5-flash"}
};
async function refreshProviderSettings() {
  const p = selectedSettingsProvider(), info = providerInfo[p];
  $("providerHeading").textContent = info.title;
  $("providerKeyLink").href = info.link;
  $("apiKeyInput").placeholder = "Tempel API key " + (p === "ollama_cloud" ? "Ollama" : p === "openai" ? "OpenAI" : "Gemini") + " di sini";
  $("apiKeyInput").value = "";
  try {
    const res = await fetch("/api/config?provider=" + encodeURIComponent(p));
    const data = await res.json();
    $("cloudStatus").textContent = data.cloudConfigured ? "● API KEY TERSIMPAN · SIAP DIGUNAKAN" : "○ API KEY PROVIDER INI BELUM ADA";
    $("cloudModel").textContent = data.model || info.model;
  } catch {}
}
$("settingsProvider")?.addEventListener("change", refreshProviderSettings);

function setKeyFeedback(message, kind = "") {
  const el = $("keyFeedback");
  if (!el) return;
  el.textContent = message;
  el.className = "key-feedback" + (kind ? " " + kind : "");
}
async function saveAndConnectKey() {
  const apiKey = $("apiKeyInput").value.trim();
  if (!apiKey) {
    setKeyFeedback("Tempel API key terlebih dahulu. Jika belum punya, gunakan tautan resmi di bawah.", "error");
    $("apiKeyInput").focus(); return;
  }
  const button = $("saveKeyButton");
  button.disabled = true; button.textContent = "Menguji koneksi…";
  setKeyFeedback("Menguji koneksi ke " + selectedSettingsProvider() + ". Key tidak akan ditampilkan.");
  try {
    const res = await fetch("/api/settings/key", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({apiKey, provider:selectedSettingsProvider()})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Gagal menyimpan API key.");
    $("apiKeyInput").value = "";
    setKeyFeedback("Terhubung ke " + data.model + ". Balasan: " + data.reply, "success");
    toast(selectedSettingsProvider() + " terhubung.");
    await checkConfig();
  } catch (err) {
    setKeyFeedback(err.message || "Koneksi gagal.", "error");
  } finally {
    button.disabled = false; button.textContent = "Simpan & Hubungkan ↗";
  }
}
async function testSavedKey() {
  const button = $("testKeyButton");
  button.disabled = true; button.textContent = "Menguji…";
  setKeyFeedback("Mengirim pesan tes ke Ollama Cloud…");
  try {
    const res = await fetch("/api/settings/test", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({provider:selectedSettingsProvider()})});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Tes koneksi gagal.");
    setKeyFeedback("Tes berhasil · " + data.model + " membalas: " + data.reply, "success");
    await checkConfig();
  } catch (err) {
    setKeyFeedback(err.message || "Tes koneksi gagal.", "error");
  } finally {
    button.disabled = false; button.textContent = "Uji koneksi";
  }
}
async function removeSavedKey() {
  if (!window.confirm("Hapus API key " + selectedSettingsProvider() + " yang tersimpan di penyimpanan kredensial?")) return;
  const button = $("removeKeyButton"); button.disabled = true;
  try {
    const res = await fetch("/api/settings/key?provider=" + encodeURIComponent(selectedSettingsProvider()), {method:"DELETE"});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Tidak dapat menghapus key.");
    setKeyFeedback(data.message, "success");
    await checkConfig();
    toast("API key dihapus.");
  } catch (err) {
    setKeyFeedback(err.message || "Tidak dapat menghapus key.", "error");
  } finally { button.disabled = false; }
}
$("saveKeyButton").addEventListener("click", saveAndConnectKey);
$("testKeyButton").addEventListener("click", testSavedKey);
$("removeKeyButton").addEventListener("click", removeSavedKey);
checkConfig();
refreshProviderSettings();
