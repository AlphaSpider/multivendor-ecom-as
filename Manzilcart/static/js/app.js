/*
 * ManzilCart — progressive enhancement, theme toggle & bilingual (EN/AR) features.
 * Nothing here is a security control: every rule is also enforced server side.
 */
(function () {
  "use strict";

  // --- Dark / Light Theme Toggle ------------------------------------------
  var themeToggle = document.querySelector("[data-theme-toggle]");

  function getPreferredTheme() {
    var stored = localStorage.getItem("theme");
    if (stored === "dark" || stored === "light") {
      return stored;
    }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
    if (themeToggle) {
      var isDark = theme === "dark";
      var label = isDark ? "Switch to light mode" : "Switch to dark mode";
      themeToggle.setAttribute("aria-label", label);
      themeToggle.setAttribute("title", label);
    }
  }

  // Initialize theme
  var currentTheme = getPreferredTheme();
  applyTheme(currentTheme);

  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      var nextTheme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      applyTheme(nextTheme);
    });
  }

  // --- English / Arabic Bilingual Feature ---------------------------------
  var i18n = {
    en: {
      skip_to_content: "Skip to content",
      switch_emirate: "Switch emirate",
      btn_switch: "Switch",
      nav_browse: "Browse",
      nav_sell: "Sell",
      nav_auctions: "Auctions",
      nav_messages: "Messages",
      nav_deals: "Deals",
      nav_my_account: "My account",
      nav_sign_out: "Sign out",
      nav_sign_in: "Sign in",
      nav_join: "Join ManzilCart",
      hero_eyebrow: "Furniture marketplace · UAE",
      hero_title: "Give good furniture<br>a second home.",
      hero_lede: "Buy and sell new and refurbished furniture with people in your own community. Choose your emirate to see what is available near you.",
      picker_title: "Where are you looking?",
      picker_subtitle: "You can switch at any time. We never guess your location.",
      no_marketplaces_title: "No marketplaces are open right now",
      no_marketplaces_desc: "We are preparing our launch. Please check back soon.",
      card_marketplace: "Marketplace",
      card_continue: "Continue where you left off",
      enter_emirate: "Enter",
      neighbourhoods: "neighbourhoods",
      remembered_note_prefix: "Last time you were browsing",
      val_moderated_title: "Moderated listings",
      val_moderated_desc: "Every item is reviewed before it appears, so what you see is real furniture from real people.",
      val_direct_title: "Talk directly",
      val_direct_desc: "Message the seller, ask questions and agree a price without leaving the platform.",
      val_local_title: "Local by design",
      val_local_desc: "Your marketplace is your emirate. Optional delivery and assembly help can be requested with a deal.",
      invalid_emirate_alert: "That marketplace is not currently available. Please select an active emirate below.",
      marketplace_label: "Marketplace",
      market_hero_title: "Furniture looking for<br>its next home.",
      market_hero_lede: "New and refurbished pieces from sellers across the emirate. Every listing is reviewed before it goes live.",
      search_furniture: "Search furniture",
      search_placeholder: "Search sofas, desks, beds…",
      btn_search: "Search",
      browse_category: "Browse by category",
      category_tagline: "Find the piece that finishes the room.",
      all_listings: "All listings",
      categories_setting_up: "Categories are being set up.",
      fresh_in: "Fresh in",
      fresh_tagline: "The newest approved listings.",
      no_listings_title: "No listings yet",
      no_listings_desc: "Be the first to list a piece of furniture in this emirate.",
      sell_an_item: "Sell an item",
      join_to_sell: "Join to sell",
      selling_in: "Selling in",
      operate_neighbourhoods_prefix: "We operate in",
      operate_neighbourhoods_suffix: "neighbourhoods here.",
      list_an_item: "List an item",
      no_active_neighbourhoods: "No neighbourhoods are currently active.",
      footer_brand_desc: "New and refurbished furniture, from homes in the UAE to homes in the UAE.",
      footer_marketplaces: "Marketplaces",
      footer_no_marketplaces: "No marketplaces are currently open.",
      footer_account: "Account",
      footer_my_auctions: "My auctions",
      footer_profile: "Profile",
      footer_create_account: "Create an account",
      footer_legal_p1: "Payments for furniture are arranged directly between buyer and seller. ManzilCart does not process or hold the purchase price.",
      uae_country: "United Arab Emirates."
    },
    ar: {
      skip_to_content: "الانتقال إلى المحتوى",
      switch_emirate: "تغيير الإمارة",
      btn_switch: "تبديل",
      nav_browse: "تصفح",
      nav_sell: "بيع",
      nav_auctions: "المزادات",
      nav_messages: "الرسائل",
      nav_deals: "الصفقات",
      nav_my_account: "حسابي",
      nav_sign_out: "تسجيل الخروج",
      nav_sign_in: "تسجيل الدخول",
      nav_join: "انضم إلى منزل كارت",
      hero_eyebrow: "سوق الأثاث · الإمارات",
      hero_title: "امنح الأثاث الجيد<br>بيتاً ثانياً.",
      hero_lede: "بيع واشترِ أثاثاً جديداً ومجدداً مع سكان مجتمعك. اختر إمارتك لمعرفة ما هو متوفر بالقرب منك.",
      picker_title: "أين تبحث؟",
      picker_subtitle: "يمكنك التغيير في أي وقت. نحن لا نخمن موقعك أبداً.",
      no_marketplaces_title: "لا توجد أسواق مفتوحة حالياً",
      no_marketplaces_desc: "نحن نجهز لإطلاقنا. يُرجى التحقق قريباً.",
      card_marketplace: "السوق",
      card_continue: "تابع من حيث توقفت",
      enter_emirate: "دخول",
      neighbourhoods: "أحياء",
      remembered_note_prefix: "في المرة الأخيرة كنت تتصفح",
      val_moderated_title: "إعلانات مفحوصة ومراجعة",
      val_moderated_desc: "تتم مراجعة كل قطعة قبل ظهورها، لتضمن أثاثاً حقيقياً من أشخاص حقيقيين.",
      val_direct_title: "تواصل مباشرة",
      val_direct_desc: "راسل البائع واستفسر وتفاوض على السعر دون مغادرة المنصة.",
      val_local_title: "محلي بطبيعته",
      val_local_desc: "سوقك هو إمارتك. يمكنك طلب المساعدة في التوصيل والتركيب مع كل صفقة.",
      invalid_emirate_alert: "هذا السوق غير متوفر حالياً. يُرجى اختيار إمارة نشطة أدناه.",
      marketplace_label: "السوق",
      market_hero_title: "أثاث يبحث عن<br>بيته القادم.",
      market_hero_lede: "قطع جديدة ومجددة من بائعين في جميع أنحاء الإمارة. يتم فحص كل إعلان قبل نشره.",
      search_furniture: "ابحث عن أثاث",
      search_placeholder: "ابحث عن أرائك، مكاتب، أسِرّة…",
      btn_search: "بحث",
      browse_category: "تصفح حسب الفئة",
      category_tagline: "اعثر على القطعة التي تكمل غرفتك.",
      all_listings: "جميع الإعلانات",
      categories_setting_up: "يتم إعداد الفئات حالياً.",
      fresh_in: "جديد في",
      fresh_tagline: "أحدث الإعلانات المعتمدة.",
      no_listings_title: "لا توجد إعلانات بعد",
      no_listings_desc: "كن أول من يعلن عن قطعة أثاث في هذه الإمارة.",
      sell_an_item: "بيع قطعة",
      join_to_sell: "انضم للبيع",
      selling_in: "البيع في",
      operate_neighbourhoods_prefix: "نعمل في",
      operate_neighbourhoods_suffix: "حياً هنا.",
      list_an_item: "أضف إعلاناً",
      no_active_neighbourhoods: "لا توجد أحياء نشطة حالياً.",
      footer_brand_desc: "أثاث جديد ومجدد، من بيوت الإمارات إلى بيوت الإمارات.",
      footer_marketplaces: "الأسواق",
      footer_no_marketplaces: "لا توجد أسواق مفتوحة حالياً.",
      footer_account: "الحساب",
      footer_my_auctions: "مزاداتي",
      footer_profile: "الملف الشخصي",
      footer_create_account: "إنشاء حساب",
      footer_legal_p1: "يتم ترتيب الدفع للأثاث مباشرة بين المشتري والبائع. لا تقوم منزل كارت بمعالجة أو احتجاز سعر الشراء.",
      uae_country: "الإمارات العربية المتحدة."
    }
  };

  var emirateNames = {
    "Abu Dhabi": "أبوظبي",
    "Ras Al Khaimah": "رأس الخيمة",
    "Sharjah": "الشارقة",
    "Dubai": "دبي",
    "Ajman": "عجمان",
    "Umm Al Quwain": "أم القيوين",
    "Fujairah": "الفجيرة"
  };

  var langToggle = document.querySelector("[data-lang-toggle]");
  var langDisplay = document.querySelector("[data-lang-display]");

  function applyLanguage(lang) {
    var isArabic = lang === "ar";
    document.documentElement.setAttribute("lang", isArabic ? "ar" : "en");
    document.documentElement.setAttribute("dir", isArabic ? "rtl" : "ltr");
    localStorage.setItem("lang", isArabic ? "ar" : "en");

    if (langDisplay) {
      langDisplay.textContent = isArabic ? "English" : "العربية";
    }
    if (langToggle) {
      var nextLabel = isArabic ? "Switch to English" : "تغيير اللغة إلى العربية";
      langToggle.setAttribute("aria-label", nextLabel);
      langToggle.setAttribute("title", nextLabel);
    }

    // Translate all [data-i18n] elements
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      if (el._origEnContent === undefined) {
        el._origEnContent = el.innerHTML;
      }
      if (isArabic) {
        if (key === "enter_emirate") {
          var emName = el.getAttribute("data-emirate") || "";
          var arEm = emirateNames[emName] || emName;
          el.textContent = (i18n.ar.enter_emirate || "دخول") + " " + arEm;
        } else if (i18n.ar[key]) {
          el.innerHTML = i18n.ar[key];
        }
      } else {
        el.innerHTML = el._origEnContent;
      }
    });

    // Translate emirate names in [data-emirate-name]
    document.querySelectorAll("[data-emirate-name]").forEach(function (el) {
      if (el._origEmirateName === undefined) {
        el._origEmirateName = el.getAttribute("data-emirate-name") || el.textContent.trim();
      }
      if (isArabic) {
        el.textContent = emirateNames[el._origEmirateName] || el._origEmirateName;
      } else {
        el.textContent = el._origEmirateName;
      }
    });

    // Translate placeholder attributes in [data-placeholder-i18n]
    document.querySelectorAll("[data-placeholder-i18n]").forEach(function (input) {
      var key = input.getAttribute("data-placeholder-i18n");
      if (input._origPlaceholder === undefined) {
        input._origPlaceholder = input.placeholder;
      }
      if (isArabic && i18n.ar[key]) {
        input.placeholder = i18n.ar[key];
      } else {
        input.placeholder = input._origPlaceholder;
      }
    });
  }

  // Initialize language from storage
  var savedLang = localStorage.getItem("lang") || "en";
  applyLanguage(savedLang);

  if (langToggle) {
    langToggle.addEventListener("click", function () {
      var currentLang = document.documentElement.getAttribute("lang") || "en";
      var nextLang = currentLang === "ar" ? "en" : "ar";
      applyLanguage(nextLang);
    });
  }

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
