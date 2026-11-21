/*
 * ManzilCart — authentication-page enhancements.
 *
 * Every check here is advisory only; the server enforces the same rules. The
 * script adds live password feedback, password visibility toggles, a resend
 * cooldown timer and light OTP input hygiene.
 */
(function () {
  "use strict";

  var started = false;
  var dynamicLabelRenderers = [];

  function isArabic() {
    return document.documentElement.getAttribute("lang") === "ar";
  }

  function text(strings) {
    return isArabic() ? strings.ar : strings.en;
  }

  function renderDynamicLabels() {
    dynamicLabelRenderers.forEach(function (render) {
      try {
        render();
      } catch (e) {
        // One broken enhancement must never stop the others.
      }
    });
  }

  function initPasswordToggles() {
    document.querySelectorAll("[data-password-toggle]").forEach(function (button) {
      var targetId = button.getAttribute("data-password-target");
      var input = targetId ? document.getElementById(targetId) : null;
      if (!input) return;

      var wrapper = button.closest(".input-with-action");
      if (wrapper) wrapper.classList.add("has-password-toggle");
      button.hidden = false;

      function render() {
        var showing = input.type === "text";
        button.textContent = text({
          en: showing ? "Hide" : "Show",
          ar: showing ? "إخفاء" : "إظهار",
        });
        button.setAttribute("aria-pressed", showing ? "true" : "false");
      }

      button.addEventListener("click", function () {
        input.type = input.type === "password" ? "text" : "password";
        render();
        input.focus();
      });

      dynamicLabelRenderers.push(render);
      render();
    });
  }

  function initPasswordRules() {
    document.querySelectorAll("[data-password-rules]").forEach(function (group) {
      var input = document.getElementById(group.getAttribute("data-password-input") || "");
      if (!input) return;
      var confirm = document.getElementById(
        group.getAttribute("data-password-confirm") || ""
      );

      var checks = {
        length: function (value) {
          return value.length >= 8;
        },
        uppercase: function (value) {
          return /[A-Z]/.test(value);
        },
        digit: function (value) {
          return /\d/.test(value);
        },
        special: function (value) {
          return /[^A-Za-z0-9]/.test(value);
        },
      };

      function mark(rule, met) {
        var item = group.querySelector('[data-rule="' + rule + '"]');
        if (item) item.classList.toggle("is-met", met);
      }

      function render() {
        var value = input.value || "";
        var allMet = true;
        Object.keys(checks).forEach(function (rule) {
          var met = checks[rule](value);
          mark(rule, met);
          if (!met) allMet = false;
        });

        if (confirm) {
          var matchMet = value.length > 0 && value === confirm.value;
          mark("match", matchMet);
        }

        group.setAttribute("data-complete", allMet ? "true" : "false");
        if (input.getAttribute("data-touched") === "true") {
          input.setAttribute("aria-invalid", allMet ? "false" : "true");
        }
      }

      function touch() {
        input.setAttribute("data-touched", "true");
        render();
      }

      input.addEventListener("input", render);
      input.addEventListener("blur", touch);
      if (confirm) {
        confirm.addEventListener("input", render);
        confirm.addEventListener("blur", touch);
      }
      if (input.form) input.form.addEventListener("submit", touch);

      dynamicLabelRenderers.push(render);
      render();
    });
  }

  function initResendCooldown() {
    var button = document.querySelector("[data-resend-button]");
    if (!button) return;

    var fallback = parseInt(button.getAttribute("data-cooldown") || "60", 10);
    if (isNaN(fallback) || fallback < 0) fallback = 60;
    var storageKey =
      "manzilcart:resend:" +
      (button.getAttribute("data-resend-key") || window.location.pathname);

    var expiresAt = 0;
    try {
      var stored = parseInt(sessionStorage.getItem(storageKey) || "0", 10);
      if (stored > Date.now()) expiresAt = stored;
      else sessionStorage.removeItem(storageKey);
    } catch (e) {
      // Storage may be disabled; the timer still works for this page load.
    }
    if (!expiresAt && fallback > 0) {
      expiresAt = Date.now() + fallback * 1000;
      try {
        sessionStorage.setItem(storageKey, String(expiresAt));
      } catch (e) {}
    }

    function remainingSeconds() {
      return Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
    }

    function render() {
      var remaining = remainingSeconds();
      if (remaining > 0) {
        button.disabled = true;
        button.textContent = text({
          en: "Send another code in " + remaining + "s",
          ar: "إرسال رمز جديد خلال " + remaining + " ث",
        });
      } else {
        button.disabled = false;
        button.textContent = text({
          en: "Send a new code",
          ar: "إرسال رمز جديد",
        });
        try {
          sessionStorage.removeItem(storageKey);
        } catch (e) {}
      }
    }

    function loop() {
      render();
      if (remainingSeconds() > 0) window.setTimeout(loop, 250);
    }

    dynamicLabelRenderers.push(render);
    loop();
  }

  function initOtpInput() {
    document.querySelectorAll(".code-input").forEach(function (input) {
      input.addEventListener("input", function () {
        var max = parseInt(input.getAttribute("maxlength") || "6", 10);
        var clean = (input.value || "").replace(/[^0-9]/g, "").slice(0, max);
        if (input.value !== clean) input.value = clean;
      });
    });
  }

  function init() {
    if (started) return;
    started = true;
    initPasswordToggles();
    initPasswordRules();
    initResendCooldown();
    initOtpInput();

    if (window.MutationObserver) {
      new MutationObserver(renderDynamicLabels).observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["lang"],
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
