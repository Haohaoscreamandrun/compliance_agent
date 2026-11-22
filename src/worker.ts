import { Container } from '@cloudflare/containers';

export interface Env {
  ASSETS: Fetcher;
  PAGES_BUCKET: R2Bucket;
  COMPLIANCE_BACKEND: DurableObjectNamespace<ComplianceBackend>;
  WRITE_ACCESS_TOKEN?: string;
  GEMINI_API_KEY?: string;
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

const PAGE_IMAGE_RE = /^\/api\/documents\/([A-Za-z0-9_-]{1,64})\/pages\/(\d+)\/image$/;

export class ComplianceBackend extends Container<Env> {
  override defaultPort = 8000;
  override sleepAfter = '10m';

  constructor(ctx: DurableObjectState<{}>, env: Env) {
    super(ctx, env);
    this.envVars = {
      BACKEND_HOST: '0.0.0.0',
      BACKEND_PORT: '8000',
      GEMINI_API_KEY: env.GEMINI_API_KEY ?? '',
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

function isAuthorizedWrite(request: Request, env: Env): boolean {
  const expected = env.WRITE_ACCESS_TOKEN?.trim();
  if (!expected) {
    // When unset, rely on Cloudflare Zero Trust Access policy on the custom domain
    return true;
  }
  const headerToken = request.headers.get('x-write-token')?.trim();
  const bearer = request.headers.get('authorization')?.replace(/^Bearer\s+/i, '').trim();
  return headerToken === expected || bearer === expected;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname.startsWith('/api/')) {
      if ((request.method === 'POST' || request.method === 'PATCH') && !isAuthorizedWrite(request, env)) {
        return Response.json(
          { detail: 'Write access requires authentication (X-Write-Token or Zero Trust Access).' },
          { status: 401 },
        );
      }

      if (request.method === 'GET') {
        const match = PAGE_IMAGE_RE.exec(url.pathname);
        if (match) {
          const [, docId, page] = match;
          const obj = await env.PAGES_BUCKET.get(`pages/${docId}/page_${page}.webp`);
          if (obj) {
            return new Response(obj.body, {
              headers: {
                'content-type': 'image/webp',
                'cache-control': 'public, max-age=86400, immutable',
              },
            });
          }
        }
      }

      const container = env.COMPLIANCE_BACKEND.getByName('primary');
      return container.fetch(request);
    }

    return env.ASSETS.fetch(request);
  },
};
