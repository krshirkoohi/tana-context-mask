import worker from './index';
import type { Env } from './types';

type SecurityEnv = Env & {
  ALLOWED_ORIGINS?: string;
};

const BLOCKED_OAUTH_PATHS = new Set([
  '/oauth/authorize',
  '/oauth/token',
  '/oauth/userinfo',
  '/.well-known/oauth-authorization-server',
  '/.well-known/openid-configuration',
  '/.well-known/oauth-authorization-server/mc',
  '/.well-known/oauth-authorization-server/mcp',
  '/.well-known/oauth-authorization-server/sse'
]);

function getPresentedKey(request: Request): string | null {
  const apiKeyHeader = request.headers.get('x-api-key');
  if (apiKeyHeader) return apiKeyHeader;

  const authorization = request.headers.get('authorization');
  if (!authorization) return null;

  const match = authorization.match(/^Bearer\s+(.+)$/i);
  return match?.[1] || null;
}

function getAllowedOrigins(env: SecurityEnv): Set<string> {
  return new Set(
    (env.ALLOWED_ORIGINS || '')
      .split(',')
      .map((origin) => origin.trim())
      .filter(Boolean)
  );
}

function restrictedCorsHeaders(origin: string | null, env: SecurityEnv): Headers {
  const headers = new Headers();
  const allowedOrigins = getAllowedOrigins(env);

  if (origin && allowedOrigins.has(origin)) {
    headers.set('Access-Control-Allow-Origin', origin);
    headers.set('Access-Control-Allow-Headers', 'authorization, content-type, x-api-key');
    headers.set('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    headers.set('Access-Control-Max-Age', '600');
    headers.set('Vary', 'Origin');
  }

  return headers;
}

function stripAndApplyCors(response: Response, request: Request, env: SecurityEnv): Response {
  const headers = new Headers(response.headers);
  headers.delete('Access-Control-Allow-Origin');
  headers.delete('Access-Control-Allow-Headers');
  headers.delete('Access-Control-Allow-Methods');
  headers.delete('Access-Control-Max-Age');

  restrictedCorsHeaders(request.headers.get('origin'), env)
    .forEach((value, key) => headers.set(key, value));

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers
  });
}

async function secureFetch(request: Request, env: SecurityEnv, ctx: ExecutionContext): Promise<Response> {
  const url = new URL(request.url);

  if (request.method === 'OPTIONS') {
    const origin = request.headers.get('origin');
    if (!origin || !getAllowedOrigins(env).has(origin)) {
      return new Response(null, { status: 403 });
    }

    return new Response(null, {
      status: 204,
      headers: restrictedCorsHeaders(origin, env)
    });
  }

  // The previous OAuth endpoints auto-approved callers and issued tokens that
  // were never validated. Disable them until a real OAuth implementation exists.
  if (BLOCKED_OAUTH_PATHS.has(url.pathname)) {
    return Response.json(
      { error: 'OAuth disabled. Authenticate with an API key.' },
      { status: 410 }
    );
  }

  // Fail closed. A missing secret must never make the Worker public.
  if (!env.API_KEY) {
    return Response.json(
      { error: 'Service unavailable: API_KEY is not configured.' },
      { status: 503 }
    );
  }

  if (getPresentedKey(request) !== env.API_KEY) {
    return Response.json(
      { error: 'Unauthorized' },
      { status: 401, headers: { 'WWW-Authenticate': 'Bearer' } }
    );
  }

  const response = await worker.fetch(request, env, ctx);
  return stripAndApplyCors(response, request, env);
}

export default {
  fetch: secureFetch,
  scheduled(event: ScheduledEvent, env: SecurityEnv, ctx: ExecutionContext) {
    return worker.scheduled(event, env, ctx);
  }
};
