import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { Language, SupplierDirectoryEntry, SupplierDirectoryInput } from '../types'
import type { Translation } from '../i18n'
import { useAuth } from '../context/AuthContext'
import { ShieldIcon } from '../components/icons'
import { api, apiEnabled } from '../lib/api'
import { loadJSON, saveJSON, STORAGE_KEYS } from '../lib/storage'
import '../styles/supplier-directory.css'

const SUGGESTED_KEYS = ['AI Search Provider', 'Pricing Data API', 'ERP Connector']

function formatTime(ts: number | undefined, language: Language): string {
  if (!ts) return '—'
  return new Date(ts).toLocaleString(language === 'zh' ? 'zh-CN' : 'en-GB', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function asList(value: string | string[] | undefined): string[] {
  const values = Array.isArray(value) ? value : (value ?? '').split(',')
  return [...new Set(values.map((item) => item.trim()).filter(Boolean))]
}

function asText(value: string[] | undefined): string {
  return (value ?? []).join(', ')
}

function emptySupplier(): SupplierDirectoryInput {
  return {
    name: '',
    tags: [],
    origin: 'internal',
    preferred: false,
  }
}

function normalizeSupplier(entry: SupplierDirectoryEntry): SupplierDirectoryEntry {
  return {
    ...entry,
    tags: asList(entry.tags),
    capabilities: asList(entry.capabilities),
    certifications: asList(entry.certifications),
    environmentalStandards: asList(entry.environmentalStandards),
    origin: entry.origin ?? 'internal',
    updatedAt: entry.updatedAt ?? Date.now(),
  }
}

function toDirectoryInput(draft: SupplierDirectoryInput): SupplierDirectoryInput {
  return {
    ...draft,
    name: draft.name.trim(),
    country: draft.country?.trim() || undefined,
    city: draft.city?.trim() || undefined,
    address: draft.address?.trim() || undefined,
    contactPerson: draft.contactPerson?.trim() || undefined,
    phone: draft.phone?.trim() || undefined,
    email: draft.email?.trim() || undefined,
    website: draft.website?.trim() || undefined,
    capabilities: asList(draft.capabilities),
    certifications: asList(draft.certifications),
    tags: asList(draft.tags),
    notes: draft.notes?.trim() || undefined,
    historicalPerformance: String(draft.historicalPerformance ?? '').trim() || undefined,
    minimumOrderQuantity: draft.minimumOrderQuantity?.trim() || undefined,
    productionCapacity: draft.productionCapacity?.trim() || undefined,
    environmentalStandards: asList(draft.environmentalStandards),
    origin: draft.origin ?? 'internal',
  }
}

function supplierLocation(entry: SupplierDirectoryEntry): string {
  return [entry.city, entry.country].filter(Boolean).join(', ')
}

function SupplierDirectory({ t, language }: { t: Translation; language: Language }) {
  const d = t.supplierDirectory
  const [entries, setEntries] = useState<SupplierDirectoryEntry[]>(() =>
    loadJSON<SupplierDirectoryEntry[]>(STORAGE_KEYS.supplierDirectory, []).map(normalizeSupplier),
  )
  const [draft, setDraft] = useState<SupplierDirectoryInput>(emptySupplier)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [isFormOpen, setIsFormOpen] = useState(false)
  const [isLoading, setIsLoading] = useState(apiEnabled)
  const [isSaving, setIsSaving] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!apiEnabled) return
    let active = true
    api.supplierDirectory
      .list()
      .then((remoteEntries) => {
        if (!active) return
        const normalized = remoteEntries.map(normalizeSupplier)
        setEntries(normalized)
        saveJSON(STORAGE_KEYS.supplierDirectory, normalized)
      })
      .catch(() => {
        if (active) setError(d.loadError)
      })
      .finally(() => {
        if (active) setIsLoading(false)
      })
    return () => {
      active = false
    }
  }, [d.loadError])

  const sortedEntries = useMemo(
    () => [...entries].sort((a, b) => Number(b.preferred) - Number(a.preferred) || b.updatedAt - a.updatedAt),
    [entries],
  )

  const beginCreate = () => {
    setEditingId(null)
    setDraft(emptySupplier())
    setError(null)
    setIsFormOpen(true)
  }

  const beginEdit = (entry: SupplierDirectoryEntry) => {
    const { id: _id, createdAt: _createdAt, updatedAt: _updatedAt, source: _source, ...input } = entry
    void _id
    void _createdAt
    void _updatedAt
    void _source
    setEditingId(entry.id)
    setDraft({ ...input, tags: [...entry.tags] })
    setError(null)
    setIsFormOpen(true)
  }

  const closeForm = () => {
    setIsFormOpen(false)
    setEditingId(null)
    setDraft(emptySupplier())
    setError(null)
  }

  const updateDraft = <K extends keyof SupplierDirectoryInput>(key: K, value: SupplierDirectoryInput[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  const toggleTag = (tag: string) => {
    setDraft((current) => ({
      ...current,
      tags: current.tags.includes(tag)
        ? current.tags.filter((value) => value !== tag)
        : [...current.tags, tag],
    }))
  }

  const saveDirectoryEntries = (next: SupplierDirectoryEntry[]) => {
    setEntries(next)
    saveJSON(STORAGE_KEYS.supplierDirectory, next)
  }

  const handleSaveSupplier = async () => {
    const input = toDirectoryInput(draft)
    if (!input.name) return
    setIsSaving(true)
    setError(null)
    try {
      let saved: SupplierDirectoryEntry
      if (apiEnabled) {
        saved = editingId
          ? await api.supplierDirectory.update(editingId, input)
          : await api.supplierDirectory.create(input)
      } else {
        const previous = editingId ? entries.find((entry) => entry.id === editingId) : undefined
        saved = {
          ...input,
          id: editingId ?? crypto.randomUUID(),
          source: 'local',
          createdAt: previous?.createdAt ?? Date.now(),
          updatedAt: Date.now(),
        }
      }
      const normalized = normalizeSupplier(saved)
      saveDirectoryEntries(
        editingId
          ? entries.map((entry) => (entry.id === editingId ? normalized : entry))
          : [...entries, normalized],
      )
      closeForm()
    } catch {
      setError(d.saveError)
    } finally {
      setIsSaving(false)
    }
  }

  const handleDeleteSupplier = async (entry: SupplierDirectoryEntry) => {
    if (!window.confirm(d.confirmDelete)) return
    setDeletingId(entry.id)
    setError(null)
    try {
      if (apiEnabled) await api.supplierDirectory.remove(entry.id)
      saveDirectoryEntries(entries.filter((candidate) => candidate.id !== entry.id))
      if (editingId === entry.id) closeForm()
    } catch {
      setError(d.saveError)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <section className="procurement-panel supplier-directory" aria-labelledby="supplier-directory-title">
      <div className="supplier-directory__header">
        <div>
          <p className="supplier-directory__eyebrow">{d.localOnly}</p>
          <h2 id="supplier-directory-title" className="supplier-directory__title">{d.title}</h2>
          <p className="supplier-directory__description">{d.description}</p>
        </div>
        <button type="button" onClick={beginCreate} className="procurement-primary-action supplier-directory__add">
          <span aria-hidden="true">+</span>
          {d.addSupplier}
        </button>
      </div>

      {error && (
        <p className="supplier-directory__error" role="alert">{error}</p>
      )}

      {isFormOpen && (
        <div className="supplier-directory__form" aria-label={editingId ? d.editSupplier : d.addSupplier}>
          <div className="supplier-directory__form-heading">
            <h3>{editingId ? d.editSupplier : d.addSupplier}</h3>
            <button type="button" className="supplier-directory__quiet-action" onClick={closeForm}>{d.cancel}</button>
          </div>

          <div className="supplier-directory__form-grid">
            <FormField label={d.companyName} required>
              <input className="procurement-input" value={draft.name} onChange={(event) => updateDraft('name', event.target.value)} autoFocus />
            </FormField>
            <FormField label={d.contact}>
              <input className="procurement-input" value={draft.contactPerson ?? ''} onChange={(event) => updateDraft('contactPerson', event.target.value)} />
            </FormField>
            <FormField label={d.country}>
              <input className="procurement-input" value={draft.country ?? ''} onChange={(event) => updateDraft('country', event.target.value)} />
            </FormField>
            <FormField label={d.city}>
              <input className="procurement-input" value={draft.city ?? ''} onChange={(event) => updateDraft('city', event.target.value)} />
            </FormField>
            <FormField label={d.email}>
              <input className="procurement-input" type="email" value={draft.email ?? ''} onChange={(event) => updateDraft('email', event.target.value)} />
            </FormField>
            <FormField label={d.phone}>
              <input className="procurement-input" value={draft.phone ?? ''} onChange={(event) => updateDraft('phone', event.target.value)} />
            </FormField>
            <FormField label={d.website}>
              <input className="procurement-input" type="url" value={draft.website ?? ''} onChange={(event) => updateDraft('website', event.target.value)} />
            </FormField>
            <FormField label={d.minimumOrderQuantity}>
              <input className="procurement-input" value={draft.minimumOrderQuantity ?? ''} onChange={(event) => updateDraft('minimumOrderQuantity', event.target.value)} />
            </FormField>
            <FormField label={d.capabilities}>
              <input className="procurement-input" value={asText(draft.capabilities)} onChange={(event) => updateDraft('capabilities', asList(event.target.value))} />
            </FormField>
            <FormField label={d.certifications}>
              <input className="procurement-input" value={asText(draft.certifications)} onChange={(event) => updateDraft('certifications', asList(event.target.value))} />
            </FormField>
            <FormField label={d.environmentalStandards}>
              <input className="procurement-input" value={asText(draft.environmentalStandards)} onChange={(event) => updateDraft('environmentalStandards', asList(event.target.value))} />
            </FormField>
            <FormField label={d.productionCapacity}>
              <input className="procurement-input" value={draft.productionCapacity ?? ''} onChange={(event) => updateDraft('productionCapacity', event.target.value)} />
            </FormField>
          </div>

          <FormField label={d.tags} hint={d.tagsHint}>
            <input className="procurement-input" value={asText(draft.tags)} onChange={(event) => updateDraft('tags', asList(event.target.value))} />
          </FormField>
          <div className="supplier-directory__suggestions" aria-label={d.tags}>
            {[d.preferredTag, d.reliableTag].map((tag) => (
              <button
                key={tag}
                type="button"
                onClick={() => toggleTag(tag)}
                className={draft.tags.includes(tag) ? 'is-selected' : ''}
                aria-pressed={draft.tags.includes(tag)}
              >
                {tag}
              </button>
            ))}
          </div>

          <label className="supplier-directory__checkbox">
            <input type="checkbox" checked={draft.preferred ?? false} onChange={(event) => updateDraft('preferred', event.target.checked)} />
            <span>{d.preferred}</span>
          </label>

          <div className="supplier-directory__form-grid supplier-directory__form-grid--wide">
            <FormField label={d.performanceNote}>
              <input className="procurement-input" placeholder={d.performancePlaceholder} value={String(draft.historicalPerformance ?? '')} onChange={(event) => updateDraft('historicalPerformance', event.target.value)} />
            </FormField>
            <FormField label={d.notes}>
              <textarea className="procurement-input supplier-directory__notes" value={draft.notes ?? ''} onChange={(event) => updateDraft('notes', event.target.value)} />
            </FormField>
          </div>

          <div className="supplier-directory__form-actions">
            <button type="button" onClick={closeForm} className="supplier-directory__secondary-action">{d.cancel}</button>
            <button type="button" onClick={handleSaveSupplier} disabled={!draft.name.trim() || isSaving} className="procurement-primary-action">
              {isSaving ? d.saving : editingId ? d.updateSupplier : d.saveSupplier}
            </button>
          </div>
        </div>
      )}

      <div className="supplier-directory__list" aria-busy={isLoading}>
        {isLoading && <p className="supplier-directory__loading">{d.localOnly}</p>}
        {!isLoading && sortedEntries.length === 0 && <p className="supplier-directory__empty">{d.empty}</p>}
        {sortedEntries.map((entry) => {
          const location = supplierLocation(entry)
          const contacts = [entry.contactPerson, entry.email, entry.phone].filter(Boolean).join(' · ')
          return (
            <article key={entry.id} className="supplier-directory__entry">
              <div className="supplier-directory__entry-main">
                <div className="supplier-directory__entry-title-row">
                  <h3>{entry.name}</h3>
                  {entry.preferred && <span className="supplier-directory__priority">{d.preferredTag}</span>}
                </div>
                {(location || contacts) && <p className="supplier-directory__entry-meta">{[location, contacts].filter(Boolean).join(' · ')}</p>}
                <div className="supplier-directory__chips">
                  {entry.tags.map((tag) => <span key={tag}>{tag}</span>)}
                  {entry.certifications?.slice(0, 2).map((cert) => <span key={cert} className="is-outline">{cert}</span>)}
                </div>
                {entry.historicalPerformance && <p className="supplier-directory__entry-note">{entry.historicalPerformance}</p>}
              </div>
              <div className="supplier-directory__entry-actions">
                <span>{d.updated} {formatTime(entry.updatedAt, language)}</span>
                <div>
                  <button type="button" onClick={() => beginEdit(entry)}>{d.editSupplier}</button>
                  <button type="button" onClick={() => handleDeleteSupplier(entry)} disabled={deletingId === entry.id} className="is-danger">{d.deleteSupplier}</button>
                </div>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}

function FormField({
  label,
  hint,
  required = false,
  children,
}: {
  label: string
  hint?: string
  required?: boolean
  children: ReactNode
}) {
  return (
    <label className="supplier-directory__field">
      <span className="procurement-label">{label}{required ? ' *' : ''}</span>
      {children}
      {hint && <span className="procurement-helper">{hint}</span>}
    </label>
  )
}

export function SettingsPage({ t, language }: { t: Translation; language: Language }) {
  const { user, vaultKeys, saveVaultKey } = useAuth()
  const s = t.settings
  const [label, setLabel] = useState(SUGGESTED_KEYS[0])
  const [secret, setSecret] = useState('')
  const [justSaved, setJustSaved] = useState(false)

  const handleSave = async () => {
    if (!label.trim() || !secret.trim()) return
    try {
      await saveVaultKey(label.trim(), secret.trim())
      setSecret('')
      setJustSaved(true)
      window.setTimeout(() => setJustSaved(false), 1500)
    } catch {
      alert('Failed to save key. Please try again.')
    }
  }

  return (
    <div className="procurement-module settings-workspace">
      <section className="procurement-panel settings-panel">
        <h2 className="settings-panel__title">{s.accountTitle}</h2>
        <dl className="settings-panel__account-grid">
          <Row label={s.name} value={user?.name ?? '—'} />
          <Row label={t.login.email} value={user?.email ?? '—'} />
          <Row label={s.company} value={user?.company ?? '—'} />
          <Row label={s.role} value={user?.role ?? '—'} />
        </dl>
      </section>

      <section className="procurement-panel settings-panel settings-panel--vault">
        <div className="settings-panel__title-row">
          <div className="settings-panel__glyph"><ShieldIcon className="h-5 w-5" /></div>
          <div>
            <h2 className="settings-panel__title">{s.vaultTitle}</h2>
            <p className="settings-panel__description">{s.vaultDesc}</p>
          </div>
        </div>

        <div className="settings-panel__vault-form">
          <FormField label={s.keyLabel}>
            <input list="suggested-keys" value={label} onChange={(event) => setLabel(event.target.value)} className="procurement-input" />
            <datalist id="suggested-keys">
              {SUGGESTED_KEYS.map((key) => <option key={key} value={key} />)}
            </datalist>
          </FormField>
          <FormField label={s.keyValue}>
            <input type="password" value={secret} onChange={(event) => setSecret(event.target.value)} placeholder={s.keyPlaceholder} className="procurement-input" />
          </FormField>
          <button type="button" onClick={handleSave} disabled={!label.trim() || !secret.trim()} className="procurement-primary-action settings-panel__vault-action">
            {justSaved ? t.common.saved : s.addKey}
          </button>
        </div>

        <div className="settings-panel__vault-list">
          {vaultKeys.length === 0 ? (
            <p className="settings-panel__empty">{s.noKeys}</p>
          ) : (
            vaultKeys.map((key) => (
              <div key={key.id} className="settings-panel__vault-row">
                <div><strong>{key.label}</strong><code>{key.maskedValue}</code></div>
                <div><span>{s.encrypted}</span><time>{s.updated} {formatTime(key.updatedAt, language)}</time></div>
              </div>
            ))
          )}
        </div>
      </section>

      <SupplierDirectory t={t} language={language} />
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
