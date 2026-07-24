import type { Language, ModuleId, SupplierCategoryKey, PaymentTermKey } from './types'

// ---------------------------------------------------------------------------
// Bilingual (EN / 中文) UI strings. Domain text inside mock data stays in its
// original language and is never run through this table.
// ---------------------------------------------------------------------------

export interface Translation {
  appName: string
  tagline: string

  login: {
    title: string
    subtitle: string
    email: string
    emailPlaceholder: string
    password: string
    passwordPlaceholder: string
    signIn: string
    signingIn: string
    sso: string
    demoHint: string
    secured: string
  }

  nav: {
    modules: string
    sourcing: string
    comparison: string
    memory: string
    settings: string
    logout: string
  }

  module: Record<ModuleId, { title: string; subtitle: string }>

  steps: [string, string, string]

  common: {
    analyze: string
    analyzing: string
    resultsFound: (n: number) => string
    analysisComplete: string
    exportExcel: string
    exporting: string
    exportSuccess: string
    printPdf: string
    viewDetails: string
    select: string
    selected: string
    giveFeedback: string
    close: string
    cancel: string
    save: string
    saved: string
    empty: string
    searchError: string
    searchTimeout: string
  }

  sourcing: {
    inputLabel: string
    placeholder: string
    hint: string
    structuredLabel: string
    structuredHint: string
    productName: string
    productNamePlaceholder: string
    quantity: string
    quantityPlaceholder: string
    unit: string
    brand: string
    brandPlaceholder: string
    model: string
    modelPlaceholder: string
    specifications: string
    specificationsPlaceholder: string
    standards: string
    standardsPlaceholder: string
    structuredCategory: string
    structuredCountry: string
    countryPlaceholder: string
    structuredCerts: string
    certsPlaceholder: string
    criteriaTitle: string
    criteriaHint: string
    criteriaAdd: string
    criteriaPlaceholder: string
    criteriaWeight: string
    criteriaRemove: string
    criteriaPresetQuality: string
    criteriaPresetReputation: string
    criteriaPresetMoq: string
    criteriaPresetCapacity: string
    criteriaPresetRegion: string
    criteriaPresetEnvironment: string
    units: { pcs: string; kg: string; m: string; set: string }
    categoryLabel: string
    categoryAll: string
    categories: Record<SupplierCategoryKey, string>
    cardAddress: string
    cardContact: string
    cardScale: string
    cardEmployees: string
    cardRevenue: string
    cardEstablished: string
    cardCapabilities: string
    cardCerts: string
    cardProduct: string
    cardSpecifications: string
    cardQuote: string
    cardLeadTime: string
    cardPaymentTerms: string
    cardVerification: string
    cardEvidence: string
    cardSources: string
    sourceLabel: string
    localDatabaseTag: string
    webSearchTag: string
    allWebNotice: string
    recommendationTitle: string
    recommendationLead: string
    recommendationReasons: string
    recommendationRisks: string
    recommendationNoRisk: string
    recommendationLocal: string
    recommendationProduct: string
    recommendationCertifications: string
    recommendationWebRisk: string
    recommendationContactRisk: string
    recommendationQuoteRisk: string
    recommendationDeliveryRisk: string
    recommendationPaymentRisk: string
    recommendationCompared: string
    match: string
    colName: string
    colLocation: string
    colEmail: string
    colWebsite: string
    colProduct: string
    colBrand: string
    colModel: string
    colSpecifications: string
    colStandards: string
    colUnitPrice: string
    colQuoteConditions: string
    colLeadTime: string
    colPaymentTerms: string
    colSources: string
    expandDetails: string
    collapseDetails: string
    agentProgress: {
      eyebrow: string
      runningTitle: string
      failedTitle: string
      description: string
      progress: string
      thoughtLogLabel: string
      emptyText: string
      activeLabel: string
      messagesLabel: string
      searchesLabel: string
      pagesLabel: string
      pricesLabel: string
    }
  }

  comparison: {
    inputLabel: string
    placeholder: string
    hint: string
    structuredLabel: string
    structuredHint: string
    productName: string
    productNamePlaceholder: string
    quantity: string
    quantityPlaceholder: string
    brand: string
    brandPlaceholder: string
    model: string
    modelPlaceholder: string
    hardFilters: string
    budget: string
    delivery: string
    targetMarket: string
    targetMarketOptions: { any: string; Germany: string; Poland: string }
    polandCurrencyNotice: string
    minPrice: string
    maxPrice: string
    deliveryOptions: { unlimited: string; within3: string; within7: string }
    weightTitle: string
    weightHint: string
    weightPrice: string
    weightDelivery: string
    weightRating: string
    customCriteriaTitle: string
    customCriteriaHint: string
    tableTitle: string
    colVendor: string
    colPlatform: string
    colProduct: string
    colMatch: string
    colScore: string
    colPrice: string
    colDelivery: string
    colPayment: string
    colDeliveryMethod: string
    colRating: string
    colAction: string
    recommended: string
    sourceLabel: string
    sourceLocal: string
    sourceWeb: string
    sourceSerpApi: string
    sourceIdealo: string
    sourceMarketplaceApi: string
    priceVerification: string
    sourceLinks: string
    priceEvidence: string
    priceFromMarketplaceApi: string
    priceFromSerpApi: string
    priceExtracted: string
    webNeedsManualCheck: string
    missingDelivery: string
    missingPayment: string
    missingDeliveryMethod: string
    noMatches: string
    allWebNotice: string
    allMarketplaceApiNotice: string
    agentProgress: {
      eyebrow: string
      runningTitle: string
      failedTitle: string
      description: string
      progress: string
      thoughtLogLabel: string
      emptyText: string
      activeLabel: string
      messagesLabel: string
      searchesLabel: string
      pagesLabel: string
      pricesLabel: string
    }
    paymentTerms: Record<PaymentTermKey, string>
  }

  memory: {
    title: string
    subtitle: string
    empty: string
    moduleCol: string
    queryCol: string
    filtersCol: string
    resultsCol: string
    feedbackCol: string
    timeCol: string
    noFeedback: string
    clearAll: string
    confirmClear: string
    chose: string
    openHint: string
    reopen: string
    delete: string
    deleteTitle: string
    confirmDelete: string
    clearTitle: string
    searchPlaceholder: string
    timeFilterAll: string
    timeFilter7d: string
    timeFilter30d: string
    timeFilter1y: string
    moduleFilterAll: string
    moduleSourcing: string
    moduleComparison: string
    restoredBanner: string
  }

  feedback: {
    title: string
    subtitle: string
    whichChosen: string
    quality: string
    logistics: string
    priceSat: string
    service: string
    comment: string
    commentPlaceholder: string
    submit: string
    thanks: string
  }

  settings: {
    vaultTitle: string
    vaultDesc: string
    encrypted: string
    keyLabel: string
    keyValue: string
    keyPlaceholder: string
    addKey: string
    updated: string
    never: string
    noKeys: string
    accountTitle: string
    name: string
    company: string
    role: string
  }

  supplierDirectory: {
    title: string
    description: string
    addSupplier: string
    editSupplier: string
    companyName: string
    country: string
    city: string
    contact: string
    email: string
    phone: string
    website: string
    capabilities: string
    certifications: string
    tags: string
    tagsHint: string
    notes: string
    saveSupplier: string
    updateSupplier: string
    cancel: string
    deleteSupplier: string
    confirmDelete: string
    empty: string
    localOnly: string
    created: string
    updated: string
    preferredTag: string
    reliableTag: string
    preferred: string
    performanceNote: string
    performancePlaceholder: string
    minimumOrderQuantity: string
    productionCapacity: string
    environmentalStandards: string
    saving: string
    loadError: string
    saveError: string
  }
}

export const translations: Record<Language, Translation> = {
  en: {
    appName: 'Fuyao Procurement Cloud',
    tagline: 'Intelligent procurement decision assistant',
    login: {
      title: 'Sign in to your workspace',
      subtitle: 'Enterprise procurement intelligence platform',
      email: 'Work email',
      emailPlaceholder: 'you@fuyao-europe.com',
      password: 'Password',
      passwordPlaceholder: 'Enter your password',
      signIn: 'Sign in',
      signingIn: 'Signing in...',
      sso: 'Continue with Enterprise SSO',
      demoHint: 'Demo: any email + password works (prototype, no real backend).',
      secured: 'Secured with end-to-end encryption · SOC 2 · ISO 27001',
    },
    nav: {
      modules: 'Modules',
      sourcing: 'Supplier Sourcing',
      comparison: 'Quote Comparison',
      memory: 'Conversation Memory',
      settings: 'Settings & Keys',
      logout: 'Sign out',
    },
    module: {
      sourcing: {
        title: 'Supplier Sourcing',
        subtitle: 'Find glass & automotive supply-chain suppliers across Europe',
      },
      comparison: {
        title: 'Quote Comparison',
        subtitle: 'AI-powered structured price benchmarking for standard products',
      },
      memory: {
        title: 'Conversation Memory',
        subtitle: 'Every query and input you have made is remembered here',
      },
      settings: {
        title: 'Settings & Keys',
        subtitle: 'Manage your account and securely stored API keys',
      },
    },
    steps: ['Understand requirements', 'Search suppliers', 'Generate structured output'],
    common: {
      analyze: 'Start analysis',
      analyzing: 'Analyzing...',
      resultsFound: (n) => `Found ${n} result${n === 1 ? '' : 's'}`,
      analysisComplete: 'Analysis complete',
      exportExcel: 'Export Excel',
      exporting: 'Exporting...',
      exportSuccess: 'Report exported successfully!',
      printPdf: 'Print / PDF',
      viewDetails: 'Details',
      select: 'Select this',
      selected: 'Selected',
      giveFeedback: 'Select & rate',
      close: 'Close',
      cancel: 'Cancel',
      save: 'Save',
      saved: 'Saved',
      empty: 'No data yet.',
      searchError: 'Search failed — could not reach the server. Please try again.',
      searchTimeout: 'Search timed out — the server took too long to respond. Please try again.',
    },
    sourcing: {
      inputLabel: 'What are you sourcing?',
      placeholder:
        'Describe what you need, e.g.: water deflector strips and glass adhesive for windscreen assembly in Germany...',
      hint: 'Mention the part, material, region, volume and any certification needs.',
      structuredLabel: 'Detailed requirements (optional — fills in automatically when you describe above)',
      structuredHint: 'Fields you fill in here will take priority over the natural-language description.',
      productName: 'Product name',
      productNamePlaceholder: 'e.g. Rubber seal strip EPDM',
      quantity: 'Quantity',
      quantityPlaceholder: 'e.g. 500 pcs / 500 kg / 500 sets',
      unit: 'Unit',
      brand: 'Brand (optional)',
      brandPlaceholder: 'e.g. Bosch, Henkel',
      model: 'Model (optional)',
      modelPlaceholder: 'e.g. TS 1234',
      specifications: 'Specifications (optional)',
      specificationsPlaceholder: 'e.g. EPDM, 20 × 8 mm',
      standards: 'Standards (optional)',
      standardsPlaceholder: 'e.g. DIN, ISO 9001',
      structuredCategory: 'Category',
      structuredCountry: 'Target region',
      countryPlaceholder: 'e.g. Germany',
      structuredCerts: 'Certifications (optional)',
      certsPlaceholder: 'e.g. DIN, ISO 9001',
      criteriaTitle: 'Decision criteria and weights',
      criteriaHint: 'Set the dimensions that matter for this sourcing run. The agent normalizes the weights to 100%.',
      criteriaAdd: 'Add criterion',
      criteriaPlaceholder: 'e.g. Packaging compatibility',
      criteriaWeight: 'Weight',
      criteriaRemove: 'Remove',
      criteriaPresetQuality: 'Product quality',
      criteriaPresetReputation: 'Supplier reputation',
      criteriaPresetMoq: 'Minimum order quantity',
      criteriaPresetCapacity: 'Production capacity',
      criteriaPresetRegion: 'Regional fit',
      criteriaPresetEnvironment: 'Environmental standards',
      units: { pcs: 'pcs', kg: 'kg', m: 'm', set: 'set' },
      categoryLabel: 'Category',
      categoryAll: 'All categories',
      categories: {
        waterDeflector: 'Water deflector strips',
        glassAdhesive: 'Glass adhesive / urethane',
        rubberSeal: 'Rubber seals / weatherstrip',
        glassRaw: 'Raw / float glass',
        hardware: 'Mounting hardware',
        packaging: 'Packaging & racks',
      },
      cardAddress: 'Address',
      cardContact: 'Contact',
      cardScale: 'Scale',
      cardEmployees: 'Employees',
      cardRevenue: 'Annual revenue',
      cardEstablished: 'Established',
      cardCapabilities: 'Core capabilities',
      cardCerts: 'Certifications',
      cardProduct: 'Product evidence',
      cardSpecifications: 'Specifications & standards',
      cardQuote: 'Quote conditions',
      cardLeadTime: 'Lead time',
      cardPaymentTerms: 'Payment terms',
      cardVerification: 'Verification status',
      cardEvidence: 'Evidence from crawled pages',
      cardSources: 'Sources opened by Agent',
      sourceLabel: 'Source',
      localDatabaseTag: 'Local database',
      webSearchTag: 'Web search',
      allWebNotice: 'No matching supplier was found in the local database. All displayed results come from web research.',
      recommendationTitle: 'Recommendation brief',
      recommendationLead: 'Top candidate',
      recommendationReasons: 'Why it is recommended',
      recommendationRisks: 'Risks or missing information',
      recommendationNoRisk: 'No material gap is visible in the current profile. Confirm commercial details before award.',
      recommendationLocal: 'Available in the curated local supplier directory.',
      recommendationProduct: 'Product evidence is available for the requested scope.',
      recommendationCertifications: 'Relevant certifications are recorded.',
      recommendationWebRisk: 'Web-sourced profile: verify the information directly with the supplier.',
      recommendationContactRisk: 'No direct contact detail is available.',
      recommendationQuoteRisk: 'Unit pricing or quote conditions are not yet confirmed.',
      recommendationDeliveryRisk: 'Delivery lead time is not yet confirmed.',
      recommendationPaymentRisk: 'Payment terms are not yet confirmed.',
      recommendationCompared: 'Match-score lead over next candidate',
      match: 'match',
      colName: 'Supplier',
      colLocation: 'Location',
      colEmail: 'Email',
      colWebsite: 'Website',
      colProduct: 'Product',
      colBrand: 'Brand',
      colModel: 'Model',
      colSpecifications: 'Specifications',
      colStandards: 'Standards',
      colUnitPrice: 'Unit price',
      colQuoteConditions: 'Quote conditions',
      colLeadTime: 'Lead time',
      colPaymentTerms: 'Payment terms',
      colSources: 'Source links',
      expandDetails: 'Show supplier details',
      collapseDetails: 'Hide supplier details',
      agentProgress: {
        eyebrow: 'Procurement Agent thinking',
        runningTitle: 'Procurement Agent is thinking',
        failedTitle: 'Agent needs attention',
        description:
          'I am researching live supplier sources, filtering irrelevant pages, extracting supplier signals, and preparing a procurement-ready shortlist.',
        progress: 'Progress',
        thoughtLogLabel: 'Agent thought log',
        emptyText: 'Waiting for the first thought from the backend agent...',
        activeLabel: 'Working live — new steps will appear here',
        messagesLabel: 'Messages',
        searchesLabel: 'Searches',
        pagesLabel: 'Pages opened',
        pricesLabel: 'Prices extracted',
      },
    },
    comparison: {
      inputLabel: 'Procurement requirement',
      placeholder:
        'Enter your procurement needs, e.g.: We need a batch of windscreen adhesive cartridges...',
      hint: 'Include specs, quantity, budget and delivery requirements.',
      structuredLabel: 'Detailed requirements (optional — fills in automatically when you describe above)',
      structuredHint: 'Product name, brand, model, and quantity here will take priority over the natural-language description for quote comparison.',
      productName: 'Product name',
      productNamePlaceholder: 'e.g. Windscreen adhesive cartridge',
      brand: 'Brand (optional)',
      brandPlaceholder: 'e.g. Bosch, Henkel, Sika',
      model: 'Model',
      modelPlaceholder: 'e.g. PU 8597 HMLC / 400 ml',
      quantity: 'Quantity',
      quantityPlaceholder: 'e.g. 500 pcs / 500 kg / 500 sets',
      hardFilters: 'Pre-filter conditions (Hard Filters)',
      budget: 'Budget limit',
      delivery: 'Delivery time',
      targetMarket: 'Target market',
      targetMarketOptions: {
        any: 'No explicit market',
        Germany: 'Germany',
        Poland: 'Poland',
      },
      polandCurrencyNotice: 'Poland: only EUR-priced API offers are compared; PLN results use the web fallback.',
      minPrice: 'Min',
      maxPrice: 'Max',
      deliveryOptions: {
        unlimited: 'No limit',
        within3: 'Within 3 business days',
        within7: 'Within 7 business days',
      },
      weightTitle: 'Decision weights',
      weightHint: 'Drag the wheel or sliders — results are ranked by your weighting',
      weightPrice: 'Price',
      weightDelivery: 'Delivery',
      weightRating: 'Reviews',
      customCriteriaTitle: 'Additional evaluation dimensions',
      customCriteriaHint: 'Add the business criteria that should influence a close quote decision; their weights are normalized separately.',
      tableTitle: 'Decision comparison table',
      colVendor: 'Vendor',
      colPlatform: 'Platform',
      colProduct: 'Product',
      colMatch: 'Product match',
      colScore: 'Weighted score',
      colPrice: 'Unit price',
      colDelivery: 'Lead time',
      colPayment: 'Payment method',
      colDeliveryMethod: 'Delivery method',
      colRating: 'Rating',
      colAction: 'Action',
      recommended: 'Top pick',
      sourceLabel: 'Source',
      sourceLocal: 'Local quote DB',
      sourceWeb: 'Web search',
      sourceSerpApi: 'SerpApi · Google Shopping',
      sourceIdealo: 'Idealo price comparison',
      sourceMarketplaceApi: 'Marketplace API',
      priceVerification: 'Price verification',
      sourceLinks: 'Source links',
      priceEvidence: 'Price evidence',
      priceFromMarketplaceApi: 'Marketplace API price',
      priceFromSerpApi: 'SerpApi marketplace price',
      priceExtracted: 'Product page extraction',
      webNeedsManualCheck: 'Needs manual price check',
      missingDelivery: 'Needs delivery check',
      missingPayment: 'Needs payment check',
      missingDeliveryMethod: 'Needs shipping check',
      noMatches: 'No quotes match the current filters. Adjust the budget, delivery time, or target market and try again.',
      allWebNotice: 'No local quote matched this request. The displayed candidates come from web search and may need manual price/delivery verification.',
      allMarketplaceApiNotice: 'No local quote matched this request. The displayed prices come from a configured marketplace API; confirm final shipping, tax, and availability before ordering.',
      agentProgress: {
        eyebrow: 'Procurement Agent thinking',
        runningTitle: 'Procurement Agent is comparing quotes',
        failedTitle: 'Agent needs attention',
        description:
          'I am combining your natural-language need, hard filters, and decision weights, then checking the local quote database and preparing a weighted comparison table.',
        progress: 'Progress',
        thoughtLogLabel: 'Agent thought log',
        emptyText: 'Waiting for the first thought from the backend agent...',
        activeLabel: 'Working live — new steps will appear here',
        messagesLabel: 'Messages',
        searchesLabel: 'Searches',
        pagesLabel: 'Pages opened',
        pricesLabel: 'Prices extracted',
      },
      paymentTerms: {
        onAccount: 'On account',
        prepayment: 'Prepayment',
        card: 'Card / PayPal',
        unknown: 'To confirm',
      },
    },
    memory: {
      title: 'Conversation Memory',
      subtitle: 'Every query and input you have made is remembered here',
      empty: 'No conversations yet. Run a search in Sourcing or Comparison.',
      moduleCol: 'Module',
      queryCol: 'Query & inputs',
      filtersCol: 'Filters',
      resultsCol: 'Results',
      feedbackCol: 'Feedback',
      timeCol: 'Time',
      noFeedback: 'No feedback',
      clearAll: 'Clear all',
      confirmClear: 'Clear all remembered conversations?',
      chose: 'Chose',
      openHint: 'Click any record to reopen it in its module',
      reopen: 'Reopen',
      delete: 'Delete',
      deleteTitle: 'Delete record',
      confirmDelete: 'Delete this conversation? This cannot be undone.',
      clearTitle: 'Clear all records',
      searchPlaceholder: 'Search by keyword, category, or product name…',
      timeFilterAll: 'All time',
      timeFilter7d: 'Last 7 days',
      timeFilter30d: 'Last 30 days',
      timeFilter1y: 'Last year',
      moduleFilterAll: 'All',
      moduleSourcing: 'Sourcing',
      moduleComparison: 'Comparison',
      restoredBanner: 'Restored from memory — previous results shown below',
    },
    feedback: {
      title: 'Share your feedback',
      subtitle: 'Tell us which supplier you chose and how it went.',
      whichChosen: 'Which supplier / vendor did you choose?',
      quality: 'Goods quality',
      logistics: 'Logistics speed',
      priceSat: 'Price satisfaction',
      service: 'Service',
      comment: 'Comment (optional)',
      commentPlaceholder: 'Anything else you want to note about this choice...',
      submit: 'Submit feedback',
      thanks: 'Thanks! Your feedback was recorded.',
    },
    settings: {
      vaultTitle: 'API Key Vault',
      vaultDesc:
        'Keys are stored in your encrypted cloud vault and never displayed in full after saving.',
      encrypted: 'Encrypted at rest',
      keyLabel: 'Key name',
      keyValue: 'Secret value',
      keyPlaceholder: 'Paste API key...',
      addKey: 'Save to vault',
      updated: 'Updated',
      never: 'never',
      noKeys: 'No keys stored yet.',
      accountTitle: 'Account',
      name: 'Name',
      company: 'Company',
      role: 'Role',
    },
    supplierDirectory: {
      title: 'Local supplier directory',
      description: 'Maintain your approved suppliers, contact evidence, and performance tags. These records are prioritized during sourcing.',
      addSupplier: 'Add supplier',
      editSupplier: 'Edit supplier',
      companyName: 'Company name',
      country: 'Country / region',
      city: 'City',
      contact: 'Contact person',
      email: 'Email',
      phone: 'Phone',
      website: 'Website',
      capabilities: 'Capabilities',
      certifications: 'Certifications',
      tags: 'Procurement tags',
      tagsHint: 'Separate tags with commas. Example: Approved supplier, Strong past performance.',
      notes: 'Internal notes',
      saveSupplier: 'Save supplier',
      updateSupplier: 'Update supplier',
      cancel: 'Cancel',
      deleteSupplier: 'Delete',
      confirmDelete: 'Delete this local supplier record? This cannot be undone.',
      empty: 'No local suppliers yet. Add approved or trusted suppliers to prioritize them in future searches.',
      localOnly: 'Local record',
      created: 'Created',
      updated: 'Updated',
      preferredTag: 'Approved supplier',
      reliableTag: 'Strong past performance',
      preferred: 'Prioritize this supplier in future searches',
      performanceNote: 'Historical performance',
      performancePlaceholder: 'e.g. On-time delivery 96%; quality rating 4.7/5',
      minimumOrderQuantity: 'Minimum order quantity',
      productionCapacity: 'Production capacity',
      environmentalStandards: 'Environmental standards',
      saving: 'Saving...',
      loadError: 'Could not load the supplier directory. Your existing records are still safe.',
      saveError: 'Could not save this supplier. Please try again.',
    },
  },

  zh: {
    appName: '福耀采购云',
    tagline: '智能采购决策助手',
    login: {
      title: '登录您的工作空间',
      subtitle: '企业级采购智能平台',
      email: '企业邮箱',
      emailPlaceholder: 'you@fuyao-europe.com',
      password: '密码',
      passwordPlaceholder: '请输入密码',
      signIn: '登录',
      signingIn: '登录中...',
      sso: '使用企业 SSO 登录',
      demoHint: '演示：任意邮箱 + 密码均可登录（原型，无真实后端）。',
      secured: '端到端加密保护 · SOC 2 · ISO 27001',
    },
    nav: {
      modules: '功能模块',
      sourcing: '寻找供应商',
      comparison: '标准品比价',
      memory: '对话记忆',
      settings: '设置与密钥',
      logout: '退出登录',
    },
    module: {
      sourcing: {
        title: '寻找供应商',
        subtitle: '检索欧洲玻璃及汽车用品产业链供应商',
      },
      comparison: {
        title: '标准品比价',
        subtitle: '基于 AI 的标准品结构化比价分析',
      },
      memory: {
        title: '对话记忆',
        subtitle: '这里记录您每一次的询问与输入内容',
      },
      settings: {
        title: '设置与密钥',
        subtitle: '管理您的账户与安全存储的 API 密钥',
      },
    },
    steps: ['理解采购需求', '检索供应商', '生成结构化结果'],
    common: {
      analyze: '开始分析',
      analyzing: '分析中...',
      resultsFound: (n) => `已找到 ${n} 条结果`,
      analysisComplete: '分析完成',
      exportExcel: '导出 Excel',
      exporting: '导出中...',
      exportSuccess: '报表导出成功！',
      printPdf: '打印 / PDF',
      viewDetails: '查看详情',
      select: '选择此项',
      selected: '已选择',
      giveFeedback: '选择并评价',
      close: '关闭',
      cancel: '取消',
      save: '保存',
      saved: '已保存',
      empty: '暂无数据。',
      searchError: '检索失败——无法连接服务器，请重试。',
      searchTimeout: '检索超时——服务器响应时间过长，请重试。',
    },
    sourcing: {
      inputLabel: '您要寻找什么供应商？',
      placeholder: '请描述需求，例如：用于挡风玻璃装配的挡水条与玻璃胶，区域德国……',
      hint: '可注明零件、材料、区域、用量及认证要求。',
      structuredLabel: '详细需求（可选 — 填写后优先使用）',
      structuredHint: '此处填写的字段会优先于自然语言描述中的内容。',
      productName: '商品名称',
      productNamePlaceholder: '例如：橡胶密封条 EPDM',
      quantity: '数量',
      quantityPlaceholder: '例如：500个、500千克、500套',
      unit: '单位',
      brand: '品牌（可选）',
      brandPlaceholder: '例如：Bosch、Henkel',
      model: '型号（可选）',
      modelPlaceholder: '例如：TS 1234',
      specifications: '规格（可选）',
      specificationsPlaceholder: '例如：EPDM，20 × 8 mm',
      standards: '标准（可选）',
      standardsPlaceholder: '例如：DIN、ISO 9001',
      structuredCategory: '品类',
      structuredCountry: '目标地区',
      countryPlaceholder: '例如：德国',
      structuredCerts: '认证要求（可选）',
      certsPlaceholder: '例如：DIN、ISO 9001',
      criteriaTitle: '评价标准与权重',
      criteriaHint: '设置本次寻源最重要的维度，系统会自动将权重规范为 100%。',
      criteriaAdd: '添加维度',
      criteriaPlaceholder: '例如：包装兼容性',
      criteriaWeight: '权重',
      criteriaRemove: '移除',
      criteriaPresetQuality: '产品质量',
      criteriaPresetReputation: '供应商信誉',
      criteriaPresetMoq: '最低订购量',
      criteriaPresetCapacity: '生产能力',
      criteriaPresetRegion: '地区匹配度',
      criteriaPresetEnvironment: '环保标准',
      units: { pcs: '个', kg: '千克', m: '米', set: '套' },
      categoryLabel: '品类',
      categoryAll: '全部品类',
      categories: {
        waterDeflector: '挡水条',
        glassAdhesive: '玻璃胶 / 聚氨酯',
        rubberSeal: '密封条 / 橡胶件',
        glassRaw: '玻璃原片',
        hardware: '安装五金件',
        packaging: '包装与料架',
      },
      cardAddress: '地址',
      cardContact: '联系方式',
      cardScale: '规模',
      cardEmployees: '员工人数',
      cardRevenue: '年营收',
      cardEstablished: '成立年份',
      cardCapabilities: '核心能力',
      cardCerts: '资质认证',
      cardProduct: '产品证据',
      cardSpecifications: '规格与标准',
      cardQuote: '报价条件',
      cardLeadTime: '交付周期',
      cardPaymentTerms: '付款方式',
      cardVerification: '信息核验状态',
      cardEvidence: '爬取页面证据',
      cardSources: 'Agent 打开的来源页面',
      sourceLabel: '来源',
      localDatabaseTag: '本地数据库',
      webSearchTag: '网络搜索',
      allWebNotice: '本地数据库没有找到相应供应商，当前显示的结果全部来源于网络搜索。',
      recommendationTitle: '推荐简报',
      recommendationLead: '首选候选',
      recommendationReasons: '推荐原因',
      recommendationRisks: '风险或待补充信息',
      recommendationNoRisk: '当前资料未发现明显缺口；下单前仍请确认商务条款。',
      recommendationLocal: '已纳入企业维护的本地供应商资料库。',
      recommendationProduct: '具备与本次需求相关的产品证据。',
      recommendationCertifications: '已记录相关认证或资质。',
      recommendationWebRisk: '网络来源资料：请与供应商直接核验。',
      recommendationContactRisk: '尚缺少直接联系方式。',
      recommendationQuoteRisk: '单价或报价条件尚未确认。',
      recommendationDeliveryRisk: '交付周期尚未确认。',
      recommendationPaymentRisk: '付款方式尚未确认。',
      recommendationCompared: '相较下一候选的匹配度优势',
      match: '匹配度',
      colName: '供应商',
      colLocation: '所在地',
      colEmail: '邮箱',
      colWebsite: '网站',
      colProduct: '产品名称',
      colBrand: '品牌',
      colModel: '型号',
      colSpecifications: '产品规格',
      colStandards: '标准',
      colUnitPrice: '单价',
      colQuoteConditions: '报价条件',
      colLeadTime: '交付周期',
      colPaymentTerms: '付款方式',
      colSources: '来源链接',
      expandDetails: '展开供应商详情',
      collapseDetails: '收起供应商详情',
      agentProgress: {
        eyebrow: '采购 Agent 思考中',
        runningTitle: '采购 Agent 正在思考',
        failedTitle: 'Agent 需要关注',
        description: '我正在实时检索供应商来源、过滤无关网页、提取供应商信号，并整理成可用于采购决策的候选名单。',
        progress: '进度',
        thoughtLogLabel: 'Agent 思考流',
        emptyText: '正在等待后端 Agent 的第一条思考...',
        activeLabel: '正在实时工作，新步骤会自动出现',
        messagesLabel: '消息',
        searchesLabel: '搜索',
        pagesLabel: '开页',
        pricesLabel: '抽价',
      },
    },
    comparison: {
      inputLabel: '采购需求输入',
      placeholder: '请输入采购需求，例如：需要采购一批挡风玻璃胶……',
      hint: '支持描述规格、数量、预算、交付要求等关键信息。',
      structuredLabel: '详细需求（可选 — 填写后优先使用）',
      structuredHint: '此处填写的商品名称、品牌、型号和数量会优先于自然语言描述，用于标准品比价。',
      productName: '商品名称',
      productNamePlaceholder: '例如：挡风玻璃胶筒',
      brand: '品牌（可选）',
      brandPlaceholder: '例如：Bosch、Henkel、Sika',
      model: '型号',
      modelPlaceholder: '例如：PU 8597 HMLC / 400 ml',
      quantity: '数量',
      quantityPlaceholder: '例如：500个、500千克、500套',
      hardFilters: '前置条件过滤 (Hard Filters)',
      budget: '价格区间',
      delivery: '配送时效',
      targetMarket: '目标市场',
      targetMarketOptions: {
        any: '不指定市场',
        Germany: '德国',
        Poland: '波兰',
      },
      polandCurrencyNotice: '波兰：仅纳入欧元报价；兹罗提结果将由网页来源补充。',
      minPrice: '最低',
      maxPrice: '最高',
      deliveryOptions: {
        unlimited: '不限时效',
        within3: '3 个工作日内',
        within7: '7 个工作日内',
      },
      weightTitle: '决策权重',
      weightHint: '拖动圆环或滑块，结果将按您的权重排序',
      weightPrice: '价格',
      weightDelivery: '货期',
      weightRating: '评价',
      customCriteriaTitle: '补充评价维度',
      customCriteriaHint: '可增加影响临近报价决策的业务维度；这些维度的权重会单独规范为 100%。',
      tableTitle: '决策对比表',
      colVendor: '供应商',
      colPlatform: '平台',
      colProduct: '产品',
      colMatch: '产品匹配度',
      colScore: '综合得分',
      colPrice: '单价',
      colDelivery: '交货周期',
      colPayment: '付款方式',
      colDeliveryMethod: '配送方式',
      colRating: '用户评分',
      colAction: '操作',
      recommended: '推荐',
      sourceLabel: '来源',
      sourceLocal: '本地报价库',
      sourceWeb: '网络搜索',
      sourceSerpApi: 'SerpApi · Google Shopping',
      sourceIdealo: 'Idealo 比价',
      sourceMarketplaceApi: '商城 API',
      priceVerification: '价格核验状态',
      sourceLinks: '来源链接',
      priceEvidence: '价格证据',
      priceFromMarketplaceApi: '商城 API 价格',
      priceFromSerpApi: 'SerpApi 商城价格',
      priceExtracted: '商品页价格提取',
      webNeedsManualCheck: '需人工核价',
      missingDelivery: '需核实交期',
      missingPayment: '需核实付款方式',
      missingDeliveryMethod: '需核实配送方式',
      noMatches: '当前过滤条件没有匹配报价，请调整价格、交期或目标市场后重试。',
      allWebNotice: '本地报价库没有匹配结果，当前候选来自网络搜索；网络价格/交期可能需要人工核验。',
      allMarketplaceApiNotice: '本地报价库没有匹配结果，当前价格来自已配置的商城 API；下单前请确认运费、税费和库存。',
      agentProgress: {
        eyebrow: '采购 Agent 思考中',
        runningTitle: '采购 Agent 正在进行标准品比价',
        failedTitle: 'Agent 需要关注',
        description: '我正在合并自然语言需求、前置过滤条件和权重偏好，优先检查本地标准品/报价库，并生成加权对比表。',
        progress: '进度',
        thoughtLogLabel: 'Agent 思考流',
        emptyText: '正在等待后端 Agent 的第一条思考...',
        activeLabel: '正在实时工作，新步骤会自动出现',
        messagesLabel: '消息',
        searchesLabel: '搜索',
        pagesLabel: '开页',
        pricesLabel: '抽价',
      },
      paymentTerms: {
        onAccount: '挂帐',
        prepayment: '预付款',
        card: '信用卡 / PayPal',
        unknown: '待确认',
      },
    },
    memory: {
      title: '对话记忆',
      subtitle: '这里记录您每一次的询问与输入内容',
      empty: '暂无对话记录。请在「寻找供应商」或「标准品比价」中发起检索。',
      moduleCol: '模块',
      queryCol: '询问与输入',
      filtersCol: '过滤条件',
      resultsCol: '结果数',
      feedbackCol: '反馈',
      timeCol: '时间',
      noFeedback: '暂无反馈',
      clearAll: '清空全部',
      confirmClear: '确定要清空所有记忆的对话吗？',
      chose: '已选择',
      openHint: '点击任意记录可在对应模块中重新打开',
      reopen: '重新打开',
      delete: '删除',
      deleteTitle: '删除记录',
      confirmDelete: '确定删除该条对话记录吗？此操作不可撤销。',
      clearTitle: '清空全部记录',
      searchPlaceholder: '按关键词、品类或产品名称搜索…',
      timeFilterAll: '全部时间',
      timeFilter7d: '近7天',
      timeFilter30d: '近30天',
      timeFilter1y: '近一年',
      moduleFilterAll: '全部',
      moduleSourcing: '寻找供应商',
      moduleComparison: '标准品比价',
      restoredBanner: '已从记忆恢复 — 下方显示上次的搜索结果',
    },
    feedback: {
      title: '分享您的反馈',
      subtitle: '请告诉我们您选择了哪家供应商，以及体验如何。',
      whichChosen: '本次您选择了哪家供应商 / 供货商？',
      quality: '货品质量',
      logistics: '物流速度',
      priceSat: '价格满意度',
      service: '服务',
      comment: '补充说明（选填）',
      commentPlaceholder: '关于本次选择的其他备注……',
      submit: '提交反馈',
      thanks: '感谢！您的反馈已记录。',
    },
    settings: {
      vaultTitle: 'API 密钥保险库',
      vaultDesc: '密钥保存在加密的云端保险库中，保存后不再以明文显示。',
      encrypted: '静态加密存储',
      keyLabel: '密钥名称',
      keyValue: '密钥内容',
      keyPlaceholder: '粘贴 API 密钥……',
      addKey: '保存至保险库',
      updated: '更新于',
      never: '从未',
      noKeys: '尚未存储任何密钥。',
      accountTitle: '账户',
      name: '姓名',
      company: '公司',
      role: '角色',
    },
    supplierDirectory: {
      title: '本地供应商资料库',
      description: '维护已合作供应商、联系方式与历史表现标签。寻源时系统会优先考虑这些记录。',
      addSupplier: '新增供应商',
      editSupplier: '编辑供应商',
      companyName: '公司名称',
      country: '国家 / 地区',
      city: '城市',
      contact: '联系人',
      email: '邮箱',
      phone: '电话',
      website: '网站',
      capabilities: '产品能力 / 品类',
      certifications: '认证或资质',
      tags: '采购标签',
      tagsHint: '用逗号分隔标签，例如：已合作供应商、历史表现良好。',
      notes: '内部备注',
      saveSupplier: '保存供应商',
      updateSupplier: '更新供应商',
      cancel: '取消',
      deleteSupplier: '删除',
      confirmDelete: '确定要删除这条本地供应商资料吗？此操作不可撤销。',
      empty: '暂无本地供应商资料。添加已合作或可信赖的供应商后，未来搜索会优先考虑它们。',
      localOnly: '本地资料',
      created: '创建于',
      updated: '更新于',
      preferredTag: '已合作供应商',
      reliableTag: '历史表现良好',
      preferred: '在后续搜索中优先推荐该供应商',
      performanceNote: '历史表现',
      performancePlaceholder: '例如：准时交付 96%；2025 年质量评分 4.7/5',
      minimumOrderQuantity: '最低订购量',
      productionCapacity: '生产能力',
      environmentalStandards: '环保标准',
      saving: '保存中...',
      loadError: '无法加载供应商资料库，已有本地记录不会丢失。',
      saveError: '无法保存该供应商，请重试。',
    },
  },
}
