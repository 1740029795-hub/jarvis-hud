/**
 * app.js — 主逻辑：语音回路 + 电弧反应堆动画 + 状态面板
 */
(() => {
  "use strict";

  /* ---------------- 元素引用 ---------------- */
  const $ = (id) => document.getElementById(id);
  const clockEl = $("clock"), dateEl = $("date");
  const cpuEl = $("cpu"), memEl = $("mem"), netEl = $("net");
  const cpuValEl = $("cpu-val"), memValEl = $("mem-val"), netValEl = $("net-val");
  const connTypeEl = $("conn-type"), sttStatusEl = $("stt-status");
  const activityEl = $("activity"), logEl = $("log");
  const micBtn = $("mic"), hintEl = $("listen-hint");

  /* ---------------- 时钟 ---------------- */
  function tickClock() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    clockEl.textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    dateEl.textContent = `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())}`;
  }
  setInterval(tickClock, 1000);
  tickClock();

  /* ---------------- 模拟仪表 ---------------- */
  const conn = (navigator.connection || {}).effectiveType || "未知";
  connTypeEl.textContent = conn;
  let t0 = Date.now();
  function tickMeters() {
    const t = (Date.now() - t0) / 1000;
    const cpu = 30 + 22 * Math.sin(t * 0.7) + 8 * Math.sin(t * 2.3);
    const mem = 55 + 12 * Math.sin(t * 0.4 + 2) + 6 * Math.sin(t * 1.1);
    const net = 40 + 30 * Math.abs(Math.sin(t * 1.3));
    cpuEl.style.width = cpu.toFixed(0) + "%";
    memEl.style.width = mem.toFixed(0) + "%";
    netEl.style.width = net.toFixed(0) + "%";
    cpuValEl.textContent = cpu.toFixed(0) + "%";
    memValEl.textContent = mem.toFixed(0) + "%";
    netValEl.textContent = net.toFixed(0) + "%";
    requestAnimationFrame(tickMeters);
  }
  requestAnimationFrame(tickMeters);

  /* ---------------- 活动流 ---------------- */
  function logActivity(text, dim = false) {
    const li = document.createElement("li");
    if (dim) li.className = "dim";
    li.textContent = `[${nowTimeStr()}] ${text}`;
    activityEl.prepend(li);
    while (activityEl.children.length > 40) activityEl.removeChild(activityEl.lastChild);
  }
  function nowTimeStr() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  /* ---------------- 对话日志 ---------------- */
  function addMsg(who, text) {
    const div = document.createElement("div");
    div.className = "msg " + who;
    if (who === "ai") {
      const w = document.createElement("span");
      w.className = "who";
      w.textContent = "J.A.R.V.I.S.";
      div.appendChild(w);
    }
    div.appendChild(document.createTextNode(text));
    logEl.appendChild(div);
    logEl.scrollTop = logEl.scrollHeight;
  }

  /* ---------------- 语音合成（TTS：后端 edge-tts） ---------------- */
  let currentAudio = null;
  let currentAudioUrl = null;
  let audioUnlocked = false;

  // 在用户手势里播放一段静音，解锁浏览器 autoplay 限制（否则异步播放会被拒）
  function unlockAudio() {
    if (audioUnlocked) return;
    audioUnlocked = true;
    try {
      const a = new Audio("data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA");
      const p = a.play();
      if (p) p.then(() => a.pause()).catch(() => {});
    } catch (e) { /* 忽略 */ }
  }

  function stopSpeaking() {
    if (currentAudio) { currentAudio.pause(); currentAudio = null; }
    if (currentAudioUrl) { URL.revokeObjectURL(currentAudioUrl); currentAudioUrl = null; }
  }

  async function speak(text) {
    stopSpeaking();  // 停掉并释放上一次的播报
    try {
      const res = await fetch("http://localhost:8001/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error("TTS 后端返回 " + res.status);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      currentAudioUrl = url;
      const audio = new Audio(url);
      currentAudio = audio;
      const done = () => {
        jarvisSpeaking = false;
        stopBargeMonitor();
        URL.revokeObjectURL(url);
        if (currentAudioUrl === url) currentAudioUrl = null;
        if (currentAudio === audio) currentAudio = null;
      };
      audio.onended = done;
      audio.onerror = done;
      await audio.play();
      jarvisSpeaking = true;
      if (bargeOn()) ensureVad().then(() => startBargeMonitor());
      logActivity("🔊 语音播报中", true);
    } catch (err) {
      jarvisSpeaking = false;
      if (err.name === "NotAllowedError") {
        // 浏览器 autoplay 策略：页面无用户手势时自动播报被拦，属正常现象
        logActivity("🔇 浏览器拦截了自动播放，点击一次页面或按 TALK 即可解锁声音", true);
      } else {
        logActivity("TTS 失败[" + (err.name || "Error") + "]：" + err.message, true);
      }
    }
  }

  /* ---------------- 语音识别（STT：MediaRecorder + 后端 whisper） ---------------- */
  let micStream = null;        // 常驻麦克风流：录音和打断检测共用
  let mediaRecorder = null;
  let audioChunks = [];
  let recording = false;
  let autoStop = false;        // true=免提模式，靠静音自动结束录音

  // 打断（barge-in）相关
  let vadCtx = null;
  let vadAnalyser = null;
  let jarvisSpeaking = false;
  let bargeTimer = null;
  let silenceTimer = null;
  let loudFrames = 0;
  let silenceFrames = 0;

  const VAD_THRESHOLD = 0.14;      // 开口判定音量阈值（需实机微调）
  const VAD_FRAMES = 6;            // 连续超阈值帧数，防误触发
  const SILENCE_THRESHOLD = 0.06;  // 静音判定阈值
  const SILENCE_FRAMES = 15;       // 连续静音帧数（约 1.2s）→ 自动结束录音

  async function ensureMic() {
    if (micStream) return micStream;
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });
    return micStream;
  }

  async function ensureVad() {
    if (vadAnalyser) return;
    await ensureMic();
    vadCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (vadCtx.state === "suspended") { try { await vadCtx.resume(); } catch (e) { /* 忽略 */ } }
    const src = vadCtx.createMediaStreamSource(micStream);
    vadAnalyser = vadCtx.createAnalyser();
    vadAnalyser.fftSize = 512;
    src.connect(vadAnalyser);
  }

  function micLevel() {
    if (!vadAnalyser) return 0;
    const buf = new Uint8Array(vadAnalyser.fftSize);
    vadAnalyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = (buf[i] - 128) / 128;
      sum += v * v;
    }
    return Math.sqrt(sum / buf.length);
  }

  function bargeOn() {
    const el = $("barge");
    return !!(el && el.checked);
  }

  function stopBargeMonitor() {
    if (bargeTimer) { clearInterval(bargeTimer); bargeTimer = null; }
  }

  function stopSilenceMonitor() {
    if (silenceTimer) { clearInterval(silenceTimer); silenceTimer = null; }
  }

  // 贾维斯说话时监听：用户一开口就打断并转去听
  function startBargeMonitor() {
    stopBargeMonitor();
    loudFrames = 0;
    const startedAt = Date.now();
    bargeTimer = setInterval(() => {
      if (!jarvisSpeaking) return;
      if (Date.now() - startedAt < 400) return;  // 宽限期，让回声消除收敛
      if (micLevel() > VAD_THRESHOLD) {
        loudFrames++;
        if (loudFrames >= VAD_FRAMES) {
          logActivity("✋ 检测到打断", true);
          jarvisSpeaking = false;
          stopBargeMonitor();
          stopSpeaking();
          startRecording({ auto: true });
        }
      } else {
        loudFrames = 0;
      }
    }, 50);
  }

  // 免提录音时监听静音：安静一段就自动结束
  function startSilenceMonitor() {
    stopSilenceMonitor();
    silenceFrames = 0;
    silenceTimer = setInterval(() => {
      if (!recording) return;
      if (micLevel() < SILENCE_THRESHOLD) {
        silenceFrames++;
        if (silenceFrames >= SILENCE_FRAMES) stopRecording();
      } else {
        silenceFrames = 0;
      }
    }, 80);
  }

  function startRecording(opts) {
    const opts0 = opts || {};
    stopSpeaking();  // 一开始就打断当前播报
    if (recording) return;
    ensureMic().then(() => {
      if (recording) return;
      audioChunks = [];
      mediaRecorder = new MediaRecorder(micStream);
      mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
      mediaRecorder.onstop = onRecordingStop;
      mediaRecorder.start();
      recording = true;
      autoStop = !!opts0.auto;
      micBtn.classList.add("listening");
      sttStatusEl.textContent = "聆听中";
      if (autoStop) ensureVad().then(() => startSilenceMonitor());
    }).catch((err) => {
      sttStatusEl.textContent = "错误";
      logActivity("麦克风错误：" + err.message, true);
      hintEl.textContent = "麦克风不可用，请检查权限";
    });
  }

  function stopRecording() {
    if (!mediaRecorder || !recording) return;
    recording = false;
    stopSilenceMonitor();
    mediaRecorder.stop();
    micBtn.classList.remove("listening");
    sttStatusEl.textContent = "识别中…";
  }

  async function onRecordingStop() {
    // 常驻麦克风流：不 stop tracks，留给下一次录音 / 打断检测复用
    const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || "audio/webm" });
    if (blob.size < 1000) { sttStatusEl.textContent = "就绪"; return; }  // 太短，忽略
    try {
      const form = new FormData();
      form.append("file", blob, "audio.webm");
      const res = await fetch("http://localhost:8001/stt", { method: "POST", body: form });
      const data = await res.json();
      const text = (data.text || "").trim();
      sttStatusEl.textContent = "就绪";
      if (!text) { logActivity("未识别到内容", true); return; }
      logActivity("🎤 识别：" + text);
      handleCommand(text);
    } catch (err) {
      sttStatusEl.textContent = "就绪";
      logActivity("识别失败：" + err.message, true);
    }
  }

  let isProcessing = false;

  async function handleCommand(text) {
    if (isProcessing) {
      logActivity("上一句还在处理，请稍等", true);
      return;
    }
    isProcessing = true;
    try {
      addMsg("user", text);
      hintEl.textContent = "正在思考…";
      logActivity("🤖 正在生成回复");
      let reply;
      try {
        reply = await JarvisBackend.ask(text);
      } catch (err) {
        reply = "抱歉，先生，后端出现异常：" + err.message;
      }
      addMsg("ai", reply);
      hintEl.textContent = "按住 TALK 说话";
      logActivity("✅ 已回复");
      speak(reply);
    } finally {
      isProcessing = false;
    }
  }

  /* ---------------- 语音控制交互（按住说话） ---------------- */
  micBtn.addEventListener("mousedown", (e) => { e.preventDefault(); unlockAudio(); startRecording(); });
  micBtn.addEventListener("mouseup", () => stopRecording());
  micBtn.addEventListener("mouseleave", () => { if (recording) stopRecording(); });
  micBtn.addEventListener("touchstart", (e) => { e.preventDefault(); unlockAudio(); startRecording(); });
  micBtn.addEventListener("touchend", () => stopRecording());

  /* ---------------- 电弧反应堆动画 ---------------- */
  const rcanvas = $("reactor");
  const rctx = rcanvas.getContext("2d");
  const W = rcanvas.width, H = rcanvas.height, cx = W / 2, cy = H / 2;

  function drawReactor(now) {
    const t = now / 1000;
    rctx.clearRect(0, 0, W, H);

    // 外圈刻度环
    rctx.save();
    rctx.translate(cx, cy);
    rctx.rotate(t * 0.5);
    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * Math.PI * 2;
      const major = i % 6 === 0;
      const r1 = 156, r2 = major ? 138 : 146;
      rctx.beginPath();
      rctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
      rctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
      rctx.strokeStyle = "rgba(0,229,255," + (major ? 0.9 : 0.5) + ")";
      rctx.lineWidth = major ? 2 : 1;
      rctx.stroke();
    }
    rctx.restore();

    // 外圈光环
    rctx.save();
    rctx.translate(cx, cy);
    rctx.strokeStyle = "rgba(0,229,255,0.55)";
    rctx.lineWidth = 2;
    rctx.beginPath(); rctx.arc(0, 0, 160, 0, Math.PI * 2); rctx.stroke();
    rctx.restore();

    // 中层反向旋转弧
    rctx.save();
    rctx.translate(cx, cy);
    rctx.rotate(-t * 0.9);
    for (let i = 0; i < 3; i++) {
      const start = i * (Math.PI * 2 / 3) + Math.PI / 6;
      rctx.beginPath();
      rctx.arc(0, 0, 118, start, start + Math.PI * 0.45);
      rctx.strokeStyle = "rgba(0,229,255,0.8)";
      rctx.lineWidth = 3;
      rctx.stroke();
    }
    rctx.restore();

    // 内层分段环
    rctx.save();
    rctx.translate(cx, cy);
    rctx.rotate(t * 0.3);
    for (let i = 0; i < 12; i++) {
      const a = (i / 12) * Math.PI * 2;
      rctx.beginPath();
      rctx.arc(0, 0, 86, a, a + Math.PI / 12);
      rctx.strokeStyle = "rgba(0,229,255,0.4)";
      rctx.lineWidth = 6;
      rctx.stroke();
    }
    rctx.restore();

    // 三角内核
    rctx.save();
    rctx.translate(cx, cy);
    rctx.rotate(t * 0.7);
    rctx.strokeStyle = "rgba(0,229,255,0.85)";
    rctx.lineWidth = 2;
    rctx.beginPath();
    for (let i = 0; i < 3; i++) {
      const a = (i / 3) * Math.PI * 2 - Math.PI / 2;
      const px = Math.cos(a) * 58, py = Math.sin(a) * 58;
      i === 0 ? rctx.moveTo(px, py) : rctx.lineTo(px, py);
    }
    rctx.closePath();
    rctx.stroke();
    rctx.restore();

    // 发光核心
    const pulse = 0.7 + 0.3 * Math.sin(t * 2.4);
    const g = rctx.createRadialGradient(cx, cy, 2, cx, cy, 34);
    g.addColorStop(0, `rgba(255,255,255,${pulse})`);
    g.addColorStop(0.35, `rgba(0,229,255,${0.75 * pulse})`);
    g.addColorStop(1, "rgba(0,229,255,0)");
    rctx.fillStyle = g;
    rctx.beginPath(); rctx.arc(cx, cy, 34, 0, Math.PI * 2); rctx.fill();

    requestAnimationFrame(drawReactor);
  }
  requestAnimationFrame(drawReactor);

  /* ---------------- 背景粒子 ---------------- */
  const bcanvas = $("bg");
  const bctx = bcanvas.getContext("2d");
  let particles = [];
  function resizeBg() {
    bcanvas.width = innerWidth;
    bcanvas.height = innerHeight;
    particles = Array.from({ length: 60 }, () => ({
      x: Math.random() * bcanvas.width,
      y: Math.random() * bcanvas.height,
      r: Math.random() * 1.6 + 0.4,
      s: Math.random() * 0.4 + 0.1,
      o: Math.random() * 0.5 + 0.15,
    }));
  }
  resizeBg();
  addEventListener("resize", resizeBg);
  function drawBg() {
    bctx.clearRect(0, 0, bcanvas.width, bcanvas.height);
    for (const p of particles) {
      p.y -= p.s;
      if (p.y < -5) { p.y = bcanvas.height + 5; p.x = Math.random() * bcanvas.width; }
      bctx.beginPath();
      bctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      bctx.fillStyle = `rgba(0,229,255,${p.o})`;
      bctx.fill();
    }
    requestAnimationFrame(drawBg);
  }
  requestAnimationFrame(drawBg);

  /* ---------------- 启动 ---------------- */
  sttStatusEl.textContent = "就绪";
  logActivity("系统启动完成", true);
  addMsg("ai", "系统在线。我是 J.A.R.V.I.S.，随时为您服务。");
  speak("系统在线，随时为您服务。");
  hintEl.textContent = "按住 TALK 说话";
})();
