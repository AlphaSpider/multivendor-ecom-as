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
var emirateNames = {
  "Abu Dhabi": "أبوظبي",
  "Dubai": "دبي",
  "Sharjah": "الشارقة",
  "Ajman": "عجمان",
  "Umm Al Quwain": "أم القيوين",
  "Ras Al Khaimah": "رأس الخيمة",
  "Fujairah": "الفجيرة"
};

var categoryNames = {
  "Sofas & Seating": "كنب ومقاعد",
  "Sofas": "كنب",
  "Living Room": "غرفة المعيشة",
  "Tables": "طاولات",
  "Dining Tables": "طاولات طعام",
  "Dining": "غرفة الطعام",
  "Beds & Mattresses": "أسرة ومراتب",
  "Beds": "أسرة",
  "Bedroom": "غرفة النوم",
  "Storage & Wardrobes": "خزائن وتخزين",
  "Storage": "وحدات تخزين",
  "Wardrobes": "خزائن ملابس",
  "Desks & Office": "مكاتب وأثاث مكتبي",
  "Desks": "مكاتب",
  "Office": "مكتبي",
  "Outdoor": "أثاث خارجي وحدائق",
  "Lighting & Decor": "إضاءة وديكور",
  "Lighting": "إضاءة",
  "Decor": "ديكور",
  "Kids": "أثاث أطفال",
  "Kids & Baby": "أطفال ورضع",
  "Kitchen": "مطابخ",
  "Rugs & Carpets": "سجاد وموكيت",
  "Other": "أخرى"
};

var conditionNames = {
  "Like new": "كالجديد",
  "Good": "جيد",
  "Fair": "مقبول"
};

var statusNames = {
  "Draft": "مسودة",
  "Pending": "قيد المراجعة",
  "Pending review": "قيد المراجعة",
  "Approved": "معتمد",
  "Approved / published": "معتمد ومتاح",
  "Live": "نشط",
  "Active": "نشط",
  "Rejected": "مرفوض",
  "Archived": "مؤرشف",
  "Reserved": "محجوز",
  "Sold": "تم البيع",
  "Negotiating": "قيد التفاوض",
  "Accepted": "مقبول",
  "Countered": "تم تقديم عرض مقابل",
  "Withdrawn": "مسحوب",
  "Scheduled": "مجدول",
  "Bid locked — awaiting confirmation": "تم قفل العرض — بانتظار التأكيد",
  "Awaiting bidder confirmation": "بانتظار تأكيد المزايد",
  "Confirmed": "مؤكد",
  "Declined": "مرفوض",
  "Timed out": "انتهت المهلة",
  "Awarded": "تمت الترسية",
  "Unsold": "لم يتم البيع",
  "Cancelled": "ملغى",
  "Cancellation requested": "تم طلب الإلغاء",
  "Awaiting response": "بانتظار الرد",
  "Rejected by the other party": "رفضه الطرف الآخر",
  "Accepted by the other party": "قبله الطرف الآخر",
  "Escalated to operations": "تم التصعيد للإدارة",
  "Cancellation finalised by staff": "تم إنهاء الإلغاء بواسطة الإدارة",
  "Verified": "موثق"
};

var areaNames = {
  "Al Reem Island": "جزيرة الريم",
  "Al Maryah Island": "جزيرة الماريه",
  "Saadiyat Island": "جزيرة السعديات",
  "Yas Island": "جزيرة ياس",
  "Khalifa City": "مدينة خليفة",
  "Al Raha Beach": "شاطئ الراحة",
  "Al Khalidiyah": "الخالدية",
  "Al Bateen": "البطين",
  "Corniche": "الكورنيش",
  "Al Mushrif": "المشرف",
  "Al Muroor": "المرور",
  "Mohammed Bin Zayed City": "مدينة محمد بن زايد",
  "Shakhbout City": "مدينة شخبوط",
  "Downtown Dubai": "وسط مدينة دبي",
  "Dubai Marina": "دبي مارينا",
  "Jumeirah Beach Residence (JBR)": "جميرا بيتش ريزيدنس (JBR)",
  "Jumeirah": "جميرا",
  "Palm Jumeirah": "نخلة جميرا",
  "Business Bay": "الخليج التجاري",
  "Dubai Hills Estate": "دبي هيلز استيت",
  "Arabian Ranches": "المرابع العربية",
  "Jumeirah Lake Towers (JLT)": "أبراج بحيرات جميرا (JLT)",
  "Al Barsha": "البرشاء",
  "Mirdif": "مردف",
  "Deira": "ديرة",
  "Bur Dubai": "بر دبي",
  "Dubai Silicon Oasis": "واحة دبي للسيليكون",
  "Al Majaz": "المجاز",
  "Al Nahda": "النهدة",
  "Al Taawun": "التعاون",
  "Al Qasimia": "القاسمية",
  "Muwaileh": "مویلح",
  "Al Khan": "الخان",
  "Al Nuaimiya": "النعيمية",
  "Al Rashidiya": "الراشدية",
  "Al Rawda": "الروضة",
  "Al Mowaihat": "المويهات",
  "Al Salamah": "السلامة",
  "Al Ramlah": "الرملة",
  "Al Humrah": "الحمرة",
  "Al Hamra Village": "قرية الحمراء",
  "Mina Al Arab": "ميناء العرب",
  "Al Nakheel": "النخيل",
  "Dhaid": "الذيد",
  "Khor Fakkan": "خورفكان",
  "Kalba": "كلباء",
  "Al Faseel": "الفصيل",
  "Dibba": "دبا",
  "Sakamkam": "سكمكم"
};

var uiPhrases = {
  "Coming soon": "قريباً",
  "Ras Al Khaimah — Coming soon": "رأس الخيمة — قريباً",
  "We are currently operating exclusively in Abu Dhabi to ensure the best local experience. The Ras Al Khaimah marketplace is preparing for launch very soon!": "نعمل حالياً في إمارة أبوظبي فقط لضمان أفضل تجربة محلية. سوق رأس الخيمة يستعد للانطلاق قريباً جداً!",
  "Abu Dhabi Marketplace is Live": "سوق أبوظبي متاح الآن",
  "Discover approved furniture listings, verified sellers, and local chat.": "تصفح معروضات الأثاث المفحوصة، وتواصل مع بائعين موثوقين.",
  "Ras Al Khaimah is Expanding Next": "رأس الخيمة هي محطتنا القادمة",
  "Neighborhood-scoped listings and furniture handover coming to your community.": "إعلانات محلية وتسليم مباشر في منطقتكم قريباً.",
  "Explore Abu Dhabi Marketplace": "استكشف سوق أبوظبي",
  "Stay on this page": "البقاء في هذه الصفحة",
  "ManzilCart": "منزل كارت",
  "Choose your Emirate": "اختر إمارتك",
  "Choose your Emirate — ManzilCart": "اختر إمارتك — منزل كارت",
  "Where are you looking?": "أين تبحث؟",
  "You can switch at any time. We never guess your location.": "يمكنك التبديل في أي وقت. نحن لا نخمن موقعك أبداً.",
  "Continue where you left off": "متابعة من حيث توقفت",
  "Marketplace": "السوق",
  "Enter": "دخول",
  "neighbourhood": "حي",
  "neighbourhoods": "أحياء",
  "Moderated listings": "إعلانات مفحوصة وموثوقة",
  "Every item is reviewed before it appears, so what you see is real furniture from real people.": "تتم مراجعة كل قطعة قبل ظهورها، لتضمن أثاثاً حقيقياً من أشخاص حقيقيين.",
  "Talk directly": "تواصل مباشرة",
  "Message the seller, ask questions and agree a price without leaving the platform.": "راسل البائع، واستفسر وتفاوض على السعر دون مغادرة المنصة.",
  "Local by design": "محلي ومخصص لإمارتك",
  "Your marketplace is your emirate. Optional delivery and assembly help can be requested with a deal.": "سوقك هو إمارتك. يمكنك طلب خدمات التوصيل والتركيب الاختيارية مع كل صفقة.",
  "Last time you were browsing": "في المرة السابقة كنت تتصفح",
  "No marketplaces are open right now": "لا توجد أسواق مفتوحة حالياً",
  "We are preparing our launch. Please check back soon.": "نحن نستعد للإطلاق قريباً. يرجى التحقق لاحقاً.",
  "That marketplace is not currently available. Please select an active emirate below.": "هذا السوق غير متاح حالياً. يرجى اختيار إمارة نشطة أدناه.",
  "That marketplace isn’t available": "هذا السوق غير متاح حالياً",
  "Browse": "تصفح",
  "Home": "الرئيسية",
  "Auctions": "المزادات",
  "Messages": "الرسائل",
  "Deals": "الصفقات",
  "My account": "حسابي",
  "Sign in": "تسجيل الدخول",
  "Sign out": "تسجيل الخروج",
  "Sell+": "بيع+",
  "Sell an item": "بيع قطعة",
  "Join to sell": "انضم للبيع",
  "List an item": "أضف قطعة",
  "Pre-owned furniture, from homes in the UAE to homes in the UAE.": "أثاث مستعمل، من منازل الإمارات إلى منازل الإمارات.",
  "Marketplaces": "الأسواق",
  "Account": "الحساب",
  "My auctions": "مزاداتي",
  "Profile": "الملف الشخصي",
  "Create an account": "إنشاء حساب",
  "Payments for furniture are arranged directly between buyer and seller. ManzilCart does not process or hold the purchase price.": "يتم ترتيب دفع ثمن الأثاث مباشرة بين المشتري والبائع. لا تقوم منزل كارت بمعالجة أو الاحتفاظ بسعر الشراء.",
  "United Arab Emirates.": "الإمارات العربية المتحدة.",
  "Skip to content": "الانتقال إلى المحتوى",
  "Mobile navigation": "التنقل عبر الجوال",
  "Show categories": "إظهار الفئات",
  "Hide categories": "إخفاء الفئات",
  "Switch emirate": "تبديل الإمارة",
  "Switch language to Arabic": "تغيير اللغة إلى العربية",
  "Switch to English": "التبديل إلى الإنجليزية",
  "Toggle dark and light mode": "تبديل الوضع الليلي والنهاري",
  "Furniture looking for its next home.": "أثاث يبحث عن منزله التالي.",
  "Furniture looking for\nits next home.": "أثاث يبحث عن منزله التالي.",
  "Pre-owned pieces from sellers across the emirate. Every listing is reviewed before it goes live.": "قطع مستعملة من بائعين في جميع أنحاء الإمارة. يتم فحص كل إعلان قبل نشره.",
  "Search furniture": "ابحث عن أثاث",
  "Search sofas, desks, beds…": "ابحث عن كنب، مكاتب، أسرة…",
  "Search": "بحث",
  "Browse by category": "تصفح حسب الفئة",
  "Find the piece that finishes the room.": "اعثر على القطعة المثالية لغرفتك.",
  "All listings": "جميع المعروضات",
  "All furniture": "كل الأثاث",
  "Similar products": "منتجات مشابهة",
  "More pieces from this category.": "قطع أخرى من هذه الفئة.",
  "View all": "عرض الكل",
  "Delivery": "التوصيل",
  "Delivery available": "التوصيل متاح",
  "ManzilCart can collect this item from the seller and deliver it safely to the buyer. A small seller transport contribution applies.": "يمكن لمنزل كارت استلام هذه القطعة من البائع وتوصيلها بأمان إلى المشتري. تُطبق مساهمة نقل رمزية على البائع.",
  "Know more": "اعرف المزيد",
  "Ask for delivery": "اطلب التوصيل",
  "Sign in to ask for delivery": "سجّل الدخول لطلب التوصيل",
  "Delivery requested": "تم طلب التوصيل",
  "Delivery requested. Operations will contact you and the seller.": "تم طلب التوصيل. سيتواصل فريق العمليات معك ومع البائع.",
  "Delivery request already received.": "تم استلام طلب التوصيل مسبقاً.",
  "You cannot request delivery for your own listing.": "لا يمكنك طلب التوصيل لإعلانك الخاص.",
  "This item is no longer available for delivery requests.": "هذه القطعة لم تعد متاحة لطلبات التوصيل.",
  "Processing": "قيد المعالجة",
  "Pickup": "تم الاستلام",
  "In transit": "في الطريق",
  "Delivered": "تم التوصيل",
  "ManzilCart service": "خدمة منزل كارت",
  "Furniture delivery, handled with care.": "توصيل الأثاث بعناية.",
  "Buying a sofa shouldn't mean solving transport. ManzilCart can collect the item from the seller and bring it safely to your address.": "شراء كنبة لا يجب أن يعني البحث عن وسيلة نقل. يمكن لمنزل كارت استلام القطعة من البائع وتوصيلها بأمان إلى عنوانك.",
  "Browse furniture": "تصفح الأثاث",
  "Back to marketplace": "العودة إلى السوق",
  "How it works": "كيف تعمل الخدمة",
  "Four simple steps between agreeing the sale and getting the item home.": "أربع خطوات بسيطة بين الاتفاق على البيع ووصول القطعة إلى منزلك.",
  "Agree the sale in chat": "اتفق على البيع في المحادثة",
  "Once you and the seller are ready, agree the details in chat.": "عندما تكون أنت والبائع جاهزين، اتفقا على التفاصيل في المحادثة.",
  "Ask for ManzilCart Delivery": "اطلب توصيل منزل كارت",
  "We confirm the item, address and transport contribution before collection.": "نؤكد القطعة والعنوان ومساهمة النقل قبل الاستلام.",
  "We collect from the seller": "نستلم القطعة من البائع",
  "Our team coordinates a suitable collection window with the seller.": "ينسق فريقنا موعد استلام مناسب مع البائع.",
  "We deliver to your address": "نوصلها إلى عنوانك",
  "The item is handled carefully and delivered to the agreed location.": "يتم التعامل مع القطعة بعناية وتوصيلها إلى الموقع المتفق عليه.",
  "What's included": "ما تشمله الخدمة",
  "Collection from the seller's address": "الاستلام من عنوان البائع",
  "Safe loading and transport": "تحميل ونقل آمن",
  "Delivery to the buyer's address": "التوصيل إلى عنوان المشتري",
  "Coordination through ManzilCart chat": "التنسيق عبر محادثة منزل كارت",
  "Seller transport contribution": "مساهمة البائع في النقل",
  "A small transport fee": "رسوم نقل رمزية",
  "The seller pays a small transport fee. The final amount depends on the item's size, distance and access, and is confirmed before collection.": "يدفع البائع رسوم نقل رمزية. يعتمد المبلغ النهائي على حجم القطعة والمسافة وسهولة الوصول، ويُؤكد قبل الاستلام.",
  "Good to know": "معلومات مفيدة",
  "Delivery is arranged separately from the furniture price. ManzilCart does not process or hold the purchase price.": "يُرتّب التوصيل بشكل منفصل عن سعر الأثاث. لا يعالج منزل كارت سعر الشراء ولا يحتفظ به.",
  "Availability depends on the item, its size and the delivery location. Confirm the details in chat before you agree.": "يعتمد التوفر على القطعة وحجمها وموقع التوصيل. أكد التفاصيل في المحادثة قبل الاتفاق.",
  "Ready when you are.": "جاهزون عندما تكون جاهزاً.",
  "Find a piece you love, chat with the seller, and ask about delivery.": "اعثر على قطعة تحبها، وتحدث مع البائع، واسأل عن التوصيل.",
  "Categories are being set up.": "جاري إعداد الفئات.",
  "The newest approved listings.": "أحدث المعروضات المعتمدة.",
  "No listings yet": "لا توجد معروضات حتى الآن",
  "We operate in": "نعمل في",
  "neighbourhoods here.": "أحياء هنا.",
  "No neighbourhoods are currently active.": "لا توجد أحياء نشطة حالياً.",
  "item available": "معروض متوفر",
  "items available": "معروضات متوفرة",
  "Filters": "الفلاتر",
  "Reset all": "إعادة ضبط",
  "Sort": "ترتيب",
  "Sort order": "طريقة الترتيب",
  "Active filters:": "الفلاتر المطبقة:",
  "Previous": "السابق",
  "Next": "التالي",
  "Clear all filters": "مسح كل الفلاتر",
  "Open filters": "فتح الفلاتر",
  "Nothing matches those filters": "لا توجد نتائج تطابق هذه الفلاتر",
  "Try widening your price range or choosing another category.": "جرب توسيع نطاق السعر أو اختيار فئة أخرى.",
  "Price range": "نطاق السعر",
  "Min AED": "الحد الأدنى (درهم)",
  "Max AED": "الحد الأقصى (درهم)",
  "Minimum price": "الحد الأدنى للسعر",
  "Maximum price": "الحد الأقصى للسعر",
  "Under AED 500": "أقل من 500 درهم",
  "AED 500 – 1.5k": "500 – 1.5 ألف درهم",
  "AED 1.5k – 4k": "1.5 ألف – 4 آلاف درهم",
  "AED 4,000+": "4,000+ درهم",
  "Any price": "أي سعر",
  "All categories": "جميع الفئات",
  "Any condition": "أي حالة",
  "Newest first": "الأحدث أولاً",
  "Price: low to high": "السعر: من الأقل للأعلى",
  "Price: high to low": "السعر: من الأعلى للأقل",
  "Oldest first": "الأقدم أولاً",
  "Reset filters": "إعادة ضبط الفلاتر",
  "Apply filters": "تطبيق الفلاتر",
  "Clear search": "مسح البحث",
  "About this piece": "عن هذه القطعة",
  "Category / condition:": "الفئة / الحالة:",
  "Category": "الفئة",
  "Condition": "الحالة",
  "Service area": "منطقة الخدمة",
  "Asking price": "السعر المطلوب",
  "Buyer": "المشتري",
  "Seller": "البائع",
  "Seller area": "منطقة البائع",
  "Ask questions, then agree a price in chat.": "اطرح الأسئلة، ثم اتفق على السعر في المحادثة.",
  "Message seller": "مراسلة البائع",
  "Create auction": "إنشاء مزاد",
  "Edit listing": "تعديل الإعلان",
  "Edit your listing": "تعديل إعلانك",
  "Stay safe.": "حافظ على سلامتك.",
  "Never pay a deposit before seeing the furniture. ManzilCart does not hold or process the purchase price.": "لا تدفع أي عربون قبل معاينة الأثاث شخصياً. منزل كارت لا تحتفظ أو تعالج مبالغ الشراء.",
  "View the furniture before paying. ManzilCart does not process or hold the purchase price.": "عاين الأثاث قبل الدفع. منزل كارت لا تعالج أو تحتفظ بسعر الشراء.",
  "Tips for a quick sale": "نصائح لبيع سريع",
  "Set a realistic asking price. Negotiation happens in chat.": "حدد سعراً واقعياً. التفاوض يتم عبر المحادثة.",
  "Give dimensions — buyers measure before they travel.": "حدد الأبعاد والقياسات بدقة — فالمشترون يقيسون قبل الحضور.",
  "Be honest about condition; it builds trust and avoids disputes.": "كن صادقاً بشأن حالة القطعة لبناء الثقة وتجنب أي خلاف.",
  "Use daylight photos from a few angles, including any marks.": "استخدم صوراً واضحة في ضوء النهار ومن عدة زوايا.",
  "Hover to zoom": "مرر المؤشر للتكبير",
  "Click to select as main cover image": "انقر للتعيين كصورة رئيسية",
  "Title": "العنوان",
  "Price (AED)": "السعر (درهم)",
  "Description": "الوصف",
  "Photos": "الصور",
  "Images": "الصور",
  "Cover photo": "صورة الغلاف",
  "Current photos": "الصور الحالية",
  "Choose the cover photo or remove a photo.": "اختر صورة الغلاف أو احذف صورة.",
  "Save changes": "حفظ التعديلات",
  "Submit for review": "إرسال للمراجعة",
  "Mark as sold": "تحديد كـ تم البيع",
  "Archive": "أرشفة",
  "Cancel": "إلغاء",
  "Confirm": "تأكيد",
  "Delete": "حذف",
  "optional": "اختياري",
  "All my listings": "جميع إعلاناتي",
  "Drafts, reviews and items you have sold.": "المسودات، قيد المراجعة، والمعروضات المباعة.",
  "Drafts, items in review, and everything you have sold.": "المسودات، قيد المراجعة، وكل ما قمت ببيعه.",
  "Create your first listing": "أضف أول إعلان لك",
  "You have not listed anything yet": "لم تقم بإضافة أي أثاث حتى الآن",
  "Clear some space and turn unused furniture into cash.": "وفر مساحة في منزلك وحوّل أثاثك غير المستخدم إلى نقد.",
  "View": "عرض",
  "Edit": "تعديل",
  "Auction": "المزاد",
  "Furniture auctions": "مزادات الأثاث",
  "Auctions you are selling in, bidding on, or have been offered.": "مزادات تشارك بها كبائع، مزايد، أو تلقيت عرضاً بشأنها.",
  "Current highest bid": "أعلى عرض حالي",
  "current highest bid": "أعلى عرض حالي",
  "No bids yet": "لا توجد عروض بعد",
  "No bids have been placed.": "لم يتم تقديم أي عروض بعد.",
  "Starts": "يبدأ في",
  "Ends": "ينتهي في",
  "Starting price": "سعر البداية",
  "starting price": "سعر البداية",
  "Minimum amount (AED)": "الحد الأدنى (درهم)",
  "Required. This is the auction's starting price; bids below this amount are not accepted.": "مطلوب. هذا هو سعر بداية المزاد؛ لا تُقبل المزايدات الأقل من هذا المبلغ.",
  "No bid has met the starting price yet.": "لم يصل أي عرض إلى سعر البداية بعد.",
  "Bids below the starting price cannot be selected.": "لا يمكن اختيار العروض الأقل من سعر البداية.",
  "The first valid bid must be at least the starting price.": "يجب أن يكون أول عرض صالحاً بقيمة سعر البداية على الأقل.",
  "Ties are decided in favour of the earliest bid.": "يُحسم التعادل لصالح العرض الأقدم.",
  "Your bid (AED)": "عرضك (درهم)",
  "Place bid": "تقديم العرض",
  "Sign in to bid": "سجل الدخول للمزايدة",
  "Lock this bid": "قفل هذا العرض",
  "Lock the highest eligible bid": "قفل أعلى عرض مؤهل",
  "Cancel auction": "إلغاء المزاد",
  "Cancel this auction (optional reason)": "إلغاء المزاد (سبب اختياري)",
  "Bid history": "سجل المزايدة",
  "Bid": "العرض",
  "Bidder": "المزايد",
  "Placed": "وقت التقديم",
  "Highest": "الأعلى",
  "Confirmation rounds": "جولات التأكيد",
  "Auction history": "سجل المزاد",
  "View the listing": "عرض الإعلان الأصلي",
  "Your confirmation": "تأكيدك",
  "Waiting for confirmation": "بانتظار التأكيد",
  "Confirm your winning bid": "تأكيد عرضك الفائز",
  "Auction confirmation": "تأكيد المزاد",
  "Your locked bid": "عرضك المقفول",
  "Confirm your bid": "تأكيد العرض",
  "Confirm and create the deal": "تأكيد وإنشاء الصفقة",
  "Decline this offer": "رفض هذا العرض",
  "Accepted deal": "الصفقة المقبولة",
  "Open the deal": "فتح تفاصيل الصفقة",
  "View the auction": "عرض المزاد",
  "View your response": "عرض ردك",
  "Start auction": "بدء المزاد",
  "Starting bid (AED)": "سعر بدء المزاد (درهم)",
  "Duration": "المدة",
  "Deal": "الصفقة",
  "Price": "السعر",
  "Agreed at": "تم الاتفاق على",
  "Offer history": "سجل العروض",
  "Respond to the current offer": "الرد على العرض الحالي",
  "Accept offer": "قبول العرض",
  "Reject": "رفض",
  "Counter with (AED)": "عرض مقابل بـ (درهم)",
  "Send counteroffer": "إرسال عرض مقابل",
  "Make an offer": "تقديم عرض",
  "Amount (AED)": "المبلغ (درهم)",
  "Note (optional)": "ملاحظة (اختياري)",
  "Send offer": "إرسال العرض",
  "Optional services": "خدمات اختيارية",
  "Save service selections": "حفظ الخدمات المختارة",
  "Cancellation": "الإلغاء",
  "Accept cancellation": "قبول الإلغاء",
  "Reject cancellation": "رفض الإلغاء",
  "Why should operations step in?": "لماذا يجب تدخل فريق العمليات؟",
  "Track negotiations and accepted deals.": "متابعة المفاوضات والصفقات المقبولة.",
  "Buying": "الشراء",
  "Selling": "البيع",
  "You are the buyer": "أنت المشتري",
  "You are the seller": "أنت البائع",
  "Conversation": "المحادثة",
  "Connecting…": "جاري الاتصال…",
  "Load older messages": "تحميل الرسائل السابقة",
  "No messages yet. Say hello and ask about the furniture.": "لا توجد رسائل بعد. ابدأ بالتحية واسأل عن تفاصيل الأثاث.",
  "Send": "إرسال",
  "Message": "الرسالة",
  "Write a message…": "اكتب رسالة…",
  "Conversations with buyers and sellers, newest first.": "المحادثات مع المشترين والبائعين، الأحدث أولاً.",
  "Conversations with buyers, sellers and the marketplace team, newest first.": "المحادثات مع المشترين والبائعين وفريق السوق، الأحدث أولاً.",
  "Marketplace team": "فريق السوق",
  "You": "أنت",
  "Needs seller reply": "بانتظار رد البائع",
  "Resolved": "تم الحل",
  "Reply to the marketplace team": "اكتب ردك على فريق السوق",
  "Attach a photo (optional)": "إرفاق صورة (اختياري)",
  "All marketplace messages": "كل رسائل فريق السوق",
  "Send": "إرسال",
  "View listing": "عرض الإعلان",
  "Your details": "بياناتك",
  "Enter with your mobile number": "الدخول برقم هاتفك المتحرك",
  "We send a one-time code on WhatsApp. New here? Verifying your number creates your account.": "نرسل رمز تحقق لمرة واحدة عبر واتساب. مستخدم جديد؟ التحقق من رقمك ينشئ حسابك تلقائياً.",
  "Phone number": "رقم الهاتف",
  "Display name": "اسم العرض",
  "Choose your area.": "اختر منطقتك السكنية.",
  "Create my account": "إنشاء حسابي",
  "Check your WhatsApp": "تحقق من تطبيق واتساب",
  "Verify your mobile.": "تحقق من رقم هاتفك.",
  "We sent a verification code to": "أرسلنا رمز تحقق إلى",
  "The code expires in about": "تنتهي صلاحية الرمز خلال",
  "Didn’t get it?": "لم يصلك الرمز؟",
  "Send a new code": "إرسال رمز جديد",
  "Verify and continue": "التحقق والمتابعة",
  "Use a different number": "استخدام رقم هاتف مختلف",
  "Edit profile": "تعديل الملف الشخصي",
  "Account status": "حالة الحساب",
  "Verified number:": "الرقم الموثق:",
  "We couldn’t find that page": "لم نتمكن من العثور على تلك الصفحة",
  "The link may be incomplete, or the item may no longer be listed.": "قد يكون الرابط غير مكتمل أو أن القطعة لم تعد معروضة.",
  "Back to the home page": "العودة للصفحة الرئيسية",
  "Go to the home page": "الانتقال للصفحة الرئيسية",
  "Something went wrong": "حدث خطأ غير متوقع",
  "We have been notified. Please try again in a moment.": "تم إشعار فريقنا. يرجى إعادة المحاولة بعد قليل.",
  "AED": "درهم",
  "from": "من",
  "to": "إلى",
  "in": "في",
  "by": "بواسطة",
  "at": "عند",
  "for": "لـ",
  "with": "مع",
  "You": "أنت",
  "you": "أنت",
  "Member": "عضو",
  "just now": "الآن",
  "yesterday": "أمس",
  "Settings": "الإعدادات",
  "Account settings": "الإعدادات",
  "Account details": "تفاصيل الحساب",
  "Choose how ManzilCart looks and which marketplace you browse.": "اختر مظهر المنصة والمتجر الذي تتصفحه.",
  "Preferences": "التفضيلات",
  "Language": "اللغة",
  "Switch the interface between English and Arabic.": "تبديل واجهة المنصة بين الإنجليزية والعربية.",
  "Appearance": "المظهر",
  "Choose light or dark mode for this device.": "اختر الوضع الفاتح أو الغامق لهذا الجهاز.",
  "Light": "فاتح",
  "Dark": "غامق",
  "Browsing marketplace": "المتجر الذي تتصفحه",
  "Change which marketplace you browse. Your profile area is set on the profile page.": "غيّر المتجر الذي تتصفحه. منطقتك تُحدَّد في صفحة الملف الشخصي.",
  "Sign out of this device. Your listings and messages stay safe.": "اخرج من هذا الجهاز. إعلاناتك ورسائلك تبقى محفوظة.",
  "Save": "حفظ",
  "Account": "الحساب",
  "Area": "المنطقة",
  "Area": "المنطقة",
  "No marketplace set": "لم يتم تحديد منطقة بعد",
  "Member since": "عضو منذ",
  "Mobile number": "رقم الهاتف المتحرك",
  "Email": "البريد الإلكتروني",
  "Not provided": "غير مُدخل",
  "My listings": "إعلاناتي",
  "Wishlist": "قائمة الرغبات",
  "My wishlist": "قائمة رغباتي",
  "Saved for later": "محفوظة لاحقاً",
  "Products you saved while browsing. Remove one any time.": "منتجات حفظتها أثناء التصفح. يمكنك إزالة أي منها في أي وقت.",
  "No saved products yet": "لا توجد منتجات محفوظة بعد",
  "Tap the heart on any listing to save it here.": "اضغط على القلب في أي إعلان لحفظه هنا.",
  "Saved products remain here if they are sold or removed from the marketplace.": "تبقى المنتجات المحفوظة هنا حتى إذا بيعت أو أزيلت من السوق.",
  "Save to wishlist": "حفظ في قائمة الرغبات",
  "Saved to wishlist": "محفوظة في قائمة الرغبات",
  "saved product": "منتج محفوظ",
  "saved products": "منتجات محفوظة",
  "Remove from wishlist": "إزالة من قائمة الرغبات",
  "Saved to your wishlist.": "تم الحفظ في قائمة رغباتك.",
  "Removed from your wishlist.": "تمت الإزالة من قائمة رغباتك.",
  "View wishlist": "عرض قائمة الرغبات",
  "Could not update wishlist.": "تعذر تحديث قائمة الرغبات.",
  "Browse listings": "تصفح المعروضات",
  "Choose your emirate": "اختر إمارتك",
  "Offers and deals": "العروض والصفقات",
  "Create listing": "إنشاء إعلان",
  "List an item with photos and a price.": "اعرض قطعة بصور وسعر.",
  "Conversations": "المحادثات",
  "Apply to auction": "التقدّم للمزاد",
  "Send photos and your proposed starting bid. Our team reviews and lists it.": "أرسل الصور وسعر البدء المقترح. يراجع فريقنا الطلب ثم ينشر المزاد.",
  "Auction application": "طلب مزاد",
  "Auction applications": "طلبات المزاد",
  "Applications": "الطلبات",
  "New application": "طلب جديد",
  "Apply to auction an item": "قدّم قطعة للمزاد",
  "Edit your application": "عدّل طلبك",
  "Edit auction application": "تعديل طلب المزاد",
  "Your auction applications": "طلبات المزاد الخاصة بك",
  "Back to my auctions": "العودة إلى مزاداتي",
  "No applications yet": "لا توجد طلبات بعد",
  "No photo": "لا توجد صورة",
  "Proposed bid": "العرض المقترح",
  "Proposed starting bid": "سعر البدء المقترح",
  "Proposed starting / minimum bid": "سعر البدء / الحد الأدنى المقترح",
  "Proposed starting / minimum bid (AED)": "سعر البدء / الحد الأدنى المقترح (درهم)",
  "View all applications": "عرض كل الطلبات",
  "Auctions and offers": "المزادات والعروض",
  "Message from ManzilCart:": "رسالة من منزل كارت:",
  "Review note:": "ملاحظة المراجعة:",
  "You have been selected.": "تم اختيارك.",
  "Confirm bid": "تأكيد العرض",
  "Open auction": "فتح المزاد",
  "Open": "فتح",
  "Edit and submit": "تعديل وإرسال",
  "Details": "التفاصيل",
  "Edit": "تعديل",
  "Withdraw application": "سحب الطلب",
  "What happens next?": "ماذا يحدث بعد ذلك؟",
  "Application": "الطلب",
  "Reference": "المرجع",
  "Status": "الحالة",
  "Location": "الموقع",
  "Review history": "سجل المراجعة",
  "No review activity yet.": "لا يوجد نشاط مراجعة بعد.",
  "Photos": "الصور",
  "Condition": "الحالة",
  "Preferred start": "البدء المفضل",
  "Preferred end": "الانتهاء المفضل",
  "Submitted": "تم الإرسال",
  "Not yet submitted": "لم يُرسل بعد",
  "No photos uploaded yet.": "لم تُرفع صور بعد.",
  "What you told us": "ما أخبرتنا به",
  "Submit application": "إرسال الطلب",
  "Save and submit for review": "حفظ وإرسال للمراجعة",
  "Save draft": "حفظ مسودة",
  "Your current photos": "صورك الحالية",
  "Choose a cover photo, or tick photos to remove when you save.": "اختر صورة الغلاف أو حدد الصور التي تريد حذفها عند الحفظ.",
  "Cover": "الغلاف",
  "Remove": "إزالة",
  "Emirate": "الإمارة",
  "Bidding starts": "يبدأ المزاد",
  "Bidding ends": "ينتهي المزاد",
  "Preferred bidding start (optional)": "بدء المزاد المفضل (اختياري)",
  "Preferred bidding end (optional)": "انتهاء المزاد المفضل (اختياري)",
  "A preference only. Staff confirm the final dates before listing.": "تفضيل فقط. يؤكد فريقنا المواعيد النهائية قبل النشر.",
  "Preferred dates are optional and only guide our team. We confirm the final schedule.": "التواريخ المفضلة اختيارية وترشد فريقنا فقط. نؤكد الجدول النهائي.",
  "At least 20 characters. Mention dimensions, age and any marks.": "20 حرفاً على الأقل. اذكر الأبعاد والعمر وأي علامات.",
  "No auction activity yet": "لا يوجد نشاط مزاد بعد",
  "When you bid on an auction or receive an offer, it will appear here.": "عندما تزايد على قطعة أو تتلقى عرضاً، سيظهر هنا.",
  "Browse auctions": "تصفح المزادات",
  "Your item": "قطعتك",
  "You are bidding": "أنت تزايد",
  "Highest bid": "أعلى عرض",
  "No bids yet": "لا توجد عروض بعد",
  "Ends": "ينتهي",
  "Review note": "ملاحظة المراجعة"
};


  var langToggle = document.querySelector("[data-lang-toggle]");
  var langDisplay = document.querySelector("[data-lang-display]");

  function translateString(str) {
    if (!str || typeof str !== "string") return str;
    var leading = (str.match(/^\s*/) || [""])[0];
    var trailing = (str.match(/\s*$/) || [""])[0];
    var trimmed = str.trim();
    if (!trimmed) return str;

    // 1. Direct dictionary matches
    if (uiPhrases[trimmed]) return leading + uiPhrases[trimmed] + trailing;
    if (emirateNames[trimmed]) return leading + emirateNames[trimmed] + trailing;
    if (categoryNames[trimmed]) return leading + categoryNames[trimmed] + trailing;
    if (conditionNames[trimmed]) return leading + conditionNames[trimmed] + trailing;
    if (statusNames[trimmed]) return leading + statusNames[trimmed] + trailing;
    if (areaNames[trimmed]) return leading + areaNames[trimmed] + trailing;

    // 2. Category with count: e.g. "Sofas & Seating (5)"
    var catMatch = trimmed.match(/^(.*?)\s*\((\d+)\)$/);
    if (catMatch && categoryNames[catMatch[1].trim()]) {
      return leading + categoryNames[catMatch[1].trim()] + " (" + catMatch[2] + ")" + trailing;
    }

    // 3. Dynamic patterns:
    // "Enter X"
    var enterMatch = trimmed.match(/^Enter\s+(.*)$/i);
    if (enterMatch) {
      var em = enterMatch[1].trim();
      return leading + "دخول " + (emirateNames[em] || em) + trailing;
    }

    // "Fresh in X"
    var freshMatch = trimmed.match(/^Fresh in\s+(.*)$/i);
    if (freshMatch) {
      var em2 = freshMatch[1].trim();
      return leading + "جديد في " + (emirateNames[em2] || em2) + trailing;
    }

    // "Selling in X"
    var sellMatch = trimmed.match(/^Selling in\s+(.*)$/i);
    if (sellMatch) {
      var em3 = sellMatch[1].trim();
      return leading + "البيع في " + (emirateNames[em3] || em3) + trailing;
    }

    // "X marketplace"
    var mktMatch = trimmed.match(/^(.*?)\s+marketplace$/i);
    if (mktMatch) {
      var em4 = mktMatch[1].trim();
      return leading + "سوق " + (emirateNames[em4] || em4) + trailing;
    }

    // "X items available" / "X item available"
    var itemMatch = trimmed.match(/^(\d+)\s+items?\s+available$/i);
    if (itemMatch) {
      return leading + itemMatch[1] + " معروضات متوفرة" + trailing;
    }

    // "X listings" / "X listing"
    var listMatch = trimmed.match(/^(\d+)\s+listings?$/i);
    if (listMatch) {
      return leading + listMatch[1] + " إعلانات" + trailing;
    }

    // "X neighbourhoods" / "X neighbourhood"
    var nghMatch = trimmed.match(/^(\d+)\s+neighbourhoods?$/i);
    if (nghMatch) {
      return leading + nghMatch[1] + " أحياء" + trailing;
    }

    // "Page X of Y"
    var pageMatch = trimmed.match(/^Page\s+(\d+)\s+of\s+(\d+)$/i);
    if (pageMatch) {
      return leading + "صفحة " + pageMatch[1] + " من " + pageMatch[2] + trailing;
    }

    // Price patterns:
    // "AED 1,200" or "AED 500"
    var priceMatch = trimmed.match(/^AED\s*([\d,]+(?:\.\d+)?)$/i);
    if (priceMatch) {
      return leading + priceMatch[1] + " درهم" + trailing;
    }

    // "From AED 500"
    var fromPrice = trimmed.match(/^From\s+AED\s*([\d,]+)$/i);
    if (fromPrice) {
      return leading + "من " + fromPrice[1] + " درهم" + trailing;
    }

    // "Up to AED 1000"
    var upToPrice = trimmed.match(/^Up to\s+AED\s*([\d,]+)$/i);
    if (upToPrice) {
      return leading + "حتى " + upToPrice[1] + " درهم" + trailing;
    }

    // "AED X – Y"
    var rangeMatch = trimmed.match(/^AED\s*([\d,]+)\s*[–—\-]\s*([\d,]+)$/i);
    if (rangeMatch) {
      return leading + rangeMatch[1] + " – " + rangeMatch[2] + " درهم" + trailing;
    }

    // "Deal X · AED Y"
    var dealMatch = trimmed.match(/^Deal\s+(\S+)\s*·\s*AED\s*([\d,]+)$/i);
    if (dealMatch) {
      return leading + "صفقة " + dealMatch[1] + " · " + dealMatch[2] + " درهم" + trailing;
    }

    // Arrows
    if (trimmed === "→") return leading + "←" + trailing;
    if (trimmed === "←") return leading + "→" + trailing;

    // Document title: e.g. "Choose your Emirate — ManzilCart"
    if (trimmed.indexOf("— ManzilCart") !== -1 || trimmed.indexOf("- ManzilCart") !== -1) {
      var parts = trimmed.split(/[-—]\s*ManzilCart/);
      var head = parts[0].trim();
      var headTrans = uiPhrases[head] || emirateNames[head] || head;
      return leading + headTrans + " — منزل كارت" + trailing;
    }

    // Substring replacements for remaining known terms
    var modified = trimmed;
    var changed = false;

    for (var phrase in uiPhrases) {
      if (phrase.length > 5 && modified.indexOf(phrase) !== -1) {
        modified = modified.split(phrase).join(uiPhrases[phrase]);
        changed = true;
      }
    }
    for (var emName in emirateNames) {
      if (modified.indexOf(emName) !== -1) {
        modified = modified.split(emName).join(emirateNames[emName]);
        changed = true;
      }
    }
    for (var cat in categoryNames) {
      if (cat.length > 4 && modified.indexOf(cat) !== -1) {
        modified = modified.split(cat).join(categoryNames[cat]);
        changed = true;
      }
    }
    for (var area in areaNames) {
      if (area.length > 4 && modified.indexOf(area) !== -1) {
        modified = modified.split(area).join(areaNames[area]);
        changed = true;
      }
    }

    if (modified.indexOf("AED ") !== -1 || modified.indexOf("AED") !== -1) {
      modified = modified.replace(/AED\s*([\d,]+)/gi, "$1 درهم");
      changed = true;
    }
    if (modified.indexOf("ManzilCart") !== -1) {
      modified = modified.replace(/ManzilCart/g, "منزل كارت");
      changed = true;
    }

    if (changed) {
      return leading + modified + trailing;
    }

    return str;
  }

  function applyLanguage(lang) {
    var isArabic = lang === "ar";
    document.documentElement.setAttribute("lang", isArabic ? "ar" : "en");
    document.documentElement.setAttribute("dir", isArabic ? "rtl" : "ltr");
    try {
      localStorage.setItem("lang", isArabic ? "ar" : "en");
    } catch (e) {}

    if (langDisplay) {
      langDisplay.textContent = isArabic ? "English" : "العربية";
    }
    if (langToggle) {
      var nextLabel = isArabic ? "Switch to English" : "تغيير اللغة إلى العربية";
      langToggle.setAttribute("aria-label", nextLabel);
      langToggle.setAttribute("title", nextLabel);
    }

    // 1. Document Title
    if (document._origTitle === undefined) {
      document._origTitle = document.title;
    }
    if (isArabic) {
      document.title = translateString(document._origTitle);
    } else {
      document.title = document._origTitle;
    }

    // 2. Comprehensive DOM Text Walker
    if (document.body) {
      var walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_TEXT,
        {
          acceptNode: function (node) {
            if (!node.parentElement) return NodeFilter.FILTER_REJECT;
            var tag = node.parentElement.tagName.toLowerCase();
            if (tag === "script" || tag === "style" || tag === "textarea") {
              return NodeFilter.FILTER_REJECT;
            }
            if (node.parentElement.hasAttribute("data-i18n-skip")) {
              return NodeFilter.FILTER_REJECT;
            }
            return NodeFilter.FILTER_ACCEPT;
          }
        }
      );

      var textNode;
      while ((textNode = walker.nextNode())) {
        if (textNode._origVal === undefined) {
          textNode._origVal = textNode.nodeValue;
        }
        if (isArabic) {
          textNode.nodeValue = translateString(textNode._origVal);
        } else {
          textNode.nodeValue = textNode._origVal;
        }
      }
    }

    // 3. Inputs & textareas (placeholder)
    document.querySelectorAll("input, textarea").forEach(function (el) {
      if (el.placeholder) {
        if (el._origPlaceholder === undefined) {
          el._origPlaceholder = el.placeholder;
        }
        if (isArabic) {
          el.placeholder = translateString(el._origPlaceholder);
        } else {
          el.placeholder = el._origPlaceholder;
        }
      }
    });

    // 4. Select dropdown options
    document.querySelectorAll("select option").forEach(function (opt) {
      if (opt._origOptionText === undefined) {
        opt._origOptionText = opt.textContent;
      }
      if (isArabic) {
        opt.textContent = translateString(opt._origOptionText);
      } else {
        opt.textContent = opt._origOptionText;
      }
    });

    // 5. Submit / action buttons with value attributes
    document.querySelectorAll("input[type=submit], input[type=button]").forEach(function (btn) {
      if (btn.value) {
        if (btn._origBtnVal === undefined) {
          btn._origBtnVal = btn.value;
        }
        if (isArabic) {
          btn.value = translateString(btn._origBtnVal);
        } else {
          btn.value = btn._origBtnVal;
        }
      }
    });

    // 6. Tooltips and accessibility labels
    document.querySelectorAll("[aria-label], [title]").forEach(function (el) {
      if (el.hasAttribute("data-lang-toggle") || el.hasAttribute("data-theme-toggle")) return;
      var aria = el.getAttribute("aria-label");
      if (aria) {
        if (el._origAriaLabel === undefined) el._origAriaLabel = aria;
        if (isArabic) {
          el.setAttribute("aria-label", translateString(el._origAriaLabel));
        } else {
          el.setAttribute("aria-label", el._origAriaLabel);
        }
      }
      var t = el.getAttribute("title");
      if (t) {
        if (el._origTitleAttr === undefined) el._origTitleAttr = t;
        if (isArabic) {
          el.setAttribute("title", translateString(el._origTitleAttr));
        } else {
          el.setAttribute("title", el._origTitleAttr);
        }
      }
    });

    // 7. Synchronize price dual-slider if present
    if (window._syncPriceSlider) {
      window._syncPriceSlider();
    }

    // 8. Dynamic labels that the text walker deliberately skips.
    updateCategoryToggleLabel();
  }

  // Initialize language from storage
  var savedLang = "en";
  try {
    savedLang = localStorage.getItem("lang") || "en";
  } catch (e) {}

  window.applyCurrentLanguage = function () {
    var l = "en";
    try { l = localStorage.getItem("lang") || "en"; } catch (e) {}
    applyLanguage(l);
  };

  applyLanguage(savedLang);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      applyLanguage(savedLang);
    });
  }

  if (langToggle) {
    langToggle.addEventListener("click", function () {
      var currentLang = document.documentElement.getAttribute("lang") || "en";
      var nextLang = currentLang === "ar" ? "en" : "ar";
      applyLanguage(nextLang);
    });
  }

  // --- Account settings: explicit language / appearance choices ------------
  function syncSettingsChoices() {
    var activeLang = document.documentElement.getAttribute("lang") || "en";
    var activeTheme = document.documentElement.getAttribute("data-theme") || "light";
    document.querySelectorAll("[data-lang-choice]").forEach(function (btn) {
      var selected = btn.getAttribute("data-lang-choice") === activeLang;
      btn.setAttribute("aria-pressed", selected ? "true" : "false");
      btn.classList.toggle("is-selected", selected);
    });
    document.querySelectorAll("[data-theme-choice]").forEach(function (btn) {
      var selected = btn.getAttribute("data-theme-choice") === activeTheme;
      btn.setAttribute("aria-pressed", selected ? "true" : "false");
      btn.classList.toggle("is-selected", selected);
    });
  }

  document.querySelectorAll("[data-lang-choice]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      applyLanguage(btn.getAttribute("data-lang-choice") === "ar" ? "ar" : "en");
      syncSettingsChoices();
    });
  });

  document.querySelectorAll("[data-theme-choice]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      applyTheme(btn.getAttribute("data-theme-choice") === "dark" ? "dark" : "light");
      syncSettingsChoices();
    });
  });

  syncSettingsChoices();






  // --- Mobile navigation ---------------------------------------------------
  var toggle = document.querySelector("[data-nav-toggle]");
  var nav = document.getElementById("primary-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // --- Collapsible category grid (home page, mobile) ----------------------
  var categoryToggle = document.querySelector("[data-category-toggle]");
  var categoryPanel = document.getElementById("category-panel");

  function updateCategoryToggleLabel() {
    if (!categoryToggle || !categoryPanel) return;
    var label = categoryToggle.querySelector("[data-category-toggle-label]");
    if (!label) return;
    var isArabic = document.documentElement.getAttribute("lang") === "ar";
    var text = categoryPanel.classList.contains("is-open") ? "Hide categories" : "Show categories";
    label.textContent = isArabic ? translateString(text) : text;
  }

  if (categoryToggle && categoryPanel) {
    categoryToggle.addEventListener("click", function () {
      var open = categoryPanel.classList.toggle("is-open");
      categoryToggle.setAttribute("aria-expanded", open ? "true" : "false");
      updateCategoryToggleLabel();
    });
    updateCategoryToggleLabel();
  }

  // --- Emirate switcher: submit on change ---------------------------------
  document.querySelectorAll("select[data-autosubmit]").forEach(function (select) {
    select.addEventListener("change", function () {
      if (select.form) {
        select.form.submit();
      }
    });
  });

  // --- Category suggestions under marketplace search boxes ----------------
  var suggestionSeq = 0;

  document.querySelectorAll("[data-search-suggestions]").forEach(function (form) {
    var input = form.querySelector('input[type="search"][name="q"]');
    var endpoint = form.getAttribute("data-suggestions-url");
    if (!input || !endpoint) return;

    suggestionSeq += 1;
    var panel = document.createElement("div");
    panel.className = "search-suggestions";
    panel.id = "search-suggestions-" + suggestionSeq;
    panel.setAttribute("role", "listbox");
    panel.setAttribute("aria-label", "Category suggestions");
    panel.hidden = true;
    document.body.appendChild(panel);

    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", panel.id);

    var items = [];
    var activeIndex = -1;
    var debounceTimer = null;
    var controller = null;
    var lastQuery = "";

    function setActive(index) {
      activeIndex = index;
      Array.prototype.forEach.call(panel.children, function (child, childIndex) {
        var selected = childIndex === index;
        child.classList.toggle("is-active", selected);
        child.setAttribute("aria-selected", selected ? "true" : "false");
      });
      if (index >= 0 && panel.children[index]) {
        input.setAttribute(
          "aria-activedescendant",
          panel.id + "-option-" + index
        );
        try {
          panel.children[index].scrollIntoView({ block: "nearest" });
        } catch (err) {}
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    }

    function closePanel() {
      panel.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      activeIndex = -1;
    }

    function positionPanel() {
      if (panel.hidden) return;
      var rect = form.getBoundingClientRect();
      var gap = 8;
      var panelHeight = panel.offsetHeight;
      var top = rect.bottom + gap;
      if (
        top + panelHeight > window.innerHeight - gap &&
        rect.top - gap - panelHeight > gap
      ) {
        top = rect.top - gap - panelHeight;
      }
      panel.style.top = (window.pageYOffset + top) + "px";
      panel.style.left = (window.pageXOffset + rect.left) + "px";
      panel.style.width = rect.width + "px";
    }

    function chooseSuggestion(index) {
      var item = items[index];
      if (!item || !item.url) return;
      window.location.assign(item.url);
    }

    function renderSuggestions(suggestions) {
      panel.innerHTML = "";
      items = suggestions || [];
      activeIndex = -1;

      if (!items.length) {
        closePanel();
        return;
      }

      var isArabic = document.documentElement.getAttribute("lang") === "ar";
      items.forEach(function (item, index) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "search-suggestion";
        button.id = panel.id + "-option-" + index;
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", "false");

        var thumb = document.createElement("span");
        thumb.className = "search-suggestion-thumb";
        thumb.setAttribute("aria-hidden", "true");
        var initial = (item.name || "?").charAt(0);
        if (item.image_url) {
          var image = document.createElement("img");
          image.src = item.image_url;
          image.alt = "";
          image.loading = "lazy";
          image.addEventListener("error", function () {
            image.remove();
            thumb.textContent = initial;
          });
          thumb.appendChild(image);
        } else {
          thumb.textContent = initial;
        }

        var name = document.createElement("span");
        name.className = "search-suggestion-name";
        name.textContent = isArabic ? translateString(item.name || "") : item.name || "";

        button.appendChild(thumb);
        button.appendChild(name);
        button.addEventListener("pointerdown", function (event) {
          event.preventDefault();
          chooseSuggestion(index);
        });
        button.addEventListener("mouseenter", function () {
          setActive(index);
        });
        panel.appendChild(button);
      });

      panel.hidden = false;
      input.setAttribute("aria-expanded", "true");
      positionPanel();
    }

    function requestSuggestions(value) {
      if (controller) controller.abort();
      controller = new AbortController();
      fetch(endpoint + "?q=" + encodeURIComponent(value), {
        headers: { Accept: "application/json" },
        signal: controller.signal
      })
        .then(function (response) {
          return response.ok ? response.json() : { suggestions: [] };
        })
        .then(function (data) {
          if (input.value.trim() !== value) return;
          renderSuggestions(data.suggestions || []);
        })
        .catch(function (error) {
          if (!error || error.name !== "AbortError") closePanel();
        });
    }

    function queueSuggestions() {
      var value = input.value.trim();
      if (!value) {
        window.clearTimeout(debounceTimer);
        if (controller) controller.abort();
        lastQuery = "";
        items = [];
        panel.innerHTML = "";
        closePanel();
        return;
      }
      if (value === lastQuery) return;
      lastQuery = value;
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(function () {
        requestSuggestions(value);
      }, 150);
    }

    input.addEventListener("input", queueSuggestions);
    input.addEventListener("focus", function () {
      if (input.value.trim() && items.length) {
        panel.hidden = false;
        input.setAttribute("aria-expanded", "true");
        positionPanel();
      } else if (input.value.trim()) {
        lastQuery = "";
        queueSuggestions();
      }
    });
    input.addEventListener("blur", function () {
      window.setTimeout(closePanel, 120);
    });
    input.addEventListener("keydown", function (event) {
      if (panel.hidden) {
        if (event.key === "ArrowDown" && input.value.trim() && items.length) {
          event.preventDefault();
          panel.hidden = false;
          input.setAttribute("aria-expanded", "true");
          positionPanel();
          setActive(0);
        }
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((activeIndex + 1) % items.length);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((activeIndex - 1 + items.length) % items.length);
      } else if (event.key === "Enter" && activeIndex >= 0) {
        event.preventDefault();
        chooseSuggestion(activeIndex);
      } else if (event.key === "Escape") {
        closePanel();
      }
    });
    form.addEventListener("submit", closePanel);
    window.addEventListener("resize", positionPanel);
    window.addEventListener("scroll", positionPanel, true);
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

      var counterPill = gallery.querySelector("[data-gallery-counter]");
      if (counterPill && thumbs.length) {
        counterPill.textContent = (currentIndex + 1) + " / " + thumbs.length;
      }
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

      // Touch swipe support for mobile image switching
      var touchStartX = 0;
      var touchStartY = 0;
      viewport.addEventListener("touchstart", function (e) {
        if (e.touches && e.touches.length === 1) {
          touchStartX = e.touches[0].clientX;
          touchStartY = e.touches[0].clientY;
        }
      }, { passive: true });

      viewport.addEventListener("touchend", function (e) {
        if (!thumbs.length || !e.changedTouches || e.changedTouches.length === 0) return;
        var diffX = e.changedTouches[0].clientX - touchStartX;
        var diffY = e.changedTouches[0].clientY - touchStartY;
        if (Math.abs(diffX) > 35 && Math.abs(diffX) > Math.abs(diffY)) {
          var isRtl = document.documentElement.getAttribute("dir") === "rtl";
          if (diffX < 0) {
            var nextIdx = isRtl
              ? (currentIndex - 1 + thumbs.length) % thumbs.length
              : (currentIndex + 1) % thumbs.length;
            selectImage(nextIdx);
          } else {
            var prevIdx = isRtl
              ? (currentIndex + 1) % thumbs.length
              : (currentIndex - 1 + thumbs.length) % thumbs.length;
            selectImage(prevIdx);
          }
        }
      }, { passive: true });
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

  // --- Asynchronous Wishlist Toggle ----------------------------------------
  var toastContainer = null;

  function getCsrfToken(form) {
    var input = form ? form.querySelector("input[name='csrfmiddlewaretoken']") : null;
    if (input && input.value) return input.value;
    var match = document.cookie && document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function showToast(message, type, link) {
    if (!toastContainer) {
      toastContainer = document.querySelector(".mc-toast-container");
      if (!toastContainer) {
        toastContainer = document.createElement("div");
        toastContainer.className = "mc-toast-container";
        document.body.appendChild(toastContainer);
      }
    }

    // Limit active toasts to at most 2
    while (toastContainer.children.length >= 2) {
      toastContainer.removeChild(toastContainer.firstChild);
    }

    var toast = document.createElement("div");
    toast.className = "mc-toast" + (type ? " mc-toast-" + type : "");
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");

    var msgSpan = document.createElement("span");
    msgSpan.textContent = message;
    toast.appendChild(msgSpan);

    if (link && link.url && link.label) {
      var a = document.createElement("a");
      a.href = link.url;
      a.className = "mc-toast-link";
      a.textContent = link.label;
      toast.appendChild(a);
    }

    toastContainer.appendChild(toast);

    setTimeout(function () {
      toast.classList.add("is-leaving");
      setTimeout(function () {
        if (toast.parentNode) {
          toast.parentNode.removeChild(toast);
        }
      }, 230);
    }, 2600);
  }

  function updateWishlistButtonState(btn, isSaved, isArabic) {
    btn.classList.toggle("is-saved", isSaved);

    // Detail button text
    var span = btn.querySelector("span");
    if (span) {
      var text = isSaved ? "Saved to wishlist" : "Save to wishlist";
      span.textContent = isArabic ? translateString(text) : text;
    }

    // Heart button labels
    var label = isSaved ? "Remove from wishlist" : "Save to wishlist";
    var translatedLabel = isArabic ? translateString(label) : label;
    btn.setAttribute("aria-label", translatedLabel);
    btn.setAttribute("title", translatedLabel);
  }

  function syncAllWishlistButtons(actionUrl, listingId, isSaved, isArabic, sourceForm) {
    var selector = "form[data-wishlist-toggle], .wishlist-heart-form, .wishlist-detail-form";
    document.querySelectorAll(selector).forEach(function (f) {
      if (f === sourceForm) return;
      var matchesAction = actionUrl && f.getAttribute("action") === actionUrl;
      var matchesId = listingId && f.getAttribute("data-listing-id") === String(listingId);
      if (matchesAction || matchesId) {
        var otherBtn = f.querySelector("button[type='submit']") || f.querySelector("button");
        if (otherBtn) {
          updateWishlistButtonState(otherBtn, isSaved, isArabic);
        }
      }
    });
  }

  function updateWishlistCounters(count) {
    var isArabic = (document.documentElement.getAttribute("lang") || "en") === "ar";
    document.querySelectorAll("[data-wishlist-counter]").forEach(function (pill) {
      if (count <= 0) {
        pill.style.display = "none";
      } else {
        pill.style.display = "";
        var label = count === 1 ? (isArabic ? "منتج محفوظ" : "saved product") : (isArabic ? "منتجات محفوظة" : "saved products");
        pill.textContent = count + " " + label;
      }
    });

    document.querySelectorAll("[data-wishlist-stat]").forEach(function (el) {
      el.textContent = count;
    });
  }

  function initWishlistAsync() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!form || !form.matches) return;
      if (!form.matches(".wishlist-heart-form") && !form.matches(".wishlist-detail-form") && !form.hasAttribute("data-wishlist-toggle")) {
        return;
      }

      e.preventDefault();

      var btn = form.querySelector("button[type='submit']") || form.querySelector("button");
      if (!btn) return;

      var actionUrl = form.getAttribute("action");
      if (!actionUrl) return;

      var isArabic = (document.documentElement.getAttribute("lang") || "en") === "ar";
      var wasSaved = btn.classList.contains("is-saved");
      var willBeSaved = !wasSaved;
      var listingId = form.getAttribute("data-listing-id") || "";

      // Instant optimistic UI update & subtle bounce animation
      updateWishlistButtonState(btn, willBeSaved, isArabic);
      btn.classList.add("is-animating");
      setTimeout(function () {
        btn.classList.remove("is-animating");
      }, 450);

      // Synchronize any other button instances on screen
      syncAllWishlistButtons(actionUrl, listingId, willBeSaved, isArabic, form);

      var csrfToken = getCsrfToken(form);

      fetch(actionUrl, {
        method: "POST",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "Accept": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: new FormData(form),
      })
      .then(function (res) {
        if (res.status === 401) {
          return res.json().then(function (data) {
            window.location.href = data.login_url || "/accounts/login/";
          }).catch(function () {
            window.location.href = "/accounts/login/";
          });
        }
        if (!res.ok) {
          throw new Error("HTTP " + res.status);
        }
        return res.json();
      })
      .then(function (data) {
        if (!data || data.ok === false) {
          // Revert optimistic state
          updateWishlistButtonState(btn, wasSaved, isArabic);
          syncAllWishlistButtons(actionUrl, listingId, wasSaved, isArabic, form);
          showToast(data && data.error ? data.error : (isArabic ? "تعذر تحديث قائمة الرغبات" : "Could not update wishlist."), "error");
          return;
        }

        var actualSaved = !!data.saved;
        if (actualSaved !== willBeSaved) {
          updateWishlistButtonState(btn, actualSaved, isArabic);
          syncAllWishlistButtons(actionUrl, listingId, actualSaved, isArabic, form);
        }

        // Floating toast feedback
        var toastText = actualSaved ? "Saved to your wishlist." : "Removed from your wishlist.";
        var translatedToast = isArabic ? translateString(toastText) : toastText;
        var viewLink = actualSaved ? {
          label: isArabic ? translateString("View wishlist") : "View wishlist",
          url: "/my/wishlist/",
        } : null;
        showToast(translatedToast, "success", viewLink);

        // If on the /my/wishlist/ page and un-saving
        var grid = document.querySelector("[data-wishlist-grid]");
        if (grid && !actualSaved) {
          var card = form.closest(".listing-card");
          if (card) {
            card.classList.add("is-removing");
            setTimeout(function () {
              card.remove();
              var remaining = grid.querySelectorAll(".listing-card").length;
              updateWishlistCounters(remaining);
              if (remaining === 0) {
                var emptyState = document.querySelector("[data-wishlist-empty]");
                if (emptyState) emptyState.style.display = "";
                var note = document.querySelector(".wishlist-note");
                if (note) note.style.display = "none";
              }
            }, 280);
          }
        } else if (typeof data.wishlist_count === "number") {
          updateWishlistCounters(data.wishlist_count);
        }
      })
      .catch(function () {
        updateWishlistButtonState(btn, wasSaved, isArabic);
        syncAllWishlistButtons(actionUrl, listingId, wasSaved, isArabic, form);
        showToast(isArabic ? "تعذر تحديث قائمة الرغبات" : "Could not update wishlist.", "error");
      });
    });
  }

  initWishlistAsync();
})();

