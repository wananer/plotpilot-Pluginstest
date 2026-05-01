import { apiAxios, apiClient } from './config'

export interface CoreRules {
  power_system: string
  physics_rules: string
  magic_tech: string
}

export interface Geography {
  terrain: string
  climate: string
  resources: string
  ecology: string
}

export interface Society {
  politics: string
  economy: string
  class_system: string
}

export interface Culture {
  history: string
  religion: string
  taboos: string
}

export interface DailyLife {
  food_clothing: string
  language_slang: string
  entertainment: string
}

export interface Worldbuilding {
  id: string
  novel_id: string
  core_rules: CoreRules
  geography: Geography
  society: Society
  culture: Culture
  daily_life: DailyLife
  created_at: string
  updated_at: string
}

function emptyWorldbuilding(slug: string): Worldbuilding {
  const emptyCoreRules = { power_system: '', physics_rules: '', magic_tech: '' }
  const emptyGeography = { terrain: '', climate: '', resources: '', ecology: '' }
  const emptySociety = { politics: '', economy: '', class_system: '' }
  const emptyCulture = { history: '', religion: '', taboos: '' }
  const emptyDailyLife = { food_clothing: '', language_slang: '', entertainment: '' }
  return {
    id: '',
    novel_id: slug,
    core_rules: emptyCoreRules,
    geography: emptyGeography,
    society: emptySociety,
    culture: emptyCulture,
    daily_life: emptyDailyLife,
    created_at: '',
    updated_at: '',
  }
}

export const worldbuildingApi = {
  getWorldbuilding: (slug: string): Promise<Worldbuilding> =>
    apiAxios.get<Worldbuilding | { detail?: unknown }>(`novels/${slug}/worldbuilding`, {
      validateStatus: status => (status >= 200 && status < 300) || status === 404,
    }).then(response => {
      const data = response as unknown as Worldbuilding | { detail?: unknown }
      if (data && typeof data === 'object' && 'detail' in data) {
        return emptyWorldbuilding(slug)
      }
      return data as Worldbuilding
    }),

  updateWorldbuilding: (slug: string, data: Partial<Worldbuilding>): Promise<Worldbuilding> =>
    apiClient.put<Worldbuilding>(`novels/${slug}/worldbuilding`, data),
}
