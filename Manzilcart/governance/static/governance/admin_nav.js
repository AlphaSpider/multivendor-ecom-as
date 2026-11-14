/*
 * ManzilCart admin shell.
 *
 * Keeps the navigation panel permanently docked on desktop and turns ordinary
 * admin link clicks into in-page navigation: the sidebar, header and theme stay
 * put while only the content region (plus breadcrumbs/title/nav state) swaps.
 * Anything unexpected falls back to a normal browser navigation.
 */
(function () {
  "use strict";

  var DESKTOP_QUERY = "(min-width: 1025px)";
  var loadedScripts = new Set();
  var controller = null;
  var token = 0;
  var progressEl = null;

  function isDesktop() {
    return window.matchMedia(DESKTOP_QUERY).matches;
  }

  function setLoading(loading) {
    document.documentElement.classList.toggle("mc-nav-loading", loading);
  }

  function ensureProgressBar() {
    if (progressEl || !document.body) return;
    progressEl = document.createElement("div");
    progressEl.id = "mc-nav-progress";
    progressEl.setAttribute("aria-hidden", "true");
    document.body.appendChild(progressEl);
  }

  function rememberLoadedScripts() {
    document.querySelectorAll("script[src]").forEach(function (script) {
      loadedScripts.add(new URL(script.src, location.href).href);
    });
  }

  function forceDockedSidebar() {
    var main = document.getElementById("main");
    var sidebar = document.getElementById("nav-sidebar");
    if (!main) return;
    if (isDesktop()) {
      main.classList.add("shifted");
      document.body.classList.remove("mc-sidebar-open");
      if (sidebar) sidebar.setAttribute("aria-expanded", "true");
    }
  }

  /* ---------------------------------------------------------------------
   * Link eligibility
   * ------------------------------------------------------------------ */

  function isAdminPage(url) {
    return (
      url.origin === location.origin &&
      (url.pathname === "/admin" || url.pathname.indexOf("/admin/") === 0)
    );
  }

  function isExcludedUrl(url) {
    if (!isAdminPage(url)) return true;
    if (url.searchParams.has("_popup") || url.searchParams.has("_changelist_filters")) {
      return true;
    }
    if (/\/(login|logout|jsi18n|autocomplete|password_change_done)\/?$/.test(url.pathname)) {
      return true;
    }
    return false;
  }

  function isNavigableLink(link, event) {
    if (!link || !link.getAttribute("href")) return false;
    if (event.defaultPrevented) return false;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return false;
    }
    if (link.target && link.target !== "_self") return false;
    if (link.hasAttribute("download")) return false;
    if (link.dataset && link.dataset.mcNoSpa !== undefined) return false;
    if (link.closest("[data-mc-no-spa]")) return false;
    if (link.closest("#logout-form, .mc-sidebar-logout-form")) return false;

    var raw = link.getAttribute("href");
    if (!raw || raw.charAt(0) === "#") return false;
    if (/^(mailto|tel|javascript):/i.test(raw)) return false;

    var url = new URL(link.href, location.href);
    if (isExcludedUrl(url)) return false;

    // Same document with only a fragment change: let the browser scroll.
    if (
      url.origin === location.origin &&
      url.pathname === location.pathname &&
      url.search === location.search &&
      url.hash
    ) {
      return false;
    }

    return true;
  }

  /* ---------------------------------------------------------------------
   * Fetching and asset loading
   * ------------------------------------------------------------------ */

  function ensureStyles(doc, pageUrl) {
    var installed = new Set(
      Array.from(document.querySelectorAll('link[rel="stylesheet"][href]')).map(function (link) {
        return link.href;
      })
    );

    doc.querySelectorAll('link[rel="stylesheet"][href]').forEach(function (link) {
      var href = new URL(link.getAttribute("href"), pageUrl).href;
      if (installed.has(href)) return;
      installed.add(href);
      var clone = document.createElement("link");
      clone.rel = "stylesheet";
      clone.href = href;
      document.head.appendChild(clone);
    });
  }

  function ensureScripts(doc, pageUrl) {
    var pending = [];

    doc.querySelectorAll("script[src]").forEach(function (script) {
      var src = new URL(script.getAttribute("src"), pageUrl).href;
      if (loadedScripts.has(src)) return;
      loadedScripts.add(src);

      var clone = document.createElement("script");
      clone.src = src;
      clone.async = false;
      if (script.type) clone.type = script.type;
      if (script.hasAttribute("defer")) clone.defer = true;
      pending.push(
        new Promise(function (resolve) {
          clone.addEventListener("load", resolve, { once: true });
          clone.addEventListener("error", resolve, { once: true });
        })
      );
      document.head.appendChild(clone);
    });

    return Promise.all(pending);
  }

  /* ---------------------------------------------------------------------
   * Content swap
   * ------------------------------------------------------------------ */

  function findAnchor(scope, href, selector) {
    var anchors = scope.querySelectorAll(selector || "a[href]");
    for (var i = 0; i < anchors.length; i += 1) {
      if (new URL(anchors[i].getAttribute("href"), location.href).href === href) {
        return anchors[i];
      }
    }
    return null;
  }

  function updateNavigationState(nextDoc, pageUrl) {
    var currentSidebar = document.getElementById("nav-sidebar");
    var nextSidebar = nextDoc.getElementById("nav-sidebar");

    if (currentSidebar && nextSidebar) {
      currentSidebar.querySelectorAll(".current-app").forEach(function (el) {
        el.classList.remove("current-app");
      });
      currentSidebar.querySelectorAll(".current-model").forEach(function (el) {
        el.classList.remove("current-model");
      });
      currentSidebar.querySelectorAll('a[aria-current="page"]').forEach(function (el) {
        el.removeAttribute("aria-current");
      });

      // Model rows are the most specific active state.
      nextSidebar.querySelectorAll(".current-model").forEach(function (row) {
        var source = row.querySelector("a[href]");
        if (!source) return;
        var match = findAnchor(currentSidebar, new URL(source.getAttribute("href"), pageUrl).href);
        if (!match) return;
        var currentRow = match.closest("tr");
        if (currentRow) currentRow.classList.add("current-model");
        match.setAttribute("aria-current", "page");
      });

      // Then the containing app section.
      nextSidebar.querySelectorAll(".current-app").forEach(function (module) {
        var source = module.querySelector("a.section[href]");
        if (!source) return;
        var match = findAnchor(
          currentSidebar,
          new URL(source.getAttribute("href"), pageUrl).href,
          "a.section[href]"
        );
        var currentModule = match && match.closest(".module");
        if (currentModule) currentModule.classList.add("current-app");
      });
    }

    // The header pills carry live badge counts, so refresh them wholesale.
    var currentNav = document.querySelector("#header .governance-nav");
    var nextNav = nextDoc.querySelector("#header .governance-nav");
    if (currentNav && nextNav) currentNav.innerHTML = nextNav.innerHTML;

    // Breadcrumbs live outside #content-start.
    var currentCrumbs = document.querySelector("#container > nav[aria-label='Breadcrumbs']");
    var nextCrumbs = nextDoc.querySelector("#container > nav[aria-label='Breadcrumbs']");
    if (currentCrumbs && nextCrumbs) {
      currentCrumbs.innerHTML = nextCrumbs.innerHTML;
    } else if (currentCrumbs && !nextCrumbs) {
      currentCrumbs.remove();
    } else if (!currentCrumbs && nextCrumbs) {
      var main = document.getElementById("main");
      if (main && main.parentNode) main.parentNode.insertBefore(nextCrumbs, main);
    }
  }

  function runInlineScripts(container, pageUrl) {
    var scripts = Array.from(container.querySelectorAll("script"));

    scripts.forEach(function (oldScript) {
      var type = (oldScript.getAttribute("type") || "").toLowerCase();
      if (type && type !== "module" && type.indexOf("javascript") === -1) return;

      var clone = document.createElement("script");
      Array.from(oldScript.attributes).forEach(function (attribute) {
        clone.setAttribute(attribute.name, attribute.value);
      });

      if (oldScript.src) {
        var src = new URL(oldScript.getAttribute("src"), pageUrl).href;
        if (loadedScripts.has(src)) {
          oldScript.remove();
          return;
        }
        loadedScripts.add(src);
        clone.src = src;
        clone.async = false;
      } else {
        clone.textContent = oldScript.textContent;
      }

      oldScript.replaceWith(clone);
    });
  }

  function swapContent(nextDoc, pageUrl, options) {
    // Let page scripts stop timers/listeners before their DOM disappears.
    document.dispatchEvent(new CustomEvent("mc:page-exit"));

    var currentContent = document.getElementById("content-start");
    var nextContent = nextDoc.getElementById("content-start");
    if (!currentContent || !nextContent) throw new Error("Missing content region");

    var keepSidebarOpen = document.body.classList.contains("mc-sidebar-open");
    document.body.className = nextDoc.body.className;
    if (keepSidebarOpen && !isDesktop()) {
      document.body.classList.add("mc-sidebar-open");
    }

    currentContent.innerHTML = nextContent.innerHTML;
    runInlineScripts(currentContent, pageUrl);
    updateNavigationState(nextDoc, pageUrl);

    document.title = nextDoc.title;

    if (options && typeof options.scrollY === "number") {
      window.scrollTo(0, options.scrollY);
    } else if (pageUrl.hash) {
      var target = document.querySelector(pageUrl.hash);
      if (target) target.scrollIntoView();
      else window.scrollTo(0, 0);
    } else {
      window.scrollTo(0, 0);
    }

    forceDockedSidebar();
  }

  function navigate(urlString, options) {
    options = options || {};
    var url = new URL(urlString, location.href);

    if (controller) controller.abort();
    controller = new AbortController();
    var requestToken = ++token;
    var signal = controller.signal;

    setLoading(true);

    fetch(url.href, {
      signal: signal,
      credentials: "same-origin",
      headers: {
        Accept: "text/html",
        "X-Requested-With": "XMLHttpRequest",
      },
    })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        var contentType = response.headers.get("content-type") || "";
        if (contentType.indexOf("text/html") === -1) {
          window.location.assign(url.href);
          return null;
        }
        return response.text();
      })
      .then(function (html) {
        if (html === null || signal.aborted || requestToken !== token) return;

        var nextDoc = new DOMParser().parseFromString(html, "text/html");
        if (!nextDoc.getElementById("content-start") || nextDoc.body.classList.contains("login")) {
          window.location.assign(url.href);
          return;
        }

        return ensureScripts(nextDoc, url.href).then(function () {
          if (signal.aborted || requestToken !== token) return;

          if (options.push !== false) {
            try {
              history.replaceState(
                Object.assign({}, history.state, { mcSpa: true, scrollY: window.scrollY }),
                "",
                location.href
              );
            } catch (error) {
              /* History state is a progressive enhancement. */
            }
            history.pushState({ mcSpa: true, scrollY: 0 }, "", url.href);
          }

          ensureStyles(nextDoc, url.href);
          swapContent(nextDoc, url.href, options);
        });
      })
      .catch(function (error) {
        if (error && error.name === "AbortError") return;
        window.location.assign(url.href);
      })
      .finally(function () {
        if (requestToken === token) setLoading(false);
      });
  }

  /* ---------------------------------------------------------------------
   * Wiring
   * ------------------------------------------------------------------ */

  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[href]");
    if (!isNavigableLink(link, event)) return;
    event.preventDefault();
    navigate(link.href, { push: true });
  });

  window.addEventListener("popstate", function (event) {
    if (!event.state || !event.state.mcSpa) return;
    navigate(location.href, {
      push: false,
      scrollY: typeof event.state.scrollY === "number" ? event.state.scrollY : 0,
    });
  });

  window.addEventListener("resize", forceDockedSidebar);

  document.addEventListener("DOMContentLoaded", function () {
    ensureProgressBar();
    rememberLoadedScripts();
    forceDockedSidebar();

    try {
      history.replaceState(
        Object.assign({}, history.state, { mcSpa: true }),
        "",
        location.href
      );
    } catch (error) {
      /* No History API state support: navigation still works. */
    }
  });
})();
