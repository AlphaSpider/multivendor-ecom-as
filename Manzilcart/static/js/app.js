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
      nav_sell_plus: "Sell+",
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
      uae_country: "United Arab Emirates.",
      detail_category: "Category",
      detail_area: "Area",
      detail_emirate: "Emirate",
      detail_listed: "Listed",
      member_since: "Member since",
      edit_listing: "Edit listing",
      view_auction: "View auction",
      create_auction: "Create auction",
      all_my_listings: "All my listings",
      msg_seller: "Message the seller",
      chat_instructions: "Ask questions, then agree a price in chat.",
      make_offer: "Or make an offer (AED)",
      btn_make_offer: "Make offer",
      handover_note: "There is no online payment. You agree handover and payment directly.",
      sign_in_to_message: "Sign in to message the seller",
      chat_verified_only: "Chat is available to verified members.",
      stay_safe: "Stay safe.",
      safety_text: "View the furniture before paying. ManzilCart does not process, hold or guarantee the purchase price — payment is agreed directly between buyer and seller.",
      about_piece: "About this piece",
      hover_to_zoom: "Hover to zoom",
      no_photos_yet: "No photos yet",
      status_reserved: "Reserved",
      status_sold: "Sold",
      filters_button: "Filters",
      filter_modal_title: "Filters",
      filter_search_label: "Search",
      filter_category_label: "Category",
      filter_all_categories: "All categories",
      filter_condition_label: "Condition",
      filter_any_condition: "Any condition",
      filter_price_label: "Price range",
      filter_min_label: "Minimum",
      filter_max_label: "Maximum",
      filter_sort_label: "Sort",
      filter_apply_btn: "Apply filters",
      filter_reset_btn: "Reset filters",
      filter_clear_all: "Reset all",
      filter_active_filters: "Active filters:",
      all_furniture: "All furniture",
      items_available: "items available",
      no_matching_filters: "Nothing matches those filters",
      empty_filters_help: "Try widening your price range or choosing another category.",
      price_under_500: "Under AED 500",
      price_500_1500: "AED 500 – 1.5k",
      price_1500_4000: "AED 1.5k – 4k",
      price_4000_plus: "AED 4,000+",
      price_any: "Any price"
    },
    ar: {
      skip_to_content: "الانتقال إلى المحتوى",
      switch_emirate: "تغيير الإمارة",
      btn_switch: "تبديل",
      nav_browse: "تصفح",
      nav_sell: "بيع",
      nav_sell_plus: "بيع+",
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
      uae_country: "الإمارات العربية المتحدة.",
      detail_category: "الفئة",
      detail_area: "المنطقة",
      detail_emirate: "الإمارة",
      detail_listed: "تاريخ العرض",
      member_since: "عضو منذ",
      edit_listing: "تعديل الإعلان",
      view_auction: "عرض المزاد",
      create_auction: "إنشاء مزاد",
      all_my_listings: "جميع إعلاناتي",
      msg_seller: "راسل البائع",
      chat_instructions: "اسأل واستفسر، ثم اتفق على السعر في المحادثة.",
      make_offer: "أو قدم عرضاً (بالدرهم)",
      btn_make_offer: "تقديم العرض",
      handover_note: "لا يوجد دفع إلكتروني. يتم الاتفاق على التسليم والدفع مباشرة.",
      sign_in_to_message: "سجل الدخول لمراسلة البائع",
      chat_verified_only: "المحادثة متاحة للأعضاء الموثقين فقط.",
      stay_safe: "ابقَ آمناً.",
      safety_text: "عاين الأثاث قبل الدفع. لا تعالج منزل كارت سعر الشراء أو تحتجزه أو تضمنه — يتم الاتفاق على الدفع مباشرة بين المشتري والبائع.",
      about_piece: "حول هذه القطعة",
      hover_to_zoom: "مرر للتكبير",
      no_photos_yet: "لا توجد صور بعد",
      status_reserved: "محجوز",
      status_sold: "تم البيع",
      filters_button: "عوامل التصفية",
      filter_modal_title: "تصفية الإعلانات",
      filter_search_label: "البحث",
      filter_category_label: "الفئة",
      filter_all_categories: "جميع الفئات",
      filter_condition_label: "الحالة",
      filter_any_condition: "أي حالة",
      filter_price_label: "نطاق السعر",
      filter_min_label: "الحد الأدنى",
      filter_max_label: "الحد الأقصى",
      filter_sort_label: "الترتيب",
      filter_apply_btn: "تطبيق التصفية",
      filter_reset_btn: "إعادة ضبط",
      filter_clear_all: "مسح الكل",
      filter_active_filters: "عوامل التصفية النشطة:",
      all_furniture: "جميع الأثاث",
      items_available: "قطعة متوفرة",
      no_matching_filters: "لا توجد نتائج تطابق عوامل التصفية",
      empty_filters_help: "جرّب توسيع نطاق السعر أو اختيار فئة أخرى.",
      price_under_500: "أقل من 500 درهم",
      price_500_1500: "500 – 1,500 درهم",
      price_1500_4000: "1,500 – 4,000 درهم",
      price_4000_plus: "+4,000 درهم",
      price_any: "أي سعر"
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

    if (window._syncPriceSlider) {
      window._syncPriceSlider();
    }
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

  // --- Product Detail Interactive Gallery (Zoom on Hover & Thumbnails) ----
  var gallery = document.querySelector("[data-product-gallery]");
  if (gallery) {
    var viewport = gallery.querySelector("[data-zoom-viewport]");
    var mainImg = gallery.querySelector("[data-main-image]");
    var thumbs = Array.from(gallery.querySelectorAll("[data-thumb-btn]"));
    var prevBtn = gallery.querySelector("[data-gallery-prev]");
    var nextBtn = gallery.querySelector("[data-gallery-next]");
    var currentIndex = 0;

    function selectImage(index) {
      if (!thumbs.length || index < 0 || index >= thumbs.length) return;
      currentIndex = index;

      thumbs.forEach(function (thumb, idx) {
        var isActive = idx === currentIndex;
        thumb.classList.toggle("is-active", isActive);
        thumb.setAttribute("aria-selected", isActive ? "true" : "false");
      });

      var selectedThumb = thumbs[currentIndex];
      var fullSrc = selectedThumb.getAttribute("data-full-src");
      var caption = selectedThumb.getAttribute("data-caption");

      if (mainImg) {
        mainImg.style.opacity = "0.7";
        setTimeout(function () {
          mainImg.src = fullSrc;
          if (caption) {
            mainImg.alt = caption;
          }
          mainImg.style.opacity = "1";
          mainImg.style.transform = "scale(1)";
          mainImg.style.transformOrigin = "center center";
        }, 80);
      }

      try {
        selectedThumb.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
      } catch (err) {}
    }

    thumbs.forEach(function (thumb, idx) {
      thumb.addEventListener("click", function () {
        selectImage(idx);
      });
    });

    if (prevBtn) {
      prevBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        var prevIdx = (currentIndex - 1 + thumbs.length) % thumbs.length;
        selectImage(prevIdx);
      });
    }

    if (nextBtn) {
      nextBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        var nextIdx = (currentIndex + 1) % thumbs.length;
        selectImage(nextIdx);
      });
    }

    if (viewport) {
      viewport.addEventListener("keydown", function (e) {
        if (e.key === "ArrowLeft") {
          var isRtl = document.documentElement.getAttribute("dir") === "rtl";
          var targetIdx = isRtl ? (currentIndex + 1) % thumbs.length : (currentIndex - 1 + thumbs.length) % thumbs.length;
          selectImage(targetIdx);
        } else if (e.key === "ArrowRight") {
          var isRtl = document.documentElement.getAttribute("dir") === "rtl";
          var targetIdx = isRtl ? (currentIndex - 1 + thumbs.length) % thumbs.length : (currentIndex + 1) % thumbs.length;
          selectImage(targetIdx);
        }
      });

      // Cursor Zoom in on Hover
      viewport.addEventListener("mousemove", function (e) {
        if (!mainImg) return;
        var rect = viewport.getBoundingClientRect();
        var x = e.clientX - rect.left;
        var y = e.clientY - rect.top;

        var xPercent = Math.max(0, Math.min(100, (x / rect.width) * 100));
        var yPercent = Math.max(0, Math.min(100, (y / rect.height) * 100));

        mainImg.style.transformOrigin = xPercent + "% " + yPercent + "%";
        mainImg.style.transform = "scale(2.2)";
      });

      viewport.addEventListener("mouseleave", function () {
        if (!mainImg) return;
        mainImg.style.transform = "scale(1)";
        mainImg.style.transformOrigin = "center center";
      });
    }
  }

  // --- Filter Modal (Popup) ------------------------------------------------
  var filterModal = document.getElementById("filter-modal");
  var openFilterBtns = document.querySelectorAll("[data-filter-open]");
  var closeFilterBtns = document.querySelectorAll("[data-filter-close]");

  function openFilterModal() {
    if (!filterModal) return;
    filterModal.style.display = "flex";
    filterModal.offsetHeight; // force reflow
    filterModal.classList.add("is-open");
    filterModal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    openFilterBtns.forEach(function (btn) {
      btn.setAttribute("aria-expanded", "true");
    });
    // Re-sync slider track fill in case modal was hidden when initialized
    if (window._syncPriceSlider) {
      window._syncPriceSlider();
    }
    // Focus first interactive control in modal (category select or close button)
    var catInput = filterModal.querySelector("#filter-category") || filterModal.querySelector(".filter-modal-close");
    if (catInput) {
      setTimeout(function () {
        catInput.focus();
      }, 100);
    }
  }

  function closeFilterModal() {
    if (!filterModal) return;
    filterModal.classList.remove("is-open");
    filterModal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    openFilterBtns.forEach(function (btn) {
      btn.setAttribute("aria-expanded", "false");
    });
    setTimeout(function () {
      if (!filterModal.classList.contains("is-open")) {
        filterModal.style.display = "none";
      }
    }, 200);
  }

  openFilterBtns.forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      openFilterModal();
    });
  });

  closeFilterBtns.forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      closeFilterModal();
    });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && filterModal && filterModal.classList.contains("is-open")) {
      closeFilterModal();
    }
  });

  // --- Dual-Handle Price Range Slider -------------------------------------
  var sliderWidget = document.querySelector("[data-price-slider-widget]");
  if (sliderWidget) {
    var rangeMin = sliderWidget.querySelector("[data-slider-min]");
    var rangeMax = sliderWidget.querySelector("[data-slider-max]");
    var inputMin = sliderWidget.querySelector("[data-input-min]");
    var inputMax = sliderWidget.querySelector("[data-input-max]");
    var displayMin = sliderWidget.querySelector("[data-slider-min-val]");
    var displayMax = sliderWidget.querySelector("[data-slider-max-val]");
    var highlight = sliderWidget.querySelector("[data-slider-highlight]");
    var presetChips = sliderWidget.querySelectorAll("[data-preset-min]");

    var SLIDER_MIN = 0;
    var SLIDER_MAX = 10000;
    var MIN_GAP = 50;

    function formatNumber(num) {
      return Number(num).toLocaleString("en-US");
    }

    function updateHighlightAndReadout() {
      var minVal = parseFloat(rangeMin.value) || 0;
      var maxVal = parseFloat(rangeMax.value) || SLIDER_MAX;

      if (displayMin) {
        displayMin.textContent = formatNumber(minVal);
      }
      if (displayMax) {
        var isMaxCapped = maxVal >= SLIDER_MAX && (!inputMax || !inputMax.value || parseFloat(inputMax.value) >= SLIDER_MAX);
        displayMax.textContent = formatNumber(maxVal) + (isMaxCapped ? "+" : "");
      }

      var percentMin = Math.max(0, Math.min(100, ((minVal - SLIDER_MIN) / (SLIDER_MAX - SLIDER_MIN)) * 100));
      var percentMax = Math.max(0, Math.min(100, ((maxVal - SLIDER_MIN) / (SLIDER_MAX - SLIDER_MIN)) * 100));
      var isRtl = document.documentElement.getAttribute("dir") === "rtl";

      if (highlight) {
        var width = Math.max(0, percentMax - percentMin);
        highlight.style.width = width + "%";
        if (isRtl) {
          highlight.style.left = "auto";
          highlight.style.right = percentMin + "%";
        } else {
          highlight.style.right = "auto";
          highlight.style.left = percentMin + "%";
        }
      }

      // Check active preset
      presetChips.forEach(function (chip) {
        var pMin = chip.getAttribute("data-preset-min");
        var pMax = chip.getAttribute("data-preset-max");
        var isActive = false;
        var curMinEmpty = !inputMin || !inputMin.value || inputMin.value === "0";
        var curMaxEmpty = !inputMax || !inputMax.value;

        if (pMin === "" && pMax === "") {
          isActive = curMinEmpty && curMaxEmpty;
        } else if (pMin === "4000" && pMax === "10000") {
          isActive = minVal >= 4000 && curMaxEmpty;
        } else if (pMin !== "" && pMax !== "") {
          isActive = (minVal === parseFloat(pMin) && maxVal === parseFloat(pMax) && !curMaxEmpty);
        }
        chip.classList.toggle("is-active", isActive);
      });
    }

    window._syncPriceSlider = updateHighlightAndReadout;

    // Range input min drag
    if (rangeMin) {
      rangeMin.addEventListener("input", function () {
        var minVal = parseFloat(rangeMin.value);
        var maxVal = parseFloat(rangeMax.value);
        if (minVal > maxVal - MIN_GAP) {
          rangeMin.value = maxVal - MIN_GAP;
          minVal = parseFloat(rangeMin.value);
        }
        if (inputMin) {
          inputMin.value = minVal === 0 ? "" : minVal;
        }
        updateHighlightAndReadout();
      });
    }

    // Range input max drag
    if (rangeMax) {
      rangeMax.addEventListener("input", function () {
        var minVal = parseFloat(rangeMin.value);
        var maxVal = parseFloat(rangeMax.value);
        if (maxVal < minVal + MIN_GAP) {
          rangeMax.value = minVal + MIN_GAP;
          maxVal = parseFloat(rangeMax.value);
        }
        if (inputMax) {
          inputMax.value = maxVal >= SLIDER_MAX ? "" : maxVal;
        }
        updateHighlightAndReadout();
      });
    }

    // Manual number input min
    if (inputMin) {
      inputMin.addEventListener("input", function () {
        var val = parseFloat(inputMin.value);
        if (isNaN(val) || val < SLIDER_MIN) val = SLIDER_MIN;
        if (val > SLIDER_MAX) val = SLIDER_MAX;
        rangeMin.value = val;
        var maxVal = parseFloat(rangeMax.value);
        if (val > maxVal) {
          rangeMax.value = Math.min(SLIDER_MAX, val + MIN_GAP);
          if (inputMax && inputMax.value && parseFloat(inputMax.value) < val) {
            inputMax.value = rangeMax.value;
          }
        }
        updateHighlightAndReadout();
      });
    }

    // Manual number input max
    if (inputMax) {
      inputMax.addEventListener("input", function () {
        var val = parseFloat(inputMax.value);
        if (isNaN(val)) val = SLIDER_MAX;
        if (val < SLIDER_MIN) val = SLIDER_MIN;
        if (val > SLIDER_MAX) val = SLIDER_MAX;
        rangeMax.value = val;
        var minVal = parseFloat(rangeMin.value);
        if (val < minVal) {
          rangeMin.value = Math.max(SLIDER_MIN, val - MIN_GAP);
          if (inputMin && inputMin.value && parseFloat(inputMin.value) > val) {
            inputMin.value = rangeMin.value;
          }
        }
        updateHighlightAndReadout();
      });
    }

    // Preset chips
    presetChips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        var pMin = chip.getAttribute("data-preset-min");
        var pMax = chip.getAttribute("data-preset-max");

        if (pMin === "" && pMax === "") {
          rangeMin.value = 0;
          rangeMax.value = SLIDER_MAX;
          if (inputMin) inputMin.value = "";
          if (inputMax) inputMax.value = "";
        } else if (pMin === "4000" && pMax === "10000") {
          rangeMin.value = 4000;
          rangeMax.value = SLIDER_MAX;
          if (inputMin) inputMin.value = "4000";
          if (inputMax) inputMax.value = "";
        } else {
          var minVal = parseFloat(pMin) || 0;
          var maxVal = parseFloat(pMax) || SLIDER_MAX;
          rangeMin.value = minVal;
          rangeMax.value = maxVal;
          if (inputMin) inputMin.value = minVal === 0 ? "" : minVal;
          if (inputMax) inputMax.value = maxVal;
        }
        updateHighlightAndReadout();
      });
    });

    // Initialize from existing input values if any
    if (inputMin && inputMin.value) {
      rangeMin.value = Math.min(SLIDER_MAX, Math.max(SLIDER_MIN, parseFloat(inputMin.value) || 0));
    }
    if (inputMax && inputMax.value) {
      rangeMax.value = Math.min(SLIDER_MAX, Math.max(SLIDER_MIN, parseFloat(inputMax.value) || SLIDER_MAX));
    }
    updateHighlightAndReadout();
  }
})();

