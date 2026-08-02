export type WorldSection =
  | 'overview'
  | 'rules'
  | 'locations'
  | 'organizations'
  | 'history'
  | 'established_facts'
  | 'unresolved_threads'
  | 'director_notes'

const labels: Array<[WorldSection, string]> = [
  ['overview', '世界概览'],
  ['rules', '世界规则'],
  ['locations', '地点'],
  ['organizations', '组织'],
  ['history', '历史'],
  ['established_facts', '已确立事实'],
  ['unresolved_threads', '未解决线索'],
  ['director_notes', '导演补充'],
]

export function WorldBibleNavigation({
  active,
  onChange,
}: {
  active: WorldSection
  onChange: (section: WorldSection) => void
}) {
  return (
    <nav className="flex overflow-x-auto border-b bg-muted/20 p-2 md:block md:border-b-0 md:border-r">
      {labels.map(([value, label]) => (
        <button
          className={`min-w-max px-3 py-2 text-left text-sm transition-colors md:mb-1 md:w-full ${
            active === value
              ? 'bg-foreground text-background'
              : 'text-muted-foreground hover:bg-accent hover:text-foreground'
          }`}
          key={value}
          onClick={() => onChange(value)}
          type="button"
        >
          {label}
        </button>
      ))}
    </nav>
  )
}
