import type { AxiosProgressEvent } from 'axios'

import { apiClient } from './config'

export interface PluginManifestRecord {
  name: string
  display_name?: string | null
  version?: string | null
  enabled?: boolean
  manifest_enabled?: boolean
  configured_enabled?: boolean | null
  frontend_scripts?: string[]
  frontend_styles?: string[]
  capabilities?: Record<string, unknown> | unknown[]
  permissions?: unknown[]
  hooks?: unknown[]
  manifest?: Record<string, unknown>
}

export interface PluginListResponse {
  items: PluginManifestRecord[]
  total: number
  frontend_scripts: string[]
  runtime?: Record<string, unknown>
}

export interface PluginImportResponse {
  ok: boolean
  source: 'github' | 'upload'
  plugin_name: string
  target_dir: string
  manifest?: Record<string, unknown>
  message?: string
}

export interface PluginEnabledResponse {
  ok: boolean
  plugin_name: string
  enabled: boolean
  plugin?: PluginManifestRecord
  message?: string
}

function pluginAdminHeaders(): Record<string, string> {
  if (typeof window === 'undefined') {
    return {}
  }
  const token = window.localStorage.getItem('plotpilot_plugin_admin_token')?.trim()
  return token ? { 'x-plugin-admin-token': token } : {}
}

export const pluginPlatformApi = {
  list(): Promise<PluginListResponse> {
    return apiClient.get('/plugins') as unknown as Promise<PluginListResponse>
  },

  importFromGithub(githubUrl: string): Promise<PluginImportResponse> {
    return apiClient.post('/plugins/import/github', { github_url: githubUrl }, {
      headers: pluginAdminHeaders(),
    }) as unknown as Promise<PluginImportResponse>
  },

  importFromZip(file: File, onProgress?: (event: AxiosProgressEvent) => void): Promise<PluginImportResponse> {
    const formData = new FormData()
    formData.append('file', file)
    return apiClient.post('/plugins/import/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data', ...pluginAdminHeaders() },
      onUploadProgress: onProgress,
    }) as unknown as Promise<PluginImportResponse>
  },

  setEnabled(pluginName: string, enabled: boolean): Promise<PluginEnabledResponse> {
    return apiClient.put(`/plugins/${encodeURIComponent(pluginName)}/enabled`, { enabled }, {
      headers: pluginAdminHeaders(),
    }) as unknown as Promise<PluginEnabledResponse>
  },
}
