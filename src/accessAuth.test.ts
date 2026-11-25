import assert from 'node:assert/strict';
import test from 'node:test';

import {
  clearJwksCacheForTests,
  prepareContainerRequest,
  verifyCloudflareAccessJwt,
} from './accessAuth.ts';

function toBase64Url(input: Uint8Array | string): string {
  const bytes = typeof input === 'string' ? new TextEncoder().encode(input) : input;
  return Buffer.from(bytes)
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
}

async function createSignedJwt(
  payload: Record<string, unknown>,
  kid = 'test-key-1',
): Promise<{ token: string; jwk: JsonWebKey }> {
  const keyPair = await crypto.subtle.generateKey(
    {
      name: 'RSASSA-PKCS1-v1_5',
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: 'SHA-256',
    },
    true,
    ['sign', 'verify'],
  );

  const publicJwk = (await crypto.subtle.exportKey('jwk', keyPair.publicKey)) as JsonWebKey;
  publicJwk.kid = kid;
  publicJwk.alg = 'RS256';
  publicJwk.use = 'sig';

  const headerB64 = toBase64Url(JSON.stringify({ alg: 'RS256', typ: 'JWT', kid }));
  const payloadB64 = toBase64Url(JSON.stringify(payload));
  const signingInput = new TextEncoder().encode(`${headerB64}.${payloadB64}`);
  const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', keyPair.privateKey, signingInput);
  const sigB64 = toBase64Url(new Uint8Array(sig));

  return { token: `${headerB64}.${payloadB64}.${sigB64}`, jwk: publicJwk };
}

test('verifyCloudflareAccessJwt accepts valid RS256 token and caches JWKS', async () => {
  clearJwksCacheForTests();
  const now = Math.floor(Date.now() / 1000);
  const { token, jwk } = await createSignedJwt({
    email: 'Alice@Example.com',
    sub: 'user-123',
    aud: ['aud-tag-1'],
    exp: now + 300,
  });

  let fetchCalls = 0;
  const mockFetch: typeof fetch = async () => {
    fetchCalls += 1;
    return new Response(JSON.stringify({ keys: [jwk] }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };

  const identity = await verifyCloudflareAccessJwt(token, 'myteam.cloudflareaccess.com', 'aud-tag-1', mockFetch);
  assert.deepEqual(identity, { email: 'alice@example.com', sub: 'user-123' });

  const identity2 = await verifyCloudflareAccessJwt(token, 'https://myteam.cloudflareaccess.com/', 'aud-tag-1', mockFetch);
  assert.equal(identity2?.email, 'alice@example.com');
  assert.equal(fetchCalls, 1);
});

test('verifyCloudflareAccessJwt rejects expired, wrong-aud, or tampered tokens', async () => {
  clearJwksCacheForTests();
  const now = Math.floor(Date.now() / 1000);

  const expired = await createSignedJwt({ email: 'a@b.com', aud: ['aud-1'], exp: now - 10 }, 'k-exp');
  const wrongAud = await createSignedJwt({ email: 'a@b.com', aud: ['other'], exp: now + 300 }, 'k-aud');
  const wrongIss = await createSignedJwt(
    { email: 'a@b.com', iss: 'https://evil.cloudflareaccess.com', aud: ['aud-1'], exp: now + 300 },
    'k-iss',
  );
  const valid = await createSignedJwt(
    { email: 'a@b.com', iss: 'https://team.cloudflareaccess.com', aud: ['aud-1'], exp: now + 300 },
    'k-valid',
  );

  let fetches = 0;
  const mockFetch: typeof fetch = async () => {
    fetches += 1;
    return new Response(JSON.stringify({ keys: [expired.jwk, wrongAud.jwk, wrongIss.jwk, valid.jwk] }), {
      status: 200,
    });
  };

  assert.equal(
    await verifyCloudflareAccessJwt(expired.token, 'team.cloudflareaccess.com', 'aud-1', mockFetch),
    null,
  );
  assert.equal(
    await verifyCloudflareAccessJwt(wrongAud.token, 'team.cloudflareaccess.com', 'aud-1', mockFetch),
    null,
  );
  assert.equal(
    await verifyCloudflareAccessJwt(wrongIss.token, 'team.cloudflareaccess.com', 'aud-1', mockFetch),
    null,
  );
  assert.equal(
    await verifyCloudflareAccessJwt(valid.token, 'team.cloudflareaccess.com', '', mockFetch),
    null,
  );
  // Unknown kid within cooldown does not trigger repeated JWKS fetches
  const unknownKid = await createSignedJwt({ email: 'a@b.com', aud: ['aud-1'], exp: now + 300 }, 'unknown-kid');
  assert.equal(
    await verifyCloudflareAccessJwt(unknownKid.token, 'team.cloudflareaccess.com', 'aud-1', mockFetch),
    null,
  );
  assert.equal(fetches, 1);

  const parts = valid.token.split('.');
  const tamperedPayload = toBase64Url(JSON.stringify({ email: 'evil@b.com', aud: ['aud-1'], exp: now + 300 }));
  const tamperedToken = `${parts[0]}.${tamperedPayload}.${parts[2]}`;
  assert.equal(
    await verifyCloudflareAccessJwt(tamperedToken, 'team.cloudflareaccess.com', 'aud-1', mockFetch),
    null,
  );
});

test('prepareContainerRequest strips spoofed X-Auth-User-Email and injects verified email', () => {
  const incoming = new Request('https://example.com/api/documents', {
    method: 'POST',
    headers: {
      'x-auth-user-email': 'spoofed@evil.com',
      'content-type': 'application/json',
    },
    body: '{}',
  });

  const unverified = prepareContainerRequest(incoming.clone(), null);
  assert.equal(unverified.headers.get('x-auth-user-email'), null);

  const verified = prepareContainerRequest(incoming.clone(), { email: 'real@company.com', sub: 'u1' });
  assert.equal(verified.headers.get('x-auth-user-email'), 'real@company.com');
});
