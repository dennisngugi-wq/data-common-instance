// The map at the top of Knowledge Graph pages (/browser/<dcid>) without Google Maps.
//
// datacommons.org draws the place's boundary (or a pin at its latitude/longitude) on a Google Map in
// <div id="overview-map">. With no real MAPS_API_KEY the site runs with DISABLE_GOOGLE_MAPS=true, so the stock
// page leaves that div empty. This fills it with the same picture using Leaflet and OpenStreetMap tiles, from
// the same Knowledge Graph properties the stock page reads (geoJsonCoordinates, latitude, longitude).
// Loaded by templates/base.html on browser pages only, after Leaflet.
(function () {
  'use strict';

  // Simplified boundary first (smaller download), then the full one: the same order Data Commons place maps use.
  var GEOJSON_PROPS = ['geoJsonCoordinatesDP3', 'geoJsonCoordinatesDP2', 'geoJsonCoordinatesDP1', 'geoJsonCoordinates'];

  function propValue(dcid, prop) {
    var url = '/api/node/propvals/out?prop=' + encodeURIComponent(prop) + '&dcids=' + encodeURIComponent(dcid);
    return fetch(url)
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (data) {
        var vals = data && data[dcid];
        return vals && vals.length ? vals[0].value : null;
      })
      .catch(function () { return null; });
  }

  function firstGeoJson(dcid, i) {
    if (i >= GEOJSON_PROPS.length) return Promise.resolve(null);
    return propValue(dcid, GEOJSON_PROPS[i]).then(function (v) {
      if (v) {
        try { return JSON.parse(v); } catch (e) { /* malformed: try the next property */ }
      }
      return firstGeoJson(dcid, i + 1);
    });
  }

  function latLong(dcid) {
    if (dcid.indexOf('latLong/') === 0) return Promise.resolve(null);  // the stock page shows these inline
    return Promise.all([propValue(dcid, 'latitude'), propValue(dcid, 'longitude')]).then(function (v) {
      var lat = parseFloat(v[0]), lng = parseFloat(v[1]);
      return isFinite(lat) && isFinite(lng) ? [lat, lng] : null;
    });
  }

  function newMap(container) {
    var el = document.createElement('div');
    el.className = 'gdc-overview-map';
    container.appendChild(el);
    var map = L.map(el, { scrollWheelZoom: false, worldCopyJump: true });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    // Scroll-wheel zoom only after a click, so scrolling down the page is not captured by the map.
    map.on('click', function () { map.scrollWheelZoom.enable(); });
    map.on('mouseout', function () { map.scrollWheelZoom.disable(); });
    return map;
  }

  function draw(container, dcid) {
    if (container.dataset.gdcMap) return;
    container.dataset.gdcMap = '1';
    firstGeoJson(dcid, 0).then(function (geometry) {
      if (geometry) {
        var map = newMap(container);
        var layer = L.geoJSON(geometry, {
          style: { color: '#1a73e8', weight: 2, fillColor: '#1a73e8', fillOpacity: 0.15 },
        }).addTo(map);
        map.fitBounds(layer.getBounds(), { padding: [12, 12] });
        return;
      }
      return latLong(dcid).then(function (ll) {
        if (!ll) return;
        var map = newMap(container);
        L.circleMarker(ll, { radius: 7, color: '#1a73e8', fillOpacity: 0.8 }).addTo(map);
        map.setView(ll, 9);
      });
    });
  }

  function start() {
    var node = document.getElementById('node');
    var dcid = node && node.getAttribute('data-dcid');
    if (!dcid || typeof L === 'undefined') return;
    // The stock React page renders #overview-map after it has fetched the node; wait for it.
    var found = document.getElementById('overview-map');
    if (found) { draw(found, dcid); return; }
    var obs = new MutationObserver(function () {
      var el = document.getElementById('overview-map');
      if (el) { obs.disconnect(); draw(el, dcid); }
    });
    obs.observe(node, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
