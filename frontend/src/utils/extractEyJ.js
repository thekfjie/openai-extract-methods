const ACCESS_TOKEN_KEYS = new Set([
  'accesstoken',
  'access_token',
  'access-token',
  'authorization',
  'bearer',
  'token',
]);

const EYJ_TOKEN_PATTERN = /eyJ[A-Za-z0-9._-]{8,}/g;

function cleanCandidate(value) {
  const text = String(value ?? '').trim().replace(/^Bearer\s+/i, '');
  const match = text.match(EYJ_TOKEN_PATTERN);
  return match?.[0] || '';
}

function collectPreferred(value, found, seen) {
  if (!value || typeof value !== 'object' || seen.has(value)) return;
  seen.add(value);
  if (Array.isArray(value)) {
    value.forEach((item) => collectPreferred(item, found, seen));
    return;
  }
  Object.entries(value).forEach(([key, item]) => {
    if (ACCESS_TOKEN_KEYS.has(key.toLowerCase()) && typeof item === 'string') {
      const token = cleanCandidate(item);
      if (token) found.push(token);
    }
    collectPreferred(item, found, seen);
  });
}

function collectStringTokens(value, found, seen) {
  if (typeof value === 'string') {
    found.push(...(value.match(EYJ_TOKEN_PATTERN) || []));
    return;
  }
  if (!value || typeof value !== 'object' || seen.has(value)) return;
  seen.add(value);
  if (Array.isArray(value)) {
    value.forEach((item) => collectStringTokens(item, found, seen));
    return;
  }
  Object.values(value).forEach((item) => collectStringTokens(item, found, seen));
}

function unique(tokens) {
  return [...new Set(tokens.map((token) => cleanCandidate(token)).filter(Boolean))];
}

export function extractEyJTokens(input) {
  const text = String(input ?? '').trim();
  if (!text) return [];

  try {
    const parsed = JSON.parse(text);
    const preferred = [];
    collectPreferred(parsed, preferred, new WeakSet());
    if (preferred.length) return unique(preferred);

    const nested = [];
    collectStringTokens(parsed, nested, new WeakSet());
    return unique(nested);
  } catch {
    return unique(text.match(EYJ_TOKEN_PATTERN) || []);
  }
}
