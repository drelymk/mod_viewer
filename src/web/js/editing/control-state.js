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

/** Return variables whose final values differ between two control snapshots. */
export function changedControlVariables(previous, current) {
  const changed = new Set();
  const keys = new Set([
    ...Object.keys(previous || {}),
    ...Object.keys(current || {}),
  ]);
  for (const variable of keys) {
    if (!Object.is(previous?.[variable], current?.[variable])) {
      changed.add(variable);
    }
  }
  return changed;
}

// True if an OR'd list of AND-groups ([[{var,value,negate}, ...], ...]) is
// satisfied by the current control state.
export function dnfSatisfied(condGroups) {
  if (!condGroups || condGroups.length === 0) return true;
  return condGroups.some(group => group.every(condition => {
    const current = values[condition.var];
    if (current === undefined) return true;
    if (condition.op && condition.op !== '==' && condition.op !== '!=') {
      const left = Number(current);
      const right = Number(condition.value);
      if (!Number.isFinite(left) || !Number.isFinite(right)) return true;
      switch (condition.op) {
        case '>': return left > right;
        case '<': return left < right;
        case '>=': return left >= right;
        case '<=': return left <= right;
        default: return true;
      }
    }
    return condition.negate
      ? current !== condition.value
      : current === condition.value;
  }));
}

function controlValues(controls) {
  const domains = new Map();
  const addDomain = (variable, domain) => {
    if (!variable || !domain) return;
    const existing = domains.get(variable);
    if (existing?.kind === 'continuous' || domain.kind === 'continuous') {
      domains.set(variable, domain.kind === 'continuous' ? domain : existing);
      return;
    }
    const values = [...new Set([...(existing?.values || []), ...(domain.values || [])])];
    domains.set(variable, { kind: 'discrete', values });
  };
  for (const info of Object.values(controls?.toggles || {})) {
    for (const variable of (info.cycle_vars || info.vars || [])) {
      addDomain(variable.var, { kind: 'discrete', values: variable.values });
    }
  }
  for (const info of Object.values(controls?.menu || {})) {
    addDomain(info.var, info.domain || {
      kind: 'discrete', values: info.values || [],
    });
  }
  return domains;
}

// Action execution has a closed-world contract: an unknown selector must not
// accidentally execute a branch.  Rendering intentionally keeps the
// fail-open dnfSatisfied() semantics so an unknown gate cannot hide geometry.
export function strictDnfSatisfied(condGroups) {
  if (!condGroups || condGroups.length === 0) return true;
  return condGroups.some(group => group.every(condition => {
    const current = values[condition.var];
    if (current === undefined) return false;
    if (condition.op && condition.op !== '==' && condition.op !== '!=') {
      const left = Number(current);
      const right = Number(condition.value);
      if (!Number.isFinite(left) || !Number.isFinite(right)) return false;
      switch (condition.op) {
        case '>': return left > right;
        case '<': return left < right;
        case '>=': return left >= right;
        case '<=': return left <= right;
        default: return false;
      }
    }
    return condition.negate
      ? current !== condition.value
      : current === condition.value;
  }));
}

/** Reconcile authoritative control semantics without resetting live values. */
export function reconcileControlState(rules, defaults, controls = null) {
  stateRules = rules || [];
  const next = controls === null ? { ...values } : {};
  const domains = controls === null ? new Map() : controlValues(controls);
  const configured = new Map(Object.entries(defaults || {}));
  if (controls !== null) {
    for (const [variable] of domains) {
      if (!configured.has(variable)) configured.set(variable, undefined);
    }
  }
  for (const [variable, defaultValue] of configured) {
    const domain = domains.get(variable);
    const current = values[variable];
    if (current !== undefined && (!domain ||
        (domain.kind === 'continuous'
          ? Number.isFinite(Number(current)) &&
            (domain.min === undefined || Number(current) >= Number(domain.min)) &&
            (domain.max === undefined || Number(current) <= Number(domain.max))
          : domain.values.includes(current)))) {
      next[variable] = current;
    } else if (domain?.kind === 'continuous') {
      const fallback = Number(defaultValue);
      const min = Number(domain.min ?? 0);
      const max = Number(domain.max ?? 1);
      const value = Number.isFinite(fallback) ? Math.min(max, Math.max(min, fallback)) : min;
      next[variable] = String(value);
    } else if (domain?.values?.length) {
      next[variable] = domain.values.includes(defaultValue)
        ? defaultValue : domain.values[0];
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
