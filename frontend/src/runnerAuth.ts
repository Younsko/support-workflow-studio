type LocalAuthConfig = {
  enabled: boolean
  workspace_id?: string
  runner_id?: string
  control_plane_api_url?: string
}

type OidcConfig = {
  configured: boolean
  issuer: string | null
  client_id: string | null
  audience: string | null
}

type OidcMetadata = {
  issuer: string
  authorization_endpoint: string
  token_endpoint: string
  jwks_uri: string
}

type Identity = { email: string; workspaces: Array<{ id: string; role: string }> }

export type RunnerAuthSnapshot = {
  enabled: boolean
  authenticated: boolean
  email?: string
  role?: string
  error?: string
}

const ACCESS_TOKEN_KEY = 'sws-support-oidc-access-token'
const ACCESS_EXPIRY_KEY = 'sws-support-oidc-access-expires-at'
const LOCALHOSTS = new Set(['localhost', '127.0.0.1', '::1'])

let localConfig: LocalAuthConfig | null = null
let oidcConfig: OidcConfig | null = null
let sessionToken: string | null = null
let sessionExpiresAt = 0
let sessionPromise: Promise<string> | null = null
let initialization: Promise<RunnerAuthSnapshot> | null = null
let authenticatedIdentity: { email: string; role: string } | null = null

function safeEndpoint(raw: string): string {
  const url = new URL(raw)
  if (url.username || url.password || url.hash) throw new Error('The identity provider endpoint is invalid.')
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && LOCALHOSTS.has(url.hostname))) {
    throw new Error('The identity provider must use HTTPS.')
  }
  return url.toString()
}

function discoveryUrl(issuer: string): string {
  const url = new URL(issuer)
  if (url.username || url.password || url.search || url.hash) throw new Error('The OIDC issuer URL is invalid.')
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && LOCALHOSTS.has(url.hostname))) {
    throw new Error('The identity provider must use HTTPS.')
  }
  url.pathname = `${url.pathname.replace(/\/+$/, '')}/.well-known/openid-configuration`
  return url.toString()
}

function randomValue(bytes = 32): string {
  const value = crypto.getRandomValues(new Uint8Array(bytes))
  return btoa(String.fromCharCode(...value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
}

function decodeSegment<T>(segment: string): T {
  const normalized = segment.replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '='))
  const bytes = Uint8Array.from(raw, (char) => char.charCodeAt(0))
  return JSON.parse(new TextDecoder().decode(bytes)) as T
}

async function loadOidcMetadata(issuer: string): Promise<OidcMetadata> {
  const response = await fetch(discoveryUrl(issuer), { cache: 'no-store' })
  if (!response.ok) throw new Error('The identity provider configuration could not be loaded.')
  const metadata = await response.json() as OidcMetadata
  if (metadata.issuer !== issuer) throw new Error('The identity provider issuer did not match its configuration.')
  safeEndpoint(metadata.authorization_endpoint)
  safeEndpoint(metadata.token_endpoint)
  safeEndpoint(metadata.jwks_uri)
  return metadata
}

async function verifyIdToken(token: string, metadata: OidcMetadata, clientId: string, nonce: string): Promise<{ exp: number }> {
  const [encodedHeader, encodedClaims, encodedSignature] = token.split('.')
  if (!encodedHeader || !encodedClaims || !encodedSignature) throw new Error('The identity provider returned an invalid ID token.')
  const header = decodeSegment<{ alg?: string; kid?: string }>(encodedHeader)
  const claims = decodeSegment<{ iss?: string; aud?: string | string[]; azp?: string; exp?: number; iat?: number; nbf?: number; sub?: string; nonce?: string }>(encodedClaims)
  if (header.alg !== 'RS256' || !header.kid) throw new Error('The identity provider returned an unsupported ID token.')
  if (claims.iss !== metadata.issuer || typeof claims.sub !== 'string' || !claims.sub || claims.sub.length > 255) throw new Error('The ID token issuer or subject is invalid.')
  const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud]
  if (!audiences.includes(clientId) || (audiences.length > 1 && claims.azp !== clientId)) throw new Error('The ID token audience is invalid.')
  const now = Math.floor(Date.now() / 1000)
  if (typeof claims.exp !== 'number' || claims.exp <= now - 60 || typeof claims.iat !== 'number' || claims.iat > now + 60 || (typeof claims.nbf === 'number' && claims.nbf > now + 60)) {
    throw new Error('The ID token is expired or not yet valid.')
  }
  if (claims.nonce !== nonce) throw new Error('The OIDC nonce did not match.')
  const response = await fetch(metadata.jwks_uri, { cache: 'no-store' })
  if (!response.ok) throw new Error('The identity provider signing keys could not be loaded.')
  const jwks = await response.json() as { keys?: Array<JsonWebKey & { kid?: string; alg?: string; use?: string }> }
  const key = jwks.keys?.find((candidate) => candidate.kid === header.kid && candidate.kty === 'RSA' && (!candidate.alg || candidate.alg === 'RS256') && (!candidate.use || candidate.use === 'sig'))
  if (!key) throw new Error('The identity provider signing key was not found.')
  const cryptoKey = await crypto.subtle.importKey('jwk', key, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify'])
  const normalized = encodedSignature.replace(/-/g, '+').replace(/_/g, '/')
  const signature = Uint8Array.from(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')), (char) => char.charCodeAt(0))
  const valid = await crypto.subtle.verify(
    { name: 'RSASSA-PKCS1-v1_5' }, cryptoKey, signature,
    new TextEncoder().encode(`${encodedHeader}.${encodedClaims}`),
  )
  if (!valid) throw new Error('The identity token signature is invalid.')
  return { exp: claims.exp }
}

async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return btoa(String.fromCharCode(...new Uint8Array(digest))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
}

function cpUrl(path: string): string {
  if (!localConfig?.control_plane_api_url) throw new Error('The workspace control plane is not configured.')
  return `${localConfig.control_plane_api_url}${path}`
}

async function controlPlaneRequest<T>(
  path: string,
  accessToken: string,
  method: 'GET' | 'POST' = 'GET',
): Promise<T> {
  const response = await fetch(cpUrl(path), {
    method,
    cache: 'no-store',
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  if (!response.ok) {
    if (response.status === 401) throw new Error('Your organization sign-in expired. Sign in again to reconnect the support runner.')
    if (response.status === 403) throw new Error('Your account does not have access to this workspace or runner.')
    throw new Error(`Workspace authorization failed (${response.status}).`)
  }
  return response.json() as Promise<T>
}

async function exchangeRunnerSession(accessToken: string): Promise<string> {
  if (!localConfig?.workspace_id || !localConfig.runner_id) throw new Error('The workspace runner identity is not configured.')
  if (!oidcConfig?.issuer || !oidcConfig.client_id) throw new Error('The organization identity provider is not configured.')
  const identity = await controlPlaneRequest<Identity>('/api/v1/me', accessToken)
  const membership = identity.workspaces.find((workspace) => workspace.id === localConfig?.workspace_id)
  if (!membership) throw new Error('Your account is not a member of this support workspace.')
  const path = `/api/v1/workspaces/${encodeURIComponent(localConfig.workspace_id)}/runners/${encodeURIComponent(localConfig.runner_id)}/session`
  const session = await controlPlaneRequest<{ access_token: string; expires_at: number }>(path, accessToken, 'POST')
  if (!session.access_token || typeof session.expires_at !== 'number') throw new Error('The workspace control plane returned an invalid runner session.')
  sessionToken = session.access_token
  sessionExpiresAt = session.expires_at * 1000
  authenticatedIdentity = { email: identity.email, role: membership.role }
  return session.access_token
}

async function ensureRunnerSession(): Promise<string | null> {
  if (!localConfig?.enabled) return null
  if (sessionToken && sessionExpiresAt > Date.now() + 30_000) return sessionToken
  if (sessionPromise) return sessionPromise
  const accessToken = sessionStorage.getItem(ACCESS_TOKEN_KEY)
  const accessExpiry = Number(sessionStorage.getItem(ACCESS_EXPIRY_KEY) || 0)
  if (!accessToken || accessExpiry < Date.now() + 30_000) {
    signOutRunnerAuth()
    throw new Error('Sign in with your organization to connect this workspace runner.')
  }
  sessionPromise = exchangeRunnerSession(accessToken)
    .finally(() => { sessionPromise = null })
  return sessionPromise
}

async function finishAuthorizationCallback(): Promise<void> {
  if (!oidcConfig?.issuer || !oidcConfig.client_id) throw new Error('The organization identity provider is not configured.')
  const params = new URLSearchParams(window.location.search)
  const code = params.get('code')
  const returnedState = params.get('state')
  const providerError = params.get('error')
  if (!code && !providerError) return
  history.replaceState({}, document.title, `${window.location.pathname}${window.location.hash}`)
  if (providerError) {
    sessionStorage.removeItem('sws-support-oidc-state')
    sessionStorage.removeItem('sws-support-oidc-verifier')
    sessionStorage.removeItem('sws-support-oidc-nonce')
    throw new Error(params.get('error_description') || 'Organization sign-in was cancelled.')
  }
  const storedState = sessionStorage.getItem('sws-support-oidc-state')
  const verifier = sessionStorage.getItem('sws-support-oidc-verifier')
  const nonce = sessionStorage.getItem('sws-support-oidc-nonce')
  sessionStorage.removeItem('sws-support-oidc-state')
  sessionStorage.removeItem('sws-support-oidc-verifier')
  sessionStorage.removeItem('sws-support-oidc-nonce')
  if (!code || !storedState || storedState !== returnedState || !verifier || !nonce) throw new Error('The authentication response does not match this browser session.')
  const metadata = await loadOidcMetadata(oidcConfig.issuer)
  const formValues: Record<string, string> = {
    grant_type: 'authorization_code',
    code,
    client_id: oidcConfig.client_id,
    redirect_uri: `${window.location.origin}${window.location.pathname}`,
    code_verifier: verifier,
  }
  if (oidcConfig.audience) { formValues.audience = oidcConfig.audience; formValues.resource = oidcConfig.audience }
  const response = await fetch(safeEndpoint(metadata.token_endpoint), {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(formValues), cache: 'no-store',
  })
  if (!response.ok) throw new Error('The identity provider rejected organization sign-in.')
  const tokens = await response.json() as { access_token?: string; id_token?: string; expires_in?: number }
  if (!tokens.access_token || !tokens.id_token) throw new Error('The identity provider returned an incomplete sign-in response.')
  const idClaims = await verifyIdToken(tokens.id_token, metadata, oidcConfig.client_id, nonce)
  const lifetime = typeof tokens.expires_in === 'number' && tokens.expires_in > 0
    ? Math.min(tokens.expires_in, idClaims.exp - Math.floor(Date.now() / 1000))
    : Math.min(90, idClaims.exp - Math.floor(Date.now() / 1000))
  if (lifetime <= 30) throw new Error('The organization sign-in is too close to expiry. Sign in again.')
  sessionStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token)
  sessionStorage.setItem(ACCESS_EXPIRY_KEY, String(Date.now() + lifetime * 1000))
}

export function bootstrapRunnerAuth(): Promise<RunnerAuthSnapshot> {
  if (initialization) return initialization
  initialization = (async () => {
    try {
      const response = await fetch('/api/auth/config', { cache: 'no-store' })
      if (!response.ok) throw new Error('Support runner authentication configuration could not be loaded.')
      localConfig = await response.json() as LocalAuthConfig
      if (!localConfig.enabled) return { enabled: false, authenticated: true }
      if (!localConfig.control_plane_api_url || !localConfig.workspace_id || !localConfig.runner_id) {
        throw new Error('The support runner workspace configuration is incomplete.')
      }
      const authResponse = await fetch(cpUrl('/api/v1/auth/config'), { cache: 'no-store' })
      if (!authResponse.ok) throw new Error('The workspace identity configuration could not be loaded.')
      oidcConfig = await authResponse.json() as OidcConfig
      if (!oidcConfig.configured || !oidcConfig.issuer || !oidcConfig.client_id) {
        throw new Error('The organization identity provider is not configured.')
      }
      await finishAuthorizationCallback()
      const accessToken = sessionStorage.getItem(ACCESS_TOKEN_KEY)
      const expiry = Number(sessionStorage.getItem(ACCESS_EXPIRY_KEY) || 0)
      if (!accessToken || expiry <= Date.now() + 30_000) {
        signOutRunnerAuth()
        return { enabled: true, authenticated: false }
      }
      await exchangeRunnerSession(accessToken)
      return {
        enabled: true, authenticated: true,
        email: authenticatedIdentity?.email, role: authenticatedIdentity?.role,
      }
    } catch (error) {
      sessionToken = null
      sessionExpiresAt = 0
      return {
        enabled: Boolean(localConfig?.enabled),
        authenticated: false,
        error: error instanceof Error ? error.message : 'Support runner sign-in could not be completed.',
      }
    }
  })()
  return initialization
}

export async function signInToRunner(): Promise<void> {
  if (!localConfig?.enabled) return
  if (!oidcConfig?.configured || !oidcConfig.issuer || !oidcConfig.client_id) throw new Error('The organization identity provider is not configured.')
  if (!crypto.subtle) throw new Error('Web Crypto is unavailable. Open this page over HTTPS.')
  const metadata = await loadOidcMetadata(oidcConfig.issuer)
  const verifier = randomValue(48)
  const state = randomValue()
  const nonce = randomValue()
  sessionStorage.setItem('sws-support-oidc-state', state)
  sessionStorage.setItem('sws-support-oidc-verifier', verifier)
  sessionStorage.setItem('sws-support-oidc-nonce', nonce)
  const params: Record<string, string> = {
    response_type: 'code', client_id: oidcConfig.client_id,
    redirect_uri: `${window.location.origin}${window.location.pathname}`,
    scope: 'openid email profile', state, nonce,
    code_challenge: await challengeFor(verifier), code_challenge_method: 'S256',
  }
  if (oidcConfig.audience) { params.audience = oidcConfig.audience; params.resource = oidcConfig.audience }
  const url = new URL(safeEndpoint(metadata.authorization_endpoint))
  url.search = new URLSearchParams(params).toString()
  window.location.assign(url.toString())
}

export function signOutRunnerAuth(): void {
  sessionStorage.removeItem(ACCESS_TOKEN_KEY)
  sessionStorage.removeItem(ACCESS_EXPIRY_KEY)
  sessionStorage.removeItem('sws-support-oidc-state')
  sessionStorage.removeItem('sws-support-oidc-verifier')
  sessionStorage.removeItem('sws-support-oidc-nonce')
  sessionToken = null
  sessionExpiresAt = 0
  sessionPromise = null
  authenticatedIdentity = null
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const inputUrl = input instanceof Request ? input.url : input.toString()
  const target = new URL(inputUrl, window.location.href)
  const headers = new Headers(init.headers)
  if (target.origin === window.location.origin && target.pathname.startsWith('/api/')) {
    const token = await ensureRunnerSession()
    if (token) headers.set('Authorization', `Bearer ${token}`)
  }
  return fetch(input, { ...init, headers })
}
