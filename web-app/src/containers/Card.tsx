import { cn } from '@/lib/utils'
import { ReactNode } from 'react'

type CardProps = {
  title?: string
  children?: ReactNode
  header?: ReactNode
}

type CardItemProps = {
  title?: string | ReactNode
  description?: string | ReactNode
  descriptionOutside?: string | ReactNode
  align?: 'start' | 'center' | 'end'
  actions?: ReactNode
  column?: boolean
  className?: string
  classNameWrapperAction?: string
}

export function CardItem({
  title,
  description,
  descriptionOutside,
  className,
  classNameWrapperAction,
  align = 'center',
  column,
  actions,
}: CardItemProps) {
  return (
    <>
      <div
        className={cn(
          'flex flex-col items-stretch justify-between mt-2 first:mt-0 border-b border-border/40 pb-3 last:border-none last:pb-0 gap-3 sm:flex-row sm:gap-8',
          descriptionOutside && 'border-0',
          align === 'start' && 'sm:items-start',
          align === 'center' && 'sm:items-center',
          align === 'end' && 'sm:items-end',
          column && 'flex-col items-start gap-y-0 sm:flex-col sm:items-start',
          className
        )}
      >
        <div className="min-w-0 space-y-1.5">
          <h1 className="font-medium text-foreground">{title}</h1>
          {description && (
            <span className="text-muted-foreground leading-normal">
              {description}
            </span>
          )}
        </div>
        {actions && (
          <div
            className={cn(
              'w-full shrink-0 sm:w-auto',
              classNameWrapperAction,
              column && 'sm:w-full'
            )}
          >
            {actions}
          </div>
        )}
      </div>
      {descriptionOutside && (
        <span className="text-muted-foreground leading-normal">
          {descriptionOutside}
        </span>
      )}
    </>
  )
}

export function Card({ title, children, header }: CardProps) {
  return (
    <div className="bg-card p-4 rounded-lg text-muted-foreground w-full">
      {title && (
        <h1 className="text-foreground font-studio font-medium text-base mb-4">
          {title}
        </h1>
      )}
      {header && header}
      {children}
    </div>
  )
}
