import { useMemo, useState } from 'react'
import type { Translation } from '../i18n'
import type { EvaluationCriterion } from '../types'
import '../styles/sourcing-criteria.css'

export const DEFAULT_SOURCING_CRITERIA: EvaluationCriterion[] = [
  { key: 'price', label: 'Price', weight: 35 },
  { key: 'delivery', label: 'Delivery lead time', weight: 25 },
  { key: 'certification', label: 'Certification fit', weight: 20 },
  { key: 'supplier_history', label: 'Supplier history', weight: 20 },
]

function bounded(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)))
}

/** Round proportional values while guaranteeing the visible total is exactly 100. */
export function normalizeCriteria(criteria: EvaluationCriterion[]): EvaluationCriterion[] {
  const cleaned = criteria
    .map((criterion) => ({
      ...criterion,
      key: criterion.key.trim(),
      label: criterion.label.trim(),
      weight: Number.isFinite(criterion.weight) ? Math.max(0, criterion.weight) : 0,
    }))
    .filter((criterion) => criterion.key && criterion.label)

  if (cleaned.length === 0) return []
  const total = cleaned.reduce((sum, criterion) => sum + criterion.weight, 0)
  const raw = total > 0
    ? cleaned.map((criterion) => (criterion.weight / total) * 100)
    : cleaned.map(() => 100 / cleaned.length)
  const rounded = raw.map((weight) => Math.floor(weight))
  let remainder = 100 - rounded.reduce((sum, weight) => sum + weight, 0)
  const fractions = raw
    .map((weight, index) => ({ index, fraction: weight - Math.floor(weight) }))
    .sort((a, b) => b.fraction - a.fraction)
  for (const { index } of fractions) {
    if (remainder <= 0) break
    rounded[index] += 1
    remainder -= 1
  }
  return cleaned.map((criterion, index) => ({ ...criterion, weight: rounded[index] }))
}

function rebalance(criteria: EvaluationCriterion[], changedKey: string, nextWeight: number): EvaluationCriterion[] {
  const changed = criteria.find((criterion) => criterion.key === changedKey)
  if (!changed) return criteria
  const others = criteria.filter((criterion) => criterion.key !== changedKey)
  if (others.length === 0) return [{ ...changed, weight: 100 }]
  const target = bounded(nextWeight)
  const remaining = 100 - target
  const otherTotal = others.reduce((sum, criterion) => sum + criterion.weight, 0)
  const redistributed = others.map((criterion) => ({
    ...criterion,
    weight: otherTotal > 0 ? (criterion.weight / otherTotal) * remaining : remaining / others.length,
  }))
  return normalizeCriteria([{ ...changed, weight: target }, ...redistributed])
}

function keyFor(label: string, criteria: EvaluationCriterion[]): string {
  const base = label
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '') || 'custom_criterion'
  let key = base
  let suffix = 2
  while (criteria.some((criterion) => criterion.key === key)) {
    key = `${base}_${suffix}`
    suffix += 1
  }
  return key
}

export function SourcingCriteriaControl({
  criteria,
  onChange,
  t,
  title,
  hint,
}: {
  criteria: EvaluationCriterion[]
  onChange: (criteria: EvaluationCriterion[]) => void
  t: Translation
  title?: string
  hint?: string
}) {
  const s = t.sourcing
  const [newLabel, setNewLabel] = useState('')
  const presets = useMemo(
    () => [
      s.criteriaPresetQuality,
      s.criteriaPresetReputation,
      s.criteriaPresetMoq,
      s.criteriaPresetCapacity,
      s.criteriaPresetRegion,
      s.criteriaPresetEnvironment,
    ],
    [s],
  )

  const addCriterion = (label: string) => {
    const trimmed = label.trim()
    if (!trimmed || criteria.some((criterion) => criterion.label.toLocaleLowerCase() === trimmed.toLocaleLowerCase())) return
    onChange(normalizeCriteria([...criteria, { key: keyFor(trimmed, criteria), label: trimmed, weight: 10 }]))
    setNewLabel('')
  }

  return (
    <section className="sourcing-criteria" aria-labelledby="sourcing-criteria-title">
      <div className="sourcing-criteria__header">
        <div>
          <h3 id="sourcing-criteria-title">{title ?? s.criteriaTitle}</h3>
          <p>{hint ?? s.criteriaHint}</p>
        </div>
        <span className="sourcing-criteria__total">{criteria.reduce((sum, criterion) => sum + criterion.weight, 0)}%</span>
      </div>

      <div className="sourcing-criteria__rows">
        {criteria.map((criterion) => (
          <div className="sourcing-criteria__row" key={criterion.key}>
            <label>{criterion.label}</label>
            <input
              type="range"
              min="0"
              max="100"
              value={criterion.weight}
              onChange={(event) => onChange(rebalance(criteria, criterion.key, Number(event.target.value)))}
              aria-label={`${criterion.label} ${s.criteriaWeight}`}
            />
            <input
              type="number"
              min="0"
              max="100"
              value={criterion.weight}
              onChange={(event) => onChange(rebalance(criteria, criterion.key, Number(event.target.value)))}
              aria-label={`${criterion.label} ${s.criteriaWeight}`}
            />
            <span>%</span>
            <button
              type="button"
              onClick={() => onChange(normalizeCriteria(criteria.filter((item) => item.key !== criterion.key)))}
              aria-label={`${s.criteriaRemove} ${criterion.label}`}
            >
              {s.criteriaRemove}
            </button>
          </div>
        ))}
      </div>

      <div className="sourcing-criteria__add-row">
        <input
          className="procurement-input"
          value={newLabel}
          onChange={(event) => setNewLabel(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              addCriterion(newLabel)
            }
          }}
          placeholder={s.criteriaPlaceholder}
        />
        <button type="button" onClick={() => addCriterion(newLabel)}>{s.criteriaAdd}</button>
      </div>

      <div className="sourcing-criteria__presets" aria-label={s.criteriaAdd}>
        {presets.map((preset) => {
          const exists = criteria.some((criterion) => criterion.label.toLocaleLowerCase() === preset.toLocaleLowerCase())
          return (
            <button key={preset} type="button" disabled={exists} onClick={() => addCriterion(preset)}>
              {preset}
            </button>
          )
        })}
      </div>
    </section>
  )
}
