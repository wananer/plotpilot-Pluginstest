import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const pluginLoaderPath = path.resolve(process.cwd(), 'public/plugin-loader.js')
const workbenchPath = path.resolve(process.cwd(), 'src/views/Workbench.vue')
const workAreaPath = path.resolve(process.cwd(), 'src/components/workbench/WorkArea.vue')

const source = fs.readFileSync(pluginLoaderPath, 'utf8')
const workbenchSource = fs.readFileSync(workbenchPath, 'utf8')
const workAreaSource = fs.readFileSync(workAreaPath, 'utf8')

test('plugin-loader exposes required runtime symbols', () => {
  assert.match(source, /window\.PlotPilotPlugins\s*=\s*runtime/)
  assert.match(source, /runtime\.events\.emit\('runtime:ready'/)
  assert.match(source, /runtime\.events\.emit\('plugins:loaded'/)
  assert.match(source, /emitChapterLoaded\(payload\)\s*\{/)
  assert.match(source, /emitChapterSaved\(payload\)\s*\{/)
  assert.match(source, /emitRouteChanged\(payload\)\s*\{/)
  assert.match(source, /if \(runtime\.scripts\.has\(src\)\) return;/)
  assert.match(source, /queueMicrotask\(\(\) =>/)
  assert.match(source, /if \(nextPlugin\.enabled !== false\)/)
})

test('plugin-loader exposes host bridge context and lifecycle APIs', () => {
  assert.match(source, /getContext\(\)\s*\{/)
  assert.match(source, /reloadPlugins\s*:\s*async\s*\(\)\s*=>/)
  assert.match(source, /refreshManifest\s*:\s*async\s*\(\)\s*=>/)
  assert.match(source, /emitGenerationCompleted\(payload\)\s*\{/)
  assert.match(source, /emitRewriteCompleted\(payload\)\s*\{/)
  assert.match(source, /emitNovelChanged\(payload\)\s*\{/)
  assert.match(source, /emitChapterCommitted\(payload\)\s*\{/)
  assert.match(source, /runtime\.plugins\.dispose\(item\.name\)/)
})

test('plugin-loader exposes world-evolution host bridge surface', () => {
  assert.match(source, /version:\s*'0\.5\.0'/)
  assert.match(source, /currentView:\s*null/)
  assert.match(source, /lastWorkbenchOpened:\s*null/)
  assert.match(source, /getView\(\)\s*\{/)
  assert.match(source, /getCurrentChapter\(\)\s*\{/)
  assert.match(source, /getLastEvent\(eventName\)\s*\{/)
  assert.match(source, /getAvailableEvents\(\)\s*\{/)
  assert.match(source, /emitWorkbenchOpened\(payload\)\s*\{/)
  assert.match(source, /emitNovelSelected\(payload\)\s*\{/)
  assert.match(source, /emitManualRerunRequested\(payload\)\s*\{/)
  assert.match(source, /emitTimelineRebuildRequested\(payload\)\s*\{/)
  assert.match(source, /manual:rerun_requested/)
  assert.match(source, /timeline:rebuild_requested/)
  assert.match(source, /workbench:opened/)
  assert.match(source, /novel:selected/)
})

test('workbench emits host bridge events for route and chapter loading', () => {
  assert.match(workbenchSource, /emitWorkbenchOpened\(/)
  assert.match(workbenchSource, /emitNovelSelected\(/)
  assert.match(workbenchSource, /emitChapterLoaded\(/)
  assert.match(workbenchSource, /source:\s*'workbench-mounted'/)
  assert.match(workbenchSource, /source:\s*'novel-route'/)
  assert.match(workbenchSource, /source:\s*'route-query'/)
})

test('work area emits host bridge events for save and generation completion', () => {
  assert.match(workAreaSource, /emitChapterSaved\(/)
  assert.match(workAreaSource, /emitGenerationCompleted\(/)
  assert.match(workAreaSource, /emitChapterCommitted\(/)
  assert.match(workAreaSource, /source:\s*'editor-save'/)
  assert.match(workAreaSource, /source:\s*'generation-save'/)
  assert.match(workAreaSource, /source:\s*'manual-generate'/)
})
