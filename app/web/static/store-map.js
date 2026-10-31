/* Ubicación de la tienda en un mapa (opcional), desde cualquier lugar.
   - Lee la dirección colombiana ("Cl. 23 #4-50" = sobre la Calle 23, a 50 m de la Carrera 4) y calcula
     dónde se cruzan esas vías con su trazado en OpenStreetMap (Nominatim), dentro de la ciudad escrita.
     Las casas casi nunca tienen número en el mapa, pero las calles y carreras sí.
   - Si no está el cruce: la calle, el barrio o la ciudad, y el tendero arrastra el pin.
   - "Estoy en la tienda" usa la ubicación del celular.
   El mapa (Leaflet + OpenStreetMap, sin API key) se carga solo cuando se abre. */
(function () {
  "use strict";

  var LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/";
  var NOMINATIM = "https://nominatim.openstreetmap.org/search?format=json&countrycodes=co";
  // Nominatim pide máximo una consulta por segundo.
  var NOMINATIM_GAP_MS = 1100;
  var COLOMBIA = [4.6, -74.08];

  // Abreviaturas de las direcciones colombianas: [patrón, vía, vía que la cruza].
  var STREET_TYPES = [
    [/^(avenida\s+calle|av\.?\s*calle|ac)\b\.?/i, "Calle", "Carrera"],
    [/^(avenida\s+carrera|av\.?\s*carrera|ak)\b\.?/i, "Carrera", "Calle"],
    [/^(calle|clle|cll|cl)\b\.?/i, "Calle", "Carrera"],
    [/^(carrera|kra|cra|kr|cr|k)\b\.?/i, "Carrera", "Calle"],
    [/^(diagonal|diag|dg)\b\.?/i, "Diagonal", "Transversal"],
    [/^(transversal|transv|trans|tv|tr)\b\.?/i, "Transversal", "Diagonal"]
  ];

  /* "Cl. 23 #4-50" -> {street: "Calle", number: "23", cross: "Carrera", crossNumber: "4", meters: 50} */
  function parseAddress(text) {
    var value = (text || "").trim().replace(/\s+/g, " ");
    for (var i = 0; i < STREET_TYPES.length; i++) {
      var type = STREET_TYPES[i];
      var head = type[0].exec(value);
      if (!head) { continue; }
      var rest = value.slice(head[0].length);
      var match = /^\s*(\d+\s?[a-z]?)(?:\s*bis)?\s*(?:#|n[oº°]\.?|número|numero)?\s*(\d+\s?[a-z]?)(?:\s*bis)?\s*-\s*(\d+)/i.exec(rest);
      if (!match) { return null; }
      return {
        street: type[1],
        number: match[1].replace(/\s/g, "").toUpperCase(),
        cross: type[2],
        crossNumber: match[2].replace(/\s/g, "").toUpperCase(),
        meters: Number(match[3])
      };
    }
    return null;
  }

  // Nombre de vía en OpenStreetMap: "Calle 23", "Carrera 4A" o "Carrera 4 A" (sin importar mayúsculas).
  function nameRegex(type, number) {
    var parts = /^(\d+)([A-Z]?)$/.exec(number);
    return new RegExp("^" + type + " 0*" + parts[1] + (parts[2] ? " ?" + parts[2] : "") + "$", "i");
  }
  function nextNumber(number) { return String(Number(/^\d+/.exec(number)[0]) + 1); }

  var lastCall = 0;
  function nominatim(params) {
    var wait = Math.max(0, lastCall + NOMINATIM_GAP_MS - Date.now());
    lastCall = Date.now() + wait;
    return new Promise(function (resolve) { setTimeout(resolve, wait); })
      .then(function () { return fetch(NOMINATIM + "&" + params, { headers: { "Accept-Language": "es" } }); })
      .then(function (response) { return response.ok ? response.json() : []; })
      .catch(function () { return []; });
  }
  function first(params) {
    return nominatim("limit=1&" + params).then(function (results) { return results[0] || null; });
  }

  // Trazado de una vía dentro de la ciudad: lista de líneas [[lon, lat], ...] (las vías vienen en tramos).
  function streetLines(viewbox, city, type, number) {
    var name = nameRegex(type, number);
    return nominatim("limit=40&dedupe=0&polygon_geojson=1&bounded=1&viewbox=" + viewbox +
      "&q=" + encodeURIComponent(type + " " + number + ", " + city))
      .then(function (results) {
        var lines = [];
        results.forEach(function (result) {
          var shape = result.geojson || {};
          if (!name.test(result.name || "")) { return; }
          if (shape.type === "LineString") { lines.push(shape.coordinates); }
          if (shape.type === "MultiLineString") { lines = lines.concat(shape.coordinates); }
        });
        return lines;
      });
  }

  // Punto donde se cruzan dos vías (o null si en el mapa no se tocan).
  function crossing(linesA, linesB) {
    for (var a = 0; a < linesA.length; a++) {
      for (var b = 0; b < linesB.length; b++) {
        var la = linesA[a];
        var lb = linesB[b];
        for (var i = 0; i < la.length - 1; i++) {
          for (var j = 0; j < lb.length - 1; j++) {
            var p1 = la[i], p2 = la[i + 1], p3 = lb[j], p4 = lb[j + 1];
            var d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0]);
            if (!d) { continue; }
            var t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d;
            var u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d;
            if (t >= 0 && t <= 1 && u >= 0 && u <= 1) {
              return [p1[1] + t * (p2[1] - p1[1]), p1[0] + t * (p2[0] - p1[0])];  // [lat, lon]
            }
          }
        }
      }
    }
    return null;
  }

  // Avanza `meters` desde la esquina hacia la esquina siguiente (la placa "-50" son ~50 m).
  function along(from, to, meters) {
    var dLat = (to[0] - from[0]) * 111320;
    var dLon = (to[1] - from[1]) * 111320 * Math.cos(from[0] * Math.PI / 180);
    var distance = Math.sqrt(dLat * dLat + dLon * dLon);
    if (!distance) { return from; }
    var fraction = Math.min(meters / distance, 0.9);
    return [from[0] + (to[0] - from[0]) * fraction, from[1] + (to[1] - from[1]) * fraction];
  }

  var leafletReady = null;
  function loadLeaflet() {
    if (leafletReady) { return leafletReady; }
    leafletReady = new Promise(function (resolve, reject) {
      var css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = LEAFLET + "leaflet.min.css";
      document.head.appendChild(css);
      var script = document.createElement("script");
      script.src = LEAFLET + "leaflet.min.js";
      script.onload = function () { resolve(window.L); };
      script.onerror = reject;
      document.head.appendChild(script);
    });
    return leafletReady;
  }

  function initStoreMap(root) {
    var form = root.closest("form");
    var lat = root.querySelector('input[name="latitude"]');
    var lng = root.querySelector('input[name="longitude"]');
    var toggle = root.querySelector(".map-toggle");
    var status = root.querySelector(".map-status");
    var box = root.querySelector(".map-box");
    var mapEl = root.querySelector(".map");
    var searchButton = root.querySelector(".map-search");
    var hereButton = root.querySelector(".map-here");
    var clearButton = root.querySelector(".map-clear");
    var map = null;
    var pin = null;
    var searching = false;

    if (!navigator.geolocation) { hereButton.hidden = true; }

    function field(name) {
      var input = form.querySelector('[name="' + name + '"]');
      return input ? input.value.trim() : "";
    }

    function setPin(latlng, zoom) {
      if (!pin) {
        pin = window.L.marker(latlng, {
          draggable: true,
          autoPan: true,
          icon: window.L.divIcon({ className: "map-pin", html: "<span></span>", iconSize: [30, 42], iconAnchor: [15, 40] })
        }).addTo(map);
        pin.on("dragend", function () { save(pin.getLatLng()); });
      } else {
        pin.setLatLng(latlng);
      }
      // setView y no flyTo: el vuelo desde todo el país pide decenas de cuadros a OpenStreetMap.
      map.setView(latlng, zoom || Math.max(map.getZoom(), 17));
      save(window.L.latLng(latlng));
      clearButton.hidden = false;
    }

    function save(latlng) {
      lat.value = latlng.lat.toFixed(6);
      lng.value = latlng.lng.toFixed(6);
      status.textContent = "Ubicación marcada. Revisa que el pin esté en la puerta de tu tienda.";
    }

    function found(latlng, zoom, message) {
      setPin(latlng, zoom);
      status.textContent = message;
    }

    function search() {
      if (searching) { return; }
      var city = field("city");
      var neighborhood = field("neighborhood");
      var address = field("address");
      if (!city && !neighborhood && !address) {
        status.textContent = "Escribe primero la ciudad, el barrio y la dirección, o mueve el mapa hasta tu tienda.";
        return;
      }
      searching = true;
      status.textContent = "Buscando tu dirección…";
      var parsed = parseAddress(address);

      first("q=" + encodeURIComponent((city || neighborhood) + ", Colombia")).then(function (place) {
        if (!place) { return null; }
        var bb = place.boundingbox.map(Number);  // [sur, norte, oeste, este]
        return { center: [Number(place.lat), Number(place.lon)], viewbox: [bb[2], bb[1], bb[3], bb[0]].join(",") };
      }).then(function (area) {
        if (!area) {
          status.textContent = "No encontramos la ciudad. Mueve el mapa y toca donde está tu tienda.";
          return;
        }
        var bounded = "bounded=1&viewbox=" + area.viewbox + "&q=";
        var streetName = parsed ? parsed.street + " " + parsed.number : "";

        // 1) La cuadra: donde la calle cruza la carrera, y los metros de la placa hacia la carrera siguiente.
        var step = parsed
          ? streetLines(area.viewbox, city, parsed.street, parsed.number).then(function (main) {
              if (!main.length) { return false; }
              return streetLines(area.viewbox, city, parsed.cross, parsed.crossNumber).then(function (cross) {
                var start = crossing(main, cross);
                if (!start) { return false; }
                return streetLines(area.viewbox, city, parsed.cross, nextNumber(parsed.crossNumber)).then(function (next) {
                  var end = crossing(main, next);
                  found(end ? along(start, end, parsed.meters) : start, 18,
                    "Encontramos tu cuadra: " + streetName + " con " + parsed.cross + " " + parsed.crossNumber +
                    ". Si el pin no quedó en tu puerta, arrástralo.");
                  return true;
                });
              });
            })
          : Promise.resolve(false);

        return step.then(function (done) {
          // 2) La calle sola.
          if (done || !streetName) { return done; }
          return first(bounded + encodeURIComponent(streetName + ", " + city)).then(function (street) {
            if (!street) { return false; }
            found([Number(street.lat), Number(street.lon)], 17,
              "Encontramos la " + streetName + ", pero no la cuadra exacta. Arrastra el pin hasta tu tienda.");
            return true;
          });
        }).then(function (done) {
          // 3) El barrio.
          if (done || !neighborhood) { return done; }
          return first(bounded + encodeURIComponent(neighborhood + ", " + city)).then(function (place) {
            if (!place) { return false; }
            found([Number(place.lat), Number(place.lon)], 16, "Encontramos tu barrio. Arrastra el pin hasta tu tienda.");
            return true;
          });
        }).then(function (done) {
          // 4) La ciudad.
          if (!done) {
            found(area.center, 14, "Encontramos tu ciudad. Acerca el mapa y toca donde está tu tienda.");
          }
        });
      }).catch(function () {
        status.textContent = "No pudimos buscar la dirección. Mueve el mapa y toca donde está tu tienda.";
      }).then(function () { searching = false; });
    }

    function openMap() {
      box.hidden = false;
      toggle.textContent = "Ocultar mapa";
      if (map) { map.invalidateSize(); return; }
      status.textContent = "Cargando el mapa…";
      loadLeaflet().then(function (L) {
        map = L.map(mapEl, { scrollWheelZoom: false, zoomControl: true }).setView(COLOMBIA, 6);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
        }).addTo(map);
        map.on("click", function (event) { setPin(event.latlng); });
        if (lat.value && lng.value) {
          setPin([Number(lat.value), Number(lng.value)], 18);
        } else {
          search();
        }
      }).catch(function () {
        status.textContent = "No pudimos cargar el mapa. No pasa nada: la ubicación es opcional.";
      });
    }

    toggle.addEventListener("click", function () {
      if (box.hidden) {
        openMap();
      } else {
        box.hidden = true;
        toggle.textContent = "📍 " + (lat.value ? "Ver mi tienda en el mapa" : "Marcar mi tienda en el mapa");
      }
    });
    searchButton.addEventListener("click", search);
    hereButton.addEventListener("click", function () {
      status.textContent = "Buscando dónde estás…";
      navigator.geolocation.getCurrentPosition(function (position) {
        setPin([position.coords.latitude, position.coords.longitude], 18);
      }, function () {
        status.textContent = "No pudimos tomar tu ubicación. Busca tu dirección o toca el mapa.";
      }, { enableHighAccuracy: true, timeout: 15000 });
    });
    clearButton.addEventListener("click", function () {
      if (pin) { map.removeLayer(pin); pin = null; }
      lat.value = "";
      lng.value = "";
      clearButton.hidden = true;
      status.textContent = "Sin ubicación. Es opcional: tu distribuidor usará la dirección escrita.";
    });
    clearButton.hidden = !lat.value;
  }

  document.querySelectorAll("[data-store-map]").forEach(initStoreMap);
})();
