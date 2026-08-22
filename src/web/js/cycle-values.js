/** 3Dmigoto cycle sections form aligned preset rows. Once one variable's
 * value list is exhausted, its final value remains active for later rows. */
export function cycleValueAt(variable, position) {
  const values = variable?.values || [];
  if (!values.length) return undefined;
  return values[Math.min(Math.max(position, 0), values.length - 1)];
}

export function cyclePositionCount(variables) {
  return Math.max(0, ...variables.map(variable => variable.values.length));
}
