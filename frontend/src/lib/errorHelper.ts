export function getFriendlyErrorMessage(errorMsg: string | null | undefined): string {
  if (!errorMsg) {
    return '这次规划没有顺利完成，请重试一次';
  }

  const cleanMsg = errorMsg.trim();

  // List of internal terms / validation failure indicators
  const internalTerms = [
    'planning_context',
    'time_horizon',
    'intentprofile',
    'validation',
    'assertion',
    'typeerror',
    'valueerror',
    'keyerror',
    'must match',
    'cannot be',
    'invalid',
    'internal server error',
    'failed to',
    'not found',
    'undefined'
  ];

  const hasInternalTerm = internalTerms.some(term => cleanMsg.toLowerCase().includes(term));

  // Also if the message is pure English/code and doesn't look like a localized business error, we map it
  const isEnglishOrCode = /^[a-zA-Z0-9_\-\s.:,(){}[\]]+$/.test(cleanMsg);

  if (hasInternalTerm || isEnglishOrCode) {
    return '这次规划没有顺利完成，请重试一次';
  }

  return cleanMsg;
}
