declare global {
  interface PlotPilotPluginHost {
    emitChapterLoaded: (payload: Record<string, unknown>) => void
    emitChapterSaved: (payload: Record<string, unknown>) => void
    emitChapterCommitted: (payload: Record<string, unknown>) => void
    emitGenerationCompleted: (payload: Record<string, unknown>) => void
    emitRewriteCompleted: (payload: Record<string, unknown>) => void
    emitWorkbenchOpened: (payload: Record<string, unknown>) => void
    emitNovelSelected: (payload: Record<string, unknown>) => void
    emitNovelChanged: (payload: Record<string, unknown>) => void
    emitManualRerunRequested: (payload: Record<string, unknown>) => void
    emitTimelineRebuildRequested: (payload: Record<string, unknown>) => void
  }

  interface PlotPilotPluginRuntime {
    host?: PlotPilotPluginHost
    refreshManifest?: () => Promise<unknown>
    reloadPlugins?: () => Promise<unknown>
  }

  interface Window {
    PlotPilotPlugins?: PlotPilotPluginRuntime
    __PLOTPILOT_CAPTURE_HOSTED_WRITE_EVENTS__?: boolean
    __PLOTPILOT_HOSTED_WRITE_EVENTS__?: Record<string, unknown>[]
    $message?: {
      success: (content: string) => void
      error: (content: string) => void
      warning: (content: string) => void
      info: (content: string) => void
    }
  }
}

export {}
