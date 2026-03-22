import { writable, derived, get } from 'svelte/store';
import { getConfig, updateConfig as apiUpdateConfig } from '../api.js';

export const config = writable({});
export const savedConfig = writable({});
export const isDirty = derived(
  [config, savedConfig],
  ([$config, $saved]) => JSON.stringify($config) !== JSON.stringify($saved)
);

export async function loadConfig() {
  const data = await getConfig();
  config.set(data);
  savedConfig.set(structuredClone(data));
}

export async function saveConfig() {
  const data = get(config);
  await apiUpdateConfig(data);
  savedConfig.set(structuredClone(data));
}

export function resetConfig() {
  const saved = get(savedConfig);
  config.set(structuredClone(saved));
}
