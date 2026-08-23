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

function controlValues(controls) {
  const legal = new Map();
  for (const info of Object.values(controls?.toggles || {})) {
    for (const variable of (info.cycle_vars || info.vars || [])) {
      legal.set(variable.var, variable.values || []);
    }
  }
  for (const info of Object.values(controls?.menu || {})) {
    if (info.values) legal.set(info.var, info.values);
  }
  return legal;
}

/** Reconcile authoritative control semantics without resetting live values. */
export function reconcileControlState(rules, defaults, controls = null) {
  stateRules = rules || [];
  const next = controls === null ? { ...values } : {};
  const legal = controls === null ? new Map() : controlValues(controls);
  const configured = new Map(Object.entries(defaults || {}));
  if (controls !== null) {
    for (const [variable] of legal) {
      if (!configured.has(variable)) configured.set(variable, undefined);
    }
  }
  for (const [variable, defaultValue] of configured) {
    const allowed = legal.get(variable);
    const current = values[variable];
    if (current !== undefined && (!allowed || allowed.includes(current))) {
      next[variable] = current;
    } else if (allowed?.length) {
      next[variable] = allowed.includes(defaultValue) ? defaultValue : allowed[0];
    } else {
      next[variable] = defaultValue;
    }
  }
  values = next;
}

export function setControlStateRules(rules, defaults, controls = null) {
  reconcileControlState(rules, defaults, controls);
}

/** Replay safe [Present] assignments in source order. Later rules deliberately
 * observe values written by earlier rules, matching the game's execution. */
export function replayControlStateRules() {
  for (const rule of stateRules) {
    if (dnfSatisfied(rule.conditions)) values[rule.var] = rule.value;
  }
}
