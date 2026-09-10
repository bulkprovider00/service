/*!
 * SMM Stats — সম্পন্ন অর্ডারের পাবলিক ফিড উইজেট
 *
 * Statistics পেজে (লাইভ ফিড):
 *   <div data-smm-feed data-limit="20"></div>
 *
 * New Order পেজে (সাম্প্রতিক ডেলিভারি টাইম):
 *   <div data-smm-times data-service-id="29018"></div>
 *
 * একবার লোড করুন:
 *   <script src="https://YOUR-HOST/smm-stats.js" data-api="https://YOUR-HOST" async></script>
 *
 * নির্ভরতা নেই। ভ্যানিলা JS।
 */
(function () {
  "use strict";
  if (window.__SMMStatsLoaded) return;
  window.__SMMStatsLoaded = true;

  // ---- API base ------------------------------------------------------------
  var me = document.currentScript || (function () {
    var s = document.getElementsByTagName("script");
    for (var i = s.length - 1; i >= 0; i--) if (/smm-stats\.js/.test(s[i].src || "")) return s[i];
    return null;
  })();
  var API = (me && me.getAttribute("data-api")) ||
    (me && me.src ? me.src.replace(/\/smm-stats\.js.*$/, "") : "");

  // ---- helpers -------------------------------------------------------------
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function num(n) { return Number(n || 0).toLocaleString("en-US"); }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function get(path) {
    return fetch(API + path, { credentials: "omit" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
  }
  function debounce(fn, ms) {
    var t; return function () {
      var a = arguments, c = this;
      clearTimeout(t); t = setTimeout(function () { fn.apply(c, a); }, ms);
    };
  }

  // "02 Sept 2026, 04:54" — প্যানেলের ফরম্যাটের সাথে মিল
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"];
  function whenText(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    var p = function (n) { return n < 10 ? "0" + n : "" + n; };
    return p(d.getDate()) + " " + MON[d.getMonth()] + " " + d.getFullYear() +
      ", " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  // প্ল্যাটফর্মের রং — লোগোর বদলে ব্র্যান্ড-রঙের ডিস্ক (কোনো লোগো কপি নয়)
  var TONE = {
    Facebook: "#1877F2", Instagram: "#C13584", TikTok: "#111827", YouTube: "#FF0033",
    Twitter: "#1D9BF0", Telegram: "#29A9EB", LinkedIn: "#0A66C2", Threads: "#111827",
    Snapchat: "#F5C000", Discord: "#5865F2", Spotify: "#1DB954", SoundCloud: "#FF5500",
    Twitch: "#9146FF", Pinterest: "#E60023", Google: "#4285F4", Other: "#7C8DB5"
  };
  function disc(platform) {
    var p = platform || "Other";
    var color = TONE[p] || TONE.Other;
    return '<span class="smst-disc" style="background:' + color + '">' +
      esc(p.charAt(0).toUpperCase()) + "</span>";
  }

  // ---- styles --------------------------------------------------------------
  function styles() {
    if (document.getElementById("smst-css")) return;
    var css = [
      ".smst,.smst *{box-sizing:border-box}",
      ".smst{--smst-ink:#16233d;--smst-dim:#7b8aa5;--smst-line:#e5ecf6;--smst-blue:#2e9bf0;",
      "--smst-blue-soft:#eaf4fe;--smst-green:#12a05e;--smst-green-soft:#e6f7ef;",
      "--smst-amber:#d3841f;--smst-amber-soft:#fdf3e3;--smst-radius:13px;",
      "font-family:inherit;color:var(--smst-ink)}",

      /* --- search --- */
      ".smst-search{position:relative;margin-bottom:14px}",
      ".smst-search input{width:100%;border:1px solid var(--smst-line);background:#fff;",
      "border-radius:var(--smst-radius);padding:13px 16px 13px 42px;font:inherit;font-size:14px;",
      "color:var(--smst-ink);outline:none;transition:border-color .15s,box-shadow .15s}",
      ".smst-search input::placeholder{color:var(--smst-dim)}",
      ".smst-search input:focus{border-color:var(--smst-blue);box-shadow:0 0 0 3px rgba(46,155,240,.13)}",
      ".smst-search svg{position:absolute;left:15px;top:50%;transform:translateY(-50%);",
      "width:16px;height:16px;stroke:var(--smst-dim);fill:none;stroke-width:2;pointer-events:none}",

      /* --- feed rows --- */
      ".smst-list{display:flex;flex-direction:column;gap:10px}",
      ".smst-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap;background:#fff;",
      "border:1px solid var(--smst-line);border-radius:var(--smst-radius);padding:12px 14px}",
      ".smst-disc{width:34px;height:34px;border-radius:50%;flex:none;display:flex;",
      "align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px}",
      ".smst-sid{background:var(--smst-blue-soft);color:#1c7fce;font-weight:700;font-size:12.5px;",
      "border-radius:8px;padding:5px 10px;flex:none}",
      ".smst-copy{background:none;border:1px solid var(--smst-line);color:var(--smst-dim);",
      "border-radius:8px;padding:5px 9px;font:inherit;font-size:11.5px;cursor:pointer;flex:none;",
      "transition:color .15s,border-color .15s}",
      ".smst-copy:hover{color:var(--smst-blue);border-color:var(--smst-blue)}",
      ".smst-copy.ok{color:var(--smst-green);border-color:var(--smst-green)}",
      ".smst-name{flex:1;min-width:170px;font-size:13.5px;line-height:1.45;word-break:break-word}",
      ".smst-tags{display:flex;gap:7px;flex-wrap:wrap;align-items:center;margin-left:auto}",
      ".smst-tag{font-size:12px;border-radius:8px;padding:5px 10px;white-space:nowrap;flex:none}",
      ".smst-tag.done{background:var(--smst-green-soft);color:var(--smst-green);font-weight:600}",
      ".smst-tag.qty,.smst-tag.dur{background:var(--smst-blue-soft);color:#1c7fce}",
      ".smst-tag.at{background:var(--smst-amber-soft);color:var(--smst-amber)}",

      /* --- delivery-time chips --- */
      ".smst-times{display:flex;gap:9px;flex-wrap:wrap}",
      ".smst-chip{background:var(--smst-blue);color:#fff;border-radius:9px;padding:8px 13px;",
      "font-size:12.5px;font-weight:600;white-space:nowrap}",
      ".smst-avg{font-size:13.5px;color:var(--smst-dim);margin-bottom:10px}",
      ".smst-avg b{color:var(--smst-ink)}",

      /* --- states --- */
      ".smst-more{display:block;margin:14px auto 0;background:#fff;border:1px solid var(--smst-line);",
      "color:var(--smst-blue);border-radius:var(--smst-radius);padding:11px 26px;font:inherit;",
      "font-size:13.5px;font-weight:600;cursor:pointer;transition:background .15s}",
      ".smst-more:hover{background:var(--smst-blue-soft)}",
      ".smst-more[disabled]{opacity:.55;cursor:default}",
      ".smst-note{padding:26px 16px;text-align:center;color:var(--smst-dim);font-size:13.5px;",
      "background:#fff;border:1px dashed var(--smst-line);border-radius:var(--smst-radius)}",
      ".smst-skel{height:60px;border-radius:var(--smst-radius);background:#fff;",
      "border:1px solid var(--smst-line);position:relative;overflow:hidden}",
      ".smst-skel::after{content:'';position:absolute;inset:0;transform:translateX(-100%);",
      "background:linear-gradient(90deg,transparent,rgba(46,155,240,.09),transparent);",
      "animation:smst-shine 1.25s infinite}",
      "@keyframes smst-shine{to{transform:translateX(100%)}}",
      "@media (prefers-reduced-motion:reduce){.smst-skel::after{animation:none}}",
      "@media (max-width:620px){",
      ".smst-name{flex-basis:100%;order:5}",
      ".smst-tags{margin-left:0;order:6;flex-basis:100%}}"
    ].join("");
    var s = el("style"); s.id = "smst-css"; s.textContent = css;
    document.head.appendChild(s);
  }

  var ICON_SEARCH = '<svg viewBox="0 0 24 24" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>';

  function skeleton(n) {
    var h = "";
    for (var i = 0; i < n; i++) h += '<div class="smst-skel"></div>';
    return '<div class="smst-list">' + h + "</div>";
  }

  // ---- widget 1: live completed feed ---------------------------------------
  function feed(node) {
    styles();
    node.classList.add("smst");

    var pageSize = parseInt(node.getAttribute("data-limit"), 10) || 20;   // এক ধাপে কয়টা আনবে
    var maxTotal = parseInt(node.getAttribute("data-max"), 10) || 1000;    // সব মিলিয়ে সর্বোচ্চ কয়টা
    if (pageSize > maxTotal) pageSize = maxTotal;
    var platform = node.getAttribute("data-platform") || "";
    var refreshMs = (parseInt(node.getAttribute("data-refresh"), 10) || 60) * 1000;
    var showSearch = node.getAttribute("data-search") !== "off";

    var query = "", offset = 0, loading = false;

    node.innerHTML = "";
    var searchWrap = null, input = null;
    if (showSearch) {
      searchWrap = el("div", "smst-search");
      searchWrap.innerHTML = ICON_SEARCH;
      input = el("input");
      input.type = "search";
      input.placeholder = "Search by order ID, service ID or name";
      input.setAttribute("aria-label", "Search completed orders");
      searchWrap.appendChild(input);
      node.appendChild(searchWrap);
    }
    var list = el("div", "smst-list");
    var listWrap = el("div"); listWrap.innerHTML = skeleton(5);
    node.appendChild(listWrap);
    var more = el("button", "smst-more", "Show more");
    more.style.display = "none";
    node.appendChild(more);

    function url() {
      var take = Math.min(pageSize, Math.max(0, maxTotal - offset));
      var q = "?limit=" + take + "&offset=" + offset;
      if (platform) q += "&platform=" + encodeURIComponent(platform);
      if (query) q += "&search=" + encodeURIComponent(query);
      return "/api/orders/completed" + q;
    }

    function row(o) {
      var r = el("div", "smst-row");
      r.innerHTML =
        disc(o.platform) +
        '<span class="smst-sid">' + esc(o.service_id) + "</span>" +
        '<button class="smst-copy" type="button">Copy</button>' +
        '<div class="smst-name">' + esc(o.service_name) + "</div>" +
        '<div class="smst-tags">' +
        '<span class="smst-tag done">Completed</span>' +
        '<span class="smst-tag qty">' + num(o.quantity) + "</span>" +
        (o.completed_in ? '<span class="smst-tag dur">' + esc(o.completed_in) + "</span>" : "") +
        (o.completed_at_timestamp
          ? '<span class="smst-tag at">' + esc(whenText(o.completed_at_timestamp)) + "</span>" : "") +
        "</div>";
      var btn = r.querySelector(".smst-copy");
      btn.onclick = function () {
        var done = function () {
          btn.textContent = "Copied"; btn.classList.add("ok");
          setTimeout(function () { btn.textContent = "Copy"; btn.classList.remove("ok"); }, 1400);
        };
        if (navigator.clipboard) navigator.clipboard.writeText(String(o.service_id)).then(done, function () {});
        else done();
      };
      return r;
    }

    function load(reset) {
      if (loading) return;
      loading = true;
      more.disabled = true;
      if (reset) { offset = 0; listWrap.innerHTML = skeleton(4); }

      get(url()).then(function (d) {
        if (reset) { list = el("div", "smst-list"); listWrap.innerHTML = ""; listWrap.appendChild(list); }
        if (reset && !d.orders.length) {
          listWrap.innerHTML = '<div class="smst-note">' +
            (query ? "No completed order matches that search." : "No completed orders yet.") + "</div>";
          more.style.display = "none";
          loading = false;
          return;
        }
        var frag = document.createDocumentFragment();
        d.orders.forEach(function (o) { frag.appendChild(row(o)); });
        list.appendChild(frag);
        offset += d.orders.length;

        var left = Math.min(d.total_completed, maxTotal) - offset;
        more.style.display = (d.has_more && left > 0) ? "block" : "none";
        more.textContent = left > 0 ? ("Show more (" + num(left) + " left)") : "Show more";
        more.disabled = false;
        loading = false;
      }).catch(function () {
        if (reset) {
          listWrap.innerHTML = '<div class="smst-note">Couldn\'t load orders. Refresh to try again.</div>';
        }
        more.disabled = false;
        loading = false;
      });
    }

    more.onclick = function () { load(false); };

    // বাটন ভিউতে এলে নিজে থেকেই পরের ধাপ লোড হবে — ১০০০ পর্যন্ত ক্লিক করতে হবে না
    if (window.IntersectionObserver) {
      new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting && more.style.display !== "none" && !loading) load(false);
      }, { rootMargin: "300px" }).observe(more);
    }
    if (input) {
      input.addEventListener("input", debounce(function () {
        query = input.value.trim();
        load(true);
      }, 350));
    }

    load(true);
    if (refreshMs > 0) {
      setInterval(function () {
        if (!query && offset <= pageSize && !document.hidden) load(true);
      }, refreshMs);
    }
  }

  // ---- widget 2: recent delivery times for one service ---------------------
  function times(node) {
    styles();
    node.classList.add("smst");

    var sid = node.getAttribute("data-service-id");
    var limit = parseInt(node.getAttribute("data-limit"), 10) || 6;
    var showAvg = node.getAttribute("data-avg") !== "off";
    if (!sid) { node.innerHTML = ""; return; }

    node.innerHTML = '<div class="smst-times"><span class="smst-chip" style="opacity:.5">Loading…</span></div>';

    get("/api/orders/completed/" + encodeURIComponent(sid) + "?limit=" + limit)
      .then(function (d) {
        if (!d.recent || !d.recent.length) {
          node.innerHTML = '<div class="smst-note">No delivery data for this service yet.</div>';
          return;
        }
        var html = "";
        if (showAvg && d.average_time) {
          html += '<div class="smst-avg">Average of the last ' + d.recent.length +
            " completed orders: <b>" + esc(d.average_time) + "</b></div>";
        }
        html += '<div class="smst-times">';
        d.recent.forEach(function (r) {
          if (!r.completed_in) return;
          html += '<span class="smst-chip">' + num(r.quantity) + " = " + esc(r.completed_in) + "</span>";
        });
        html += "</div>";
        node.innerHTML = html;
      })
      .catch(function () { node.innerHTML = ""; });
  }

  // ---- boot ----------------------------------------------------------------
  function mountAll(scope) {
    (scope || document).querySelectorAll("[data-smm-feed]").forEach(function (n) {
      if (!n.__smst) { n.__smst = 1; feed(n); }
    });
    (scope || document).querySelectorAll("[data-smm-times]").forEach(function (n) {
      if (!n.__smst) { n.__smst = 1; times(n); }
    });
  }

  window.SMMStats = {
    api: API,
    mount: mountAll,
    feed: feed,
    times: times,
    // সার্ভিস বদলালে (PerfectPanel-এ New Order ড্রপডাউন) আবার লোড করতে:
    setService: function (node, serviceId) {
      node.setAttribute("data-service-id", serviceId);
      times(node);
    }
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { mountAll(); });
  else mountAll();
})();
