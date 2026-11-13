/**
 * Place search without Google Maps.
 *
 * The stock Data Commons place boxes ("Enter a place" in Map / Timelines / Scatter, the place page search,
 * the search page) are built on Google Places Autocomplete and need a real Google Maps key. Without one they
 * show errors and find no places. This file provides the small part of the google.maps.places API those
 * boxes use (Autocomplete, AutocompleteService, PlacesService, PlacesServiceStatus), answered from this
 * site's own place index (places.json: World, continents, sub-regions, countries). Suggestions are drawn in
 * the same .pac-container / .pac-item markup as Google's, so the stock styles and code keep working.
 *
 * Place ids are "dcid:<dcid>"; custom.py makes /api/place/placeid2dcid return the dcid for them.
 * Loaded from templates/base.html only when no real Google Maps key is configured (LOCAL_PLACE_SEARCH);
 * custom.py then also drops the Google Maps script tag from pages.
 */
(function () {
  "use strict";
  var INDEX_URL = "/custom_dc/custom/places.json";
  var MAX_RESULTS = 6;
  var PREFIX = "dcid:";

  var indexPromise = null;
  function loadIndex() {
    if (!indexPromise) {
      indexPromise = fetch(INDEX_URL)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          return data.places.map(function (p) {
            p._keys = [p.name].concat(p.alt || []).map(norm);
            return p;
          });
        })
        .catch(function () { return []; });
    }
    return indexPromise;
  }

  function norm(s) {
    return String(s || "")
      .normalize("NFD").replace(/[̀-ͯ]/g, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  }

  // Lower is better: exact name, then name prefix, then a word prefix, then anywhere in the name.
  function score(p, q) {
    var best = 99;
    p._keys.forEach(function (k, i) {
      var alt = i > 0 ? 0.5 : 0;
      var s = 99;
      if (k === q) s = 0;
      else if (k.indexOf(q) === 0) s = 1;
      else if ((" " + k).indexOf(" " + q) >= 0) s = 2;
      else if (q.length >= 3 && k.indexOf(q) >= 0) s = 3;
      if (s + alt < best) best = s + alt;
    });
    return best;
  }

  function search(places, input) {
    var q = norm(input);
    if (!q) return [];
    return places
      .map(function (p) { return { p: p, s: score(p, q) }; })
      .filter(function (x) { return x.s < 99; })
      .sort(function (a, b) { return a.s - b.s || (a.p.level || 0) - (b.p.level || 0) || a.p.name.length - b.p.name.length; })
      .slice(0, MAX_RESULTS)
      .map(function (x) { return x.p; });
  }

  // The place name only: the hierarchy (top-down, "World › Africa › Eastern Africa") is shown beside it.
  function description(p) { return p.name; }

  function prediction(p) {
    return {
      place_id: PREFIX + p.dcid,
      description: description(p),
      types: ["political"],
      structured_formatting: { main_text: p.name, secondary_text: p.parent },
      matched_substrings: [],
      terms: [{ value: p.name, offset: 0 }],
    };
  }

  var PlacesServiceStatus = { OK: "OK", ZERO_RESULTS: "ZERO_RESULTS", INVALID_REQUEST: "INVALID_REQUEST" };

  function AutocompleteService() {}
  AutocompleteService.prototype.getPlacePredictions = function (req, cb) {
    var p = loadIndex().then(function (places) {
      var found = search(places, req && req.input).map(prediction);
      var status = found.length ? PlacesServiceStatus.OK : PlacesServiceStatus.ZERO_RESULTS;
      if (cb) cb(found, status);
      return { predictions: found };
    });
    return p;
  };

  function PlacesService() {}
  PlacesService.prototype.findPlaceFromQuery = function (req, cb) {
    loadIndex().then(function (places) {
      var found = search(places, req && req.query).slice(0, 1).map(function (p) {
        return { place_id: PREFIX + p.dcid, name: p.name, types: ["political"] };
      });
      cb(found, found.length ? PlacesServiceStatus.OK : PlacesServiceStatus.ZERO_RESULTS);
    });
  };
  PlacesService.prototype.textSearch = PlacesService.prototype.findPlaceFromQuery;

  /** Autocomplete attached to an <input>, drawing Google-style suggestions under it. */
  function Autocomplete(input, opts) {
    this.input = input;
    this.listeners = [];
    this.place = {};
    this.items = [];
    this.active = -1;
    this.box = document.createElement("div");
    this.box.className = "pac-container pac-logo gdc-pac";
    this.box.style.display = "none";
    this.list = document.createElement("div");
    this.box.appendChild(this.list);
    document.body.appendChild(this.box);
    input.setAttribute("autocomplete", "off");

    var self = this;
    input.addEventListener("input", function () { self.update(); });
    input.addEventListener("focus", function () { if (input.value) self.update(); });
    input.addEventListener("blur", function () { setTimeout(function () { self.hide(); }, 150); });
    input.addEventListener("keydown", function (e) { self.onKey(e); });
    window.addEventListener("resize", function () { self.position(); });
    window.addEventListener("scroll", function () { self.position(); }, true);
    loadIndex();
  }

  Autocomplete.prototype.addListener = function (name, fn) {
    if (name === "place_changed") this.listeners.push(fn);
    var listeners = this.listeners;
    return { remove: function () { var i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1); } };
  };
  Autocomplete.prototype.getPlace = function () { return this.place; };
  Autocomplete.prototype.setComponentRestrictions = function () {};
  Autocomplete.prototype.setFields = function () {};
  Autocomplete.prototype.setTypes = function () {};
  Autocomplete.prototype.setOptions = function () {};
  Autocomplete.prototype.unbindAll = function () {};

  Autocomplete.prototype.update = function () {
    var self = this;
    var value = this.input.value;
    loadIndex().then(function (places) {
      if (self.input.value !== value) return; // a newer keystroke is on its way
      self.items = search(places, value);
      self.active = -1;
      self.render();
    });
  };

  Autocomplete.prototype.render = function () {
    var self = this;
    // The stock place bar may draw its own result into the last .pac-container; keep our list attached.
    if (this.list.parentNode !== this.box) this.box.appendChild(this.list);
    this.list.innerHTML = "";
    if (!this.items.length) {
      if (this.input.value.trim().length >= 2) {
        var none = document.createElement("div");
        none.className = "pac-item gdc-pac-empty";
        none.textContent = "No matching country or region in this Data Commons";
        this.list.appendChild(none);
        this.show();
      } else {
        this.hide();
      }
      return;
    }
    this.items.forEach(function (p, i) {
      var row = document.createElement("div");
      row.className = "pac-item" + (i === self.active ? " pac-item-selected" : "");
      var icon = document.createElement("span");
      icon.className = "pac-icon pac-icon-marker";
      var main = document.createElement("span");
      main.className = "pac-item-query";
      var matched = document.createElement("span");
      matched.className = "pac-matched";
      matched.textContent = p.name;
      main.appendChild(matched);
      row.appendChild(icon);
      row.appendChild(main);
      var sub = document.createElement("span");
      sub.textContent = p.parent ? p.parent : p.type;
      row.appendChild(sub);
      row.addEventListener("mousedown", function (e) { e.preventDefault(); self.select(i); });
      row.addEventListener("mouseenter", function () { self.active = i; self.highlight(); });
      self.list.appendChild(row);
    });
    this.show();
  };

  Autocomplete.prototype.highlight = function () {
    var rows = this.list.children;
    for (var i = 0; i < rows.length; i++) {
      rows[i].classList.toggle("pac-item-selected", i === this.active);
    }
  };

  Autocomplete.prototype.onKey = function (e) {
    var open = this.box.style.display !== "none" && this.items.length;
    if (e.key === "ArrowDown" && open) {
      this.active = (this.active + 1) % this.items.length; this.highlight(); e.preventDefault();
    } else if (e.key === "ArrowUp" && open) {
      this.active = (this.active - 1 + this.items.length) % this.items.length; this.highlight(); e.preventDefault();
    } else if (e.key === "Enter") {
      if (open) {
        e.preventDefault();
        this.select(this.active >= 0 ? this.active : 0);
      } else {
        this.place = { name: this.input.value };
        this.fire();
      }
    } else if (e.key === "Escape") {
      this.hide();
    }
  };

  Autocomplete.prototype.select = function (i) {
    var p = this.items[i];
    if (!p) return;
    this.input.value = p.name;
    this.place = { place_id: PREFIX + p.dcid, name: p.name, types: ["political"] };
    this.hide();
    this.fire();
  };

  Autocomplete.prototype.fire = function () {
    this.listeners.slice().forEach(function (fn) { fn(); });
  };

  Autocomplete.prototype.position = function () {
    if (this.box.style.display === "none") return;
    var r = this.input.getBoundingClientRect();
    var host = this.input.closest("#location-field, .search-box-section, form, .input-group") || this.input;
    var hr = host.getBoundingClientRect();
    this.box.style.left = window.scrollX + hr.left + "px";
    this.box.style.top = window.scrollY + r.bottom + 2 + "px";
    this.box.style.width = Math.max(hr.width, 260) + "px";
  };

  Autocomplete.prototype.show = function () {
    this.box.style.display = "block";
    this.position();
  };
  Autocomplete.prototype.hide = function () { this.box.style.display = "none"; };

  var g = (window.google = window.google || {});
  g.maps = g.maps || {};
  g.maps.places = {
    Autocomplete: Autocomplete,
    AutocompleteService: AutocompleteService,
    PlacesService: PlacesService,
    PlacesServiceStatus: PlacesServiceStatus,
  };
  g.maps.event = g.maps.event || {
    clearInstanceListeners: function () {},
    addListener: function (obj, name, fn) { return obj.addListener ? obj.addListener(name, fn) : null; },
  };
  g.maps.__gdcLocalPlaces = true;
})();
