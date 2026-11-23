import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, ArrowRight, Check, ChevronDown, Copy, KeyRound, LogOut,
  MailPlus, RefreshCw, ShieldCheck, UserRoundPlus, Users, X,
} from 'lucide-react'
import './control-plane.css'

type Locale = 'en' | 'fr'
type Role = 'Owner' | 'Admin' | 'Builder' | 'Operator' | 'Approver' | 'Viewer'
type Workspace = { id: string; name: string; slug: string; role: Role; created_at: string }
type Member = { subject: string; email: string; role: Role; created_at: string }
type Invitation = { id: string; email: string; role: Role; expires_at: string; accepted_at: string | null; revoked_at: string | null }
type Runner = { id: string; name: string; created_by: string; created_at: string; expires_at: string; last_heartbeat_at: string | null; revoked_at: string | null }
type AuditEvent = { id: string; actor_subject: string; event_type: string; target_type: string; target_id: string; occurred_at: string }
type Identity = { subject: string; email: string; workspaces: Workspace[] }
type AuthConfig = { configured: boolean; issuer: string | null; client_id: string | null; audience: string | null }
type OneTimeCredential = { title: string; id: string; token: string; expiresAt: string; kind: 'invitation' | 'runner' }
type Section = 'members' | 'invitations' | 'runners' | 'audit'

const SESSION_KEY = 'sws-control-plane-access-token'
const LOCALHOSTS = new Set(['localhost', '127.0.0.1', '[::1]'])

const copy = {
  en: {
    brand: 'Support Workflow Studio', title: 'Workspace access', subtitle: 'Manage your team and runner registrations.',
    boundary: 'Workspace administration only', boundaryCopy: 'This console manages workspace metadata. It does not sign users into, or secure, the support ticket application.',
    signIn: 'Sign in', signInTitle: 'Your team workspace', signInCopy: 'Continue with your organization’s identity provider. This page uses OIDC Authorization Code with PKCE and has no client secret.',
    loading: 'Loading workspace…', loadFailed: 'Could not load the control-plane configuration.', notConfigured: 'The identity provider is not configured for this control plane.', retry: 'Try again', recent: 'Recent heartbeat',
    email: 'Signed in as', signOut: 'Sign out', workspace: 'Workspace', createWorkspace: 'Create workspace', workspaceName: 'Workspace name', workspacePlaceholder: 'e.g. Support Operations', create: 'Create', cancel: 'Cancel',
    members: 'Members', invitations: 'Invitations', runners: 'Runners', audit: 'Audit log', role: 'Role', member: 'Member', joined: 'Joined', actions: 'Actions',
    inviteMember: 'Invite a member', inviteEmail: 'Work email', inviteRole: 'Workspace role', sendInvite: 'Create invitation', noMembers: 'No members yet.', noInvites: 'No invitations to show.', noRunners: 'No runners registered.', noAudit: 'No audit events yet.',
    pending: 'Pending', accepted: 'Accepted', revoked: 'Revoked', expired: 'Expired', expires: 'Expires', created: 'Created', heartbeat: 'Last heartbeat', never: 'Never', revoke: 'Revoke', remove: 'Remove', changeRole: 'Change role',
    registerRunner: 'Register a runner', runnerName: 'Runner name', runnerPlaceholder: 'e.g. EU support runner', register: 'Create registration', status: 'Status', active: 'Active', offline: 'No heartbeat yet',
    credentialTitle: 'Copy this credential now', credentialCopy: 'It is shown once and stored only as a hash. Deliver it through your approved secure channel. Do not paste it into chat or a ticket.', invitationId: 'Invitation ID', runnerId: 'Runner ID', token: 'One-time token', copyToken: 'Copy token', copied: 'Copied', close: 'Close',
    redeem: 'Accept an invitation', redeemId: 'Invitation ID', redeemToken: 'Invitation token', acceptInvite: 'Accept invitation',
    noticeLogin: 'Authentication is handled by your configured OIDC provider. The identity provider and this service must agree on issuer, audience, and verified email claims.',
    noAccess: 'You do not have access to this workspace.', noPermission: 'Your role cannot perform this action.', requestFailed: 'The request could not be completed.',
    confirmRemove: 'Remove this member from the workspace?', confirmRevokeRunner: 'Revoke this runner credential? Its heartbeat will stop being accepted immediately.', confirmRevokeInvite: 'Revoke this invitation?',
    locale: 'Language', oneTime: 'Shown once', activeWorkspace: 'Active workspace', emptyWorkspace: 'Create a workspace to start inviting your team.', ownerAdmin: 'Only Owners and Admins can manage membership and runners.', runnerExpiry: 'Runner credentials expire within 90 days.', auditHelp: 'Workspace-level administrative changes. Ticket content is not recorded here.',
  },
  fr: {
    brand: 'Support Workflow Studio', title: 'Accès à l’espace', subtitle: 'Gérez votre équipe et les enregistrements de runners.',
    boundary: 'Administration des espaces uniquement', boundaryCopy: 'Cette console gère les métadonnées des espaces. Elle ne connecte pas les utilisateurs à l’application de tickets et ne protège pas ses routes.',
    signIn: 'Se connecter', signInTitle: 'Votre espace d’équipe', signInCopy: 'Continuez avec le fournisseur d’identité de votre organisation. Cette page utilise OIDC Authorization Code avec PKCE, sans secret client.',
    loading: 'Chargement de l’espace…', loadFailed: 'Impossible de charger la configuration du control plane.', notConfigured: 'Le fournisseur d’identité n’est pas configuré pour ce control plane.', retry: 'Réessayer', recent: 'Heartbeat récent',
    email: 'Connecté en tant que', signOut: 'Se déconnecter', workspace: 'Espace', createWorkspace: 'Créer un espace', workspaceName: 'Nom de l’espace', workspacePlaceholder: 'ex. Opérations support', create: 'Créer', cancel: 'Annuler',
    members: 'Membres', invitations: 'Invitations', runners: 'Runners', audit: 'Journal d’audit', role: 'Rôle', member: 'Membre', joined: 'Inscription', actions: 'Actions',
    inviteMember: 'Inviter un membre', inviteEmail: 'Adresse professionnelle', inviteRole: 'Rôle dans l’espace', sendInvite: 'Créer l’invitation', noMembers: 'Aucun membre pour le moment.', noInvites: 'Aucune invitation à afficher.', noRunners: 'Aucun runner enregistré.', noAudit: 'Aucun événement d’audit.',
    pending: 'En attente', accepted: 'Acceptée', revoked: 'Révoquée', expired: 'Expirée', expires: 'Expire le', created: 'Création', heartbeat: 'Dernier heartbeat', never: 'Jamais', revoke: 'Révoquer', remove: 'Retirer', changeRole: 'Modifier le rôle',
    registerRunner: 'Enregistrer un runner', runnerName: 'Nom du runner', runnerPlaceholder: 'ex. Runner support EU', register: 'Créer l’enregistrement', status: 'État', active: 'Actif', offline: 'Aucun heartbeat',
    credentialTitle: 'Copiez cet identifiant maintenant', credentialCopy: 'Il ne sera affiché qu’une fois et n’est conservé que sous forme de hash. Transmettez-le par un canal sécurisé approuvé. Ne le collez pas dans un chat ou un ticket.', invitationId: 'ID d’invitation', runnerId: 'ID du runner', token: 'Jeton à usage unique', copyToken: 'Copier le jeton', copied: 'Copié', close: 'Fermer',
    redeem: 'Accepter une invitation', redeemId: 'ID d’invitation', redeemToken: 'Jeton d’invitation', acceptInvite: 'Accepter l’invitation',
    noticeLogin: 'L’authentification est gérée par le fournisseur OIDC configuré. Le fournisseur et ce service doivent avoir le même issuer, audience et une adresse vérifiée.',
    noAccess: 'Vous n’avez pas accès à cet espace.', noPermission: 'Votre rôle ne permet pas cette action.', requestFailed: 'La requête n’a pas abouti.',
    confirmRemove: 'Retirer ce membre de l’espace ?', confirmRevokeRunner: 'Révoquer cet identifiant runner ? Ses heartbeats seront immédiatement refusés.', confirmRevokeInvite: 'Révoquer cette invitation ?',
    locale: 'Langue', oneTime: 'Affiché une fois', activeWorkspace: 'Espace actif', emptyWorkspace: 'Créez un espace pour commencer à inviter votre équipe.', ownerAdmin: 'Seuls les Owners et Admins peuvent gérer les membres et les runners.', runnerExpiry: 'Les identifiants runner expirent sous 90 jours maximum.', auditHelp: 'Modifications administratives de l’espace. Aucun contenu de ticket n’est enregistré ici.',
  },
} as const

function issuerDiscoveryUrl(issuer: string): string {
  const url = new URL(issuer)
  if (url.username || url.password || url.search || url.hash) throw new Error('The OIDC issuer URL is invalid.')
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && LOCALHOSTS.has(url.hostname))) {
    throw new Error('The identity provider must use HTTPS.')
  }
  url.pathname = `${url.pathname.replace(/\/+$/, '')}/.well-known/openid-configuration`
  return url.toString()
}

function acceptedEndpoint(raw: string): string {
  const url = new URL(raw)
  if (url.username || url.password || url.hash) throw new Error('The identity provider endpoint is invalid.')
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && LOCALHOSTS.has(url.hostname))) {
    throw new Error('The identity provider must use HTTPS.')
  }
  return url.toString()
}

function randomValue(bytes = 32): string {
  const value = crypto.getRandomValues(new Uint8Array(bytes))
  return btoa(String.fromCharCode(...value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
}

function decodeBase64Url(segment: string): ArrayBuffer {
  const normalized = segment.replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '='))
  return Uint8Array.from(raw, (char) => char.charCodeAt(0)).buffer as ArrayBuffer
}

function decodeJwtSegment<T>(segment: string): T {
  return JSON.parse(new TextDecoder().decode(decodeBase64Url(segment))) as T
}

type OidcMetadata = {
  issuer: string
  authorization_endpoint: string
  token_endpoint: string
  jwks_uri: string
}

async function loadOidcMetadata(issuer: string): Promise<OidcMetadata> {
  const response = await fetch(issuerDiscoveryUrl(issuer), { cache: 'no-store' })
  if (!response.ok) throw new Error('The identity provider configuration could not be loaded.')
  const metadata = await response.json() as OidcMetadata
  if (metadata.issuer !== issuer) throw new Error('The identity provider issuer did not match its configuration.')
  acceptedEndpoint(metadata.authorization_endpoint)
  acceptedEndpoint(metadata.token_endpoint)
  acceptedEndpoint(metadata.jwks_uri)
  return metadata
}

async function verifyIdToken(token: string, metadata: OidcMetadata, clientId: string, nonce: string): Promise<void> {
  const [encodedHeader, encodedClaims, encodedSignature] = token.split('.')
  if (!encodedHeader || !encodedClaims || !encodedSignature) throw new Error('The identity provider returned an invalid ID token.')
  const header = decodeJwtSegment<{ alg?: string; kid?: string }>(encodedHeader)
  const claims = decodeJwtSegment<{ iss?: string; aud?: string | string[]; azp?: string; exp?: number; iat?: number; nbf?: number; sub?: string; nonce?: string }>(encodedClaims)
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
  if (!key) throw new Error('The ID token signing key was not found.')
  const cryptoKey = await crypto.subtle.importKey('jwk', key, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify'])
  const valid = await crypto.subtle.verify(
    { name: 'RSASSA-PKCS1-v1_5' },
    cryptoKey,
    decodeBase64Url(encodedSignature),
    new TextEncoder().encode(`${encodedHeader}.${encodedClaims}`),
  )
  if (!valid) throw new Error('The ID token signature is invalid.')
}

async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return btoa(String.fromCharCode(...new Uint8Array(digest))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
}

function friendlyDate(value: string | null, locale: Locale): string {
  if (!value) return locale === 'fr' ? 'Jamais' : 'Never'
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed)
}

function isExpired(value: string): boolean { return new Date(value).valueOf() <= Date.now() }

export default function ControlPlaneApp() {
  const [locale, setLocale] = useState<Locale>(() => sessionStorage.getItem('sws-locale') === 'fr' ? 'fr' : 'en')
  const t = copy[locale]
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null)
  const [accessToken, setAccessToken] = useState<string | null>(() => sessionStorage.getItem(SESSION_KEY))
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [members, setMembers] = useState<Member[]>([])
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [runners, setRunners] = useState<Runner[]>([])
  const [audit, setAudit] = useState<AuditEvent[]>([])
  const [section, setSection] = useState<Section>('members')
  const [busy, setBusy] = useState(false)
  const [booting, setBooting] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [workspaceName, setWorkspaceName] = useState('')
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<Role>('Builder')
  const [runnerName, setRunnerName] = useState('')
  const [redeemId, setRedeemId] = useState('')
  const [redeemToken, setRedeemToken] = useState('')
  const [credential, setCredential] = useState<OneTimeCredential | null>(null)
  const [copied, setCopied] = useState(false)
  const oidcIssuer = authConfig?.issuer
  const oidcClientId = authConfig?.client_id
  const oidcAudience = authConfig?.audience

  const api = useCallback(async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    const headers = new Headers(init.headers)
    if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
    if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    const response = await fetch(path, { ...init, headers, cache: 'no-store', credentials: 'same-origin' })
    if (response.status === 401 && accessToken) {
      sessionStorage.removeItem(SESSION_KEY)
      setAccessToken(null)
      setIdentity(null)
      throw new Error(locale === 'fr' ? 'Votre session a expiré. Reconnectez-vous.' : 'Your session expired. Sign in again.')
    }
    if (!response.ok) {
      if (response.status === 403) throw new Error(t.noPermission)
      if (response.status === 404) throw new Error(t.noAccess)
      let detail = ''
      try { const body = await response.json() as { detail?: unknown }; if (typeof body.detail === 'string') detail = body.detail } catch { /* Response body is optional. */ }
      throw new Error(detail || `${t.requestFailed} (${response.status})`)
    }
    if (response.status === 204) return undefined as T
    return response.json() as Promise<T>
  }, [accessToken, locale, t.noAccess, t.noPermission, t.requestFailed])

  const refreshWorkspaceData = useCallback(async (selected: Workspace) => {
    setWorkspace(selected)
    setError('')
    const hasAdminAccess = selected.role === 'Owner' || selected.role === 'Admin'
    const [memberRows, invitationRows, runnerRows, auditRows] = await Promise.all([
      api<Member[]>(`/api/v1/workspaces/${encodeURIComponent(selected.id)}/members`),
      hasAdminAccess ? api<Invitation[]>(`/api/v1/workspaces/${encodeURIComponent(selected.id)}/invitations`) : Promise.resolve([]),
      api<Runner[]>(`/api/v1/workspaces/${encodeURIComponent(selected.id)}/runners`),
      hasAdminAccess ? api<AuditEvent[]>(`/api/v1/workspaces/${encodeURIComponent(selected.id)}/audit?limit=100`) : Promise.resolve([]),
    ])
    setMembers(memberRows)
    setInvitations(invitationRows)
    setRunners(runnerRows)
    setAudit(auditRows)
  }, [api])

  const refreshIdentity = useCallback(async (preferredId?: string) => {
    const profile = await api<Identity>('/api/v1/me')
    setIdentity(profile)
    const next = profile.workspaces.find((item) => item.id === preferredId)
      ?? profile.workspaces.find((item) => item.id === workspace?.id)
      ?? profile.workspaces[0]
    if (next) await refreshWorkspaceData(next)
    else { setWorkspace(null); setMembers([]); setInvitations([]); setRunners([]); setAudit([]) }
  }, [api, refreshWorkspaceData, workspace?.id])

  useEffect(() => {
    let alive = true
    const boot = async () => {
      try {
        const response = await fetch('/api/v1/auth/config', { cache: 'no-store', credentials: 'same-origin' })
        if (!response.ok) throw new Error(t.loadFailed)
        const config = await response.json() as AuthConfig
        if (alive) setAuthConfig(config)
      } catch (caught) { if (alive) setError(caught instanceof Error ? caught.message : t.loadFailed) }
      finally { if (alive) setBooting(false) }
    }
    void boot()
    return () => { alive = false }
  }, [t.loadFailed, t.notConfigured])

  useEffect(() => {
    if (!accessToken) return
    let alive = true
    void refreshIdentity().catch((caught: unknown) => { if (alive) setError(caught instanceof Error ? caught.message : t.requestFailed) })
    return () => { alive = false }
  }, [accessToken, refreshIdentity, t.requestFailed])

  useEffect(() => {
    if (!oidcIssuer) return
    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')
    const providerError = params.get('error')
    const returnedState = params.get('state')
    if (!code && !providerError) return
    history.replaceState({}, document.title, `${window.location.pathname}${window.location.hash}`)
    if (providerError) {
      sessionStorage.removeItem('sws-oidc-state')
      sessionStorage.removeItem('sws-oidc-verifier')
      sessionStorage.removeItem('sws-oidc-nonce')
      setError(params.get('error_description') || (locale === 'fr' ? 'La connexion a été annulée.' : 'Sign-in was cancelled.'))
      return
    }
    if (!code || !oidcClientId) return
    const storedState = sessionStorage.getItem('sws-oidc-state')
    const verifier = sessionStorage.getItem('sws-oidc-verifier')
    const nonce = sessionStorage.getItem('sws-oidc-nonce')
    sessionStorage.removeItem('sws-oidc-state')
    sessionStorage.removeItem('sws-oidc-verifier')
    sessionStorage.removeItem('sws-oidc-nonce')
    if (!storedState || storedState !== returnedState || !verifier) {
      setError(locale === 'fr' ? 'La réponse d’authentification ne correspond pas à cette session.' : 'The authentication response does not match this session.')
      return
    }
    let alive = true
    void (async () => {
      try {
        const metadata = await loadOidcMetadata(oidcIssuer)
        const tokenUrl = acceptedEndpoint(metadata.token_endpoint)
        const formValues: Record<string, string> = { grant_type: 'authorization_code', code, client_id: oidcClientId, redirect_uri: `${window.location.origin}${window.location.pathname}`, code_verifier: verifier }
        if (oidcAudience) { formValues.audience = oidcAudience; formValues.resource = oidcAudience }
        const form = new URLSearchParams(formValues)
        const tokenResponse = await fetch(tokenUrl, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: form })
        if (!tokenResponse.ok) throw new Error(locale === 'fr' ? 'Le fournisseur a refusé la connexion.' : 'The identity provider rejected sign-in.')
        const tokens = await tokenResponse.json() as { access_token?: string; id_token?: string }
        if (!tokens.access_token || !tokens.id_token || !nonce) throw new Error(t.requestFailed)
        await verifyIdToken(tokens.id_token, metadata, oidcClientId, nonce)
        if (!alive) return
        sessionStorage.setItem(SESSION_KEY, tokens.access_token)
        setAccessToken(tokens.access_token)
      } catch (caught) {
        if (alive) setError(caught instanceof Error ? caught.message : t.requestFailed)
      }
    })()
    return () => { alive = false }
  }, [oidcIssuer, oidcClientId, oidcAudience, locale, t.requestFailed])

  const signIn = async () => {
    setError('')
    if (!authConfig?.configured || !authConfig.issuer || !authConfig.client_id) return
    try {
      if (!crypto.subtle) throw new Error(locale === 'fr' ? 'Web Crypto est indisponible. Ouvrez cette page via HTTPS.' : 'Web Crypto is unavailable. Open this page over HTTPS.')
      const discovery = await loadOidcMetadata(authConfig.issuer)
      const authorize = acceptedEndpoint(discovery.authorization_endpoint)
      const verifier = randomValue(48)
      const state = randomValue()
      const nonce = randomValue()
      sessionStorage.setItem('sws-oidc-state', state)
      sessionStorage.setItem('sws-oidc-verifier', verifier)
      sessionStorage.setItem('sws-oidc-nonce', nonce)
      const parameters: Record<string, string> = { response_type: 'code', client_id: authConfig.client_id, redirect_uri: `${window.location.origin}${window.location.pathname}`, scope: 'openid email profile', state, nonce, code_challenge: await challengeFor(verifier), code_challenge_method: 'S256' }
      if (authConfig.audience) { parameters.audience = authConfig.audience; parameters.resource = authConfig.audience }
      const url = new URL(authorize)
      url.search = new URLSearchParams(parameters).toString()
      window.location.assign(url.toString())
    } catch (caught) { setError(caught instanceof Error ? caught.message : t.requestFailed) }
  }

  const signOut = () => {
    sessionStorage.removeItem(SESSION_KEY)
    setAccessToken(null); setIdentity(null); setWorkspace(null); setMembers([]); setInvitations([]); setRunners([]); setAudit([])
    setNotice('')
  }

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch (caught) { setError(caught instanceof Error ? caught.message : t.requestFailed) }
    finally { setBusy(false) }
  }

  const createWorkspace = () => run(async () => {
    const created = await api<Workspace>('/api/v1/workspaces', { method: 'POST', body: JSON.stringify({ name: workspaceName }) })
    setCreateOpen(false); setWorkspaceName(''); await refreshIdentity(created.id)
    setNotice(locale === 'fr' ? 'Espace créé.' : 'Workspace created.')
  })

  const createInvitation = () => workspace && run(async () => {
    const created = await api<Invitation & { token: string }>(`/api/v1/workspaces/${encodeURIComponent(workspace.id)}/invitations`, { method: 'POST', body: JSON.stringify({ email: inviteEmail, role: inviteRole, expires_in_hours: 168 }) })
    setCredential({ title: t.inviteMember, id: created.id, token: created.token, expiresAt: created.expires_at, kind: 'invitation' })
    setInviteEmail(''); await refreshIdentity(workspace.id)
  })

  const createRunner = () => workspace && run(async () => {
    const created = await api<Runner & { token: string }>(`/api/v1/workspaces/${encodeURIComponent(workspace.id)}/runners`, { method: 'POST', body: JSON.stringify({ name: runnerName, expires_in_days: 90 }) })
    setCredential({ title: t.registerRunner, id: created.id, token: created.token, expiresAt: created.expires_at, kind: 'runner' })
    setRunnerName(''); await refreshIdentity(workspace.id)
  })

  const saveMemberRole = (member: Member, role: Role) => workspace && run(async () => {
    await api<void>(`/api/v1/workspaces/${encodeURIComponent(workspace.id)}/members/${encodeURIComponent(member.subject)}`, { method: 'PATCH', body: JSON.stringify({ role }) })
    await refreshWorkspaceData(workspace)
  })

  const removeMember = (member: Member) => workspace && window.confirm(t.confirmRemove) && run(async () => {
    await api<void>(`/api/v1/workspaces/${encodeURIComponent(workspace.id)}/members/${encodeURIComponent(member.subject)}`, { method: 'DELETE' })
    await refreshWorkspaceData(workspace)
  })

  const revokeInvitation = (invitation: Invitation) => workspace && window.confirm(t.confirmRevokeInvite) && run(async () => {
    await api<void>(`/api/v1/workspaces/${encodeURIComponent(workspace.id)}/invitations/${encodeURIComponent(invitation.id)}`, { method: 'DELETE' })
    await refreshWorkspaceData(workspace)
  })

  const revokeRunner = (runner: Runner) => workspace && window.confirm(t.confirmRevokeRunner) && run(async () => {
    await api<void>(`/api/v1/workspaces/${encodeURIComponent(workspace.id)}/runners/${encodeURIComponent(runner.id)}`, { method: 'DELETE' })
    await refreshWorkspaceData(workspace)
  })

  const redeemInvitation = () => run(async () => {
    const accepted = await api<{ workspace_id: string }>(`/api/v1/invitations/${encodeURIComponent(redeemId)}/redeem`, { method: 'POST', body: JSON.stringify({ token: redeemToken }) })
    await refreshIdentity(accepted.workspace_id); setRedeemId(''); setRedeemToken('')
    setNotice(locale === 'fr' ? 'Invitation acceptée.' : 'Invitation accepted.')
  })

  const copyToken = async () => {
    if (!credential) return
    try {
      await navigator.clipboard.writeText(credential.token)
      setCopied(true); window.setTimeout(() => setCopied(false), 1800)
    } catch { setError(locale === 'fr' ? 'Copie impossible. Sélectionnez le jeton et copiez-le manuellement.' : 'Could not copy. Select the token and copy it manually.') }
  }

  const roleOptions = useMemo(() => ['Owner', 'Admin', 'Builder', 'Operator', 'Approver', 'Viewer'] as Role[], [])
  const canAdmin = workspace?.role === 'Owner' || workspace?.role === 'Admin'
  const sectionLabels: Record<Section, string> = { members: t.members, invitations: t.invitations, runners: t.runners, audit: t.audit }
  const visibleSections = (Object.keys(sectionLabels) as Section[]).filter((item) => canAdmin || (item !== 'invitations' && item !== 'audit'))
  const recentHeartbeat = (value: string | null) => value !== null && Date.now() - new Date(value).valueOf() < 5 * 60 * 1000

  useEffect(() => {
    if (!canAdmin && (section === 'invitations' || section === 'audit')) setSection('members')
  }, [canAdmin, section])

  if (booting || (accessToken && !identity && !error)) return <main className="cp-shell"><div className="cp-boot"><span className="cp-skeleton cp-skeleton-title"/><span className="cp-skeleton cp-skeleton-row"/><span className="cp-skeleton cp-skeleton-row"/></div></main>

  if (!accessToken || !identity) return <main className="cp-shell cp-login-shell">
    <header className="cp-login-brand"><span className="cp-brand-mark">S</span><span>{t.brand}</span><button className="cp-locale" onClick={() => { const next = locale === 'en' ? 'fr' : 'en'; setLocale(next); sessionStorage.setItem('sws-locale', next) }}>{locale.toUpperCase()} <ChevronDown size={13}/></button></header>
    <section className="cp-login-content">
      <div className="cp-login-side"><div className="cp-orbit" aria-hidden="true"><span/><i/><b/></div><p>{t.boundary}</p><small>{t.boundaryCopy}</small></div>
      <div className="cp-login-form"><div className="cp-symbol"><Users size={18}/></div><h1>{t.signInTitle}</h1><p>{t.signInCopy}</p>
        <div className="cp-info-note"><ShieldCheck size={16}/><span>{t.noticeLogin}</span></div>
        {error && <div className="cp-alert error" role="alert">{error}</div>}
        <button className="cp-primary cp-sign-in" onClick={() => void signIn()} disabled={!authConfig?.configured}><span>{t.signIn}</span><ArrowRight size={16}/></button>
        {authConfig && !authConfig.configured && <p className="cp-muted">{t.notConfigured}</p>}
      </div>
    </section>
    <footer className="cp-login-foot">{t.boundaryCopy}</footer>
  </main>

  return <main className="cp-shell">
    <aside className="cp-sidebar">
      <div className="cp-brand"><span className="cp-brand-mark">S</span><div><b>{t.brand}</b><small>{t.workspace}</small></div></div>
      <div className="cp-workspace-label">{t.activeWorkspace}</div>
      <label className="cp-select-wrap"><span className="cp-visually-hidden">{t.workspace}</span>
        <select value={workspace?.id ?? ''} onChange={(event) => { const next = identity.workspaces.find((item) => item.id === event.target.value); if (next) void run(() => refreshWorkspaceData(next)) }}>
          <option value="" disabled>{t.workspace}</option>{identity.workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select><ChevronDown size={14}/>
      </label>
      <button className="cp-sidebar-add" onClick={() => setCreateOpen(true)}><span>+</span>{t.createWorkspace}</button>
      <nav className="cp-nav" aria-label={t.workspace}>
        {visibleSections.map((item) => <button key={item} className={section === item ? 'active' : ''} onClick={() => setSection(item)}>
          {item === 'members' ? <Users size={16}/> : item === 'invitations' ? <MailPlus size={16}/> : item === 'runners' ? <Activity size={16}/> : <ShieldCheck size={16}/>}{sectionLabels[item]}
          {item === 'members' && <span className="cp-nav-count">{members.length}</span>}
        </button>)}
      </nav>
      <div className="cp-sidebar-bottom">
        <div className="cp-user"><span className="cp-avatar"><UserRoundPlus size={15}/></span><span><b>{identity.email}</b><small>{workspace?.role ?? t.member}</small></span></div>
        <button className="cp-logout" onClick={signOut}><LogOut size={15}/>{t.signOut}</button>
      </div>
    </aside>
    <section className="cp-main">
      <header className="cp-topbar"><div><span>{t.workspace}</span><ChevronDown size={13}/><strong>{workspace?.name ?? t.createWorkspace}</strong></div><div className="cp-top-actions"><button className="cp-locale" onClick={() => { const next = locale === 'en' ? 'fr' : 'en'; setLocale(next); sessionStorage.setItem('sws-locale', next) }}>{locale.toUpperCase()}</button><button className="cp-icon-btn" onClick={() => workspace && void run(() => refreshWorkspaceData(workspace))} aria-label={t.retry}><RefreshCw size={15}/></button><button className="cp-icon-btn" onClick={signOut} aria-label={t.signOut}><LogOut size={15}/></button></div></header>
      <div className="cp-content">
        <div className="cp-page-heading"><div><h1>{workspace ? sectionLabels[section] : t.createWorkspace}</h1><p>{workspace ? `${workspace.name} · ${workspace.role}` : t.emptyWorkspace}</p></div>
          {workspace && canAdmin && section === 'members' && <button className="cp-primary" onClick={() => document.getElementById('cp-invite-email')?.focus()}><MailPlus size={15}/>{t.inviteMember}</button>}
        </div>
        <div className="cp-boundary-banner"><ShieldCheck size={15}/><span><b>{t.boundary}</b> — {t.boundaryCopy}</span></div>
        {error && <div className="cp-alert error" role="alert">{error}<button onClick={() => setError('')} aria-label={t.close}><X size={14}/></button></div>}
        {notice && <div className="cp-alert success" role="status"><Check size={15}/>{notice}<button onClick={() => setNotice('')} aria-label={t.close}><X size={14}/></button></div>}
        {createOpen && <form className="cp-inline-form" onSubmit={(event) => { event.preventDefault(); void createWorkspace() }}><div><label htmlFor="cp-workspace-name">{t.workspaceName}</label><input id="cp-workspace-name" autoFocus maxLength={100} value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} placeholder={t.workspacePlaceholder}/></div><button className="cp-primary" disabled={busy || !workspaceName.trim()}>{busy ? '…' : t.create}</button><button type="button" className="cp-secondary" onClick={() => setCreateOpen(false)}>{t.cancel}</button></form>}
        {workspace && <>
          <div className="cp-statline"><span><i>{members.length}</i>{t.members}</span><span><i>{invitations.filter((item) => !item.accepted_at && !item.revoked_at && !isExpired(item.expires_at)).length}</i>{t.pending}</span><span><i>{runners.filter((item) => !item.revoked_at && !isExpired(item.expires_at)).length}</i>{t.runners}</span></div>
          {section === 'members' && <>
            {canAdmin && <form className="cp-invite-form" onSubmit={(event) => { event.preventDefault(); void createInvitation() }}><div className="cp-form-heading"><div><h2>{t.inviteMember}</h2><p>{t.ownerAdmin}</p></div><MailPlus size={19}/></div><div className="cp-form-row"><div className="cp-field grow"><label htmlFor="cp-invite-email">{t.inviteEmail}</label><input id="cp-invite-email" type="email" required maxLength={254} value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)}/></div><div className="cp-field"><label htmlFor="cp-invite-role">{t.inviteRole}</label><select id="cp-invite-role" value={inviteRole} onChange={(event) => setInviteRole(event.target.value as Role)}>{roleOptions.filter((role) => workspace.role === 'Owner' || !['Owner', 'Admin'].includes(role)).map((role) => <option key={role}>{role}</option>)}</select></div><button className="cp-primary" disabled={busy || !inviteEmail.trim()}>{busy ? '…' : t.sendInvite}</button></div></form>}
            <div className="cp-table-wrap"><table><thead><tr><th>{t.member}</th><th>{t.role}</th><th>{t.joined}</th><th>{t.actions}</th></tr></thead><tbody>{members.map((member) => <tr key={member.subject}><td><div className="cp-person"><span className="cp-avatar small">{member.email.slice(0,1).toUpperCase()}</span><span><b>{member.email}</b><small>{member.subject}</small></span></div></td><td>{canAdmin ? <select className="cp-role-select" value={member.role} onChange={(event) => void saveMemberRole(member, event.target.value as Role)} disabled={busy || (workspace.role !== 'Owner' && ['Owner', 'Admin'].includes(member.role))}>{roleOptions.filter((role) => workspace.role === 'Owner' || !['Owner', 'Admin'].includes(role)).map((role) => <option key={role}>{role}</option>)}</select> : <span className="cp-role-badge">{member.role}</span>}</td><td>{friendlyDate(member.created_at, locale)}</td><td>{canAdmin && member.subject !== identity.subject && <button className="cp-text-danger" onClick={() => removeMember(member)} disabled={busy}>{t.remove}</button>}</td></tr>)}</tbody></table>{!members.length && <p className="cp-empty">{t.noMembers}</p>}</div>
          </>}
          {section === 'invitations' && <div className="cp-table-wrap"><table><thead><tr><th>{t.member}</th><th>{t.role}</th><th>{t.status}</th><th>{t.expires}</th><th>{t.actions}</th></tr></thead><tbody>{invitations.map((invite) => { const state = invite.revoked_at ? t.revoked : invite.accepted_at ? t.accepted : isExpired(invite.expires_at) ? t.expired : t.pending; return <tr key={invite.id}><td><div className="cp-person"><span className="cp-avatar small"><MailPlus size={14}/></span><span><b>{invite.email}</b><small>{invite.id}</small></span></div></td><td><span className="cp-role-badge">{invite.role}</span></td><td><span className={`cp-status ${invite.revoked_at ? 'revoked' : invite.accepted_at ? 'accepted' : isExpired(invite.expires_at) ? 'expired' : 'pending'}`}>{state}</span></td><td>{friendlyDate(invite.expires_at, locale)}</td><td>{canAdmin && !invite.accepted_at && !invite.revoked_at && !isExpired(invite.expires_at) && <button className="cp-text-danger" onClick={() => revokeInvitation(invite)} disabled={busy}>{t.revoke}</button>}</td></tr>})}</tbody></table>{!invitations.length && <p className="cp-empty">{t.noInvites}</p>}</div>}
          {section === 'runners' && <>
            {canAdmin && <form className="cp-invite-form" onSubmit={(event) => { event.preventDefault(); void createRunner() }}><div className="cp-form-heading"><div><h2>{t.registerRunner}</h2><p>{t.runnerExpiry}</p></div><KeyRound size={19}/></div><div className="cp-form-row"><div className="cp-field grow"><label htmlFor="cp-runner-name">{t.runnerName}</label><input id="cp-runner-name" required maxLength={100} value={runnerName} onChange={(event) => setRunnerName(event.target.value)} placeholder={t.runnerPlaceholder}/></div><button className="cp-primary" disabled={busy || !runnerName.trim()}>{busy ? '…' : t.register}</button></div></form>}
            <div className="cp-table-wrap"><table><thead><tr><th>{t.runnerName}</th><th>{t.status}</th><th>{t.heartbeat}</th><th>{t.expires}</th><th>{t.actions}</th></tr></thead><tbody>{runners.map((runner) => { const expired = isExpired(runner.expires_at); const stopped = Boolean(runner.revoked_at); const isRecent = recentHeartbeat(runner.last_heartbeat_at); return <tr key={runner.id}><td><div className="cp-person"><span className="cp-avatar small"><Activity size={14}/></span><span><b>{runner.name}</b><small>{runner.id}</small></span></div></td><td><span className={`cp-status ${stopped || expired ? 'revoked' : isRecent ? 'accepted' : 'pending'}`}>{stopped ? t.revoked : expired ? t.expired : isRecent ? t.recent : t.offline}</span></td><td>{friendlyDate(runner.last_heartbeat_at, locale)}</td><td>{friendlyDate(runner.expires_at, locale)}</td><td>{canAdmin && !stopped && <button className="cp-text-danger" onClick={() => revokeRunner(runner)} disabled={busy}>{t.revoke}</button>}</td></tr>})}</tbody></table>{!runners.length && <p className="cp-empty">{t.noRunners}</p>}</div>
          </>}
          {section === 'audit' && <><p className="cp-section-note">{t.auditHelp}</p><div className="cp-table-wrap"><table><thead><tr><th>{t.created}</th><th>{t.actions}</th><th>{t.member}</th><th>{t.workspace}</th></tr></thead><tbody>{audit.map((event) => <tr key={event.id}><td>{friendlyDate(event.occurred_at, locale)}</td><td><span className="cp-event-type">{event.event_type}</span></td><td>{event.actor_subject}</td><td>{event.target_type} · {event.target_id}</td></tr>)}</tbody></table>{!audit.length && <p className="cp-empty">{t.noAudit}</p>}</div></>}
          {section === 'members' && <form className="cp-redeem" onSubmit={(event) => { event.preventDefault(); void redeemInvitation() }}><div><h2>{t.redeem}</h2><p>{locale === 'fr' ? 'Utilisez l’identifiant et le jeton reçus par un canal sécurisé.' : 'Use the ID and token received through a secure channel.'}</p></div><div className="cp-form-row"><div className="cp-field"><label htmlFor="cp-redeem-id">{t.redeemId}</label><input id="cp-redeem-id" value={redeemId} onChange={(event) => setRedeemId(event.target.value)} required/></div><div className="cp-field grow"><label htmlFor="cp-redeem-token">{t.redeemToken}</label><input id="cp-redeem-token" type="password" autoComplete="off" value={redeemToken} onChange={(event) => setRedeemToken(event.target.value)} required/></div><button className="cp-secondary" disabled={busy || !redeemId || !redeemToken}>{t.acceptInvite}</button></div></form>}
          <p className="cp-footnote">{t.ownerAdmin}</p>
        </>}
      </div>
    </section>
    {credential && <div className="cp-credential-backdrop" role="presentation"><section className="cp-credential" role="dialog" aria-modal="true" aria-labelledby="cp-credential-title"><button className="cp-dialog-close" onClick={() => setCredential(null)} aria-label={t.close}><X size={16}/></button><span className="cp-dialog-mark"><KeyRound size={19}/></span><div className="cp-once-label">{t.oneTime}</div><h2 id="cp-credential-title">{t.credentialTitle}</h2><p>{t.credentialCopy}</p><dl><div><dt>{credential.kind === 'invitation' ? t.invitationId : t.runnerId}</dt><dd>{credential.id}</dd></div><div><dt>{t.expires}</dt><dd>{friendlyDate(credential.expiresAt, locale)}</dd></div></dl><label className="cp-secret-label" htmlFor="cp-one-time-token">{t.token}</label><div className="cp-secret-row"><input id="cp-one-time-token" readOnly value={credential.token} onFocus={(event) => event.currentTarget.select()}/><button className="cp-primary" onClick={() => void copyToken()}>{copied ? <Check size={14}/> : <Copy size={14}/>} {copied ? t.copied : t.copyToken}</button></div><button className="cp-secondary cp-dialog-done" onClick={() => setCredential(null)}>{t.close}</button></section></div>}
  </main>
}
