import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const publicLoaderPath = path.resolve(process.cwd(), 'public/plugin-loader.js')
const platformLoaderPath = path.resolve(process.cwd(), '../platform/frontend/public/plugin-loader.js')

const publicSource = fs.readFileSync(publicLoaderPath, 'utf8')
const platformSource = fs.readFileSync(platformLoaderPath, 'utf8')

function assertWorldEvolutionBridge(source) {
  assert.match(source, /version:\s*'0\.5\.0'/)
  assert.match(source, /window\.PlotPilotPlugins\s*=\s*runtime/)
  assert.match(source, /refreshManifest\s*:\s*async\s*\(\)\s*=>/)
  assert.match(source, /reloadPlugins\s*:\s*async\s*\(\)\s*=>/)
  assert.match(source, /getContext\(\)\s*\{/)
  assert.match(source, /getCurrentChapter\(\)\s*\{/)
  assert.match(source, /getLastEvent\(eventName\)\s*\{/)
  assert.match(source, /getAvailableEvents\(\)\s*\{/)
  assert.match(source, /emitWorkbenchOpened\(payload\)\s*\{/)
  assert.match(source, /emitNovelSelected\(payload\)\s*\{/)
  assert.match(source, /emitNovelChanged\(payload\)\s*\{/)
  assert.match(source, /emitChapterCommitted\(payload\)\s*\{/)
  assert.match(source, /emitGenerationCompleted\(payload\)\s*\{/)
  assert.match(source, /emitRewriteCompleted\(payload\)\s*\{/)
  assert.match(source, /emitManualRerunRequested\(payload\)\s*\{/)
  assert.match(source, /emitTimelineRebuildRequested\(payload\)\s*\{/)
  assert.match(source, /manual:rerun_requested/)
  assert.match(source, /timeline:rebuild_requested/)
  assert.match(source, /workbench:opened/)
  assert.match(source, /novel:selected/)
  assert.match(source, /runtime\.events\.emit\('runtime:ready'/)
  assert.match(source, /runtime\.events\.emit\('plugins:loaded'/)
}

test('public plugin loader exposes the world_evolution_core host bridge', () => {
  assertWorldEvolutionBridge(publicSource)
})

test('platform installer copy keeps the same plugin loader runtime', () => {
  assert.equal(platformSource, publicSource)
})
