/*!
 * SMM Support Widget — embeddable AI chat
 * Usage:  <script src="https://YOUR-HOST/widget.js" async></script>
 * No dependencies. Vanilla JS. Works on any website.
 */
(function () {
  "use strict";
  if (window.__SMMChatLoaded) return;
  window.__SMMChatLoaded = true;

  // ---- locate this script + derive API base ---------------------------------
  var thisScript = document.currentScript || (function () {
    var s = document.getElementsByTagName("script");
    for (var i = s.length - 1; i >= 0; i--) if (/widget\.js/.test(s[i].src || "")) return s[i];
    return s[s.length - 1];
  })();
  var API_BASE = (thisScript && thisScript.getAttribute("data-api")) ||
    (thisScript && thisScript.src ? thisScript.src.replace(/\/widget\.js.*$/, "") : "");

  // ---- config (filled from /api/chatbot/init) -------------------------------
  var CFG = {
    bot_name: "Assistant",
    panel_name: "",
    panel_domain: "",
    brand_color: "#6c5ce7",
    greeting: "Hi! 👋 How can I help you today?",
    greeting_interval_hours: 6,
    suggestions: [],
    currency: "USD",
    ai_enabled: false
  };

  // ---- persisted state ------------------------------------------------------
  var STORAGE_KEY = "smmchat_v1";
  var state = { open: false, sending: false, messages: [], greetedAt: 0, sessionId: null, suggestionsShown: true };

  function loadState() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        var s = JSON.parse(raw);
        state.messages = Array.isArray(s.messages) ? s.messages : [];
        state.greetedAt = s.greetedAt || 0;
        state.sessionId = s.sessionId || null;
      }
    } catch (e) {}
    if (!state.sessionId) state.sessionId = "s_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
  function saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        messages: state.messages.slice(-40),
        greetedAt: state.greetedAt,
        sessionId: state.sessionId
      }));
    } catch (e) {}
  }

  // ---- i18n (UI labels only; the AI itself mirrors the user's language) -----
  var I18N = {
    en: { placeholder: "Type your message…", online: "Online", newChat: "New chat", send: "Send", error: "Something went wrong. Please try again." },
    bn: { placeholder: "আপনার মেসেজ লিখুন…", online: "অনলাইন", newChat: "নতুন চ্যাট", send: "পাঠান", error: "কিছু একটা সমস্যা হয়েছে। আবার চেষ্টা করুন।" },
    hi: { placeholder: "अपना संदेश लिखें…", online: "ऑनलाइन", newChat: "नई चैट", send: "भेजें", error: "कुछ गड़बड़ हो गई। फिर से प्रयास करें।" },
    ar: { placeholder: "اكتب رسالتك…", online: "متصل", newChat: "محادثة جديدة", send: "إرسال", error: "حدث خطأ ما. حاول مرة أخرى." },
    es: { placeholder: "Escribe tu mensaje…", online: "En línea", newChat: "Nuevo chat", send: "Enviar", error: "Algo salió mal. Inténtalo de nuevo." },
    pt: { placeholder: "Digite sua mensagem…", online: "Online", newChat: "Nova conversa", send: "Enviar", error: "Algo deu errado. Tente novamente." },
    id: { placeholder: "Ketik pesan Anda…", online: "Online", newChat: "Obrolan baru", send: "Kirim", error: "Terjadi kesalahan. Coba lagi." },
    ru: { placeholder: "Введите сообщение…", online: "В сети", newChat: "Новый чат", send: "Отправить", error: "Что-то пошло не так. Попробуйте снова." }
  };
  var LANG = (navigator.language || "en").slice(0, 2).toLowerCase();
  var T = I18N[LANG] || I18N.en;

  // ---- tiny helpers ---------------------------------------------------------
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  function escapeHtml(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }

  // markdown-ish → safe HTML (bold, links, bullets, headings, line breaks)
  function formatMessage(text) {
    var lines = String(text || "").split(/\r?\n/);
    var out = [], listBuf = [];
    function flushList() {
      if (listBuf.length) { out.push("<ul>" + listBuf.join("") + "</ul>"); listBuf = []; }
    }
    function inline(s) {
      s = escapeHtml(s);
      s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
      s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
      s = s.replace(/\b(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
      s = s.replace(/_([^_]+)_/g, "<em>$1</em>");
      return s;
    }
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      var trimmed = ln.trim();
      if (/^#{1,4}\s+/.test(trimmed)) { flushList(); out.push('<div class="smmc-h">' + inline(trimmed.replace(/^#{1,4}\s+/, "")) + "</div>"); }
      else if (/^[-•*]\s+/.test(trimmed)) { listBuf.push("<li>" + inline(trimmed.replace(/^[-•*]\s+/, "")) + "</li>"); }
      else if (trimmed === "") { flushList(); out.push('<div class="smmc-sp"></div>'); }
      else { flushList(); out.push("<div>" + inline(trimmed) + "</div>"); }
    }
    flushList();
    return out.join("");
  }

  // ---- styles ---------------------------------------------------------------
  function injectStyles() {
    if (document.getElementById("smmc-styles")) return;
    var c = CFG.brand_color || "#6c5ce7";
    var css = `
:root{--smmc:${c};}
#smmc-root,#smmc-root *{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,"Noto Sans Bengali",sans-serif;}
#smmc-btn{position:fixed;right:20px;bottom:20px;width:60px;height:60px;border-radius:50%;background:var(--smmc);color:#fff;border:none;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.22);display:flex;align-items:center;justify-content:center;z-index:2147483000;transition:transform .18s ease, box-shadow .18s ease;}
#smmc-btn:hover{transform:scale(1.06);box-shadow:0 10px 30px rgba(0,0,0,.28);}
#smmc-btn svg{width:28px;height:28px;transition:opacity .15s,transform .2s;}
#smmc-btn .smmc-ic-close{display:none;}
#smmc-root.open #smmc-btn .smmc-ic-open{display:none;}
#smmc-root.open #smmc-btn .smmc-ic-close{display:block;}
#smmc-bubble{position:fixed;right:88px;bottom:32px;max-width:240px;background:#fff;color:#1a1a1a;padding:12px 14px;border-radius:14px 14px 2px 14px;box-shadow:0 8px 30px rgba(0,0,0,.16);font-size:14px;line-height:1.45;z-index:2147482999;cursor:pointer;animation:smmc-pop .3s ease;}
#smmc-bubble .smmc-x{position:absolute;top:-8px;right:-8px;width:20px;height:20px;border-radius:50%;background:#e0e0e0;color:#555;border:none;font-size:12px;line-height:20px;cursor:pointer;}
@keyframes smmc-pop{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
#smmc-win{position:fixed;right:20px;bottom:92px;width:384px;height:min(620px,calc(100vh - 120px));background:#fff;border-radius:18px;box-shadow:0 16px 48px rgba(0,0,0,.28);display:flex;flex-direction:column;overflow:hidden;z-index:2147483000;opacity:0;transform:translateY(16px) scale(.98);pointer-events:none;transition:opacity .2s ease, transform .2s ease;}
#smmc-root.open #smmc-win{opacity:1;transform:none;pointer-events:auto;}
.smmc-head{background:var(--smmc);color:#fff;padding:14px 16px;display:flex;align-items:center;gap:12px;flex-shrink:0;}
.smmc-av{width:40px;height:40px;border-radius:50%;background:rgba(255,255,255,.22);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:17px;flex-shrink:0;}
.smmc-hmeta{flex:1;min-width:0;}
.smmc-hname{font-weight:700;font-size:15px;line-height:1.2;}
.smmc-hstat{font-size:12px;opacity:.9;display:flex;align-items:center;gap:5px;margin-top:2px;}
.smmc-dot{width:7px;height:7px;border-radius:50%;background:#31d158;display:inline-block;box-shadow:0 0 0 0 rgba(49,209,88,.6);animation:smmc-pulse 2s infinite;}
@keyframes smmc-pulse{0%{box-shadow:0 0 0 0 rgba(49,209,88,.5)}70%{box-shadow:0 0 0 6px rgba(49,209,88,0)}100%{box-shadow:0 0 0 0 rgba(49,209,88,0)}}
.smmc-hbtn{background:rgba(255,255,255,.15);border:none;color:#fff;width:32px;height:32px;border-radius:8px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .15s;}
.smmc-hbtn:hover{background:rgba(255,255,255,.28);}
.smmc-hbtn svg{width:18px;height:18px;}
.smmc-body{flex:1;overflow-y:auto;padding:16px;background:#f7f8fa;display:flex;flex-direction:column;gap:10px;}
.smmc-body::-webkit-scrollbar{width:6px;}.smmc-body::-webkit-scrollbar-thumb{background:#ccd;border-radius:3px;}
.smmc-row{display:flex;gap:8px;align-items:flex-end;max-width:100%;}
.smmc-row.user{flex-direction:row-reverse;}
.smmc-msg{padding:10px 13px;border-radius:16px;font-size:14.5px;line-height:1.5;max-width:78%;word-wrap:break-word;overflow-wrap:break-word;animation:smmc-in .22s ease;}
@keyframes smmc-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.smmc-msg.bot{background:#fff;color:#1c1c1e;border:1px solid #eceef1;border-bottom-left-radius:5px;box-shadow:0 1px 2px rgba(0,0,0,.04);}
.smmc-msg.user{background:var(--smmc);color:#fff;border-bottom-right-radius:5px;}
.smmc-msg .smmc-h{font-weight:700;margin:2px 0;}
.smmc-msg ul{margin:6px 0;padding-left:18px;}.smmc-msg li{margin:3px 0;}
.smmc-msg .smmc-sp{height:7px;}
.smmc-msg a{color:var(--smmc);text-decoration:underline;}.smmc-msg.user a{color:#fff;}
.smmc-msg code{background:rgba(0,0,0,.06);padding:1px 5px;border-radius:5px;font-size:13px;}
.smmc-sav{width:30px;height:30px;border-radius:50%;background:var(--smmc);color:#fff;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0;margin-bottom:2px;}
.smmc-typing{display:flex;gap:4px;padding:12px 14px;background:#fff;border:1px solid #eceef1;border-radius:16px;border-bottom-left-radius:5px;width:fit-content;}
.smmc-typing span{width:8px;height:8px;border-radius:50%;background:#b8bdc7;animation:smmc-bounce 1.3s infinite;}
.smmc-typing span:nth-child(2){animation-delay:.18s;}.smmc-typing span:nth-child(3){animation-delay:.36s;}
@keyframes smmc-bounce{0%,60%,100%{transform:translateY(0);opacity:.6}30%{transform:translateY(-5px);opacity:1}}
.smmc-sugg{display:flex;flex-wrap:wrap;gap:8px;padding:0 16px 10px;background:#f7f8fa;}
.smmc-chip{background:#fff;border:1.5px solid var(--smmc);color:var(--smmc);border-radius:16px;padding:7px 12px;font-size:13px;cursor:pointer;transition:all .15s;line-height:1.2;}
.smmc-chip:hover{background:var(--smmc);color:#fff;}
.smmc-foot{padding:10px 12px;border-top:1px solid #eceef1;background:#fff;display:flex;gap:8px;align-items:flex-end;flex-shrink:0;}
.smmc-input{flex:1;border:1.5px solid #e3e6ea;border-radius:22px;padding:10px 14px;font-size:14.5px;resize:none;outline:none;max-height:110px;line-height:1.4;transition:border-color .15s;font-family:inherit;overflow-y:hidden;box-sizing:border-box;}
.smmc-input:focus{border-color:var(--smmc);}
.smmc-send{width:42px;height:42px;border-radius:50%;background:var(--smmc);color:#fff;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:opacity .15s, transform .1s;}
.smmc-send:hover{transform:scale(1.05);}.smmc-send:disabled{opacity:.45;cursor:default;transform:none;}
.smmc-send svg{width:20px;height:20px;}
.smmc-brand{text-align:center;font-size:11px;color:#9aa1ab;padding:6px 0 8px;background:#fff;}
@media (max-width:480px){
  #smmc-win{right:0;bottom:0;width:100vw;height:100vh;height:100dvh;border-radius:0;}
  #smmc-btn{right:16px;bottom:16px;}
  #smmc-bubble{right:16px;bottom:86px;max-width:calc(100vw - 90px);}
}`;
    var st = el("style"); st.id = "smmc-styles"; st.textContent = css; document.head.appendChild(st);
  }

  // ---- SVG icons ------------------------------------------------------------
  var IC_OPEN = '<svg class="smmc-ic-open" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>';
  var IC_CLOSE = '<svg class="smmc-ic-close" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  var IC_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
  var IC_REFRESH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';

  // ---- DOM refs -------------------------------------------------------------
  var root, btn, win, bodyEl, suggEl, inputEl, sendBtn, bubbleEl, typingEl;

  function buildUI() {
    injectStyles();
    root = el("div"); root.id = "smmc-root";

    btn = el("button"); btn.id = "smmc-btn"; btn.setAttribute("aria-label", "Open chat"); btn.innerHTML = IC_OPEN + IC_CLOSE;
    btn.onclick = toggle;

    win = el("div"); win.id = "smmc-win"; win.setAttribute("role", "dialog");

    var initial = (CFG.bot_name || "A").trim().charAt(0).toUpperCase();
    var head = el("div", "smmc-head");
    head.innerHTML =
      '<div class="smmc-av">' + escapeHtml(initial) + '</div>' +
      '<div class="smmc-hmeta"><div class="smmc-hname">' + escapeHtml(CFG.bot_name) +
      '</div><div class="smmc-hstat"><span class="smmc-dot"></span>' + escapeHtml(T.online) + '</div></div>';
    var newBtn = el("button", "smmc-hbtn"); newBtn.title = T.newChat; newBtn.innerHTML = IC_REFRESH; newBtn.onclick = resetChat;
    var clsBtn = el("button", "smmc-hbtn"); clsBtn.title = "Close"; clsBtn.innerHTML = IC_CLOSE.replace("smmc-ic-close", ""); clsBtn.onclick = close;
    head.appendChild(newBtn); head.appendChild(clsBtn);

    bodyEl = el("div", "smmc-body");
    suggEl = el("div", "smmc-sugg");

    var foot = el("div", "smmc-foot");
    inputEl = el("textarea", "smmc-input"); inputEl.rows = 1; inputEl.placeholder = T.placeholder;
    inputEl.addEventListener("input", autoGrow);
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); }
    });
    sendBtn = el("button", "smmc-send"); sendBtn.setAttribute("aria-label", T.send); sendBtn.innerHTML = IC_SEND; sendBtn.onclick = onSend;
    foot.appendChild(inputEl); foot.appendChild(sendBtn);

    var brand = el("div", "smmc-brand", CFG.panel_name ? ("Powered by " + escapeHtml(CFG.panel_name)) : "");

    win.appendChild(head); win.appendChild(bodyEl); win.appendChild(suggEl); win.appendChild(foot);
    if (CFG.panel_name) win.appendChild(brand);
    root.appendChild(btn); root.appendChild(win);
    document.body.appendChild(root);
    autoGrow();
  }

  function autoGrow() {
    inputEl.style.height = "auto";
    var h = Math.min(inputEl.scrollHeight, 110);
    inputEl.style.height = h + "px";
    inputEl.style.overflowY = inputEl.scrollHeight > 110 ? "auto" : "hidden";
  }
  function scrollDown() { bodyEl.scrollTop = bodyEl.scrollHeight; }

  // ---- rendering ------------------------------------------------------------
  function renderMessage(role, text) {
    var row = el("div", "smmc-row " + (role === "user" ? "user" : "bot"));
    if (role !== "user") { var av = el("div", "smmc-sav", escapeHtml((CFG.bot_name || "A").charAt(0).toUpperCase())); row.appendChild(av); }
    var msg = el("div", "smmc-msg " + (role === "user" ? "user" : "bot"));
    msg.innerHTML = role === "user" ? escapeHtml(text).replace(/\n/g, "<br>") : formatMessage(text);
    row.appendChild(msg);
    bodyEl.appendChild(row);
    scrollDown();
  }

  function renderAll() {
    bodyEl.innerHTML = "";
    if (!state.messages.length) renderMessage("bot", CFG.greeting);
    else state.messages.forEach(function (m) { renderMessage(m.role, m.text); });
    scrollDown();
  }

  function renderSuggestions() {
    suggEl.innerHTML = "";
    var show = state.messages.length === 0 && CFG.suggestions && CFG.suggestions.length;
    if (!show) { suggEl.style.display = "none"; return; }
    suggEl.style.display = "flex";
    CFG.suggestions.slice(0, 4).forEach(function (s) {
      var text = typeof s === "string" ? s : (s && s.text) || "";
      if (!text) return;
      var chip = el("button", "smmc-chip", escapeHtml(text));
      chip.onclick = function () { inputEl.value = text.replace(/^[^\wঀ-৿]+\s*/, ""); onSend(); };
      suggEl.appendChild(chip);
    });
  }

  function showTyping() {
    hideTyping();
    typingEl = el("div", "smmc-row bot");
    typingEl.appendChild(el("div", "smmc-sav", escapeHtml((CFG.bot_name || "A").charAt(0).toUpperCase())));
    typingEl.appendChild(el("div", "smmc-typing", "<span></span><span></span><span></span>"));
    bodyEl.appendChild(typingEl); scrollDown();
  }
  function hideTyping() { if (typingEl && typingEl.parentNode) typingEl.parentNode.removeChild(typingEl); typingEl = null; }

  // ---- messaging ------------------------------------------------------------
  function apiHistory() {
    return state.messages.slice(-16).map(function (m) {
      return { role: m.role === "user" ? "user" : "model", content: m.text };
    });
  }

  function onSend() {
    var text = (inputEl.value || "").trim();
    if (!text || state.sending) return;
    inputEl.value = ""; autoGrow();
    var prior = apiHistory();
    state.messages.push({ role: "user", text: text }); saveState();
    renderMessage("user", text);
    renderSuggestions();
    send(text, prior);
  }

  function send(text, prior) {
    state.sending = true; sendBtn.disabled = true;
    showTyping();
    var started = Date.now();
    fetch(API_BASE + "/api/chatbot/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: prior, lang: LANG })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        var reply = (res.j && res.j.reply) || T.error;
        var think = Math.min(2200, Math.max(500, 300 + reply.length * 9));
        var wait = Math.max(0, think - (Date.now() - started));
        setTimeout(function () {
          hideTyping();
          state.messages.push({ role: "bot", text: reply }); saveState();
          renderMessage("bot", reply);
          state.sending = false; sendBtn.disabled = false;
        }, wait);
      })
      .catch(function () {
        hideTyping();
        renderMessage("bot", T.error);
        state.sending = false; sendBtn.disabled = false;
      });
  }

  // ---- open / close / reset -------------------------------------------------
  function open() {
    state.open = true; root.classList.add("open"); btn.setAttribute("aria-label", "Close chat");
    hideBubble();
    if (!bodyEl.childNodes.length) renderAll();
    renderSuggestions();
    setTimeout(function () { try { inputEl.focus(); } catch (e) {} }, 250);
    scrollDown();
  }
  function close() { state.open = false; root.classList.remove("open"); }
  function toggle() { state.open ? close() : open(); }

  function resetChat() {
    state.messages = []; saveState();
    renderAll(); renderSuggestions();
  }

  // ---- greeting bubble ------------------------------------------------------
  function maybeBubble() {
    if (state.open || state.messages.length) return;
    var interval = (CFG.greeting_interval_hours || 6) * 3600 * 1000;
    if (Date.now() - (state.greetedAt || 0) < interval) return;
    setTimeout(showBubble, 1400);
  }
  function showBubble() {
    if (state.open || bubbleEl) return;
    bubbleEl = el("div"); bubbleEl.id = "smmc-bubble";
    bubbleEl.innerHTML = escapeHtml(CFG.greeting);
    var x = el("button", "smmc-x", "✕"); x.onclick = function (e) { e.stopPropagation(); hideBubble(); };
    bubbleEl.appendChild(x);
    bubbleEl.onclick = open;
    root.appendChild(bubbleEl);
    state.greetedAt = Date.now(); saveState();
  }
  function hideBubble() { if (bubbleEl && bubbleEl.parentNode) bubbleEl.parentNode.removeChild(bubbleEl); bubbleEl = null; }

  // ---- boot -----------------------------------------------------------------
  function boot() {
    loadState();
    fetch(API_BASE + "/api/chatbot/init")
      .then(function (r) { return r.json(); })
      .then(function (cfg) { if (cfg && typeof cfg === "object") Object.assign(CFG, cfg); })
      .catch(function () {})
      .then(function () {
        buildUI();
        maybeBubble();
        window.SMMChat = { open: open, close: close, toggle: toggle, reset: resetChat };
      });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
