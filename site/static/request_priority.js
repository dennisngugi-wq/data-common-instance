(function () {
  "use strict";
  var MAX_BULK = 4;
  var PRIORITY = ["/api/autocomplete", "/api/variable/info", "/api/place/placeid2dcid"];

  function isBulk(url) {
    try {
      var u = new URL(url, window.location.href);
      if (u.origin !== window.location.origin || u.pathname.indexOf("/api/") !== 0) return false;
      for (var i = 0; i < PRIORITY.length; i++) {
        if (u.pathname.indexOf(PRIORITY[i]) === 0) return false;
      }
      return true;
    } catch (e) {
      return false;
    }
  }

  var active = 0;
  var queue = [];

  function done() {
    active = Math.max(0, active - 1);
    pump();
  }

  function pump() {
    while (active < MAX_BULK && queue.length) {
      active++;
      queue.shift()();
    }
  }

  window.__dcpRequestQueue = function () { return { active: active, queued: queue.length, max: MAX_BULK }; };

  function enqueue(start) {
    if (active < MAX_BULK) {
      active++;
      start();
    } else {
      queue.push(start);
    }
  }

  var XHR = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
  if (XHR) {
    var open = XHR.open, send = XHR.send, abort = XHR.abort;
    XHR.open = function (method, url) {
      this.__dcpBulk = isBulk(String(url));
      return open.apply(this, arguments);
    };
    XHR.send = function () {
      var xhr = this, args = arguments;
      if (!xhr.__dcpBulk) return send.apply(xhr, args);
      var start = function () {
        xhr.__dcpStart = null;
        var finished = false;
        xhr.addEventListener("loadend", function () {
          if (!finished) { finished = true; done(); }
        });
        try {
          send.apply(xhr, args);
        } catch (e) {
          if (!finished) { finished = true; done(); }
          throw e;
        }
      };
      xhr.__dcpStart = start;
      enqueue(start);
    };
    XHR.abort = function () {
      if (this.__dcpStart) {
        var i = queue.indexOf(this.__dcpStart);
        if (i >= 0) queue.splice(i, 1);
        this.__dcpStart = null;
      }
      return abort.apply(this, arguments);
    };
  }

  function guardStore(store) {
    if (!store || store.__dcpGuarded || typeof store.setQueryString !== "function") return;
    var set = store.setQueryString;
    store.setQueryString = function (q) {
      var el = document.activeElement;
      if (el && el.name === "searchInput" && el.value && el.value !== q) return;
      return set.apply(this, arguments);
    };
    store.__dcpGuarded = true;
  }
  if (globalThis.queryStore) {
    guardStore(globalThis.queryStore);
  } else {
    var current;
    try {
      Object.defineProperty(globalThis, "queryStore", {
        configurable: true,
        enumerable: true,
        get: function () { return current; },
        set: function (v) { current = v; guardStore(v); }
      });
    } catch (e) {  }
  }

  if (window.fetch) {
    var origFetch = window.fetch;
    window.fetch = function (input, init) {
      var url = typeof input === "string" ? input : (input && input.url) || String(input);
      if (!isBulk(url)) return origFetch.apply(this, arguments);
      var self = this, args = arguments;
      var signal = (init && init.signal) || (input && input.signal);
      return new Promise(function (resolve, reject) {
        var start = function () {
          if (signal) signal.removeEventListener("abort", onAbort);
          var p;
          try {
            p = origFetch.apply(self, args);
          } catch (e) {
            done();
            reject(e);
            return;
          }
          p.then(function (r) { done(); resolve(r); }, function (e) { done(); reject(e); });
        };
        var onAbort = function () {
          var i = queue.indexOf(start);
          if (i >= 0) {
            queue.splice(i, 1);
            reject(new DOMException("The operation was aborted.", "AbortError"));
          }
        };
        if (signal) {
          if (signal.aborted) { reject(new DOMException("The operation was aborted.", "AbortError")); return; }
          signal.addEventListener("abort", onAbort);
        }
        enqueue(start);
      });
    };
  }
})();
