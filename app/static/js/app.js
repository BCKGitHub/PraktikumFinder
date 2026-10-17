/* PraktikumFinder – client-side features. Personal data stays in this browser. */
(function () {
  "use strict";

  // ---------- storage helpers (fail safe in private mode) ----------
  function load(key, fallback, store) {
    try {
      var raw = (store || window.localStorage).getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) { return fallback; }
  }
  function save(key, value, store) {
    try { (store || window.localStorage).setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ }
  }
  var KEYS = { saved: "pf_saved", tracker: "pf_tracker", checks: "pf_checks" };
  var FIELDS_KEY = "pf_apply_fields"; // sessionStorage only: cleared when the tab closes

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") n.textContent = attrs[k];
      else if (k === "class") n.className = attrs[k];
      else n.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function deDate(iso) {
    if (!iso) return "";
    var p = iso.split("-");
    return p.length === 3 ? p[2] + "." + p[1] + "." + p[0] : iso;
  }
  function todayIso() { return new Date().toISOString().slice(0, 10); }
  function daysUntil(iso) { return Math.round((new Date(iso + "T00:00:00") - new Date(todayIso() + "T00:00:00")) / 86400000); }
  function sendEvent(name) {
    try {
      fetch("/api/event", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ event: name }), keepalive: true });
    } catch (e) { /* ignore */ }
  }

  // ---------- saved listings ----------
  function savedIds() { return load(KEYS.saved, []); }
  function updateSavedUi() {
    var ids = savedIds();
    $$("[data-saved-count]").forEach(function (b) { b.textContent = ids.length; b.hidden = ids.length === 0; });
    $$("[data-save]").forEach(function (btn) {
      var on = ids.indexOf(Number(btn.getAttribute("data-save"))) !== -1;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.textContent = on ? "★ Gemerkt" : "☆ Merken";
    });
  }
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-save]");
    if (btn) {
      var id = Number(btn.getAttribute("data-save"));
      var ids = savedIds();
      var i = ids.indexOf(id);
      if (i === -1) { ids.push(id); sendEvent("saved"); } else { ids.splice(i, 1); }
      save(KEYS.saved, ids);
      updateSavedUi();
    }
    var ext = ev.target.closest("[data-event]");
    if (ext) sendEvent(ext.getAttribute("data-event"));
  });

  // ---------- search form date check ----------
  $$("[data-search-form]").forEach(function (form) {
    var s = $("[data-date-start]", form), e = $("[data-date-end]", form), msg = $("[data-date-error]", form);
    function check() {
      var bad = s.value && e.value && e.value < s.value;
      if (msg) msg.hidden = !bad;
      e.setCustomValidity(bad ? "Das Ende liegt vor dem Beginn." : "");
    }
    if (s && e) { s.addEventListener("change", check); e.addEventListener("change", check); }
  });

  // ---------- checklists ----------
  $$("[data-checklist]").forEach(function (box) {
    var key = box.getAttribute("data-checklist");
    var all = load(KEYS.checks, {});
    var state = all[key] || {};
    $$("[data-check-item]", box).forEach(function (cb) {
      var id = cb.getAttribute("data-check-item");
      cb.checked = !!state[id];
      cb.addEventListener("change", function () {
        var a = load(KEYS.checks, {});
        a[key] = a[key] || {};
        a[key][id] = cb.checked;
        save(KEYS.checks, a);
      });
    });
  });

  // ---------- tracker data ----------
  var STATUSES = [
    ["saved", "Gemerkt"], ["preparing", "Bewerbung in Vorbereitung"], ["applied", "Beworben"],
    ["follow_up", "Nachfassen nötig"], ["interview", "Gespräch / Telefonat"], ["accepted", "Zusage"],
    ["rejected", "Absage"], ["withdrawn", "Zurückgezogen"], ["closed", "Geschlossen / abgelaufen"]
  ];
  function trackerItems() { return load(KEYS.tracker, []); }
  function addToTracker(item) {
    var items = trackerItems();
    if (!items.some(function (x) { return x.id === item.id; })) {
      items.push(Object.assign({ status: "preparing", followUp: "", notes: "", docs: false, school: false, parents: false, added: todayIso() }, item));
      save(KEYS.tracker, items);
    }
  }

  // ---------- application helper ----------
  var tplNode = $("#apply-templates");
  if (tplNode) {
    var T = JSON.parse(tplNode.textContent);
    var form = $("[data-apply-form]");
    var outputs = { email_subject: $('[data-output="email_subject"]'), email_body: $('[data-output="email_body"]'), cover_letter: $('[data-output="cover_letter"]') };
    var confirmBox = $("[data-confirm]");
    var stored = load(FIELDS_KEY, {}, window.sessionStorage);
    $$("[data-field]", form).forEach(function (inp) {
      var k = inp.getAttribute("data-field");
      if (stored[k]) inp.value = stored[k];
      inp.addEventListener("input", function () {
        var s = load(FIELDS_KEY, {}, window.sessionStorage);
        s[k] = inp.value;
        save(FIELDS_KEY, s, window.sessionStorage);
      });
    });

    function val(k) {
      var inp = $('[data-field="' + k + '"]', form);
      return inp ? inp.value.trim() : "";
    }
    function ph(k) { return "[bitte ergänzen: " + (T.placeholders[k] || k) + "]"; }
    var OPTIONAL = ["phone_part", "employer_place"];
    function fill(template, values) {
      return template.replace(/\{(\w+)\}/g, function (_, k) {
        var v = values[k];
        return v ? v : (OPTIONAL.indexOf(k) !== -1 ? "" : ph(k));
      });
    }
    function generate() {
      var contact = val("contact_person");
      var phone = val("phone");
      var email = val("email");
      var homeCity = val("home_city");
      var now = new Date();
      var dateDe = ("0" + now.getDate()).slice(-2) + "." + ("0" + (now.getMonth() + 1)).slice(-2) + "." + now.getFullYear();
      var v = {
        name: val("name"), age: val("age"), school: val("school"), grade: val("grade"),
        start: deDate(val("start")), end: deDate(val("end")), domain: val("domain"),
        practicum_kind: val("practicum_kind"), motivation: val("motivation"), strengths: val("strengths"),
        email: email, employer: T.fixed.employer, title: T.fixed.title,
        employer_place: T.fixed.employer_place || "",
        salutation: contact ? "Guten Tag " + contact + "," : "Sehr geehrte Damen und Herren,",
        phone_part: phone ? " oder telefonisch unter " + phone : "",
        contact_line: [email, phone].filter(Boolean).join(" · "),
        city_date: homeCity ? homeCity + ", " + dateDe : "",
        attachments: T.fixed.attachments
      };
      if (!v.strengths) v.strengths = ph("strengths");
      outputs.email_subject.value = fill(T.email_subject, v);
      outputs.email_body.value = fill(T.email_body, v);
      outputs.cover_letter.value = fill(T.cover_letter, v);
      refreshGate();
      $("#email").scrollIntoView({ behavior: "smooth" });
    }
    function hasPlaceholders() {
      return /\[bitte ergänzen/.test(outputs.email_body.value + outputs.cover_letter.value + outputs.email_subject.value);
    }
    function refreshGate() {
      var ok = confirmBox.checked && outputs.email_body.value.length > 0;
      $$("[data-copy], [data-download-txt], [data-docx-btn]").forEach(function (b) { b.disabled = !ok; });
      var f = $("[data-docx-form]");
      if (f) f.querySelector('[name="confirmed"]').value = ok ? "1" : "";
    }
    $("[data-generate]").addEventListener("click", generate);
    $("[data-forget-fields]").addEventListener("click", function () {
      $$("[data-field]", form).forEach(function (i) { if (i.tagName !== "SELECT") i.value = ""; });
      try { window.sessionStorage.removeItem(FIELDS_KEY); } catch (e) { /* ignore */ }
    });
    confirmBox.addEventListener("change", function () {
      if (confirmBox.checked && hasPlaceholders()) {
        alertInline("Es gibt noch Stellen mit [bitte ergänzen: …]. Bitte ersetze sie oder lösche sie, bevor du den Text verwendest.");
      }
      if (confirmBox.checked) sendEvent("helper_completed");
      refreshGate();
    });
    Object.keys(outputs).forEach(function (k) { outputs[k].addEventListener("input", refreshGate); });

    function textFor(which) {
      return which === "email" ? "Betreff: " + outputs.email_subject.value + "\n\n" + outputs.email_body.value : outputs.cover_letter.value;
    }
    function alertInline(msg) {
      var box = $(".confirm-box");
      var p = $(".confirm-warn", box) || el("p", { class: "alert alert-warn small confirm-warn", role: "alert" });
      p.textContent = msg;
      box.insertBefore(p, box.firstChild);
    }
    $$("[data-copy]").forEach(function (b) {
      b.addEventListener("click", function () {
        var t = textFor(b.getAttribute("data-copy"));
        (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject()).then(function () {
          b.textContent = "Kopiert ✓";
          setTimeout(function () { b.textContent = "Kopieren"; }, 2000);
        }, function () {
          var ta = b.getAttribute("data-copy") === "email" ? outputs.email_body : outputs.cover_letter;
          ta.select();
          b.textContent = "Bitte mit Strg+C kopieren";
        });
      });
    });
    $$("[data-download-txt]").forEach(function (b) {
      b.addEventListener("click", function () {
        var which = b.getAttribute("data-download-txt");
        var blob = new Blob([textFor(which)], { type: "text/plain;charset=utf-8" });
        var a = el("a", { href: URL.createObjectURL(blob), download: which === "email" ? "Bewerbung_EMail.txt" : "Anschreiben.txt" });
        document.body.appendChild(a); a.click(); a.remove();
      });
    });
    var docxForm = $("[data-docx-form]");
    if (docxForm) {
      docxForm.addEventListener("submit", function () { docxForm.querySelector('[name="body"]').value = outputs.cover_letter.value; });
    }
  }
  $$("[data-track-add]").forEach(function (b) {
    b.addEventListener("click", function () {
      addToTracker({ id: Number(b.getAttribute("data-track-add")), title: b.getAttribute("data-title"), employer: b.getAttribute("data-employer") });
      var msg = $("[data-track-msg]");
      if (msg) msg.hidden = false;
    });
  });

  // ---------- saved list page ----------
  function fetchSummaries(ids) {
    if (!ids.length) return Promise.resolve([]);
    return fetch("/api/opportunities?ids=" + ids.join(",")).then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return null; });
  }
  var savedList = $("[data-saved-list]");
  if (savedList) {
    var ids = savedIds();
    fetchSummaries(ids).then(function (rows) {
      savedList.innerHTML = "";
      if (rows === null) { savedList.appendChild(el("p", { class: "alert alert-error", text: "Die Merkliste konnte nicht geladen werden. Bitte später erneut versuchen." })); return; }
      if (!rows.length) { savedList.appendChild($("#saved-empty").content.cloneNode(true)); return; }
      var byId = {}; rows.forEach(function (r) { byId[r.id] = r; });
      ids.forEach(function (id) {
        var r = byId[id];
        var card = el("div", { class: "card" });
        if (!r) {
          card.appendChild(el("p", { class: "muted", text: "Dieses Angebot ist nicht mehr verfügbar." }));
        } else {
          card.appendChild(el("h2", { class: "h3" }, [el("a", { href: r.url, text: r.title })]));
          card.appendChild(el("p", { class: "employer", text: r.employer + (r.place ? " · " + r.place : "") }));
          var info = [];
          if (r.deadline) info.push("Frist: " + deDate(r.deadline));
          if (r.status === "expired") info.push("abgelaufen");
          if (r.status === "closed") info.push("keine Plätze mehr");
          if (info.length) card.appendChild(el("p", { class: "small", text: info.join(" · ") }));
        }
        var actions = el("div", { class: "actions" });
        var rm = el("button", { type: "button", class: "btn btn-ghost", text: "Entfernen" });
        rm.addEventListener("click", function () { save(KEYS.saved, savedIds().filter(function (x) { return x !== id; })); card.remove(); updateSavedUi(); });
        actions.appendChild(rm);
        if (r) {
          var tr = el("button", { type: "button", class: "btn", text: "Zu Bewerbungen" });
          tr.addEventListener("click", function () { addToTracker({ id: r.id, title: r.title, employer: r.employer, status: "saved" }); tr.textContent = "In Bewerbungen ✓"; tr.disabled = true; });
          actions.appendChild(tr);
          actions.appendChild(el("a", { class: "btn btn-primary", href: r.url + "/bewerbung", text: "Bewerbung vorbereiten" }));
        }
        card.appendChild(actions);
        savedList.appendChild(card);
      });
    });
  }

  // ---------- tracker page ----------
  var trackerList = $("[data-tracker-list]");
  if (trackerList) {
    var items = trackerItems();
    fetchSummaries(items.map(function (x) { return x.id; })).then(function (rows) {
      var byId = {}; (rows || []).forEach(function (r) { byId[r.id] = r; });
      renderTracker(byId);
    });
  }
  function renderTracker(byId) {
    var items = trackerItems();
    trackerList.innerHTML = "";
    var rem = $("[data-reminders]");
    rem.innerHTML = "";
    if (!items.length) { trackerList.appendChild($("#tracker-empty").content.cloneNode(true)); return; }
    var reminders = [];
    items.forEach(function (it) {
      var live = byId[it.id];
      var active = ["saved", "preparing", "applied", "follow_up", "interview"].indexOf(it.status) !== -1;
      if (live && live.deadline && ["saved", "preparing"].indexOf(it.status) !== -1) {
        var d = daysUntil(live.deadline);
        if (d >= 0 && d <= 7) reminders.push("„" + it.title + "“: Bewerbungsfrist in " + d + " Tag(en) (" + deDate(live.deadline) + ").");
        if (d < 0) reminders.push("„" + it.title + "“: Die Bewerbungsfrist ist abgelaufen.");
      }
      if (it.followUp && active && daysUntil(it.followUp) <= 0) reminders.push("„" + it.title + "“: Heute nachfassen (z. B. kurz anrufen oder freundlich nachfragen).");
      if (["preparing", "applied"].indexOf(it.status) !== -1 && !it.docs) reminders.push("„" + it.title + "“: Unterlagen noch nicht vollständig abgehakt.");
      if (["applied", "interview", "accepted"].indexOf(it.status) !== -1 && !it.school) reminders.push("„" + it.title + "“: Zustimmung der Schule noch offen.");
      if (["applied", "interview", "accepted"].indexOf(it.status) !== -1 && !it.parents) reminders.push("„" + it.title + "“: Einverständnis der Eltern noch offen.");
      if (live && live.status !== "open" && active) reminders.push("„" + it.title + "“: Das Angebot ist " + (live.status === "expired" ? "abgelaufen" : "geschlossen") + ".");

      var card = el("div", { class: "card tracker-item" });
      card.appendChild(el("h2", { class: "h3" }, [el("a", { href: "/angebote/" + it.id, text: it.title })]));
      card.appendChild(el("p", { class: "employer", text: it.employer || "" }));
      var sel = el("select", { "aria-label": "Status" });
      STATUSES.forEach(function (s) { var o = el("option", { value: s[0], text: s[1] }); if (s[0] === it.status) o.selected = true; sel.appendChild(o); });
      var fu = el("input", { type: "date", value: it.followUp || "", "aria-label": "Nachfassen am" });
      var notes = el("textarea", { rows: "2", maxlength: "500", "aria-label": "Notizen" }); notes.value = it.notes || "";
      function cb(key, label) {
        var c = el("input", { type: "checkbox" }); c.checked = !!it[key];
        c.addEventListener("change", function () { update(it.id, key, c.checked); });
        return el("label", { class: "check" }, [c, " " + label]);
      }
      sel.addEventListener("change", function () { update(it.id, "status", sel.value); });
      fu.addEventListener("change", function () { update(it.id, "followUp", fu.value); });
      notes.addEventListener("change", function () { update(it.id, "notes", notes.value); });
      card.appendChild(el("div", { class: "grid-2" }, [
        el("label", {}, ["Status", sel]),
        el("label", {}, ["Nachfassen am", fu]),
        el("label", { class: "span-2" }, ["Notizen (nur in diesem Browser)", notes])
      ]));
      card.appendChild(cb("docs", "Unterlagen vollständig"));
      card.appendChild(cb("school", "Schule hat zugestimmt"));
      card.appendChild(cb("parents", "Eltern / Erziehungsberechtigte einverstanden"));
      var rm = el("button", { type: "button", class: "btn btn-ghost", text: "Entfernen" });
      rm.addEventListener("click", function () { save(KEYS.tracker, trackerItems().filter(function (x) { return x.id !== it.id; })); renderTracker(byId); });
      card.appendChild(el("div", { class: "actions" }, [rm]));
      trackerList.appendChild(card);
    });
    if (reminders.length) {
      var box = el("section", { class: "card", "aria-label": "Erinnerungen" }, [el("h2", { class: "h3", text: "Erinnerungen" })]);
      reminders.forEach(function (r) { box.appendChild(el("p", { class: "reminder", text: r })); });
      rem.appendChild(box);
    }
    function update(id, key, value) {
      var all = trackerItems();
      all.forEach(function (x) { if (x.id === id) x[key] = value; });
      save(KEYS.tracker, all);
      if (key !== "notes") renderTracker(byId);
    }
  }

  // ---------- privacy settings page ----------
  var summary = $("[data-storage-summary]");
  if (summary) {
    var n1 = savedIds().length, n2 = trackerItems().length, n3 = Object.keys(load(KEYS.checks, {})).length;
    summary.textContent = n1 + " gemerkte Angebote, " + n2 + " Bewerbungen in der Übersicht, " + n3 + " Checklisten.";
    $("[data-export]").addEventListener("click", function () {
      var data = { exportiert_am: new Date().toISOString(), merkliste: savedIds(), bewerbungen: trackerItems(), checklisten: load(KEYS.checks, {}) };
      var blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      var a = el("a", { href: URL.createObjectURL(blob), download: "praktikumfinder-meine-daten.json" });
      document.body.appendChild(a); a.click(); a.remove();
    });
    $("[data-wipe]").addEventListener("click", function () {
      try {
        Object.keys(KEYS).forEach(function (k) { window.localStorage.removeItem(KEYS[k]); });
        window.sessionStorage.removeItem(FIELDS_KEY);
      } catch (e) { /* ignore */ }
      summary.textContent = "Keine Daten gespeichert.";
      $("[data-wipe-msg]").hidden = false;
      updateSavedUi();
    });
  }

  updateSavedUi();
})();
