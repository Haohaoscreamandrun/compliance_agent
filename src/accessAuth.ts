export interface VerifiedAccessIdentity {
  email: string;
  sub?: string;
}

interface JwksCacheEntry {
  fetchedAtMs: number;
  expiresAtMs: number;
  keysByKid: Map<string, CryptoKey>;
}

const JWKS_TTL_MS = 5 * 60 * 1000;
const JWKS_MIN_REFRESH_INTERVAL_MS = 30 * 1000;
const jwksCache = new Map<string, JwksCacheEntry>();

export function clearJwksCacheForTests(): void {
  jwksCache.clear();
}

function decodeBase64UrlToBytes(input: string): Uint8Array {
  const normalized = input.replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function decodeBase64UrlJson<T>(input: string): T | null {
  try {
    const text = new TextDecoder().decode(decodeBase64UrlToBytes(input));
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

function normalizeTeamOrigin(teamDomain: string): string | null {
  const cleaned = teamDomain.trim().replace(/^https?:\/\//i, '').replace(/\/+$/, '');
  if (!cleaned || /[^a-z0-9.-]/i.test(cleaned)) {
    return null;
  }
  return `https://${cleaned}`;
}

async function getVerificationKey(
  teamOrigin: string,
  kid: string,
  fetchFn: typeof fetch,
): Promise<CryptoKey | null> {
  const now = Date.now();
  const cached = jwksCache.get(teamOrigin);
  if (cached && cached.expiresAtMs > now) {
    const hit = cached.keysByKid.get(kid);
    if (hit) return hit;
    if (now - cached.fetchedAtMs < JWKS_MIN_REFRESH_INTERVAL_MS) {
      return null;
    }
  }

  const resp = await fetchFn(`${teamOrigin}/cdn-cgi/access/certs`, {
    headers: { accept: 'application/json' },
  });
  if (!resp.ok) return null;

  const body = (await resp.json()) as { keys?: Array<Record<string, unknown>> };
  if (!Array.isArray(body.keys)) return null;

  const keysByKid = new Map<string, CryptoKey>();
  for (const rawJwk of body.keys) {
    const keyId = typeof rawJwk.kid === 'string' ? rawJwk.kid : '';
    if (!keyId || rawJwk.kty !== 'RSA') continue;
    try {
      const cryptoKey = await crypto.subtle.importKey(
        'jwk',
        rawJwk,
        { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
        false,
        ['verify'],
      );
      keysByKid.set(keyId, cryptoKey);
    } catch {
      // Skip malformed JWKs rather than failing all keys.
    }
  }

  jwksCache.set(teamOrigin, { fetchedAtMs: now, expiresAtMs: now + JWKS_TTL_MS, keysByKid });
  return keysByKid.get(kid) ?? null;
}

export async function verifyCloudflareAccessJwt(
  token: string,
  teamDomain: string,
  expectedAud?: string,
  fetchFn: typeof fetch = fetch,
): Promise<VerifiedAccessIdentity | null> {
  const parts = token.trim().split('.');
  if (parts.length !== 3) return null;

  const [headerB64, payloadB64, sigB64] = parts;
  const header = decodeBase64UrlJson<{ alg?: unknown; kid?: unknown }>(headerB64);
  const payload = decodeBase64UrlJson<{
    email?: unknown;
    sub?: unknown;
    iss?: unknown;
    aud?: unknown;
    exp?: unknown;
    nbf?: unknown;
  }>(payloadB64);
  if (!header || !payload) return null;

  if (header.alg !== 'RS256' || typeof header.kid !== 'string' || !header.kid) {
    return null;
  }

  const teamOrigin = normalizeTeamOrigin(teamDomain);
  if (!teamOrigin) return null;

  if (typeof payload.iss === 'string' && payload.iss.replace(/\/+$/, '') !== teamOrigin) {
    return null;
  }

  const nowSec = Math.floor(Date.now() / 1000);
  if (typeof payload.exp !== 'number' || payload.exp <= nowSec) {
    return null;
  }
  if (typeof payload.nbf === 'number' && payload.nbf > nowSec) {
    return null;
  }

  const audExpected = expectedAud?.trim();
  if (!audExpected) {
    return null;
  }
  const audList = Array.isArray(payload.aud)
    ? payload.aud
    : typeof payload.aud === 'string'
      ? [payload.aud]
      : [];
  if (!audList.includes(audExpected)) {
    return null;
  }

  if (typeof payload.email !== 'string' || !payload.email.trim()) {
    return null;
  }

  try {
    const key = await getVerificationKey(teamOrigin, header.kid, fetchFn);
    if (!key) return null;

    const signingInput = new TextEncoder().encode(`${headerB64}.${payloadB64}`);
    const signature = decodeBase64UrlToBytes(sigB64);
    const valid = await crypto.subtle.verify(
      'RSASSA-PKCS1-v1_5',
      key,
      signature as BufferSource,
      signingInput,
    );
    if (!valid) return null;
  } catch {
    return null;
  }

  return {
    email: payload.email.trim().toLowerCase(),
    ...(typeof payload.sub === 'string' && payload.sub.trim() ? { sub: payload.sub.trim() } : {}),
  };
}

export function prepareContainerRequest(
  request: Request,
  identity: VerifiedAccessIdentity | null,
): Request {
  const headers = new Headers(request.headers);
  // Never trust client-supplied identity headers; only inject after JWT verification.
  headers.delete('x-auth-user-email');
  if (identity) {
    headers.set('x-auth-user-email', identity.email);
  }
  return new Request(request, { headers });
}
