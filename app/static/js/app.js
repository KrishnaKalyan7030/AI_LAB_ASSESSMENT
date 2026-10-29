// Small UI helpers. No secrets or API calls are made from the browser:
// every AI (Claude/Gemini) request happens on the Flask server.
(function () {
  "use strict";

  // Character counters: <textarea data-counter="#id">
  document.querySelectorAll("[data-counter]").forEach(function (el) {
    var target = document.querySelector(el.getAttribute("data-counter"));
    if (!target) return;
    var update = function () { target.textContent = el.value.length; };
    el.addEventListener("input", update);
    update();
  });

  // Insert 4 spaces on Tab in code editors.
  document.querySelectorAll(".code-editor").forEach(function (el) {
    el.addEventListener("keydown", function (e) {
      if (e.key !== "Tab" || e.shiftKey) return;
      e.preventDefault();
      var start = el.selectionStart, end = el.selectionEnd;
      el.value = el.value.substring(0, start) + "    " + el.value.substring(end);
      el.selectionStart = el.selectionEnd = start + 4;
    });
  });

  // Registration: show the teacher-code field only when "Teacher" is selected.
  var codeGroup = document.getElementById("teacher-code-group");
  if (codeGroup) {
    document.querySelectorAll('input[name="role"]').forEach(function (radio) {
      radio.addEventListener("change", function () {
        codeGroup.hidden = !document.getElementById("role-teacher").checked;
      });
    });
  }

  // Ask before destructive actions: <form data-confirm="Question?">
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  // Disable the submit button and show progress while slow requests run.
  document.querySelectorAll("form[data-busy-form]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("button[data-busy-text]");
      if (!btn) return;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>' +
        btn.getAttribute("data-busy-text");
    });
  });
})();
