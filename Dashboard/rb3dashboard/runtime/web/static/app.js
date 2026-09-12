/* Stage Kit dashboard.

   Plain ES5-compatible browser JavaScript, no build step and no dependencies.

   The token lives in localStorage because EventSource cannot send an
   Authorization header, so it has to travel as a query parameter anyway. It is
   never written to the page or to a log.

   The lamp row is the real command: a Stage Kit command is an eight-bit mask
   plus a colour byte, so the mask is the interface rather than a preset list. */

(function () {
  "use strict";

  var TOKEN_KEY = "rb3e.token";
  var LAMPS = 8;

  var state = { mask: 0xFF, colour: 0x80, version: -1 };
  var source = null;
  var token = readToken();

  var el = {
    pulse: document.getElementById("pulse"),
    link: document.getElementById("link"),
    gameState: document.getElementById("game-state"),
    song: document.getElementById("song"),
    artist: document.getElementById("artist"),
    facts: document.getElementById("facts"),
    ring: document.getElementById("ring"),
    bytes: document.getElementById("bytes"),
    send: document.getElementById("send"),
    devices: document.getElementById("devices"),
    rigHint: document.getElementById("rig-hint"),
    lightsHint: document.getElementById("lights-hint"),
    log: document.getElementById("log"),
    gate: document.getElementById("gate"),
    gateForm: document.getElementById("gate-form"),
    gateError: document.getElementById("gate-error"),
    tokenInput: document.getElementById("token")
  };

  function readToken() {
    try { return window.localStorage.getItem(TOKEN_KEY) || ""; }
    catch (error) { return ""; }
  }

  function writeToken(value) {
    try { window.localStorage.setItem(TOKEN_KEY, value); }
    catch (error) { /* private browsing; the session still works */ }
  }

  function withToken(path) {
    if (!token) { return path; }
    return path + (path.indexOf("?") === -1 ? "?" : "&") +
      "token=" + encodeURIComponent(token);
  }

  function request(path, options) {
    options = options || {};
    options.headers = options.headers || {};
    if (token) { options.headers.Authorization = "Bearer " + token; }
    if (options.body) { options.headers["Content-Type"] = "application/json"; }
    return fetch(path, options).then(function (response) {
      if (response.status === 401) {
        askForToken(true);
        throw new Error("unauthorised");
      }
      return response.json().then(function (document) {
        if (!response.ok) {
          throw new Error(document.error || "The server refused that request");
        }
        return document;
      });
    });
  }

  /* ---- token gate ---- */

  function askForToken(failed) {
    if (el.gateError) { el.gateError.hidden = !failed; }
    if (el.gate && !el.gate.open && el.gate.showModal) {
      el.gate.showModal();
      el.tokenInput.focus();
    }
  }

  if (el.gateForm) {
    el.gateForm.addEventListener("submit", function () {
      token = el.tokenInput.value.trim();
      writeToken(token);
      el.tokenInput.value = "";
      connect();
    });
  }

  /* ---- lamps ---- */

  function buildRing() {
    for (var index = 0; index < LAMPS; index++) {
      var lamp = document.createElement("button");
      lamp.className = "lamp";
      lamp.type = "button";
      lamp.setAttribute("aria-pressed", "true");
      lamp.setAttribute("aria-label", "Lamp " + (index + 1));
      lamp.dataset.bit = String(index);
      lamp.addEventListener("click", toggleLamp);
      el.ring.appendChild(lamp);
    }
    paintLamps();
  }

  function toggleLamp(event) {
    var bit = Number(event.currentTarget.dataset.bit);
    /* eslint-disable no-bitwise */
    state.mask = state.mask ^ (1 << bit);
    /* eslint-enable no-bitwise */
    paintLamps();
  }

  function paintLamps() {
    var colourName = colourVariable(state.colour);
    var lamps = el.ring.querySelectorAll(".lamp");
    for (var index = 0; index < lamps.length; index++) {
      var on = Boolean(state.mask & (1 << index));
      lamps[index].setAttribute("aria-pressed", on ? "true" : "false");
      lamps[index].style.setProperty("--lamp", "var(" + colourName + ")");
    }
    el.bytes.textContent = "L " + hex(state.mask) + " · R " + hex(state.colour);
  }

  function colourVariable(value) {
    if (value === 0x40) { return "--green"; }
    if (value === 0x20) { return "--blue"; }
    if (value === 0x60) { return "--yellow"; }
    return "--red";
  }

  function hex(value) {
    var text = value.toString(16).toUpperCase();
    return "0x" + (text.length < 2 ? "0" + text : text);
  }

  Array.prototype.forEach.call(
    document.querySelectorAll(".swatch"), function (swatch) {
      swatch.addEventListener("click", function () {
        state.colour = Number(swatch.dataset.colour);
        Array.prototype.forEach.call(
          document.querySelectorAll(".swatch"), function (other) {
            other.setAttribute("aria-checked",
              other === swatch ? "true" : "false");
          });
        paintLamps();
      });
    });

  Array.prototype.forEach.call(
    document.querySelectorAll(".patterns .chip"), function (chip) {
      chip.addEventListener("click", function () {
        state.mask = Number(chip.dataset.mask);
        paintLamps();
      });
    });

  el.send.addEventListener("click", function () {
    send({ left: state.mask, right: state.colour });
  });

  Array.prototype.forEach.call(
    document.querySelectorAll(".effects .chip"), function (chip) {
      chip.addEventListener("click", function () {
        runScene(chip.dataset.scene);
      });
    });

  function send(body) {
    el.send.disabled = true;
    request("/api/stagekit/command",
      { method: "POST", body: JSON.stringify(body) })
      .then(function () { note("Sent " + hex(body.left) + " to the lamps"); })
      .catch(function (error) { note(error.message); })
      .then(function () { el.send.disabled = false; });
  }

  function runScene(name) {
    request("/api/stagekit/scene",
      { method: "POST", body: JSON.stringify({ name: name }) })
      .then(function () { note("Running " + name.replace(/_/g, " ")); })
      .catch(function (error) { note(error.message); });
  }

  function note(message) {
    el.lightsHint.textContent = message;
  }

  /* ---- live state ---- */

  function connect() {
    if (source) { source.close(); }
    setLink("connecting", "Connecting");
    source = new EventSource(withToken("/api/events"));
    source.addEventListener("open", function () {
      setLink("live", "Live");
      if (el.gate && el.gate.open) { el.gate.close(); }
    });
    source.addEventListener("state", function (event) {
      try { render(JSON.parse(event.data)); }
      catch (error) { /* a malformed frame must not stop the stream */ }
    });
    source.addEventListener("error", function () {
      /* EventSource retries on its own; say so rather than looking frozen. */
      setLink("lost", "Reconnecting");
      if (!token) { probeForAuth(); }
    });
  }

  function probeForAuth() {
    fetch("/api/state").then(function (response) {
      if (response.status === 401) { askForToken(false); }
    }).catch(function () { /* offline; the stream will retry */ });
  }

  function setLink(kind, text) {
    el.pulse.dataset.state = kind;
    el.link.textContent = text;
  }

  function render(snapshot) {
    state.version = snapshot.version;
    renderNow(snapshot);
    renderDevices(snapshot.devices || {});
    renderLog(snapshot.log || []);
  }

  function renderNow(snapshot) {
    var game = snapshot.game || {};
    var console_ = snapshot.console || {};
    var playing = game.state === 1;

    if (!console_.address) {
      el.gameState.textContent = "Waiting for the console";
    } else if (playing) {
      el.gameState.textContent = "Playing";
    } else {
      el.gameState.textContent = console_.available
        ? "In the menus" : "Console went quiet";
    }

    el.song.textContent = game.song || "—";
    el.artist.textContent = game.artist || "";

    var facts = [];
    if (game.venue) { facts.push(["Venue", game.venue]); }
    if (game.score) { facts.push(["Score", game.score.toLocaleString()]); }
    if (game.stars) { facts.push(["Stars", String(game.stars)]); }
    if (console_.address) { facts.push(["Console", console_.address]); }
    el.facts.innerHTML = "";
    facts.forEach(function (pair) {
      var wrap = document.createElement("div");
      var term = document.createElement("dt");
      var value = document.createElement("dd");
      term.textContent = pair[0];
      value.textContent = pair[1];
      wrap.appendChild(term);
      wrap.appendChild(value);
      el.facts.appendChild(wrap);
    });
  }

  function renderDevices(devices) {
    var addresses = Object.keys(devices).sort();
    el.devices.innerHTML = "";
    el.rigHint.textContent = addresses.length
      ? addresses.length + (addresses.length === 1 ? " lamp unit" : " lamp units")
      : "No lamps have reported in yet.";

    addresses.forEach(function (address) {
      var device = devices[address];
      var row = document.createElement("li");
      row.className = "device";
      row.dataset.available = device.available ? "true" : "false";

      var name = document.createElement("div");
      var title = document.createElement("div");
      title.className = "device-name";
      title.textContent = device.name || address;
      var where = document.createElement("div");
      where.className = "device-where";
      where.textContent = address + (device.available ? "" : " — offline");
      name.appendChild(title);
      name.appendChild(where);

      row.appendChild(name);
      row.appendChild(signalBars(device.wifi_signal));

      var usb = document.createElement("div");
      usb.className = "usb";
      usb.dataset.connected =
        device.usb_status === "Connected" ? "true" : "false";
      usb.textContent = device.usb_status === "Connected"
        ? "Stage Kit attached" : "No Stage Kit";
      row.appendChild(usb);

      el.devices.appendChild(row);
    });
  }

  function signalBars(rssi) {
    /* Wi-Fi RSSI in dBm: -50 is strong, -80 is barely there. */
    var strength = 0;
    if (typeof rssi === "number") {
      if (rssi > -60) { strength = 4; }
      else if (rssi > -70) { strength = 3; }
      else if (rssi > -80) { strength = 2; }
      else { strength = 1; }
    }
    var wrap = document.createElement("div");
    wrap.className = "signal";
    wrap.title = typeof rssi === "number" ? rssi + " dBm" : "No signal reading";
    for (var index = 1; index <= 4; index++) {
      var bar = document.createElement("i");
      bar.dataset.on = index <= strength ? "true" : "false";
      wrap.appendChild(bar);
    }
    return wrap;
  }

  function renderLog(entries) {
    el.log.innerHTML = "";
    entries.slice(-60).forEach(function (entry) {
      var row = document.createElement("li");
      row.dataset.level = entry.level || "info";
      var when = document.createElement("time");
      when.textContent = clock(entry.time);
      var text = document.createElement("span");
      text.textContent = entry.message;
      row.appendChild(when);
      row.appendChild(text);
      el.log.appendChild(row);
    });
  }

  function clock(seconds) {
    if (typeof seconds !== "number") { return ""; }
    var when = new Date(seconds * 1000);
    return when.toTimeString().slice(0, 8);
  }

  buildRing();
  connect();
}());
