// Shared condition semantics for controls and ordered animation programs.

export function compareValues(left, op, right) {
  switch (op) {
    case '==':
    case '===':
      return left === right;
    case '!=':
    case '!==':
      return left !== right;
    case '>':
      return Number(left) > Number(right);
    case '<':
      return Number(left) < Number(right);
    case '>=':
      return Number(left) >= Number(right);
    case '<=':
      return Number(left) <= Number(right);
    default:
      return false;
  }
}

export function evaluateExpression(expression, state) {
  if (!expression) return 0;
  switch (expression.kind) {
    case 'literal':
      return expression.value;
    case 'dt':
      return state.dt;
    case 'variable': {
      const value = Number(state.variables[expression.variable]);
      return Number.isFinite(value) ? value : 0;
    }
    case 'binary': {
      const left = evaluateExpression(expression.left, state);
      const right = evaluateExpression(expression.right, state);
      switch (expression.op) {
        case '+':
          return left + right;
        case '-':
          return left - right;
        case '*':
          return left * right;
        case '/':
          return right === 0 ? 0 : left / right;
        case '//':
          return right === 0 ? 0 : Math.floor(left / right);
        case '%':
          return right === 0 ? 0 : left % right;
        default:
          return 0;
      }
    }
    case 'compare':
      return Number(
        compareValues(
          evaluateExpression(expression.left, state),
          expression.op,
          evaluateExpression(expression.right, state),
        ),
      );
    case 'not':
      return Number(!evaluateExpression(expression.operand, state));
    case 'and':
      return Number(expression.parts.every((part) => evaluateExpression(part, state)));
    case 'or':
      return Number(expression.parts.some((part) => evaluateExpression(part, state)));
    default:
      return 0;
  }
}

export function evaluateCondition(condition, state) {
  return !condition || Boolean(evaluateExpression(condition, state));
}
