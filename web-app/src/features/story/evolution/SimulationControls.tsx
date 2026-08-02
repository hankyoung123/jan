import { Ban, Pause, Play, Save, SkipForward, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useTranslation } from '@/i18n'

interface Props {
  status: string
  pending: (name: 'step' | 'run' | 'pause' | 'resume' | 'terminate' | 'cancel' | 'checkpoint') => boolean
  onStep: () => void
  onRun: () => void
  onPause: () => void
  onResume: () => void
  onTerminate: () => void
  onCancel: () => void
  onCheckpoint: () => void
}

export function SimulationControls(props: Props) {
  const { t } = useTranslation('evolution')
  const running = props.status === 'running'
  const paused = props.status === 'paused'
  const ended = ['terminated', 'cancelled', 'failed'].includes(props.status)
  return (
    <section className="flex flex-wrap items-center gap-2 border-x border-b bg-muted/20 p-3">
      <Button disabled={running || ended || props.pending('step')} onClick={props.onStep}>
        <SkipForward size={15} /> {t('controls.step')}
      </Button>
      <Button
        disabled={running || ended || props.pending('run')}
        onClick={props.onRun}
        variant="outline"
      >
        <Play size={15} /> {t('controls.run')}
      </Button>
      <Button
        disabled={!running || props.pending('pause')}
        onClick={props.onPause}
        variant="outline"
      >
        <Pause size={15} /> {t('controls.pause')}
      </Button>
      <Button
        disabled={!paused || props.pending('resume')}
        onClick={props.onResume}
        variant="outline"
      >
        <Play size={15} /> {t('controls.resume')}
      </Button>
      <Button
        disabled={running || ended || props.pending('checkpoint')}
        onClick={props.onCheckpoint}
        variant="outline"
      >
        <Save size={15} /> {t('controls.checkpoint')}
      </Button>
      <div className="mx-1 h-6 w-px bg-border" />
      <Button
        disabled={ended || props.pending('terminate')}
        onClick={props.onTerminate}
        variant="outline"
      >
        <Square size={14} /> {t('controls.safeTerminate')}
      </Button>
      <Button
        disabled={!running || props.pending('cancel')}
        onClick={props.onCancel}
        variant="destructive"
      >
        <Ban size={14} /> {t('controls.cancel')}
      </Button>
    </section>
  )
}
