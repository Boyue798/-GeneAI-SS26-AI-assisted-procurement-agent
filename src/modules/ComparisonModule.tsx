import { useMemo, useState } from 'react'
import type { Translation } from '../i18n'
import type {
  ComparisonItem,
  ConversationRecord,
  DeliveryOptionKey,
  EvaluationCriterion,
  FactorWeights,
  FeedbackRecord,
} from '../types'
import { MOCK_COMPARISON } from '../data/comparison'
import { api, apiEnabled, ApiError, type ComparisonJob, withTimeout } from '../lib/api'
import { useMemory } from '../context/MemoryContext'
import { StepIndicator, MatchScoreBadge, ExportPrintToolbar, AnalyzeButton, RestoredBanner } from '../components/shared'
import { FeedbackModal } from '../components/FeedbackModal'
import { WeightControl } from '../components/WeightControl'
import { normalizeCriteria, SourcingCriteriaControl } from '../components/SourcingCriteriaControl'
import { AgentChatProgress } from '../components/AgentChatProgress'
import { SpinnerIcon } from '../components/icons'

const DELIVERY_KEYS: DeliveryOptionKey[] = ['unlimited', 'within3', 'within7']
const TARGET_MARKETS = ['Germany', 'Poland'] as const
type TargetMarket = (typeof TARGET_MARKETS)[number] | ''

// Default importance: price-led, then delivery, then reviews.
const DEFAULT_WEIGHTS: FactorWeights = { price: 40, delivery: 35, rating: 25 }

/** A quote plus its user-weighted decision score (0–100). */
type ScoredItem = ComparisonItem & { score: number }

interface RankOptions {
  minPrice: string
  maxPrice: string
  deliveryTime: DeliveryOptionKey
  weights: FactorWeights
  criteria: EvaluationCriterion[]
}

type SearchStatus = 'idle' | 'running' | 'success' | 'empty' | 'error'

const JOB_TIMEOUT_MS = 120_000
const JOB_POLL_INTERVAL_MS = 1_500

function isSerpApiItem(item: ComparisonItem): boolean {
  return item.sourceDetail === 'marketplace:serpapi'
    || item.platform.trim().toLowerCase() === 'google shopping (serpapi)'
}

function isIdealoItem(item: ComparisonItem): boolean {
  return item.sourceDetail === 'idealo' || item.platform.trim().toLowerCase().includes('idealo')
}

function comparisonSourceLabel(item: ComparisonItem, copy: Translation['comparison']): string {
  if (item.source !== 'web') return copy.sourceLocal
  if (isSerpApiItem(item)) return copy.sourceSerpApi
  if (isIdealoItem(item)) return copy.sourceIdealo
  if (item.priceConfidence === 'api') return copy.sourceMarketplaceApi
  return copy.sourceWeb
}

function priceVerificationLabel(item: ComparisonItem, copy: Translation['comparison']): string {
  if (item.source !== 'web') return copy.sourceLocal
  if (isSerpApiItem(item)) return copy.priceFromSerpApi
  if (item.priceConfidence === 'api') return copy.priceFromMarketplaceApi
  if (item.priceConfidence === 'extracted' || item.unitPriceEur != null) return copy.priceExtracted
  return copy.webNeedsManualCheck
}

function externalHref(value: string): string {
  const trimmed = value.trim()
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  if (trimmed.startsWith('//')) return `https:${trimmed}`
  return `https://${trimmed.replace(/^\/+/, '')}`
}

/** Normalize a value to 0–1; higher is always better. */
function normalize(value: number, min: number, max: number, higherIsBetter: boolean): number {
  if (max === min) return 1
  const t = (value - min) / (max - min)
  return higherIsBetter ? t : 1 - t
}

/**
 * Apply hard filters (price range + delivery time), then rank by the
 * user-weighted composite score over price / delivery / reviews.
 * Pure function — used both for rendering and for logging to memory.
 */
function rankItems(items: ComparisonItem[], { minPrice, maxPrice, deliveryTime, weights, criteria }: RankOptions): ScoredItem[] {
  const min = minPrice ? Number(minPrice) : -Infinity
  const max = maxPrice ? Number(maxPrice) : Infinity
  const deliveryCap = deliveryTime === 'within3' ? 3 : deliveryTime === 'within7' ? 7 : Infinity

  const filtered = items.filter((r) => {
    const priceOk = r.unitPriceEur == null || (r.unitPriceEur >= min && r.unitPriceEur <= max)
    const deliveryOk = r.deliveryDays == null || r.deliveryDays <= deliveryCap
    return priceOk && deliveryOk
  })
  if (filtered.length === 0) return []

  const knownPrices = filtered.map((r) => r.unitPriceEur).filter((price): price is number => price != null)
  const knownDays = filtered.map((r) => r.deliveryDays).filter((days): days is number => days != null)
  const ratings = filtered.map((r) => r.rating)
  const minP = knownPrices.length ? Math.min(...knownPrices) : 0
  const maxP = knownPrices.length ? Math.max(...knownPrices) : 0
  const minD = knownDays.length ? Math.min(...knownDays) : 0
  const maxD = knownDays.length ? Math.max(...knownDays) : 0
  const minR = Math.min(...ratings)
  const maxR = Math.max(...ratings)

  const wp = weights.price / 100
  const wd = weights.delivery / 100
  const wr = weights.rating / 100

  return filtered
    .map<ScoredItem>((r) => {
      const sPrice = r.unitPriceEur == null ? 0.15 : normalize(r.unitPriceEur, minP, maxP, false)
      const sDelivery = r.deliveryDays == null ? 0.2 : normalize(r.deliveryDays, minD, maxD, false)
      const sRating = normalize(r.rating, minR, maxR, true)
      const sourcePenalty = r.source === 'web' && (r.unitPriceEur == null || r.deliveryDays == null) ? 8 : 0
      const locallyWeightedScore = Math.max(0, Math.round((wp * sPrice + wd * sDelivery + wr * sRating) * 100) - sourcePenalty)
      // When the backend has evaluated the buyer's extra criteria, preserve its
      // evidence-based composite rather than overwriting it with the legacy
      // three-factor client calculation. Older backends and offline data keep
      // the reliable local fallback.
      const score = criteria.length > 0 && typeof r.weightedCriteriaScore === 'number'
        ? Math.max(0, Math.round(r.matchScore))
        : locallyWeightedScore
      return { ...r, score }
    })
    .sort((a, b) => b.score - a.score || (a.unitPriceEur ?? Number.POSITIVE_INFINITY) - (b.unitPriceEur ?? Number.POSITIVE_INFINITY))
}

function PriceInput({
  value,
  onChange,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  placeholder: string
}) {
  return (
    <div className="relative w-28">
      <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-slate-400">€</span>
      <input
        type="number"
        min={0}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="procurement-input w-full border py-1.5 pl-7 pr-2 text-sm focus:outline-none"
      />
    </div>
  )
}

export function ComparisonModule({
  t,
  restore,
}: {
  t: Translation
  /** When set, the module opens pre-filled with this past conversation. */
  restore: ConversationRecord | null
}) {
  const c = t.comparison
  const { remember, attachFeedback } = useMemory()
  const init = restore?.restore
  const restoredResults = Array.isArray(restore?.resultsSnapshot)
    ? (restore.resultsSnapshot as unknown as ComparisonItem[])
    : undefined

  const [requirement, setRequirement] = useState(init?.query ?? '')
  const [productName, setProductName] = useState(init?.productName ?? '')
  const [brand, setBrand] = useState(init?.brand ?? '')
  const [model, setModel] = useState(init?.model ?? '')
  const [quantity, setQuantity] = useState(init?.quantity ?? '')
  const [minPrice, setMinPrice] = useState(init?.minPrice ?? '')
  const [maxPrice, setMaxPrice] = useState(init?.maxPrice ?? '')
  const [deliveryTime, setDeliveryTime] = useState<DeliveryOptionKey>(init?.deliveryTime ?? 'unlimited')
  const [targetMarket, setTargetMarket] = useState<TargetMarket>(
    init?.comparisonCountry === 'Germany' || init?.comparisonCountry === 'Poland'
      ? init.comparisonCountry
      : '',
  )
  const [weights, setWeights] = useState<FactorWeights>(init?.weights ?? DEFAULT_WEIGHTS)
  const [criteria, setCriteria] = useState<EvaluationCriterion[]>(() =>
    init?.comparisonCriteria?.length ? normalizeCriteria(init.comparisonCriteria) : [],
  )
  const [items, setItems] = useState<ComparisonItem[]>(restoredResults ?? (apiEnabled ? [] : MOCK_COMPARISON))
  const [currentStep, setCurrentStep] = useState(restoredResults || !apiEnabled ? 3 : 0)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [searchStatus, setSearchStatus] = useState<SearchStatus>(
    restoredResults ? 'success' : apiEnabled ? 'idle' : 'success',
  )
  const [comparisonJob, setComparisonJob] = useState<ComparisonJob | null>(null)
  const [searchError, setSearchError] = useState(false)
  const [errorDetail, setErrorDetail] = useState<string | null>(null)
  const [feedbackFor, setFeedbackFor] = useState<string | null>(null)
  // Reopening a past conversation re-links feedback to that same record.
  const [activeConversationId, setActiveConversationId] = useState<string | null>(restore?.id ?? null)

  const rows = useMemo<ScoredItem[]>(
    () => rankItems(items, { minPrice, maxPrice, deliveryTime, weights, criteria }),
    [items, minPrice, maxPrice, deliveryTime, weights, criteria],
  )

  // Top of the ranked list is the recommended pick.
  const recommendedId = rows.length > 0 ? rows[0].id : null

  /** Append explicit comparison fields to the free-text requirement for backend compatibility. */
  const buildEnhancedRequirement = () => {
    if (!productName && !brand && !model && !quantity && !targetMarket) return requirement
    const parts: string[] = []
    if (productName) parts.push(`Product: ${productName}`)
    if (brand) parts.push(`Brand: ${brand}`)
    if (model) parts.push(`Model: ${model}`)
    if (quantity) parts.push(`Quantity: ${quantity}`)
    if (targetMarket) parts.push(`Target Country: ${targetMarket}`)
    return `${requirement}\n---\n${parts.join('\n')}`
  }

  /** Human-readable structured input summary for the conversation memory card. */
  const buildStructuredSummary = (): Record<string, string> => {
    const summary: Record<string, string> = {}
    if (productName) summary[c.productName] = productName
    if (brand) summary[c.brand] = brand
    if (model) summary[c.model] = model
    if (quantity) summary[c.quantity] = quantity
    if (targetMarket) summary[c.targetMarket] = c.targetMarketOptions[targetMarket]
    return summary
  }

  // Builds the memory record for the current query + all entered inputs.
  const buildRecord = (ranked: ScoredItem[]) => ({
    module: 'comparison' as const,
    query: requirement.trim() || '(no text — filter browse)',
    filters: {
      ...buildStructuredSummary(),
      [c.budget]: `${minPrice || '0'} – ${maxPrice || '∞'} €`,
      [c.delivery]: c.deliveryOptions[deliveryTime],
      [c.weightTitle]: `${c.weightPrice} ${weights.price}% · ${c.weightDelivery} ${weights.delivery}% · ${c.weightRating} ${weights.rating}%`,
      ...(criteria.length > 0
        ? { [c.customCriteriaTitle]: criteria.map((criterion) => `${criterion.label} ${criterion.weight}%`).join(' · ') }
        : {}),
    },
    restore: { query: requirement, productName, brand, model, quantity, minPrice, maxPrice, deliveryTime, comparisonCountry: targetMarket || undefined, weights, comparisonCriteria: criteria },
    requestSnapshot: {
      query: requirement,
      enhancedQuery: buildEnhancedRequirement(),
      structured: {
        productName: productName || undefined,
        brand: brand || undefined,
        model: model || undefined,
        quantity: quantity || undefined,
      },
      minPrice,
      maxPrice,
      deliveryTime,
      country: targetMarket || undefined,
      weights,
      criteria,
    },
    resultCount: ranked.length,
    candidateNames: ranked.map((row) => row.vendor),
    resultsSnapshot: ranked as unknown as Record<string, unknown>[],
  })

  const pollComparisonJob = async (jobId: string): Promise<ComparisonJob> => {
    const deadline = Date.now() + JOB_TIMEOUT_MS
    for (;;) {
      const remaining = deadline - Date.now()
      if (remaining <= 0) throw new ApiError(t.common.searchTimeout, 'TIMEOUT', 408)
      await new Promise((resolve) => setTimeout(resolve, Math.min(JOB_POLL_INTERVAL_MS, remaining)))
      const job = await withTimeout(api.comparison.getJob(jobId), Math.max(1, deadline - Date.now()), t.common.searchTimeout)
      setComparisonJob(job)
      if (job.progress >= 35 && job.progress < 75) setCurrentStep(2)
      if (job.progress >= 75) setCurrentStep(3)
      if (job.status === 'completed' || job.status === 'failed') return job
    }
  }

  const handleAnalyze = async () => {
    const enhancedRequirement = buildEnhancedRequirement()
    setIsAnalyzing(true)
    setCurrentStep(1)
    setSearchStatus('running')
    setComparisonJob(null)
    setSearchError(false)
    setErrorDetail(null)
    setItems([])

    const filters = {
      minPrice: minPrice ? Number(minPrice) : undefined,
      maxPrice: maxPrice ? Number(maxPrice) : undefined,
      deliveryTime,
      country: targetMarket || undefined,
    }

    let list: ComparisonItem[]
    let requestFailed = false
    try {
      if (apiEnabled) {
        try {
          const created = await withTimeout(
            api.comparison.createJob(enhancedRequirement, filters, weights, criteria),
            JOB_TIMEOUT_MS,
            t.common.searchTimeout,
          )
          setComparisonJob(created)
          let finished: ComparisonJob
          try {
            finished = await withTimeout(
              api.comparison.streamJob(created.jobId, (job) => {
                setComparisonJob(job)
                if (job.progress >= 35 && job.progress < 75) setCurrentStep(2)
                if (job.progress >= 75) setCurrentStep(3)
              }),
              JOB_TIMEOUT_MS,
              t.common.searchTimeout,
            )
          } catch (streamError) {
            if (streamError instanceof ApiError && streamError.code === 'TIMEOUT') throw streamError
            finished = await pollComparisonJob(created.jobId)
          }
          list = finished.results ?? []
          if (finished.status === 'failed') {
            requestFailed = true
            setSearchError(true)
            setErrorDetail(finished.error ?? null)
            setSearchStatus('error')
          } else {
            setSearchStatus(list.length > 0 ? 'success' : 'empty')
            setCurrentStep(3)
          }
        } catch (jobError) {
          if (jobError instanceof ApiError && jobError.code === 'TIMEOUT') throw jobError
          setComparisonJob(null)
          const res = await api.comparison.search(enhancedRequirement, filters, weights, criteria)
          list = res.results
          setSearchStatus(list.length > 0 ? 'success' : 'empty')
          setCurrentStep(3)
        }
        setItems(list)
      } else {
        await new Promise((r) => setTimeout(r, 1800))
        list = MOCK_COMPARISON
        setItems(list)
        setSearchStatus('success')
        setCurrentStep(3)
      }

      const ranked = rankItems(list, { minPrice, maxPrice, deliveryTime, weights, criteria })
      if (!requestFailed) setSearchStatus(list.length > 0 && ranked.length > 0 ? 'success' : 'empty')
      try {
        setActiveConversationId(await remember(buildRecord(ranked)))
      } catch (e) {
        console.warn('[ComparisonModule] Failed to save conversation snapshot; results unaffected.', e)
      }
    } catch (e) {
      console.error('[ComparisonModule] handleAnalyze failed', e)
      list = []
      setItems(list)
      setErrorDetail(e instanceof Error ? e.message : String(e))
      setSearchError(true)
      setSearchStatus('error')
      setComparisonJob(null)
    } finally {
      setIsAnalyzing(false)
    }
  }

  const handleFeedbackSubmit = async (feedback: FeedbackRecord) => {
    // Lazily create a conversation if feedback is given before running analysis.
    const id = activeConversationId ?? (await remember(buildRecord(rows)))
    setActiveConversationId(id)
    await attachFeedback(id, feedback)
  }

  return (
    <div className="comparison-module space-y-8">
      <section className="procurement-panel procurement-panel--emphasis p-6 print:hidden">
        <label className="procurement-label mb-2 block">{c.inputLabel}</label>
        <textarea
          value={requirement}
          onChange={(e) => setRequirement(e.target.value)}
          rows={3}
          placeholder={c.placeholder}
          className="procurement-input w-full resize-none border px-4 py-3 text-sm focus:outline-none"
        />
        <p className="procurement-helper mt-1.5 text-xs">{c.hint}</p>

        <div className="procurement-structured mt-6 border-t pt-6">
          <p className="procurement-structured__title">{c.structuredLabel}</p>
          <p className="procurement-structured-hint mb-4 mt-0.5 text-xs">{c.structuredHint}</p>
          <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{c.productName}</label>
              <input
                type="text"
                value={productName}
                onChange={(e) => setProductName(e.target.value)}
                placeholder={c.productNamePlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{c.brand}</label>
              <input
                type="text"
                value={brand}
                onChange={(e) => setBrand(e.target.value)}
                placeholder={c.brandPlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{c.model}</label>
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={c.modelPlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>
            <div className="procurement-field">
              <label className="procurement-label mb-1.5 block">{c.quantity}</label>
              <input
                type="text"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                placeholder={c.quantityPlaceholder}
                className="procurement-input w-full border px-3 py-2 text-sm focus:outline-none"
              />
            </div>
          </div>
        </div>

        <div className="comparison-criteria mt-4 px-4 py-3.5">
          <p className="comparison-criteria__title mb-3">{c.hardFilters}</p>
          <div className="flex flex-wrap items-start gap-8">
            <div>
              <label className="procurement-label mb-2 block">{c.budget}</label>
              <div className="flex items-center gap-2">
                <PriceInput value={minPrice} onChange={setMinPrice} placeholder={c.minPrice} />
                <span className="text-sm text-slate-400">—</span>
                <PriceInput value={maxPrice} onChange={setMaxPrice} placeholder={c.maxPrice} />
              </div>
            </div>
            <div>
              <label className="procurement-label mb-2 block">{c.delivery}</label>
              <div className="flex flex-wrap gap-2">
                {DELIVERY_KEYS.map((key) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setDeliveryTime(key)}
                    className={`comparison-filter-chip px-3 py-1 text-sm ${deliveryTime === key ? 'is-active' : ''}`}
                  >
                    {c.deliveryOptions[key]}
                  </button>
                ))}
              </div>
            </div>
            <div className="min-w-40">
              <label className="procurement-label mb-2 block" htmlFor="comparison-target-market">{c.targetMarket}</label>
              <select
                id="comparison-target-market"
                value={targetMarket}
                onChange={(event) => setTargetMarket(event.target.value as TargetMarket)}
                className="procurement-input w-full border px-3 py-1.5 text-sm focus:outline-none"
              >
                <option value="">{c.targetMarketOptions.any}</option>
                {TARGET_MARKETS.map((market) => (
                  <option key={market} value={market}>{c.targetMarketOptions[market]}</option>
                ))}
              </select>
              {targetMarket === 'Poland' && (
                <p className="procurement-helper mt-1 max-w-56 text-xs" role="status">{c.polandCurrencyNotice}</p>
              )}
            </div>
          </div>
        </div>

        <div className="comparison-criteria mt-4 px-4 py-3.5">
          <p className="comparison-criteria__title">{c.weightTitle}</p>
          <p className="procurement-helper mb-3 mt-0.5 text-xs">{c.weightHint}</p>
          <WeightControl weights={weights} onChange={setWeights} t={t} />
        </div>

        <SourcingCriteriaControl
          criteria={criteria}
          onChange={setCriteria}
          t={t}
          title={c.customCriteriaTitle}
          hint={c.customCriteriaHint}
        />

        <div className="mt-4 flex justify-end">
          <AnalyzeButton isAnalyzing={isAnalyzing} onClick={handleAnalyze} t={t} />
        </div>
      </section>

      <section className="procurement-panel procurement-steps-panel px-8 py-6 print:hidden">
        <StepIndicator currentStep={currentStep} steps={t.steps} />
      </section>

      {comparisonJob && searchStatus !== 'idle' && <AgentChatProgress key={comparisonJob.jobId} job={comparisonJob} copy={t.comparison.agentProgress} />}

      {restoredResults && <RestoredBanner t={t} />}

      <section className="procurement-panel comparison-panel p-6 print:border-0 print:shadow-none">
        <div className="comparison-results__header mb-4 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="procurement-results-title font-semibold">{c.tableTitle}</h2>
            <p className="procurement-helper mt-0.5 text-sm">{t.common.resultsFound(rows.length)}</p>
          </div>
          <div className="flex flex-col items-end gap-3">
            <ExportPrintToolbar
              t={t}
              filename="fuyao-quote-comparison.xlsx"
              sheetName="Comparison"
              columns={[
                c.colVendor,
                c.colPlatform,
                c.colProduct,
                c.colScore,
                c.colPrice,
                c.colDelivery,
                c.colPayment,
                c.colDeliveryMethod,
                c.colRating,
                c.sourceLabel,
                c.priceVerification,
                c.sourceLinks,
                c.priceEvidence,
              ]}
              rows={rows.map((r) => [
                r.vendor,
                r.platform,
                r.product,
                `${r.score}%`,
                r.unitLabel || (r.unitPriceEur == null ? c.webNeedsManualCheck : `€ ${r.unitPriceEur}`),
                r.deliveryLabel,
                r.paymentLabel,
                r.deliveryMethod,
                `${r.rating.toFixed(1)} (${r.reviews})`,
                comparisonSourceLabel(r, c),
                priceVerificationLabel(r, c),
                (r.sourceUrls ?? []).join('; '),
                (r.evidenceSnippets ?? []).join(' | '),
              ])}
            />
          </div>
        </div>

        {rows.length > 0 && rows.every((row) => row.source === 'web') && (
          <div className="mb-4 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm leading-6 text-blue-700">
            {rows.every((row) => row.priceConfidence === 'api') ? c.allMarketplaceApiNotice : c.allWebNotice}
          </div>
        )}

        {searchStatus === 'running' ? (
          <div role="status" aria-live="polite" className="flex flex-col items-center justify-center rounded-lg border border-dashed border-blue-200 bg-blue-50 px-6 py-12 text-sm text-blue-700">
            <SpinnerIcon />
            <p className="mt-3">{t.common.analyzing}</p>
          </div>
        ) : searchError ? (
          <div role="alert" className="flex flex-col items-center justify-center rounded-lg border border-dashed border-red-200 bg-red-50 px-6 py-12 text-sm text-red-500">
            <p>{t.common.searchError}</p>
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
        ) : rows.length === 0 ? (
          <div role="status" className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-sm text-slate-500">
            <p>{items.length > 0 ? c.noMatches : t.common.empty}</p>
            <button
              type="button"
              onClick={() => void handleAnalyze()}
              disabled={isAnalyzing}
              className="comparison-row__action mt-5 border px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-70"
            >
              {t.common.analyze}
            </button>
          </div>
        ) : (
          <ComparisonTable rows={rows} recommendedId={recommendedId} t={t} onSelect={(name) => setFeedbackFor(name)} />
        )}
      </section>

      {feedbackFor && (
        <FeedbackModal
          options={rows.map((r) => r.vendor)}
          defaultChosen={feedbackFor}
          t={t}
          onSubmit={handleFeedbackSubmit}
          onClose={() => setFeedbackFor(null)}
        />
      )}
    </div>
  )
}

const HEAD_CELL = 'comparison-head-cell px-4 py-3.5 align-middle text-xs font-semibold uppercase tracking-wider text-slate-500'

function ComparisonTable({
  rows,
  recommendedId,
  t,
  onSelect,
}: {
  rows: ScoredItem[]
  recommendedId: string | null
  t: Translation
  onSelect: (vendor: string) => void
}) {
  const c = t.comparison
  if (rows.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 py-12 text-sm text-slate-400">
        {t.common.empty}
      </div>
    )
  }
  return (
    <div className="comparison-table cmp-print overflow-x-auto border print:overflow-visible">
      <table className="min-w-[1480px] w-full border-collapse text-left text-sm align-middle print:min-w-0">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50">
            <th className={`comparison-sticky-cell sticky left-0 z-20 min-w-[220px] border-r bg-slate-50 ${HEAD_CELL}`}>
              {c.colVendor}
            </th>
            <th className={`min-w-[180px] ${HEAD_CELL}`}>{c.colPlatform}</th>
            <th className={`min-w-[320px] ${HEAD_CELL}`}>{c.colProduct}</th>
            <th className={`min-w-[140px] ${HEAD_CELL}`}>{c.colScore}</th>
            <th className={`min-w-[120px] ${HEAD_CELL}`}>{c.colPrice}</th>
            <th className={`min-w-[140px] ${HEAD_CELL}`}>{c.colDelivery}</th>
            <th className={`min-w-[170px] ${HEAD_CELL}`}>{c.colPayment}</th>
            <th className={`min-w-[170px] ${HEAD_CELL}`}>{c.colDeliveryMethod}</th>
            <th className={`min-w-[120px] ${HEAD_CELL}`}>{c.colRating}</th>
            <th className={`min-w-[110px] print:hidden ${HEAD_CELL}`}>{c.colAction}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 bg-white">
          {rows.map((row) => {
            const highlight = row.id === recommendedId
            const stickyBg = highlight ? 'bg-blue-50' : 'bg-white'
            const displayPlatform = row.platform || row.sourceDetail || row.source || '—'
            const displayProduct = row.product || row.vendor || '—'
            const hasPublishedPrice = row.unitPriceEur != null
            const displayPrice = row.unitLabel || (hasPublishedPrice ? `€ ${row.unitPriceEur}` : c.webNeedsManualCheck)
            const displayDelivery = row.deliveryLabel || c.missingDelivery
            const displayPayment = row.paymentLabel || c.missingPayment
            const displayDeliveryMethod = row.deliveryMethod || c.missingDeliveryMethod
            const sourceLabel = comparisonSourceLabel(row, c)
            const verificationLabel = priceVerificationLabel(row, c)
            const isApiPrice = row.priceConfidence === 'api'
            return (
              <tr key={row.id} className={`comparison-row ${highlight ? 'comparison-row--recommended' : ''}`}>
                <td className={`comparison-sticky-cell sticky left-0 z-10 min-w-[220px] border-r px-4 py-4 align-middle ${stickyBg}`}>
                  <div className="mb-0.5 flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold text-slate-900">{row.vendor}</p>
                    <span
                      className={`comparison-source shrink-0 px-2 py-0.5 text-[10px] font-bold uppercase ${
                        row.source !== 'web'
                          ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100'
                          : isSerpApiItem(row)
                            ? 'bg-cyan-50 text-cyan-800 ring-1 ring-cyan-100'
                            : isIdealoItem(row)
                              ? 'bg-sky-50 text-sky-800 ring-1 ring-sky-100'
                              : 'bg-indigo-50 text-indigo-700 ring-1 ring-indigo-100'
                      }`}
                    >
                      {sourceLabel}
                    </span>
                    {highlight && (
                      <span className="comparison-recommended shrink-0 px-2 py-0.5 text-[10px] font-bold uppercase text-white">
                        {c.recommended}
                      </span>
                    )}
                  </div>
                  {row.source === 'web' && row.sourceUrls?.[0] && (
                    <a
                      href={externalHref(row.sourceUrls[0])}
                      target="_blank"
                      rel="noreferrer"
                      className="comparison-web-link mt-1 inline-flex max-w-[190px] truncate text-xs font-medium hover:underline"
                    >
                      {row.sourceUrls[0]}
                    </a>
                  )}
                </td>
                <td className="comparison-value px-4 py-4 align-middle text-sm">{displayPlatform}</td>
                <td className="px-4 py-4 align-middle">
                  <p className="comparison-value-strong line-clamp-3 text-sm font-medium leading-5">{displayProduct}</p>
                </td>
                <td className="px-4 py-4 align-middle">
                  <MatchScoreBadge score={row.score} />
                </td>
                <td className="px-4 py-4 align-middle">
                  <span className="comparison-value-strong whitespace-nowrap text-sm font-semibold">{displayPrice}</span>
                  {row.source === 'web' && (
                    <span className={`mt-1 block w-fit rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ${
                      !hasPublishedPrice
                        ? 'bg-amber-50 text-amber-700 ring-amber-100'
                        : isApiPrice
                          ? 'bg-cyan-50 text-cyan-800 ring-cyan-100'
                          : 'bg-slate-50 text-slate-600 ring-slate-200'
                    }`}>
                      {verificationLabel}
                    </span>
                  )}
                </td>
                <td className="comparison-value px-4 py-4 align-middle text-sm">{displayDelivery}</td>
                <td className="px-4 py-4 align-middle">
                  <span className="comparison-value text-sm">{displayPayment}</span>
                  {row.paymentTerm === 'onAccount' && (
                    <span className="ml-1.5 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                      {c.paymentTerms.onAccount}
                    </span>
                  )}
                </td>
                <td className="comparison-value px-4 py-4 align-middle text-sm">{displayDeliveryMethod}</td>
                <td className="px-4 py-4 align-middle">
                  <span className="comparison-value inline-flex items-center gap-1 whitespace-nowrap text-sm">
                    <span className="comparison-value-strong font-semibold">{row.rating.toFixed(1)}</span>
                    <span className="text-amber-400">★</span>
                    <span className="text-slate-500">({row.reviews})</span>
                  </span>
                </td>
                <td className="px-4 py-4 align-middle print:hidden">
                  <button
                    type="button"
                    onClick={() => onSelect(row.vendor)}
                    className="comparison-row__action whitespace-nowrap border px-3 py-1.5 text-sm font-medium print:hidden"
                  >
                    {t.common.giveFeedback}
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
