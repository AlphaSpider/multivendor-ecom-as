/*
 * Mobile conversation UI.
 *
 * - The Make Offer / Chat switcher shows or hides the offer sheet.
 * - Quick-amount chips fill the offer input.
 * - The offer helper line reacts to how close the amount is to the asking price.
 * - The kebab menu closes when tapping elsewhere.
 *
 * Everything here is progressive enhancement: without JavaScript the offer
 * sheet stays visible and all forms still submit normally.
 */
(function () {
  "use strict";

  var switchRoot = document.querySelector(".chat-mobile-switch");
  var offerPanel = document.getElementById("chat-mobile-offer");

  function setView(view) {
    if (!switchRoot || !offerPanel) return;
    switchRoot.querySelectorAll("[data-chat-view]").forEach(function (button) {
      button.setAttribute(
        "aria-pressed",
        button.dataset.chatView === view ? "true" : "false"
      );
    });
    offerPanel.hidden = view !== "offer";
  }

  if (switchRoot && offerPanel) {
    var defaultView = switchRoot.dataset.defaultView === "offer" ? "offer" : "chat";
    setView(defaultView);

    switchRoot.addEventListener("click", function (event) {
      var button = event.target.closest("[data-chat-view]");
      if (!button) return;
      setView(button.dataset.chatView);
    });
  }

  // Quick offer amounts.
  document.querySelectorAll(".chat-offer-chips").forEach(function (group) {
    group.addEventListener("click", function (event) {
      var chip = event.target.closest(".chat-offer-chip");
      if (!chip) return;

      var panel = group.closest(".chat-mobile-offer");
      var input = panel ? panel.querySelector(".chat-mobile-offer-input") : null;
      if (!input) return;

      input.value = chip.dataset.amount || "";
      group.querySelectorAll(".chat-offer-chip").forEach(function (item) {
        item.setAttribute("aria-pressed", item === chip ? "true" : "false");
      });
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
  });

  // Friendly helper text under the amount.
  document.querySelectorAll(".chat-mobile-offer-form").forEach(function (form) {
    var input = form.querySelector(".chat-mobile-offer-input");
    var hint = form.querySelector("[data-offer-hint]");
    var asking = parseFloat(form.dataset.askingPrice || "0");
    if (!input || !hint || !asking) return;

    function updateHint() {
      var value = parseFloat(input.value);
      if (!value || value <= 0) {
        hint.textContent = "Pick a quick amount or enter your own.";
        hint.dataset.tone = "neutral";
        return;
      }

      var ratio = value / asking;
      if (ratio >= 1) {
        hint.textContent = "Asking price offer — great chance of a quick reply.";
        hint.dataset.tone = "great";
      } else if (ratio >= 0.9) {
        hint.textContent = "Very good offer! High chances of seller's response.";
        hint.dataset.tone = "great";
      } else if (ratio >= 0.8) {
        hint.textContent = "Good offer. The seller may counter.";
        hint.dataset.tone = "fair";
      } else {
        hint.textContent = "Low offers may take longer to get a reply.";
        hint.dataset.tone = "low";
      }
    }

    input.addEventListener("input", updateHint);
    updateHint();
  });

  // Close the kebab menu when tapping outside it.
  document.querySelectorAll(".chat-mobile-menu").forEach(function (menu) {
    document.addEventListener("click", function (event) {
      if (menu.open && !menu.contains(event.target)) {
        menu.open = false;
      }
    });
  });
})();
