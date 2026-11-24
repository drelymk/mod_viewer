// Per-game default animation names saved in Settings, and the lookup that picks one by name.
import { viewerState } from './state.js';

const GAME_IDS = { genshin: 'GIMI', zzz: 'ZZMI', wuwa: 'WWMI' };
let defaults = {};

export function setDefaultAnimations(value) {
  defaults = { ...value };
}
export function getDefaultAnimation(kind) {
  return defaults[kind] || '';
}
// Game kind (GIMI/ZZMI/WWMI) of the loaded model, from an Asset's type or the payload's game id.
export function gameKindFromPayload(metadata) {
  return metadata?.asset?.type || GAME_IDS[String(metadata?.game?.id || '').toLowerCase()] || null;
}
// The default is a ;-separated name list, highest priority first. Returns the clip or sequence
// for the first name that matches (own clips, then sequences, then shared), else null.
export function findDefaultAnimationId(state, kind = viewerState.gameKind) {
  for (const entry of (defaults[kind] || '').toLowerCase().split(';')) {
    const wanted = entry.trim();
    if (!wanted) continue;
    const named = (item) => item.name.trim().toLowerCase() === wanted;
    const found =
      state.clips.find((clip) => !clip.shared && named(clip)) ??
      state.sequences.find(named) ??
      state.clips.find((clip) => clip.shared && named(clip));
    if (found) return found.id;
  }
  return null;
}
