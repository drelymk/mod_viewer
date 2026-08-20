// Mod control values plus safe source-ordered derived-state replay.

let values = {};
let stateRules = [];

export function resetControlState() {
  values = {};
  stateRules = [];
}

export function setControlValue(variable, value) {
  values[variable] = value;
}

export function getControlValue(variable) {
  return values[variable];
}

export function getControlState() {
  return { ...values };
}

// True if an OR'd list of AND-groups ([[{var,value,negate}, ...], ...]) is
// satisfied by the current control state.
export function dnfSatisfied(condGroups) {
  if (!condGroups || condGroups.length === 0) return true;
  return condGroups.some(group => group.every(condition => {
    const current = values[condition.var];
    if (current === undefined) return true;
    return condition.negate
      ? current !== condition.value
      : current === condition.value;
  }));
}

export function setControlStateRules(rules, defaults) {
  stateRules = rules || [];
  for (const [variable, value] of Object.entries(defaults || {})) {
    if (values[variable] === undefined) values[variable] = value;
  }
}

/** Replay safe [Present] assignments in source order. Later rules deliberately
 * observe values written by earlier rules, matching the game's execution. */
export function replayControlStateRules() {
  for (const rule of stateRules) {
    if (dnfSatisfied(rule.conditions)) values[rule.var] = rule.value;
  }
}
