type SceneViewProps = {
  sceneText: string
  visibleEvents: string[]
}

export function SceneView({ sceneText, visibleEvents }: SceneViewProps) {
  return (
    <section aria-live="polite" className="min-w-0">
      <div className="whitespace-pre-wrap text-lg leading-8 text-foreground">
        {sceneText}
      </div>
      {visibleEvents.length > 0 && (
        <div className="mt-8 border-t pt-5">
          {visibleEvents.map((event, index) => (
            <p className="mt-3 whitespace-pre-wrap leading-7 text-foreground" key={`${index}:${event}`}>
              {event}
            </p>
          ))}
        </div>
      )}
    </section>
  )
}
