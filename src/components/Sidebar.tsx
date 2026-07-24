import type { ModuleId } from '../types'
import type { Translation } from '../i18n'
import { MODULE_ICONS } from './icons'

const MODULE_ORDER: ModuleId[] = ['sourcing', 'comparison', 'memory', 'settings']

export function Sidebar({
  active,
  onChange,
  t,
}: {
  active: ModuleId
  onChange: (id: ModuleId) => void
  t: Translation
}) {
  return (
    <aside className="procurement-sidebar relative z-10 flex shrink-0 flex-col overflow-y-auto print:hidden">
      <nav className="flex-1 space-y-1 px-3 py-4">
        <p className="procurement-nav-label mb-2 px-3">
          {t.nav.modules}
        </p>
        {MODULE_ORDER.map((id) => {
          const isActive = active === id
          const Icon = MODULE_ICONS[id]
          return (
            <button
              key={id}
              type="button"
              onClick={() => onChange(id)}
              aria-current={isActive ? 'page' : undefined}
              className={`procurement-nav-item flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm font-medium ${
                isActive ? 'is-active' : ''
              }`}
            >
              <span className="procurement-nav-icon">
                <Icon className="h-5 w-5" />
              </span>
              {t.nav[id]}
            </button>
          )
        })}
      </nav>
      <div className="procurement-sidebar-footer px-5 py-4 text-[11px]">{t.tagline}</div>
    </aside>
  )
}
