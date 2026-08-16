/* Altamira Rule Extractor -- mejoras de interfaz minimas (modernizacion
 * UI). Sin frameworks, sin CDN, sin eval. Todo lo esencial (subida,
 * navegacion, formularios, descarga, CSRF) funciona sin este archivo:
 * aqui solo se agregan mejoras de experiencia (arrastrar y soltar,
 * copiar al portapapeles, busqueda/orden client-side, menu movil,
 * prevencion de doble envio). Nunca usa innerHTML con datos del
 * servidor, nunca eval, nunca credenciales. */

(function () {
  "use strict";

  /* --------------------------- Menu movil --------------------------- */

  function initNavToggle() {
    var toggle = document.querySelector(".nav-toggle");
    var nav = document.getElementById("main-nav");
    if (!toggle || !nav) return;
    toggle.addEventListener("click", function () {
      var isOpen = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
  }

  /* ------------------------- Zona de subida -------------------------- */

  function formatBytes(bytes) {
    if (!bytes && bytes !== 0) return "";
    var units = ["B", "KB", "MB", "GB"];
    var value = bytes;
    var unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
      value = value / 1024;
      unitIndex += 1;
    }
    var rounded = unitIndex === 0 ? value.toString() : value.toFixed(1);
    return rounded + " " + units[unitIndex];
  }

  function initDropzone() {
    var dropzone = document.querySelector(".dropzone");
    var input = document.querySelector('.dropzone input[type="file"]');
    var summary = document.querySelector(".file-summary");
    var nameEl = document.querySelector(".file-summary-name");
    var sizeEl = document.querySelector(".file-summary-size");
    var submitBtn = document.querySelector(".upload-submit");
    var extensionWarning = document.querySelector(".file-extension-warning");
    if (!dropzone || !input) return;

    function updateFromFile(file) {
      if (!file) {
        if (summary) summary.classList.remove("is-visible");
        if (submitBtn) submitBtn.setAttribute("disabled", "disabled");
        return;
      }
      if (nameEl) nameEl.textContent = file.name;
      if (sizeEl) sizeEl.textContent = formatBytes(file.size);
      if (summary) summary.classList.add("is-visible");
      if (submitBtn) submitBtn.removeAttribute("disabled");
      var looksLikeZip = /\.zip$/i.test(file.name);
      if (extensionWarning) {
        extensionWarning.hidden = looksLikeZip;
      }
    }

    input.addEventListener("change", function () {
      updateFromFile(input.files && input.files[0]);
    });

    ["dragenter", "dragover"].forEach(function (eventName) {
      dropzone.addEventListener(eventName, function (event) {
        event.preventDefault();
        dropzone.classList.add("is-dragover");
      });
    });

    ["dragleave", "drop"].forEach(function (eventName) {
      dropzone.addEventListener(eventName, function (event) {
        event.preventDefault();
        dropzone.classList.remove("is-dragover");
      });
    });

    dropzone.addEventListener("drop", function (event) {
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length > 0) {
        input.files = files;
        updateFromFile(files[0]);
      }
    });

    var form = dropzone.closest("form");
    if (form) {
      form.addEventListener("submit", function () {
        if (submitBtn) {
          submitBtn.classList.add("is-loading");
          submitBtn.setAttribute("aria-disabled", "true");
        }
      });
    }

    // Mejora progresiva UNICAMENTE: sin JS, el boton nunca tiene
    // `disabled` (el atributo `required` del input ya impide el envio
    // vacio de forma nativa). Con JS, se deshabilita visualmente hasta
    // que se elija un archivo, y se rehabilita al elegirlo.
    updateFromFile(null);
  }

  /* ------------------- Prevencion de doble envio ---------------------- */

  function initSubmitOnce() {
    document.body.addEventListener("submit", function (event) {
      var form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (form.dataset.submitted === "true") {
        event.preventDefault();
        return;
      }
      form.dataset.submitted = "true";
      var submitControl = form.querySelector('button[type="submit"]');
      if (submitControl) {
        submitControl.classList.add("is-loading");
        submitControl.setAttribute("aria-disabled", "true");
      }
      window.setTimeout(function () {
        form.dataset.submitted = "false";
      }, 4000);
    });
  }

  /* --------------------- Copiar al portapapeles ------------------------ */

  function initCopyButtons() {
    document.body.addEventListener("click", function (event) {
      var button = event.target.closest(".copy-btn");
      if (!button) return;
      var targetSelector = button.getAttribute("data-copy-target");
      var text = "";
      if (targetSelector) {
        var target = document.querySelector(targetSelector);
        text = target ? target.textContent || "" : "";
      }
      if (!text) return;
      var restoreLabel = button.textContent;
      var finish = function (ok) {
        button.setAttribute("data-copied", ok ? "true" : "false");
        window.setTimeout(function () {
          button.removeAttribute("data-copied");
          button.textContent = restoreLabel;
        }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          function () { finish(true); },
          function () { finish(false); }
        );
      } else {
        finish(false);
      }
    });
  }

  /* ------------------- Busqueda/filtro client-side ---------------------- */

  function normalize(value) {
    return (value || "").toString().toLowerCase();
  }

  function initTableFilters() {
    var searchInputs = document.querySelectorAll("[data-table-search]");
    searchInputs.forEach(function (input) {
      var tableId = input.getAttribute("data-table-search");
      var table = document.getElementById(tableId);
      if (!table) return;
      var countEl = document.querySelector(
        '[data-table-count-for="' + tableId + '"]'
      );
      var applyFilter = function () {
        var query = normalize(input.value);
        var statusSelect = document.querySelector(
          '[data-table-status-filter="' + tableId + '"]'
        );
        var statusValue = statusSelect ? statusSelect.value : "";
        var rows = table.querySelectorAll("tbody tr");
        var visibleCount = 0;
        rows.forEach(function (row) {
          var haystack = normalize(row.getAttribute("data-search-text") || row.textContent);
          var matchesQuery = query === "" || haystack.indexOf(query) !== -1;
          var matchesStatus =
            statusValue === "" || row.getAttribute("data-status") === statusValue;
          var visible = matchesQuery && matchesStatus;
          row.hidden = !visible;
          if (visible) visibleCount += 1;
        });
        if (countEl) {
          countEl.textContent =
            visibleCount + " de " + rows.length + (rows.length === 1 ? " fila" : " filas");
        }
      };
      input.addEventListener("input", applyFilter);
      var statusSelect = document.querySelector(
        '[data-table-status-filter="' + tableId + '"]'
      );
      if (statusSelect) statusSelect.addEventListener("change", applyFilter);
      applyFilter();
    });
  }

  /* ---------------------------- Orden de tablas -------------------------- */

  function compareCells(a, b) {
    var na = parseFloat(a.replace(",", "."));
    var nb = parseFloat(b.replace(",", "."));
    if (!isNaN(na) && !isNaN(nb)) return na - nb;
    return a.localeCompare(b, "es");
  }

  function initSortableTables() {
    document.body.addEventListener("click", function (event) {
      var header = event.target.closest("th[data-sortable]");
      if (!header) return;
      var table = header.closest("table");
      if (!table) return;
      var tbody = table.querySelector("tbody");
      if (!tbody) return;
      var headers = Array.prototype.slice.call(
        table.querySelectorAll("th[data-sortable]")
      );
      var columnIndex = Array.prototype.indexOf.call(
        header.parentElement.children,
        header
      );
      var currentDirection = header.getAttribute("aria-sort");
      var nextDirection = currentDirection === "ascending" ? "descending" : "ascending";
      headers.forEach(function (h) { h.removeAttribute("aria-sort"); });
      header.setAttribute("aria-sort", nextDirection);

      var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
      rows.sort(function (rowA, rowB) {
        var cellA = rowA.children[columnIndex];
        var cellB = rowB.children[columnIndex];
        var textA = cellA ? cellA.textContent.trim() : "";
        var textB = cellB ? cellB.textContent.trim() : "";
        var result = compareCells(textA, textB);
        return nextDirection === "ascending" ? result : -result;
      });
      rows.forEach(function (row) { tbody.appendChild(row); });
    });
  }

  /* -------------------------- Compact table toggle ----------------------- */

  function initCompactToggle() {
    document.body.addEventListener("click", function (event) {
      var toggle = event.target.closest("[data-compact-toggle]");
      if (!toggle) return;
      var tableId = toggle.getAttribute("data-compact-toggle");
      var table = document.getElementById(tableId);
      if (!table) return;
      var isCompact = table.classList.toggle("compact");
      toggle.setAttribute("aria-pressed", isCompact ? "true" : "false");
    });
  }

  /* --------------------- Volver (historial del navegador) --------------- */

  function initHistoryBackLinks() {
    document.body.addEventListener("click", function (event) {
      var link = event.target.closest("[data-history-back]");
      if (!link) return;
      if (window.history.length > 1) {
        event.preventDefault();
        window.history.back();
      }
      // Sin historial util: se deja seguir el href normal (fallback seguro
      // ya presente en el propio enlace, p. ej. /ui/runs).
    });
  }

  /* --------------- Confirmacion de "Limpiar job" (Feature 5) ------------ */

  function initCleanJobDialogs() {
    document.body.addEventListener("click", function (event) {
      var trigger = event.target.closest("[data-clean-job-trigger]");
      if (trigger) {
        var dialogId = trigger.getAttribute("data-clean-job-trigger");
        var dialog = document.getElementById(dialogId);
        if (dialog && typeof dialog.showModal === "function") {
          dialog.showModal();
        }
        return;
      }
      var cancel = event.target.closest("[data-clean-job-cancel]");
      if (cancel) {
        var openDialog = cancel.closest("dialog");
        if (openDialog) openDialog.close();
      }
    });
  }

  /* ------------------------------ Inicio --------------------------------- */

  function init() {
    initNavToggle();
    initDropzone();
    initSubmitOnce();
    initCopyButtons();
    initTableFilters();
    initSortableTables();
    initCompactToggle();
    initHistoryBackLinks();
    initCleanJobDialogs();
  }

  init();

  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (event.target && event.target.querySelector) {
      initTableFilters();
    }
  });
})();
