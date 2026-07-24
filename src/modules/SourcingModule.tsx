import { useEffect, useState } from 'react'
import type { Translation } from '../i18n'
import type { Supplier, ConversationRecord, EvaluationCriterion, FeedbackRecord } from '../types'
import { MOCK_SUPPLIERS } from '../data/suppliers'
import { api, apiEnabled, ApiError, type SourcingJob, type SourcingStructuredFields, withTimeout } from '../lib/api'
import { useMemory } from '../context/MemoryContext'
import { StepIndicator, ExportPrintToolbar, AnalyzeButton, RestoredBanner } from '../components/shared'
import { FeedbackModal } from '../components/FeedbackModal'
import { SearchIcon } from '../components/icons'
import { AgentChatProgress } from '../components/AgentChatProgress'
import { DEFAULT_SOURCING_CRITERIA, normalizeCriteria, SourcingCriteriaControl } from '../components/SourcingCriteriaControl'
import { SupplierResultsTable } from '../components/SupplierResultsTable'

function RecommendationBrief({ results, t }: { results: Supplier[]; t: Translation }) {
  const s = t.sourcing
  const ranked = [...results].sort((a, b) => b.matchScore - a.matchScore)
  const candidate = ranked[0]
  const runnerUp = ranked[1]

  if (!candidate) return null

  const reasons = [
    `${Math.round(candidate.matchScore)}% ${s.match}`,
    candidate.weightedCriteriaScore == null ? null : `${s.criteriaTitle}: ${Math.round(candidate.weightedCriteriaScore)}%`,
    isDatabaseSupplier(candidate) ? s.recommendationLocal : null,
    candidate.productName || candidate.brand || candidate.model ? s.recommendationProduct : null,
    (candidate.certifications?.length ?? 0) > 0 ? s.recommendationCertifications : null,
    runnerUp && candidate.matchScore > runnerUp.matchScore
      ? `${s.recommendationCompared}: ${Math.round(candidate.matchScore - runnerUp.matchScore)}%`
      : null,
  ].filter((reason): reason is string => Boolean(reason))

  const hasContact = Boolean(candidate.email || candidate.phone || candidate.website)
  const hasQuote = candidate.unitPrice != null || Boolean(candidate.quoteConditions)
  const risks = [
    !isDatabaseSupplier(candidate) ? s.recommendationWebRisk : null,
    !hasContact ? s.recommendationContactRisk : null,
    !hasQuote ? s.recommendationQuoteRisk : null,
    !candidate.deliveryLeadTime ? s.recommendationDeliveryRisk : null,
    !candidate.paymentTerms ? s.recommendationPaymentRisk : null,
    candidate.verificationStatus && candidate.verificationStatus !== 'verified'
      ? `${s.cardVerification}: ${candidate.verificationStatus}`
      : null,
  ].filter((risk): risk is string => Boolean(risk))

  return (
    <section className="procurement-recommendation" aria-labelledby="sourcing-recommendation-title">
      <div className="procurement-recommendation__lead">
        <p className="procurement-recommendation__eyebrow">{s.recommendationTitle}</p>
        <h3 id="sourcing-recommendation-title">{s.recommendationLead}: {candidate.name}</h3>
      </div>
      <div className="procurement-recommendation__column">
        <h4>{s.recommendationReasons}</h4>
        <ul>
          {reasons.map((reason) => <li key={reason}>{reason}</li>)}
        </ul>
      </div>
      <div className="procurement-recommendation__column procurement-recommendation__column--risk">
        <h4>{s.recommendationRisks}</h4>
        {risks.length > 0 ? (
          <ul>{risks.map((risk) => <li key={risk}>{risk}</li>)}</ul>
        ) : (
          <p>{s.recommendationNoRisk}</p>
        )}
      </div>
    </section>
  )
}

type SearchStatus = 'idle' | 'running' | 'success' | 'empty' | 'error'

const JOB_TIMEOUT_MS = 120_000
const JOB_POLL_INTERVAL_MS = 1_500

function isDatabaseSupplier(supplier: Supplier): boolean {
  const source = supplier.source?.trim().toLowerCase()
  // Offline demo rows have no source metadata; treat them as curated local data.
  return !source || source === 'database' || source === 'local' || source === 'db'
}

export function SourcingModule({
  t,
  restore,
}: {
  t: Translation
  /** When set, the module opens pre-filled with this past conversation. */
  restore: ConversationRecord | null
}) {
  const { remember, attachFeedback } = useMemory()

  // ═══════════════════════════════════════════════════════════════════════════
  // Debug logging — detects unexpected page reloads.
  // ═══════════════════════════════════════════════════════════════════════════
  useEffect(() => {
    try {
      const nav = performance.getEntriesByType?.('navigation')?.[0] as PerformanceNavigationTiming | undefined
      console.log(`[SourcingModule] MOUNTED (type=${nav?.type ?? 'unknown'})`, {
        restore: !!restore,
      })
    } catch {
      console.log(`[SourcingModule] MOUNTED (perf API unavailable)`)
    }
  }, [restore])

  // Cleanup any stale sessionStorage artifacts from previous versions.
  useEffect(() => {
    try {
      ;['_sourcing_save', '_sourcing_save_v2', '_sourcing_reloading', '_sourcing_analyzing', '_sourcing_convId']
        .forEach(k => sessionStorage.removeItem(k))
    } catch { /* ignore */ }
  }, [])

  // ── State initializers ────────────────────────────────────────────────
  // Every time this module mounts, it starts FRESH — no cache, no persistence.
  // Only the `restore` prop (from Memory module → conversation history) can
  // pre-fill state.

  const savedResults = Array.isArray(restore?.resultsSnapshot)
    ? (restore.resultsSnapshot as unknown as Supplier[])
    : undefined
  const savedRestore = restore?.restore

  const [query, setQuery] = useState(savedRestore?.query ?? '')
  const [results, setResults] = useState<Supplier[]>(
    savedResults ?? (apiEnabled ? [] : MOCK_SUPPLIERS)
  )
  const [currentStep, setCurrentStep] = useState(savedResults || !apiEnabled ? 3 : 0)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [hasRun, setHasRun] = useState(!!savedResults || !apiEnabled)
  const [searchStatus, setSearchStatus] = useState<SearchStatus>(
    savedResults ? 'success' : apiEnabled ? 'idle' : 'success'
  )
  const [searchJob, setSearchJob] = useState<SourcingJob | null>(null)
  const [searchError, setSearchError] = useState(false)
  const [errorDetail, setErrorDetail] = useState<string | null>(null)
  const [feedbackFor, setFeedbackFor] = useState<string | null>(null)
  const [activeConversationId, setActiveConversationId] = useState<string | null>(
    restore?.id ?? null
  )
  const hasOnlyWebResults = results.length > 0 && results.every((supplier) => !isDatabaseSupplier(supplier))

  // ── Structured fields state (双模输入表单) ──────────────────────────────
  const [structProductName, setStructProductName] = useState(savedRestore?.productName ?? '')
  const [structQuantity, setStructQuantity] = useState(savedRestore?.quantity ?? '')
  const [structBrand, setStructBrand] = useState(savedRestore?.brand ?? '')
  const [structModel, setStructModel] = useState(savedRestore?.model ?? '')
  const [structSpecifications, setStructSpecifications] = useState(savedRestore?.specifications ?? '')
  const [structStandards, setStructStandards] = useState(savedRestore?.standards ?? '')
  const [structCategory, setStructCategory] = useState(savedRestore?.structuredCategory ?? '')
  const [structCountry, setStructCountry] = useState(savedRestore?.structuredCountry ?? '')
  const [structCerts, setStructCerts] = useState(savedRestore?.structuredCerts ?? '')
  const [criteria, setCriteria] = useState<EvaluationCriterion[]>(() =>
    savedRestore?.sourcingCriteria?.length
      ? normalizeCriteria(savedRestore.sourcingCriteria)
      : DEFAULT_SOURCING_CRITERIA,
  )

  /** Append structured info to the NL query for backward compat / mock mode. */
  const buildEnhancedQuery = () => {
    if (!structProductName && !structQuantity && !structBrand && !structModel && !structSpecifications && !structStandards && !structCategory && !structCountry && !structCerts)
      return query
    const parts: string[] = []
    if (structProductName) parts.push(`Product: ${structProductName}`)
    if (structQuantity) parts.push(`Quantity: ${structQuantity}`)
    if (structBrand) parts.push(`Brand: ${structBrand}`)
    if (structModel) parts.push(`Model: ${structModel}`)
    if (structSpecifications) parts.push(`Specifications: ${structSpecifications}`)
    if (structStandards) parts.push(`Standards: ${structStandards}`)
    if (structCategory) parts.push(`Category: ${structCategory}`)
    if (structCountry) parts.push(`Target Country: ${structCountry}`)
    if (structCerts) parts.push(`Certifications: ${structCerts}`)
    return `${query}\n---\n${parts.join('\n')}`
  }

  const getStructuredPayload = (): SourcingStructuredFields | undefined => {
    const structured: SourcingStructuredFields = {
      productName: structProductName || undefined,
      quantity: structQuantity || undefined,
      brand: structBrand || undefined,
      model: structModel || undefined,
      specifications: structSpecifications || undefined,
      standards: structStandards || undefined,
      category: structCategory || undefined,
      country: structCountry || undefined,
      targetRegion: structCountry || undefined,
      certifications: structCerts || undefined,
    }
    return Object.values(structured).some(Boolean) ? structured : undefined
  }

  /**
   * Let the agent make the natural-language input reviewable instead of opaque.
   * Existing manual values always win, so a late parser response cannot overwrite
   * a buyer's correction.
   */
  const applyParsedIntent = (intent?: Record<string, unknown> | null) => {
    if (!intent) return
    const category = typeof intent.category === 'string' ? intent.category : ''
    const country = typeof intent.country === 'string' ? intent.country : ''
    const certifications = Array.isArray(intent.certifications)
      ? intent.certifications.filter((value): value is string => typeof value === 'string').join(', ')
      : ''
    const categoryIsVisible = Boolean(category && Object.hasOwn(t.sourcing.categories, category))

    if (categoryIsVisible && !structCategory) setStructCategory(category)
    if (country && !structCountry) setStructCountry(country)
    if (certifications && !structCerts) setStructCerts(certifications)
  }

  /** Human-readable filter summary for the memory card. */
  const buildFilterSummary = (): Record<string, string> => {
    const s: Record<string, string> = {}
    const add = (k: string, v: string) => { if (v) s[k] = v }
    add(t.sourcing.productName, structProductName)
    add(t.sourcing.quantity, structQuantity)
    add(t.sourcing.brand, structBrand)
    add(t.sourcing.model, structModel)
    add(t.sourcing.specifications, structSpecifications)
    add(t.sourcing.standards, structStandards)
    add(t.sourcing.structuredCategory, structCategory)
    add(t.sourcing.structuredCountry, structCountry)
    add(t.sourcing.structuredCerts, structCerts)
    if (criteria.length > 0) add(t.sourcing.criteriaTitle, criteria.map((criterion) => `${criterion.label} ${criterion.weight}%`).join(' · '))
    return s
  }

  // Builds the memory record for the current query + all entered inputs.
  const buildRecord = (list: Supplier[]) => {
    const enhancedQuery = buildEnhancedQuery()
    const structured = getStructuredPayload()
    return {
      module: 'sourcing' as const,
      query: query.trim() || '(no text — browse all suppliers)',
      filters: buildFilterSummary(),
      restore: {
        query,
        productName: structProductName || undefined,
        quantity: structQuantity || undefined,
        brand: structBrand || undefined,
        model: structModel || undefined,
        specifications: structSpecifications || undefined,
        standards: structStandards || undefined,
        structuredCategory: structCategory || undefined,
        structuredCountry: structCountry || undefined,
        structuredCerts: structCerts || undefined,
        sourcingCriteria: criteria,
      },
      requestSnapshot: { query, enhancedQuery, structured: structured ?? null, criteria },
      resultCount: list.length,
      candidateNames: list.map((r) => r.name),
      resultsSnapshot: list as unknown as Record<string, unknown>[],
    }
  }

  const pollSearchJob = async (jobId: string): Promise<SourcingJob> => {
    const deadline = Date.now() + JOB_TIMEOUT_MS
    for (;;) {
      const remaining = deadline - Date.now()
      if (remaining <= 0) throw new ApiError(t.common.searchTimeout, 'TIMEOUT', 408)
      await new Promise((resolve) => setTimeout(resolve, Math.min(JOB_POLL_INTERVAL_MS, remaining)))
      const job = await withTimeout(api.sourcing.getJob(jobId), Math.max(1, deadline - Date.now()), t.common.searchTimeout)
      setSearchJob(job)

      if (job.progress >= 35 && job.progress < 75) setCurrentStep(2)
      if (job.progress >= 75) setCurrentStep(3)
      if (job.status === 'completed' || job.status === 'failed') return job
    }
  }

  const handleAnalyze = async () => {
    console.log(`[SourcingModule] handleAnalyze STARTED`, { query: query.slice(0, 60) })
    const enhancedQuery = buildEnhancedQuery()
    const structured = getStructuredPayload()
    setIsAnalyzing(true)
    setHasRun(false)
    setSearchStatus('running')
    setSearchJob(null)
    setSearchError(false)
    setErrorDetail(null)
    setResults([])
    setCurrentStep(1)

    let list: Supplier[]
    try {
      if (apiEnabled) {
        try {
          // Preferred path: async job with live "Agent Thinking" progress (SSE).
          // Send both enhanced text and structured fields; older backends ignore unknown structured payloads.
          const created = await withTimeout(
            api.sourcing.createJob(enhancedQuery, structured, criteria),
            JOB_TIMEOUT_MS,
            t.common.searchTimeout,
          )
          setSearchJob(created)
          applyParsedIntent(created.intent)
          console.log(`[SourcingModule] Job created:`, created.jobId)
          let finished: SourcingJob
          try {
            finished = await withTimeout(
              api.sourcing.streamJob(created.jobId, (job) => {
                setSearchJob(job)
                if (job.progress >= 35 && job.progress < 75) setCurrentStep(2)
                if (job.progress >= 75) setCurrentStep(3)
              }),
              JOB_TIMEOUT_MS,
              t.common.searchTimeout,
            )
          } catch (streamError) {
            if (streamError instanceof ApiError && streamError.code === 'TIMEOUT') throw streamError
            // Some deployment proxies buffer or close long-lived streams. Keep the
            // stable polling path as a fallback so the UX still progresses.
            finished = await pollSearchJob(created.jobId)
          }
          console.log(`[SourcingModule] Job finished:`, { status: finished.status, results: finished.results?.length })
          applyParsedIntent(finished.intent)
          list = finished.results ?? []
          if (finished.status === 'failed') {
            setSearchError(true)
            setErrorDetail(finished.error ?? null)
            setSearchStatus('error')
          } else {
            setResults(list)
            setSearchStatus(list.length > 0 ? 'success' : 'empty')
            setCurrentStep(3)
          }
        } catch (jobError) {
          if (jobError instanceof ApiError && jobError.code === 'TIMEOUT') throw jobError
          // Backend has no job endpoints yet (older deploy → 404). Fall back to the
          // plain synchronous search so results still load (without live progress).
          setSearchJob(null)
          const res = await api.sourcing.search(enhancedQuery, structured, criteria)
          applyParsedIntent(res.intent)
          list = res.results ?? []
          setResults(list)
          setSearchStatus(list.length > 0 ? 'success' : 'empty')
          setCurrentStep(3)
        }
      } else {
        // Keep the step animation visible in mock mode.
        await new Promise((r) => setTimeout(r, 1800))
        list = MOCK_SUPPLIERS
        setResults(list)
        setSearchStatus('success')
        setCurrentStep(3)
      }
      setHasRun(true)
      console.log(`[SourcingModule] Results set, saving to memory...`, { resultCount: list.length })
      // Save to conversation memory independently — a failure here must NOT
      // clear already-fetched results.
      try {
        const convId = await remember(buildRecord(list))
        console.log(`[SourcingModule] remember() succeeded:`, convId)
        setActiveConversationId(convId)
      } catch (e) {
        console.warn(`[SourcingModule] Failed to save conversation to memory; search results unaffected.`, e)
      }
      console.log(`[SourcingModule] handleAnalyze SUCCESS, hasRun=true`)
    } catch (e) {
      console.error(`[SourcingModule] handleAnalyze CATCH:`, e)
      setErrorDetail(e instanceof Error ? e.message : String(e))
      setSearchError(true)
      setSearchStatus('error')
      setHasRun(true)
      setSearchJob(null)
    } finally {
      setIsAnalyzing(false)
    }
  }

  const handleFeedbackSubmit = async (feedback: FeedbackRecord) => {
    // Lazily create a conversation if feedback is given before running analysis.
    const id = activeConversationId ?? (await remember(buildRecord(results)))
    setActiveConversationId(id)
    await attachFeedback(id, feedback)
  }

  return (
    <div className="sourcing-module space-y-8">
      <section className="procurement-panel procurement-panel--emphasis p-6 print:hidden">
        <label className="procurement-label mb-2 block">{t.sourcing.inputLabel}</label>
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          rows={3}
          placeholder={t.sourcing.placeholder}
          className="procurement-input w-full resize-none border px-4 py-3 text-sm focus:outline-none"
        />
        <p className="procurement-helper mt-1.5 text-xs">{t.sourcing.hint}</p>

        {/* ── Structured form (双模输入) ───────────────────────────────── */}
        <div className="procurement-structured mt-6 border-t pt-6">
          <p className="procurement-structured__title">
            {t.sourcing.structuredLabel}
          </p>
          <p className="procurement-structured-hint mb-4 mt-0.5 text-xs">{t.sourcing.structuredHint}</p>

          <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
            {/* Product Name */}
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{t.sourcing.productName}</label>
              <input
                type="text"
                value={structProductName}
                onChange={(e) => setStructProductName(e.target.value)}
                placeholder={t.sourcing.productNamePlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>

            {/* Quantity */}
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{t.sourcing.quantity}</label>
              <input
                type="text"
                value={structQuantity}
                onChange={(e) => setStructQuantity(e.target.value)}
                placeholder={t.sourcing.quantityPlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>

            {/* Brand */}
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{t.sourcing.brand}</label>
              <input
                type="text"
                value={structBrand}
                onChange={(e) => setStructBrand(e.target.value)}
                placeholder={t.sourcing.brandPlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>

            {/* Model */}
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{t.sourcing.model}</label>
              <input
                type="text"
                value={structModel}
                onChange={(e) => setStructModel(e.target.value)}
                placeholder={t.sourcing.modelPlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>

            {/* Specifications */}
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{t.sourcing.specifications}</label>
              <input
                type="text"
                value={structSpecifications}
                onChange={(e) => setStructSpecifications(e.target.value)}
                placeholder={t.sourcing.specificationsPlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>

            {/* Standards */}
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{t.sourcing.standards}</label>
              <input
                type="text"
                value={structStandards}
                onChange={(e) => setStructStandards(e.target.value)}
                placeholder={t.sourcing.standardsPlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>

            {/* Category */}
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{t.sourcing.structuredCategory}</label>
              <select
                value={structCategory}
                onChange={(e) => setStructCategory(e.target.value)}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              >
                <option value="">{t.sourcing.categoryAll}</option>
                {Object.entries(t.sourcing.categories).map(([key, label]) => (
                  <option key={key} value={key}>{label}</option>
                ))}
              </select>
            </div>

            {/* Country */}
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{t.sourcing.structuredCountry}</label>
              <input
                type="text"
                value={structCountry}
                onChange={(e) => setStructCountry(e.target.value)}
                placeholder={t.sourcing.countryPlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>

            {/* Certifications */}
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{t.sourcing.structuredCerts}</label>
              <input
                type="text"
                value={structCerts}
                onChange={(e) => setStructCerts(e.target.value)}
                placeholder={t.sourcing.certsPlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>
          </div>

          <SourcingCriteriaControl criteria={criteria} onChange={setCriteria} t={t} />
        </div>
        {/* ── End structured form ────────────────────────────────────────── */}

        <div className="mt-4 flex justify-end">
          <AnalyzeButton isAnalyzing={isAnalyzing} onClick={handleAnalyze} t={t} />
        </div>
      </section>

      <section className="procurement-panel procurement-steps-panel px-8 py-6 print:hidden">
        <StepIndicator currentStep={currentStep} steps={t.steps} />
      </section>

      {searchJob && searchStatus !== 'idle' && <AgentChatProgress key={searchJob.jobId} job={searchJob} copy={t.sourcing.agentProgress} />}

      {hasRun && (
        <section className="sourcing-results space-y-4">
          {savedResults && <RestoredBanner t={t} />}
          <div className="sourcing-results__header flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <h2 className="procurement-results-title font-semibold">
                {t.common.resultsFound(results.length)}
              </h2>
              {!searchError && (
                <span className="procurement-complete-badge px-3 py-1 text-xs font-medium print:hidden">
                  {t.common.analysisComplete}
                </span>
              )}
            </div>
            <ExportPrintToolbar
              t={t}
              filename="fuyao-suppliers.xlsx"
              sheetName="Suppliers"
              columns={[
                t.sourcing.colName,
                t.sourcing.cardEstablished,
                t.sourcing.colLocation,
                t.sourcing.cardAddress,
                t.sourcing.cardContact,
                t.sourcing.colEmail,
                t.sourcing.colWebsite,
                t.sourcing.cardEmployees,
                t.sourcing.cardRevenue,
                t.sourcing.cardCapabilities,
                t.sourcing.cardCerts,
                t.sourcing.colProduct,
                t.sourcing.colBrand,
                t.sourcing.colModel,
                t.sourcing.colSpecifications,
                t.sourcing.colStandards,
                t.sourcing.colUnitPrice,
                t.sourcing.colQuoteConditions,
                t.sourcing.colLeadTime,
                t.sourcing.colPaymentTerms,
                t.sourcing.cardVerification,
                t.sourcing.sourceLabel,
                t.sourcing.colSources,
                t.sourcing.cardEvidence,
                t.sourcing.match,
              ]}
              rows={results.map((r) => [
                r.name,
                r.established ?? '',
                [r.city, r.country].filter(Boolean).join(', '),
                r.address ?? '',
                [r.contactPerson, r.phone].filter(Boolean).join(' · '),
                r.email ?? '',
                r.website ?? '',
                r.employees ?? '',
                r.annualRevenue ?? '',
                (r.capabilities ?? []).join('; '),
                (r.certifications ?? []).join('; '),
                r.productName ?? '',
                r.brand ?? '',
                r.model ?? '',
                r.specifications ?? '',
                (r.standards ?? []).join('; '),
                r.unitPrice == null ? '' : `${r.currency ?? 'EUR'} ${r.unitPrice}`,
                r.quoteConditions ?? '',
                r.deliveryLeadTime ?? '',
                r.paymentTerms ?? '',
                r.verificationStatus ?? '',
                isDatabaseSupplier(r) ? t.sourcing.localDatabaseTag : t.sourcing.webSearchTag,
                (r.sourceUrls ?? []).join('; '),
                (r.evidenceSnippets ?? []).join(' | '),
                `${Math.round(r.matchScore)}%`,
              ])}
            />
          </div>

          {!searchError && hasOnlyWebResults && (
            <div className="procurement-notice border px-5 py-3 text-sm font-medium print:hidden">
              {t.sourcing.allWebNotice}
            </div>
          )}

          {!searchError && results.length > 0 && (
            <RecommendationBrief results={results} t={t} />
          )}

          {searchError ? (
            <div role="alert" className="flex flex-col items-center justify-center rounded-xl border border-dashed border-red-200 bg-red-50 p-12 text-red-500">
              <SearchIcon className="mb-3 h-7 w-7" />
              <p className="text-sm">{t.common.searchError}</p>
              {errorDetail && <p className="mt-2 max-w-2xl text-center text-xs text-red-600">{errorDetail}</p>}
              <button
                type="button"
                onClick={() => void handleAnalyze()}
                disabled={isAnalyzing}
                className="procurement-primary-action mt-5 inline-flex items-center px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-70"
              >
                {t.common.analyze}
              </button>
            </div>
          ) : results.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white p-12 text-slate-400">
              <SearchIcon className="mb-3 h-7 w-7" />
              <p className="text-sm">{t.common.empty}</p>
            </div>
          ) : (
            <SupplierResultsTable
              key={results.map((supplier) => supplier.id).join('|')}
              suppliers={results}
              t={t}
              onSelect={setFeedbackFor}
            />
          )}
        </section>
      )}

      {feedbackFor && (
        <FeedbackModal
          options={results.map((r) => r.name)}
          defaultChosen={feedbackFor}
          t={t}
          onSubmit={handleFeedbackSubmit}
          onClose={() => setFeedbackFor(null)}
        />
      )}
    </div>
  )
}
