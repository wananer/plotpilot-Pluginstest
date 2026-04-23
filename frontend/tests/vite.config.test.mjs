import test from 'node:test'
import assert from 'node:assert/strict'

const CONFIG_PATH = new URL('../vite.config.ts', import.meta.url).pathname

async function loadConfig(env = {}) {
  const original = {
    PLOTPILOT_FRONTEND_PORT: process.env.PLOTPILOT_FRONTEND_PORT,
    PLOTPILOT_API_TARGET: process.env.PLOTPILOT_API_TARGET,
    PLOTPILOT_PLUGIN_TARGET: process.env.PLOTPILOT_PLUGIN_TARGET,
  }

  for (const key of Object.keys(original)) {
    if (key in env) {
      process.env[key] = String(env[key])
    } else {
      delete process.env[key]
    }
  }

  try {
    const mod = await import(`${CONFIG_PATH}?t=${Date.now()}-${Math.random()}`)
    return mod.default
  } finally {
    for (const [key, value] of Object.entries(original)) {
      if (value === undefined) {
        delete process.env[key]
      } else {
        process.env[key] = value
      }
    }
  }
}

test('vite config defaults to upstream-like dev settings while keeping plugins proxy', async () => {
  const config = await loadConfig()

  assert.equal(config.server.port, 3000)
  assert.equal(config.server.proxy['/api'].target, 'http://127.0.0.1:8005')
  assert.equal(config.server.proxy['/plugins'].target, 'http://127.0.0.1:8005')
})

test('vite config allows local environment overrides for api and plugins proxy', async () => {
  const config = await loadConfig({
    PLOTPILOT_FRONTEND_PORT: 3001,
    PLOTPILOT_API_TARGET: 'http://127.0.0.1:3000',
    PLOTPILOT_PLUGIN_TARGET: 'http://127.0.0.1:3000',
  })

  assert.equal(config.server.port, 3001)
  assert.equal(config.server.proxy['/api'].target, 'http://127.0.0.1:3000')
  assert.equal(config.server.proxy['/plugins'].target, 'http://127.0.0.1:3000')
})
