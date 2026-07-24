import { Fragment, useState } from 'react'
import type { Translation } from '../i18n'
import type { Supplier } from '../types'
import { MatchScoreBadge } from './shared'

type SupplierResultsTableProps = {
  suppliers: Supplier[]
  t: Translation
  onSelect: (supplierName: string) => void
}

const HEAD_CELL = 'supplier-results-table__head px-4 py-3.5 align-middle text-xs font-semibold uppercase tracking-wider'
const EMPTY_VALUE = '-'

function isDatabaseSupplier(supplier: Supplier): boolean {
  const source = supplier.source?.trim().toLowerCase()
  return !source || source === 'database' || source === 'local' || source === 'db'
}

function externalHref(value: string): string {
  const trimmed = value.trim()
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  if (trimmed.startsWith('//')) return `https:${trimmed}`
  return `https://${trimmed.replace(/^\/+/, '')}`
}

function formatLocation(supplier: Supplier): string {
  return [supplier.city, supplier.country].filter(Boolean).join(', ') || EMPTY_VALUE
}

function formatProduct(supplier: Supplier): string {
  return [supplier.productName, supplier.brand, supplier.model].filter(Boolean).join(' / ') || EMPTY_VALUE
}

function formatUnitPrice(supplier: Supplier): string {
  if (supplier.unitPrice == null) return EMPTY_VALUE
  return `${supplier.currency ?? 'EUR'} ${supplier.unitPrice.toLocaleString()}`
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      aria-hidden="true"
      className={`h-4 w-4 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.8}
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="m6 9 6 6 6-6" />
    </svg>
  )
}

function DetailField({ label, value }: { label: string; value?: string | number }) {
  return (
    <div className="supplier-result-details__field min-w-0">
      <dt>{label}</dt>
      <dd>{value || EMPTY_VALUE}</dd>
    </div>
  )
}

function DetailTags({ values, className }: { values: string[]; className: string }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((value) => (
        <span key={value} className={className}>
          {value}
        </span>
      ))}
    </div>
  )
}

function SupplierDetailRow({
  supplier,
  detailId,
  t,
  onSelect,
}: {
  supplier: Supplier
  detailId: string
  t: Translation
  onSelect: () => void
}) {
  const s = t.sourcing
  const contact = [supplier.contactPerson, supplier.phone].filter(Boolean).join(' / ')
  const contactSub = [supplier.email, supplier.website].filter(Boolean).join(' / ')
  const specifications = [supplier.specifications, ...(supplier.standards ?? [])].filter(Boolean).join(' / ')
  const quote = [formatUnitPrice(supplier), supplier.quoteConditions].filter((value) => value && value !== EMPTY_VALUE).join(' / ')
  const capabilities = supplier.capabilities ?? []
  const certifications = supplier.certifications ?? []
  const evidenceSnippets = supplier.evidenceSnippets ?? []
  const sourceUrls = supplier.sourceUrls ?? []

  return (
    <tr className="supplier-result-details" id={detailId}>
      <td colSpan={9}>
        <div className="supplier-result-details__inner">
          <dl className="supplier-result-details__grid">
            <DetailField label={s.cardAddress} value={supplier.address || formatLocation(supplier)} />
            <DetailField label={s.cardContact} value={contact} />
            <DetailField label={s.colEmail} value={supplier.email} />
            <DetailField label={s.colWebsite} value={supplier.website} />
            <DetailField label={s.cardEstablished} value={supplier.established} />
            <DetailField label={s.cardEmployees} value={supplier.employees} />
            <DetailField label={s.cardRevenue} value={supplier.annualRevenue} />
            <DetailField label={s.cardProduct} value={formatProduct(supplier)} />
            <DetailField label={s.cardSpecifications} value={specifications} />
            <DetailField label={s.cardQuote} value={quote} />
            <DetailField label={s.cardLeadTime} value={supplier.deliveryLeadTime} />
            <DetailField label={s.cardPaymentTerms} value={supplier.paymentTerms} />
            <DetailField label={s.cardVerification} value={supplier.verificationStatus} />
          </dl>

          {(capabilities.length > 0 || certifications.length > 0) && (
            <div className="supplier-result-details__groups">
              {capabilities.length > 0 && (
                <section>
                  <h4>{s.cardCapabilities}</h4>
                  <DetailTags values={capabilities} className="supplier-result-details__tag" />
                </section>
              )}
              {certifications.length > 0 && (
                <section>
                  <h4>{s.cardCerts}</h4>
                  <DetailTags values={certifications} className="supplier-result-details__cert" />
                </section>
              )}
            </div>
          )}

          {(evidenceSnippets.length > 0 || sourceUrls.length > 0) && (
            <div className="supplier-result-details__evidence">
              {evidenceSnippets.length > 0 && (
                <section>
                  <h4>{s.cardEvidence}</h4>
                  <div className="space-y-1.5">
                    {evidenceSnippets.slice(0, 3).map((snippet, index) => (
                      <p key={`${supplier.id}-evidence-${index}`}>{snippet}</p>
                    ))}
                  </div>
                </section>
              )}
              {sourceUrls.length > 0 && (
                <section>
                  <h4>{s.cardSources}</h4>
                  <div className="flex flex-wrap gap-1.5">
                    {sourceUrls.slice(0, 4).map((url) => (
                      <a key={url} href={externalHref(url)} target="_blank" rel="noreferrer" title={url}>
                        {url.replace(/^https?:\/\//, '').replace(/\/$/, '')}
                      </a>
                    ))}
                  </div>
                </section>
              )}
            </div>
          )}

          <div className="supplier-result-details__footer print:hidden">
            {contactSub && <p>{contactSub}</p>}
            <button type="button" onClick={onSelect} className="supplier-result-details__feedback border px-3 py-1.5 text-sm font-medium">
              {t.common.giveFeedback}
            </button>
          </div>
        </div>
      </td>
    </tr>
  )
}

export function SupplierResultsTable({ suppliers, t, onSelect }: SupplierResultsTableProps) {
  const [expandedSupplierIds, setExpandedSupplierIds] = useState<Set<string>>(() => new Set())
  const s = t.sourcing

  const toggleDetails = (supplierId: string) => {
    setExpandedSupplierIds((current) => {
      const next = new Set(current)
      if (next.has(supplierId)) next.delete(supplierId)
      else next.add(supplierId)
      return next
    })
  }

  return (
    <div className="supplier-results-table cmp-print overflow-x-auto border">
      <table className="min-w-[1450px] w-full border-collapse text-left text-sm align-middle print:min-w-0">
        <thead>
          <tr>
            <th className={`supplier-results-table__sticky sticky left-0 z-20 min-w-[245px] border-r ${HEAD_CELL}`}>
              {s.colName}
            </th>
            <th className={`min-w-[150px] ${HEAD_CELL}`}>{s.colLocation}</th>
            <th className={`min-w-[260px] ${HEAD_CELL}`}>{s.colProduct}</th>
            <th className={`min-w-[170px] ${HEAD_CELL}`}>{s.cardCerts}</th>
            <th className={`min-w-[180px] ${HEAD_CELL}`}>{s.cardQuote}</th>
            <th className={`min-w-[140px] ${HEAD_CELL}`}>{s.cardLeadTime}</th>
            <th className={`min-w-[165px] ${HEAD_CELL}`}>{s.cardPaymentTerms}</th>
            <th className={`min-w-[135px] ${HEAD_CELL}`}>{s.match}</th>
            <th className={`w-[58px] print:hidden ${HEAD_CELL}`}>
              <span className="sr-only">{s.expandDetails}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {suppliers.map((supplier, index) => {
            const expanded = expandedSupplierIds.has(supplier.id)
            const detailId = `supplier-details-${index}`
            const certifications = supplier.certifications ?? []
            const sourceText = isDatabaseSupplier(supplier) ? s.localDatabaseTag : s.webSearchTag
            const quote = [formatUnitPrice(supplier), supplier.quoteConditions].filter((value) => value && value !== EMPTY_VALUE).join(' / ') || EMPTY_VALUE

            return (
              <Fragment key={supplier.id}>
                <tr
                  className={`supplier-results-table__row ${expanded ? 'is-expanded' : ''}`}
                  style={{ animationDelay: `${index * 45}ms` }}
                >
                  <td className="supplier-results-table__sticky sticky left-0 z-10 min-w-[245px] border-r px-4 py-4 align-middle">
                    <p className="supplier-results-table__name text-sm font-semibold">{supplier.name}</p>
                    <span
                      title={`${s.sourceLabel}: ${sourceText}`}
                      className={`supplier-results-table__source mt-1.5 inline-flex px-2 py-0.5 text-[10px] font-bold uppercase ${isDatabaseSupplier(supplier) ? '' : 'is-web'}`}
                    >
                      {sourceText}
                    </span>
                  </td>
                  <td className="supplier-results-table__value px-4 py-4 align-middle">{formatLocation(supplier)}</td>
                  <td className="px-4 py-4 align-middle">
                    <p className="supplier-results-table__product line-clamp-2 font-medium leading-5">{formatProduct(supplier)}</p>
                  </td>
                  <td className="px-4 py-4 align-middle">
                    {certifications.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {certifications.slice(0, 2).map((certification) => (
                          <span key={certification} className="supplier-results-table__cert">
                            {certification}
                          </span>
                        ))}
                        {certifications.length > 2 && <span className="supplier-results-table__more">+{certifications.length - 2}</span>}
                      </div>
                    ) : EMPTY_VALUE}
                  </td>
                  <td className="px-4 py-4 align-middle">
                    <span className="supplier-results-table__quote">{quote}</span>
                  </td>
                  <td className="supplier-results-table__value px-4 py-4 align-middle">{supplier.deliveryLeadTime || EMPTY_VALUE}</td>
                  <td className="supplier-results-table__value px-4 py-4 align-middle">{supplier.paymentTerms || EMPTY_VALUE}</td>
                  <td className="px-4 py-4 align-middle"><MatchScoreBadge score={Math.round(supplier.matchScore)} /></td>
                  <td className="px-3 py-4 align-middle print:hidden">
                    <button
                      type="button"
                      onClick={() => toggleDetails(supplier.id)}
                      aria-expanded={expanded}
                      aria-controls={detailId}
                      title={expanded ? s.collapseDetails : s.expandDetails}
                      aria-label={expanded ? s.collapseDetails : s.expandDetails}
                      className="supplier-results-table__toggle inline-flex h-8 w-8 items-center justify-center"
                    >
                      <ChevronIcon expanded={expanded} />
                    </button>
                  </td>
                </tr>
                {expanded && <SupplierDetailRow supplier={supplier} detailId={detailId} t={t} onSelect={() => onSelect(supplier.name)} />}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
