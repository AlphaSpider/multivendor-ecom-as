/*
 * ManzilCart — progressive enhancement only.
 * Nothing here is a security control: every rule is also enforced server side.
 */
(function () {
  "use strict";

  // --- Mobile navigation ---------------------------------------------------
  var toggle = document.querySelector("[data-nav-toggle]");
  var nav = document.getElementById("primary-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // --- Emirate switcher: submit on change ---------------------------------
  document.querySelectorAll("select[data-autosubmit]").forEach(function (select) {
    select.addEventListener("change", function () {
      if (select.form) {
        select.form.submit();
      }
    });
  });

  // --- Dismissible messages ----------------------------------------------
  document.querySelectorAll("[data-dismiss]").forEach(function (button) {
    button.addEventListener("click", function () {
      var message = button.closest("[data-dismissible]");
      if (message) {
        message.remove();
      }
    });
  });

  // --- OTP input: digits only --------------------------------------------
  document.querySelectorAll("input.code-input").forEach(function (input) {
    input.addEventListener("input", function () {
      var cleaned = input.value.replace(/\D+/g, "");
      if (cleaned !== input.value) {
        input.value = cleaned;
      }
    });
  });

  // --- OTP expiry countdown ----------------------------------------------
  var expiry = document.querySelector("[data-code-expiry]");
  if (expiry) {
    var seconds = parseInt(expiry.getAttribute("data-seconds"), 10) || 0;
    var label = expiry.querySelector("[data-expiry-label]");
    var tick = function () {
      if (seconds <= 0) {
        if (label) {
          label.textContent = "0";
        }
        expiry.textContent = "That code has expired. Please request a new one.";
        clearInterval(timer);
        return;
      }
      if (label) {
        label.textContent = String(seconds);
      }
      seconds -= 1;
    };
    var timer = setInterval(tick, 1000);
    tick();
  }

  // --- Listing image preview ---------------------------------------------
  document.querySelectorAll("input[type=file][data-image-input]").forEach(function (input) {
    input.addEventListener("change", function () {
      var preview = document.querySelector(input.getAttribute("data-preview-target"));
      if (!preview || !input.files || !input.files[0]) {
        return;
      }
      var reader = new FileReader();
      reader.onload = function (event) {
        preview.innerHTML = "";
        var img = document.createElement("img");
        img.src = event.target.result;
        img.alt = "Selected image preview";
        preview.appendChild(img);
        preview.hidden = false;
      };
      reader.readAsDataURL(input.files[0]);
    });
  });
})();
