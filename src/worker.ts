import { Container } from '@cloudflare/containers';
import {
  prepareContainerRequest,
  VerifiedAccessIdentity,
  verifyCloudflareAccessJwt,
} from './accessAuth';

export interface Env {
  ASSETS: Fetcher;
  PAGES_BUCKET: R2Bucket;
  COMPLIANCE_BACKEND: DurableObjectNamespace<ComplianceBackend>;
  WRITE_ACCESS_TOKEN?: string;
  REQUIRE_CF_ACCESS?: string;
  CF_ACCESS_TEAM_DOMAIN?: string;
  CF_ACCESS_AUD?: string;
  GEMINI_API_KEY?: string;
  DATABASE_URL?: string;
  POSTGRES_HOST?: string;
  POSTGRES_PORT?: string;
  POSTGRES_USER?: string;
  POSTGRES_PASSWORD?: string;
  POSTGRES_DB?: string;
  POSTGRES_SSL?: string;
  R2_ENDPOINT_URL?: string;
  R2_BUCKET?: string;
  R2_ACCESS_KEY_ID?: string;
  R2_SECRET_ACCESS_KEY?: string;
}

const PAGE_IMAGE_RE = /^\/api\/documents\/([A-Za-z0-9_-]{1,64})\/pages\/([1-9]\d*)\/image$/;
const SAFE_READ_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export class ComplianceBackend extends Container<Env> {
  override defaultPort = 8000;
  override sleepAfter = '10m';

  constructor(ctx: DurableObjectState<{}>, env: Env) {
    super(ctx, env);
    this.envVars = {
      BACKEND_HOST: '0.0.0.0',
      BACKEND_PORT: '8000',
      GEMINI_API_KEY: env.GEMINI_API_KEY ?? '',
      DATABASE_URL: env.DATABASE_URL ?? '',
      POSTGRES_HOST: env.POSTGRES_HOST ?? '',
      POSTGRES_PORT: env.POSTGRES_PORT ?? '5432',
      POSTGRES_USER: env.POSTGRES_USER ?? '',
      POSTGRES_PASSWORD: env.POSTGRES_PASSWORD ?? '',
      POSTGRES_DB: env.POSTGRES_DB ?? '',
      POSTGRES_SSL: env.POSTGRES_SSL ?? 'true',
      R2_ENDPOINT_URL: env.R2_ENDPOINT_URL ?? '',
      R2_BUCKET: env.R2_BUCKET ?? '',
      R2_ACCESS_KEY_ID: env.R2_ACCESS_KEY_ID ?? '',
      R2_SECRET_ACCESS_KEY: env.R2_SECRET_ACCESS_KEY ?? '',
    };
  }
}

async function constantTimeEqual(a: string, b: string): Promise<boolean> {
  const enc = new TextEncoder();
  const [ha, hb] = await Promise.all([
    crypto.subtle.digest('SHA-256', enc.encode(a)),
    crypto.subtle.digest('SHA-256', enc.encode(b)),
  ]);
  const va = new Uint8Array(ha);
  const vb = new Uint8Array(hb);
  let diff = 0;
  for (let i = 0; i < va.length; i++) {
    diff |= va[i] ^ vb[i];
  }
  return diff === 0;
}

async function resolveVerifiedIdentity(
  request: Request,
  env: Env,
): Promise<VerifiedAccessIdentity | null> {
  if (env.REQUIRE_CF_ACCESS !== 'true') {
    return null;
  }
  const jwt = request.headers.get('cf-access-jwt-assertion')?.trim();
  const teamDomain = env.CF_ACCESS_TEAM_DOMAIN?.trim();
  if (!jwt || !teamDomain) {
    return null;
  }
  return verifyCloudflareAccessJwt(jwt, teamDomain, env.CF_ACCESS_AUD);
}

async function isAuthorizedWrite(
  request: Request,
  env: Env,
  verifiedIdentity: VerifiedAccessIdentity | null,
): Promise<boolean> {
  const expected = env.WRITE_ACCESS_TOKEN?.trim();
  if (expected) {
    const headerToken = request.headers.get('x-write-token')?.trim() ?? '';
    const bearer = request.headers.get('authorization')?.replace(/^Bearer\s+/i, '').trim() ?? '';
    if (headerToken && (await constantTimeEqual(headerToken, expected))) {
      return true;
    }
    if (bearer && (await constantTimeEqual(bearer, expected))) {
      return true;
    }
    return false;
  }
  if (env.REQUIRE_CF_ACCESS === 'true') {
    return verifiedIdentity !== null;
  }
  return true;
}

function withSecurityHeaders(response: Response): Response {
  const headers = new Headers(response.headers);
  headers.set('X-Content-Type-Options', 'nosniff');
  headers.set('X-Frame-Options', 'DENY');
  headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const method = request.method.toUpperCase();

    if (url.pathname.startsWith('/api/')) {
      const verifiedIdentity = await resolveVerifiedIdentity(request, env);
      if (!SAFE_READ_METHODS.has(method) && !(await isAuthorizedWrite(request, env, verifiedIdentity))) {
        return withSecurityHeaders(
          Response.json(
            { detail: 'Write access requires authentication (X-Write-Token or Zero Trust Access).' },
            { status: 401 },
          ),
        );
      }

      if (method === 'GET') {
        const match = PAGE_IMAGE_RE.exec(url.pathname);
        if (match) {
          const [, docId, page] = match;
          const obj = await env.PAGES_BUCKET.get(`pages/${docId}/page_${page}.webp`);
          if (obj) {
            return withSecurityHeaders(
              new Response(obj.body, {
                headers: {
                  'content-type': 'image/webp',
                  'cache-control': 'public, max-age=86400, immutable',
                },
              }),
            );
          }
        }
      }

      const container = env.COMPLIANCE_BACKEND.getByName('primary');
      const containerRequest = prepareContainerRequest(request, verifiedIdentity);
      return withSecurityHeaders(await container.fetch(containerRequest));
    }

    return withSecurityHeaders(await env.ASSETS.fetch(request));
  },
};
