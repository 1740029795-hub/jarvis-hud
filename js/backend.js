/**
 * backend.js — AI 后端接口（Phase 1：接 FastAPI 后端）
 *
 * 后端地址 http://localhost:8001（见 server.py）
 */
const JarvisBackend = {
  async ask(text) {
    const res = await fetch("http://localhost:8001/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text })
    });
    if (!res.ok) throw new Error("后端返回 " + res.status);
    const data = await res.json();
    return data.reply;
  }
};
