import { Button } from '@/components/ui/button'
import { useTranslation } from '@/i18n'

/**
 * Downloaded models are assigned to Story task profiles rather than opened in
 * Jan's retired generic-chat flow. Profile assignment is introduced on the
 * Story model-profile surface; the model center only reports readiness.
 */
export function DownloadedModelAction() {
  const { t } = useTranslation()

  return (
    <Button disabled size="sm" variant="outline">
      {t('hub:downloaded')}
    </Button>
  )
}
