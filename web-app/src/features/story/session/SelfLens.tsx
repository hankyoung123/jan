import { UserRound } from 'lucide-react'

type PlayerState = {
  identity: string
  capabilities: string[]
  conditions: string[]
  possessions: string[]
  relationships: string[]
}

export function SelfLens({ player }: { player: PlayerState }) {
  const sections = [
    ['能力 / 经历', player.capabilities],
    ['当前身体状态', player.conditions],
    ['重要随身物', player.possessions],
    ['当前重要关系', player.relationships],
  ] as const

  return (
    <aside className="border-l pl-5 lg:sticky lg:top-8 lg:self-start">
      <div className="flex items-center gap-2 text-sm font-medium">
        <UserRound size={16} />
        <span>{player.identity}</span>
      </div>
      <div className="mt-5 space-y-5">
        {sections.map(([title, values]) => (
          values.length > 0 && (
            <section key={title}>
              <h2 className="text-xs font-medium text-muted-foreground">{title}</h2>
              <ul className="mt-2 space-y-1.5 text-sm leading-6">
                {values.map((value) => <li key={value}>{value}</li>)}
              </ul>
            </section>
          )
        ))}
      </div>
    </aside>
  )
}
