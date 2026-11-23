import { useEffect, useMemo, useState } from 'react'
import {
  Activity, ArrowDownRight, Check, CheckCheck, ChevronDown,
  CircleHelp, Clock3, FileSearch, Inbox, Layers3, LockKeyhole, MessageSquareText,
  MoreHorizontal, Play, Plus, Search, ShieldCheck, Sparkles, Target, UserRound, Copy, Waypoints,
  CloudDownload,
  Settings2, Languages, X,
} from 'lucide-react'
import WorkflowBuilder from './WorkflowBuilder'
import SetupPanel from './SetupPanel'
import { translate, type AppLocale } from './i18n'

type SupportCase = {
  id: string; title: string; requester: string; channel: string; created_at: string
  priority: string; category: string; status: string; body: string; impact: string; tags: string[]
  source: string; external_key?: string | null
}
type Stage = { id: string; label: string; status: string; detail: string; actor: string; elapsed_ms: number }
type Finding = { lane: string; score: number; verdict: string; findings: string[] }
type WorkLane = { lane: string; scope: string; finding: string; evidence_ids: string[]; unknowns: string[] }
type CouncilOption = { label: string; proposal: string; score: number; rationale: string; criteria: string[] }
  type PlanReview = { round: number; origin: 'architect' | 'deterministic_fallback'; critic_score: number; score: number; verdict: 'pass' | 'revise' | 'stop'; plan: string[]; findings: string[]; missing_checks: string[]; deterministic_findings: string[] }
type Run = {
  id: string; case_id: string; status: string; provider_mode: string; model_name: string
  started_at: string; elapsed_ms: number; score: number; rounds: number; revision_count: number; human_edited: boolean; approval_version: number; candidate_count: number
  category: string; urgency: string; support_level: string; support_route_reason: string; confidence: number; evidence: { id: string; title: string; excerpt: string; category?: string; source_version?: string; last_modified?: string; space_key?: string }[]
  plan: string[]; plan_reviews: PlanReview[]; draft: string; verifier_findings: string[]; reviewer_findings: Finding[]; work_lanes: WorkLane[]; council_options: CouncilOption[]; stages: Stage[]; human_decision?: string | null
  ticket_comment_published: boolean; ticket_comment_id?: string | null; ticket_comment_outcome: string
  workflow_id?: string | null; workflow_version?: number | null; workflow_content_hash?: string | null; workflow_node_ids?: string[]
}
type RunAuditEvent = {
  sequence: number; event_id: string; run_id: string; case_id: string
  event_type: 'workflow.started' | 'draft.edited' | 'decision.approved' | 'decision.rejected' | 'publish.attempted' | 'publish.succeeded' | 'publish.failed' | 'publish.unknown'
  occurred_at: string; from_status?: string; to_status?: string
  decision_version?: number; score?: number; outcome?: string
}
type Health = { status: string; provider_mode: string; provider: string; model: string; credentials_configured: boolean; private_local_mode: boolean }
type WorkspaceMetrics = { open_cases: number; total_cases: number; workflow_runs: number; live_model_runs: number; drafts_passing_gate: number; reviewer_approved_runs: number; reviewer_edited_runs: number; reviewer_rejected_runs: number; public_replies_published: number; median_triage_seconds: number | null }
type ProductSignal = { category: string; cases: { id: string; title: string; source: string; confidence: number }[] }
type KnowledgeStatus = { mode: string; configured: boolean; available: boolean; source_count: number; message: string }
type JiraMonitor = { enabled: boolean; last_poll_at: string | null; last_error: string | null; new_cases_seen: number; auto_triaged_count: number; auto_triage_deferred: number; retry_pending_count: number; page_cap_reached: boolean; auto_triage_enabled: boolean; interval_seconds: number }
type Integrations = { private_local_mode: boolean; itsm_provider: string; jira_configured: boolean; github_configured: boolean; knowledge: KnowledgeStatus; jira_monitor: JiraMonitor }
const API = '/api'

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

const priorityColor = (priority: string) => priority.toLowerCase() === 'urgent' ? 'urgent' : priority.toLowerCase() === 'high' ? 'high' : 'normal'
const cleanBriefField = (value: string, maxLength = 160) => value
  .replace(/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu, ' ')
  .replace(/\s+/g, ' ')
  .trim()
  .slice(0, maxLength)

export default function App() {
  const [locale, setLocale] = useState<AppLocale>(() => window.localStorage.getItem('support-workflow-locale') === 'fr' ? 'fr' : 'en')
  const tr = (value: string) => translate(locale, value)
  const [cases, setCases] = useState<SupportCase[]>([])
  const [selectedId, setSelectedId] = useState('SUP-4821')
  const [run, setRun] = useState<Run | null>(null)
  const [runAuditEvents, setRunAuditEvents] = useState<RunAuditEvent[]>([])
  const [runAuditError, setRunAuditError] = useState(false)
  const [runAuditLoading, setRunAuditLoading] = useState(false)
  const [runAuditRefresh, setRunAuditRefresh] = useState(0)
  const [health, setHealth] = useState<Health | null>(null)
  const [metrics, setMetrics] = useState<WorkspaceMetrics | null>(null)
  const [productSignals, setProductSignals] = useState<ProductSignal[]>([])
  const [integrations, setIntegrations] = useState<Integrations | null>(null)
  const [activeSection, setActiveSection] = useState<'workflow' | 'inbox' | 'signals'>('workflow')
  const [copiedSignal, setCopiedSignal] = useState<string | null>(null)
  const [signalCopyError, setSignalCopyError] = useState('')
  const [running, setRunning] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [lastQueueSync, setLastQueueSync] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [candidateCount, setCandidateCount] = useState(2)
  const [councilEnabled, setCouncilEnabled] = useState(false)
  const [draft, setDraft] = useState('')
  const [showIntake, setShowIntake] = useState(false)
  const [showSetup, setShowSetup] = useState(false)
  const [setupRevision, setSetupRevision] = useState(0)
  const [intake, setIntake] = useState({ title: '', requester: '', body: '', impact: 'Unknown impact' })
  const [decisionBusy, setDecisionBusy] = useState(false)
  const selected = cases.find((item) => item.id === selectedId)
const jiraKnowledgeReady = selected?.source !== 'jira-service-management' || (
  ['local', 'confluence'].includes(integrations?.knowledge.mode ?? '') && integrations?.knowledge.configured && integrations.knowledge.available
)

  useEffect(() => { window.localStorage.setItem('support-workflow-locale', locale) }, [locale])

  useEffect(() => {
    Promise.all([api<SupportCase[]>('/cases'), api<Health>('/health'), api<Integrations>('/integrations/status'), api<WorkspaceMetrics>('/metrics'), api<ProductSignal[]>('/product-signals')])
      .then(([items, state, integrationState, metricState, signals]) => {
        setCases(items); setHealth(state); setIntegrations(integrationState); setMetrics(metricState)
        setProductSignals(signals)
        setLastQueueSync(integrationState.jira_monitor.last_poll_at ? new Date(integrationState.jira_monitor.last_poll_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : null)
      })
      .catch((reason: Error) => setError(reason.message))
  }, [])

  useEffect(() => {
    setRun(null)
    if (selectedId) api<Run>(`/workflows/latest/${selectedId}`).then(setRun).catch(() => undefined)
  }, [selectedId])

  useEffect(() => {
    if (!run) { setRunAuditEvents([]); setRunAuditError(false); setRunAuditLoading(false); return }
    let cancelled = false
    setRunAuditLoading(true)
    api<RunAuditEvent[]>(`/workflows/${run.id}/events`)
      .then((events) => { if (!cancelled) { setRunAuditEvents(events); setRunAuditError(false); setRunAuditLoading(false) } })
      .catch(() => { if (!cancelled) { setRunAuditEvents([]); setRunAuditError(true); setRunAuditLoading(false) } })
    return () => { cancelled = true }
  }, [run?.id, runAuditRefresh])

  const filteredCases = useMemo(() => cases.filter((item) =>
    `${item.id} ${item.title} ${item.requester} ${item.tags.join(' ')}`.toLowerCase().includes(query.toLowerCase()),
  ), [cases, query])

  async function refreshMetrics() {
    try { setMetrics(await api<WorkspaceMetrics>('/metrics')) } catch { /* metrics are supplementary */ }
  }

  async function refreshProductSignals() {
    try { setProductSignals(await api<ProductSignal[]>('/product-signals')) } catch { /* signals are supplementary */ }
  }

  async function copyEscalationBrief(signal: ProductSignal) {
    const lines = [
      `Product feedback candidate: ${cleanBriefField(signal.category, 120)}`,
      '',
      `Observed classification: ${signal.cases.length} requests received this inferred triage category.`,
      ...signal.cases.map((item) => `- ${cleanBriefField(item.id, 80)}: ${cleanBriefField(item.title)} (triage confidence ${Math.round(item.confidence * 100)}%)`),
      '',
      'Inference: the requests may concern a related product area; a shared root cause is not verified.',
      'Unknowns: symptom overlap, affected versions, scope, customer impact, and root cause.',
      'Human follow-up: compare the source requests and add verified impact and technical context before escalating.',
    ]
    try {
      await navigator.clipboard.writeText(lines.join('\n'))
      setSignalCopyError('')
      setCopiedSignal(signal.category)
      window.setTimeout(() => setCopiedSignal((current) => current === signal.category ? null : current), 2000)
    } catch {
      setSignalCopyError('Could not copy the brief. Review the source requests and prepare it manually.')
    }
  }

  async function startWorkflow() {
    if (!selected) return
    setRunning(true); setError('')
    try {
      const result = await api<Run>('/workflows', {
        method: 'POST', body: JSON.stringify({ case_id: selected.id, candidate_count: candidateCount, include_model_council: councilEnabled }),
      })
      setRun(result); setDraft(result.draft)
      setRunAuditRefresh((value) => value + 1)
      void refreshMetrics()
      void refreshProductSignals()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Workflow could not start') }
    finally { setRunning(false) }
  }

  async function startPublishedWorkflow(workflowId: string, version: number, contentHash: string) {
    if (!selected) return
    setRunning(true); setError('')
    try {
      const result = await api<Run>(`/v1/workflows/${encodeURIComponent(workflowId)}/runs`, {
        method: 'POST',
        body: JSON.stringify({
          case_id: selected.id,
          workflow_version: version,
          workflow_content_hash: contentHash,
          candidate_count: candidateCount,
          include_model_council: councilEnabled,
        }),
      })
      setRun(result); setDraft(result.draft); setActiveSection('inbox')
      setRunAuditRefresh((value) => value + 1)
      void refreshMetrics()
      void refreshProductSignals()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Published workflow could not start') }
    finally { setRunning(false) }
  }

  async function submitDecision(decision: 'approve' | 'edit' | 'reject') {
    if (!run) return
    setDecisionBusy(true); setError('')
    try {
      const updated = await api<Run>(`/workflows/${run.id}/decision`, {
        method: 'POST', body: JSON.stringify({ decision, expected_version: run.approval_version, edited_draft: decision === 'edit' ? draft : null }),
      })
      setRun(updated)
      setRunAuditRefresh((value) => value + 1)
      if (decision === 'edit') setDraft(updated.draft)
      void refreshMetrics()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Decision could not be saved') }
    finally { setDecisionBusy(false) }
  }

  async function submitIntake(event: React.FormEvent) {
    event.preventDefault(); setError('')
    try {
      const created = await api<SupportCase>('/cases', { method: 'POST', body: JSON.stringify(intake) })
      setCases((current) => [...current, created]); setSelectedId(created.id); setShowIntake(false)
      void refreshMetrics()
      setIntake({ title: '', requester: '', body: '', impact: 'Unknown impact' })
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Case could not be added') }
  }

  async function syncJiraQueue() {
    setSyncing(true); setError('')
    try {
      const remote = await api<SupportCase[]>('/itsm/sync', { method: 'POST' })
      setCases((current) => [...current.filter((item) => item.source !== 'jira-service-management'), ...remote])
      setLastQueueSync(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))
      void refreshMetrics()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Jira queue could not be read') }
    finally { setSyncing(false) }
  }

  async function refreshSetupStatus() {
    try {
      const [state, modelState] = await Promise.all([api<Integrations>('/integrations/status'), api<Health>('/health')])
      const casesNow = state.jira_configured ? await api<SupportCase[]>('/itsm/sync', { method: 'POST' }) : await api<SupportCase[]>('/cases')
      setIntegrations(state)
      setHealth(modelState)
      setSetupRevision((revision) => revision + 1)
      setCases((current) => state.jira_configured
        ? [...current.filter((item) => item.source !== 'jira-service-management'), ...casesNow]
        : casesNow)
      if (state.jira_configured && selected?.source !== 'jira-service-management' && casesNow.length > 0) {
        setSelectedId(casesNow[0].id)
      }
      setLastQueueSync(state.jira_configured
        ? new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        : state.jira_monitor.last_poll_at ? new Date(state.jira_monitor.last_poll_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : null)
      void refreshMetrics()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Workspace status could not be refreshed') }
  }

  useEffect(() => {
    if (!integrations?.jira_configured) return
    async function refreshMonitorState() {
      try {
        const [items, state, metricState, signals] = await Promise.all([api<SupportCase[]>('/cases'), api<Integrations>('/integrations/status'), api<WorkspaceMetrics>('/metrics'), api<ProductSignal[]>('/product-signals')])
        setCases((current) => [...current.filter((item) => item.source !== 'jira-service-management'), ...items.filter((item) => item.source === 'jira-service-management')])
        setIntegrations(state)
        setMetrics(metricState)
        setProductSignals(signals)
        setLastQueueSync(state.jira_monitor.last_poll_at ? new Date(state.jira_monitor.last_poll_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : null)
      } catch (reason) { setError(reason instanceof Error ? reason.message : 'Jira monitor status could not be read') }
    }
    const interval = window.setInterval(() => { void refreshMonitorState() }, 15_000)
    return () => window.clearInterval(interval)
  }, [integrations?.jira_configured])

  async function publishTicketReply() {
    if (!run) return
    setDecisionBusy(true); setError('')
    try {
      const result = await api<{ published: boolean; comment_id: string | null }>(`/workflows/${run.id}/publish-ticket-reply`, { method: 'POST' })
      setRun({ ...run, ticket_comment_published: true, ticket_comment_id: result.comment_id, ticket_comment_outcome: 'published' })
      setRunAuditRefresh((value) => value + 1)
      void refreshMetrics()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Reply could not be published') }
    finally { setRunAuditRefresh((value) => value + 1); setDecisionBusy(false) }
  }

  return (
    <div className="app-shell" lang={locale}>
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Waypoints size={19} /></div><div><strong>Support Workflow</strong><span>Studio</span></div></div>
        <div className="workspace-switch"><span className="workspace-avatar">{integrations?.jira_configured ? 'J' : 'L'}</span><span><b>{tr(integrations?.jira_configured ? 'Support workspace' : 'Local lab')}</b><small>{integrations?.jira_configured ? 'Jira Service Management' : tr('Synthetic workspace')}</small></span><ChevronDown size={15} /></div>
        <div className="nav-section-label">{tr('WORKSPACE')}</div>
        <nav className="side-nav">
          <a className={`nav-item ${activeSection === 'workflow' ? 'active' : ''}`} href="#workflow-studio" onClick={() => setActiveSection('workflow')} aria-current={activeSection === 'workflow' ? 'page' : undefined}><Layers3 size={17} /> {tr('Workflows')}</a>
          <a className={`nav-item ${activeSection === 'inbox' ? 'active' : ''}`} href="#workbench" onClick={() => setActiveSection('inbox')} aria-current={activeSection === 'inbox' ? 'page' : undefined}><Inbox size={17} /> {tr('Inbox')} <span className="nav-count">{metrics?.open_cases ?? '—'}</span></a>
          <a className={`nav-item ${activeSection === 'signals' ? 'active' : ''}`} href="#product-signals" onClick={() => setActiveSection('signals')} aria-current={activeSection === 'signals' ? 'page' : undefined}><Activity size={17} /> {tr('Product signals')}</a>
          <button className="nav-item setup-nav-item" type="button" title={tr('Workspace setup')} aria-label={tr('Open workspace setup')} onClick={() => setShowSetup(true)}><Settings2 size={17} /> {tr('Setup')}</button>
        </nav>
        <div className="sidebar-bottom">
          <div className="safety-note"><div><ShieldCheck size={16} /> {tr('Human review required')}</div><p>{tr('Every response stays a draft until a person reviews it.')}</p></div>
          <div className="user-profile"><div className="profile-avatar"><UserRound size={16} /></div><div><b>{locale === 'fr' ? 'Réviseur support' : 'Support reviewer'}</b><small>{locale === 'fr' ? 'Opérateur local' : 'Local operator'}</small></div><MoreHorizontal size={18} /></div>
        </div>
      </aside>

      <main className="main-area">
        {activeSection !== 'workflow' && <header className="topbar">
          <div className="breadcrumbs"><span>{tr('Workspace')}</span><span className="crumb-slash">/</span><b>{activeSection === 'signals' ? tr('Product signals') : tr('Support inbox')}</b></div>
          <div className="topbar-right">{integrations?.private_local_mode && <span className="provider-pill live"><span className="status-dot" />{tr('PRIVATE LOCAL MODE')}</span>}<span className={`provider-pill ${health?.provider_mode === 'live' ? 'live' : ''}`}><span className="status-dot" />{health?.provider_mode === 'live' ? `${health.provider} · ${health.model}` : tr('Demo mode')}</span>{integrations?.jira_configured && <><span className={`provider-pill ${integrations.jira_monitor.enabled ? 'live' : ''}`}><span className="status-dot" />{integrations.jira_monitor.enabled ? `${tr('Jira monitor')} · ${integrations.jira_monitor.auto_triage_enabled ? tr('auto-triage on') : tr('review to triage')}` : tr('Jira monitor starting')}</span><span className={`provider-pill ${integrations.knowledge.configured && integrations.knowledge.available ? 'live' : ''}`}><span className="status-dot" />{integrations.knowledge.configured && integrations.knowledge.available ? integrations.knowledge.mode === 'confluence' ? tr('Confluence knowledge ready') : `${integrations.knowledge.source_count} ${tr('local KB sources')}` : tr('Knowledge setup required')}</span></>}<button className="language-switch" type="button" onClick={() => setLocale((current) => current === 'en' ? 'fr' : 'en')} aria-label={locale === 'en' ? 'Switch language to French' : 'Changer la langue en anglais'} title={locale === 'en' ? 'Change language' : 'Changer de langue'}><Languages size={15} /><span>{locale.toUpperCase()}</span></button><button className="icon-button" title={tr('Help')} aria-label={tr('Help')}><CircleHelp size={18} /></button><div className="top-avatar">SR</div></div>
        </header>}

        {activeSection === 'workflow' && <div id="workflow-studio" className="workflow-studio-page"><WorkflowBuilder locale={locale} onLocaleChange={setLocale} onOpenConnections={() => setShowSetup(true)} onRunPublishedWorkflow={startPublishedWorkflow} onOpenRuns={() => setActiveSection('inbox')} canRunPublishedWorkflow={Boolean(selected && integrations?.jira_configured && integrations.knowledge.available && health && (health.provider_mode === 'demo' || health.credentials_configured))} runningPublishedWorkflow={running} executionError={error} connectionsRevision={setupRevision} /></div>}

        <div className="content-wrap" style={activeSection === 'workflow' ? { display: 'none' } : undefined}>
          <section className="page-heading">
            <div><p className="eyebrow">{tr('SUPPORT AUTOMATION WORKFLOW')}</p><h1>{tr('Inbox')}</h1><p className="page-subtitle">{tr('Turn incoming requests into careful, evidence-backed support.')}</p></div>
            <div className="heading-actions">{integrations?.jira_configured && <button className="sync-button" onClick={syncJiraQueue} disabled={syncing}>{syncing ? <span className="spinner dark" /> : <CloudDownload size={15} />}{syncing ? tr('Syncing') : tr('Sync Jira queue')}</button>}<button className="primary-button" onClick={() => setShowIntake(true)}><Plus size={16} /> {tr('Add lab request')}</button></div>
          </section>

          {integrations?.private_local_mode && <div className="safety-note private-mode-note"><b>{tr('Private local mode is active.')}</b><span>{tr('Inference requires Ollama on loopback. Jira synchronization, public replies, external providers, and model councils are disabled. Add only reviewed, minimized case summaries through local intake.')}</span></div>}

          <section className="metrics-row" aria-label={tr('Workspace overview')}>
            <div className="metric-card"><span className="metric-icon moss"><Inbox size={17} /></span><div><small>{tr('Open requests')}</small><b>{metrics?.open_cases ?? '—'}</b></div><span className="metric-caption">{metrics ? `${metrics.total_cases} ${tr('tickets saved locally')}` : tr('Loading workspace metrics')}</span></div>
            <div className="metric-card"><span className="metric-icon blue"><Sparkles size={17} /></span><div><small>{tr('Triage workflows')}</small><b>{metrics?.workflow_runs ?? '—'}</b></div><span className="metric-caption">{metrics ? `${metrics.live_model_runs} ${tr('used a live model')}` : tr('Loading workflow metrics')}</span></div>
            <div className="metric-card"><span className="metric-icon rose"><Clock3 size={17} /></span><div><small>{tr('Median triage time')}</small><b>{metrics?.median_triage_seconds == null ? '—' : `${metrics.median_triage_seconds}s`}</b></div><span className="metric-caption">{tr('Draft generation; not customer response time')}</span></div>
            <div className="metric-card"><span className="metric-icon cream"><CheckCheck size={17} /></span><div><small>{tr('Drafts passing gate')}</small><b>{metrics ? `${metrics.drafts_passing_gate}/${metrics.workflow_runs}` : '—'}</b></div><span className="metric-caption">≥9/10 · {metrics?.public_replies_published ?? 0} {tr('public replies sent')}</span></div>
            <div className="metric-card"><span className="metric-icon blue"><MessageSquareText size={17} /></span><div><small>{tr('Reviewer decisions')}</small><b>{metrics ? `${metrics.reviewer_approved_runs} / ${metrics.reviewer_edited_runs} / ${metrics.reviewer_rejected_runs}` : '—'}</b></div><span className="metric-caption">{tr('Approved / edited / rejected · session runs')}</span></div>
          </section>

          <section className="signals-panel panel" id="product-signals" aria-labelledby="product-signals-title">
            <div className="panel-heading"><div><h2 id="product-signals-title">{tr('Emerging product signals')}</h2><span className="muted-count">{productSignals.length}</span></div><span className="signals-note">{tr('Repeated inferred categories · human review required')}</span></div>
            {signalCopyError && <p className="signal-copy-error" role="alert">{tr(signalCopyError)}</p>}
            {productSignals.length === 0 ? <p className="signals-empty">{tr('No repeated triage categories yet. Signals appear after at least two requests have a completed workflow in the same inferred category.')}</p> : <div className="signal-list">{productSignals.map((signal) => <article className="signal-card" key={signal.category}><div className="signal-card-head"><b>{signal.category}</b><span>{signal.cases.length} {tr('requests')}</span></div><p>{tr('This is a shared model classification, not a verified common cause.')}</p><div className="signal-cases">{signal.cases.map((item) => <button key={item.id} className="signal-case" onClick={() => { setActiveSection('inbox'); setSelectedId(item.id); document.getElementById('workbench')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}><span>{item.id}</span><b>{item.title}</b><small>{Math.round(item.confidence * 100)}% {locale === 'fr' ? 'confiance déduite' : 'inferred confidence'}</small></button>)}</div><button className="signal-copy-button" onClick={() => void copyEscalationBrief(signal)}><Copy size={13} />{copiedSignal === signal.category ? tr('Copied brief') : tr('Copy escalation brief')}<span className="sr-only" role="status" aria-live="polite">{copiedSignal === signal.category ? tr('Escalation brief copied to clipboard.') : ''}</span></button><small className="signal-copy-note">{tr('Includes ticket keys and titles; review before sharing.')}</small></article>)}</div>}
          </section>

          <div className="workbench" id="workbench">
            <section className="queue-panel panel">
              <div className="panel-heading"><div><h2>{tr('Requests')}</h2><span className="muted-count">{filteredCases.length}</span></div><button className="icon-button subtle" aria-label={tr('Queue options')}><MoreHorizontal size={18} /></button></div>
              <label className="search-box"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tr('Search requests')} /></label>
              <div className="queue-filter"><span>{tr('All requests')} <ChevronDown size={13} /></span><button>{tr('Newest first')} <ChevronDown size={13} /></button></div>
              <div className="case-list">
                {filteredCases.map((item) => (
                  <button key={item.id} className={`case-row ${selectedId === item.id ? 'selected' : ''}`} onClick={() => setSelectedId(item.id)}>
                    <div className="case-row-top"><span className="case-id">{item.id}</span><span className={`priority ${priorityColor(item.priority)}`}>{item.priority}</span></div>
                    <strong>{item.title}</strong><p>{item.requester} <span>·</span> {item.channel}</p>
                    <div className="case-row-bottom"><span className="case-category">{item.source === 'jira-service-management' ? 'Jira Cloud' : item.category}</span><span className={`case-status ${item.status === 'Escalated' || item.tags.some((tag) => tag.startsWith('studio-triage-')) ? 'escalated' : ''}`}>{item.tags.includes('studio-triage-paused-knowledge') ? tr('Knowledge required') : item.tags.includes('studio-triage-paused-consent') ? tr('Consent required') : item.tags.includes('studio-triage-retry-pending') ? tr('Retry pending') : item.tags.includes('studio-triage-failed') ? tr('Auto-triage failed') : item.status}</span></div>
                  </button>
                ))}
                {filteredCases.length === 0 && <div className="empty-queue">{tr('No requests match this search.')}</div>}
              </div>
              {integrations?.jira_monitor.last_error && <div className="error-banner"><X size={15} />{integrations.jira_monitor.last_error}</div>}
              {integrations?.jira_monitor.page_cap_reached && <div className="error-banner"><X size={15} />{tr('This scan reached 200 queue items. Split high-volume work into narrower Jira queues.')}</div>}
              {integrations && integrations.jira_monitor.retry_pending_count > 0 && <div className="queue-footer"><span>{integrations.jira_monitor.retry_pending_count} {tr('ticket(s) pending review-only auto-triage')}</span></div>}
              {integrations && integrations.jira_monitor.auto_triage_deferred > 0 && <div className="error-banner"><X size={15} />{integrations.jira_monitor.auto_triage_deferred} {tr('ticket(s) deferred by the per-poll cap.')}</div>}
              <div className="queue-footer"><span><span className="tiny-green-dot" />{integrations?.jira_configured ? `${locale === 'fr' ? 'Vérification serveur toutes les' : 'Server checks every'} ${integrations.jira_monitor.interval_seconds}s${lastQueueSync ? ` · ${locale === 'fr' ? 'mise à jour' : 'updated'} ${lastQueueSync}` : ''}` : tr('Local fixtures only')}</span><span>{cases.length} {tr('total')}</span></div>
            </section>

            <section className="detail-panel panel">
              {!selected ? <div className="empty-detail">{tr('Choose a request from the queue.')}</div> : <>
                <div className="ticket-header"><div><div className="ticket-meta"><span>{selected.id}</span><span>·</span><span>{selected.channel}</span><span>·</span><span>{selected.created_at.slice(11, 16) || '09:20'}</span></div><h2>{selected.title}</h2><div className="requester-line"><div className="requester-avatar">{selected.requester.split(' ').map((part) => part[0]).join('').slice(0, 2)}</div><span><b>{selected.requester}</b><small>{selected.source === 'jira-service-management' ? tr('Jira requester') : tr('Requester · Synthetic profile')}</small></span><button className="icon-button subtle"><MoreHorizontal size={18} /></button></div></div>
                  <div className="ticket-actions"><span className={`priority ${priorityColor(selected.priority)}`}>{selected.priority}</span><button className="quiet-button">{selected.status}<ChevronDown size={14} /></button></div>
                </div>

                <div className="ticket-body"><div className="section-label"><MessageSquareText size={15} /> {tr('REQUEST DETAILS')}</div><p>{selected.body}</p><div className="impact-strip"><span>{tr('Reported impact')}</span><b>{selected.impact}</b></div><div className="tag-row">{selected.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div></div>

                <div className="workflow-card">
                  <div className="workflow-card-head"><div><div className="section-label"><Sparkles size={15} /> {tr('WORKFLOW STUDIO')}</div><h3>{tr('Support response workflow')}</h3><p>{tr('Specialist agents prepare a draft; a person makes the call.')}</p></div><div className="workflow-controls"><label className="candidate-select"><span>{tr('Best of')}</span><select value={candidateCount} onChange={(event) => setCandidateCount(Number(event.target.value))}><option value={1}>{tr('1 candidate')}</option><option value={2}>{tr('2 candidates')}</option><option value={3}>{tr('3 candidates')}</option></select></label><label className="council-toggle"><input type="checkbox" checked={councilEnabled} onChange={(event) => setCouncilEnabled(event.target.checked)} /><span>{tr('Model council')}</span></label><button className="run-button" onClick={startWorkflow} disabled={running || !jiraKnowledgeReady}>{running ? <span className="spinner" /> : <Play size={14} fill="currentColor" />}{running ? tr('Running') : tr('Run workflow')}</button></div></div>

                  {selected.source === 'jira-service-management' && !jiraKnowledgeReady && <div className="error-banner"><X size={15} />{tr('Jira triage is paused until local knowledge is explicitly allowlisted (KNOWLEDGE_DIR with .support-workflow-approved-sources.json).')} {integrations?.knowledge.message || ''}</div>}

                  {error && <div className="error-banner"><X size={15} />{tr(error)}</div>}

                  {!run && !running && <div className="workflow-empty"><div className="empty-orbit"><Sparkles size={19} /></div><div><b>{tr('Ready to triage this request')}</b><p>{tr('Run the workflow to classify, retrieve guidance, draft a reply, and open a human review.')}</p></div><div className="empty-stats"><span><b>9+</b> {tr('stages')}</span><span><b>3 + 3</b> {tr('work and review lanes')}</span></div></div>}
                  {running && <div className="running-state"><span className="spinner dark" /><div><b>{tr('Preparing a reviewable response')}</b><p>{tr('Independent roles are working through bounded steps.')}</p></div></div>}

                  {run && <>
                    <div className="run-summary"><div className="run-state"><span className={`run-state-dot ${run.status === 'awaiting_human' ? 'waiting' : 'done'}`} />{run.status === 'awaiting_human' ? tr('Awaiting your review') : run.human_decision}</div><div className="run-meta"><span><Clock3 size={13} /> {(run.elapsed_ms / 1000).toFixed(1)}s</span><span><Layers3 size={13} /> {run.candidate_count} {tr(run.candidate_count === 1 ? 'candidate' : 'candidates')} · {run.revision_count} {tr(run.revision_count === 1 ? 'revision' : 'revisions')}</span><span className={`mode-chip ${run.provider_mode}`}>{run.provider_mode === 'demo' ? tr('SIMULATED') : run.model_name}</span></div></div>

                    {selected.source === 'jira-service-management' && run.evidence.length === 0 && <div className="error-banner"><X size={15} />{tr('No approved knowledge source matched this request. The quality gate prevents publishing this draft.')}</div>}

                    <section className="investigation-panel" aria-labelledby="investigation-title">
                      <div className="section-label" id="investigation-title">{tr('PARALLEL INVESTIGATION · UNVERIFIED LEADS')}</div>
                      <div className="investigation-grid">{run.work_lanes.map((lane) => <article className="investigation-lane" key={lane.lane}><b>{lane.lane}</b><small>{lane.scope}</small><p>{lane.finding}</p>{lane.evidence_ids.length > 0 && <div className="investigation-evidence">{tr('Evidence to check:')} {lane.evidence_ids.join(', ')}</div>}{lane.unknowns.length > 0 && <div className="investigation-unknown">{tr('Unknown:')} {lane.unknowns.join(' · ')}</div>}</article>)}</div>
                    </section>

                    {run.plan_reviews?.length > 0 && <section className="plan-review-panel" aria-labelledby="plan-review-title">
                      <div className="plan-review-heading"><div><h4 id="plan-review-title">{tr('Architect → critic → revise')}</h4><p>{run.provider_mode === 'live' ? tr('Fresh-context critic · bounded revisions · deterministic 9/10 gate') : tr('Simulated rounds · no model calls')}</p></div><span className={run.plan_reviews[run.plan_reviews.length - 1].verdict === 'pass' ? 'plan-gate-pass' : 'plan-gate-stop'}>{run.plan_reviews[run.plan_reviews.length - 1].score.toFixed(1)} / 10</span></div>
                      {run.plan_reviews.map((review) => <article className={`plan-review-round ${review.verdict}`} key={review.round}>
                        <div className="plan-round-heading"><b>{review.origin === 'deterministic_fallback' ? `${tr('Safe fallback · Round')} ${review.round}` : `${tr('Round')} ${review.round}`}</b><span>{tr('Critic')} {review.critic_score.toFixed(1)} · {tr('gate')} {review.score.toFixed(1)}</span><strong>{tr(review.verdict === 'pass' ? 'Passed' : review.verdict === 'revise' ? 'Revising' : 'Stopped')}</strong></div>
                        <ol>{review.plan.map((step, index) => <li key={`${review.round}-${index}`}>{step}</li>)}</ol>
                        {(review.findings.length > 0 || review.missing_checks.length > 0 || review.deterministic_findings.length > 0) && <ul className="plan-review-findings">{review.findings.map((item, index) => <li key={`f-${review.round}-${index}`}>{item}</li>)}{review.missing_checks.map((item, index) => <li key={`m-${review.round}-${index}`}>{tr('Missing check:')} {item}</li>)}{review.deterministic_findings.map((item, index) => <li key={`d-${review.round}-${index}`}>{item}</li>)}</ul>}
                      </article>)}
                    </section>}

                    {run.verifier_findings.length > 0 && <section className="verifier-panel" aria-labelledby="verifier-title"><div className="section-label" id="verifier-title">{tr('INDEPENDENT VERIFIER')} · {run.provider_mode === 'live' ? tr('LIVE MODEL') : tr('DEMO MODE')}</div><ul>{run.verifier_findings.map((finding, index) => <li key={`${index}-${finding}`}>{finding}</li>)}</ul><small>{tr('The verifier cannot edit or approve the response. Findings are advisory and visible for human review.')}</small></section>}

                    <div className="process-track">
                      {run.stages.map((stage, index) => <div className={`process-step ${stage.status}`} key={stage.id}><div className="step-marker">{stage.status === 'complete' ? <Check size={12} /> : stage.status === 'pending' ? <UserRound size={12} /> : <span>{index + 1}</span>}</div><div className="step-copy"><b>{stage.label}</b><small>{stage.actor}</small><small className="step-detail">{stage.detail}</small></div>{index < run.stages.length - 1 && <div className="step-line" />}</div>)}
                    </div>

                    <section className="run-audit" aria-labelledby="run-audit-title">
                      <div className="run-audit-heading"><div><span className="section-label" id="run-audit-title">{tr('Local run activity')}</span><p>{tr('Metadata only · local operator · not tamper-evident')}</p></div><Clock3 size={15} /></div>
                      {runAuditLoading ? <p className="run-audit-empty" role="status">{tr('Loading run activity…')}</p> : runAuditError ? <p className="run-audit-empty" role="status">{tr('Run activity could not be loaded.')}</p> : runAuditEvents.length === 0
                        ? <p className="run-audit-empty">{tr('No recorded run activity yet.')}</p>
                        : <ol className="run-audit-list">{runAuditEvents.map((event) => <li key={event.event_id}>
                          <span className={`run-audit-mark ${event.event_type.startsWith('publish.') ? 'publish' : event.event_type.startsWith('decision.') ? 'decision' : ''}`} aria-hidden="true" />
                          <div className="run-audit-copy"><b>{tr(event.event_type)}</b><small><time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString(locale === 'fr' ? 'fr-FR' : 'en-US', { dateStyle: 'medium', timeStyle: 'short' })}</time>{event.to_status ? ` · ${tr(event.to_status)}` : ''}{typeof event.score === 'number' ? ` · ${event.score.toFixed(1)}/10` : ''}{event.decision_version !== undefined ? ` · ${tr('Approval version')} ${event.decision_version}` : ''}{event.outcome ? ` · ${tr(event.outcome)}` : ''}</small></div>
                        </li>)}</ol>}
                    </section>

                    <div className="result-grid">
                      <div className="result-main">
                        <div className="result-heading"><div><span className="section-label">{tr('RESPONSE DRAFT')}</span><h4>{tr('Suggested reply')}</h4></div><span className="draft-badge"><LockKeyhole size={12} /> {tr('Draft only')}</span></div>
                        <textarea className="draft-editor" value={draft || run.draft} onChange={(event) => setDraft(event.target.value)} readOnly={run.status !== 'awaiting_human'} aria-label={tr('Suggested customer response')} />
                        <div className="draft-footer"><span><FileSearch size={13} /> {tr('Grounded in')} {run.evidence.length} {tr(run.evidence.length === 1 ? 'knowledge source' : 'knowledge sources')}</span><span>{(draft || run.draft).length} {tr('characters')}</span></div>
                        <div className="evidence-list">{run.evidence.map((item) => <div className="evidence-item" key={item.id}><span className="evidence-icon"><FileSearch size={14} /></span><span><b>{item.title}</b><small>{item.id} · {item.category === 'confluence' ? `${tr('Confluence page')} · ${item.space_key ?? ''} · ${tr('Version')} ${item.source_version ?? '—'}` : item.id.startsWith('LOCAL-') ? tr('Operator knowledge') : tr('Synthetic demo knowledge')}{item.last_modified ? ` · ${item.last_modified}` : ''}</small></span><ArrowDownRight size={14} /></div>)}</div>
                      {run.status === 'awaiting_human' && <div className="human-actions"><div><span className="human-indicator"><UserRound size={14} /></span><span><b>{tr('Your decision')}</b><small>{tr('Saving an edit re-runs review; approval and external sending stay separate.')} {run.revision_count >= 3 ? tr('Revision limit reached; start a new workflow.') : ''}</small></span></div><div className="action-buttons"><button className="reject-button" onClick={() => submitDecision('reject')} disabled={decisionBusy}>{tr('Reject')}</button><button className="edit-button" onClick={() => submitDecision('edit')} disabled={decisionBusy || !run.draft.trim() || run.revision_count >= 3}>{tr('Save edit & re-review')}</button><button className="approve-button" onClick={() => submitDecision('approve')} disabled={decisionBusy || run.score < 9 || !run.draft.trim()}><Check size={14} /> {run.score < 9 ? tr('Below 9.0 gate') : tr('Approve draft')}</button></div></div>}
                        {run.human_decision && <div className={`decision-banner ${run.status}`}><CheckCheck size={15} />{run.human_decision}<span>{tr('· no customer send')}</span></div>}
                        {(run.status === 'approved' || run.status === 'edited') && selected.source === 'jira-service-management' && !run.ticket_comment_published && <div className="publish-confirm"><div><b>{tr('Separate confirmation required')}</b><small>{tr('This will add a public comment to')} {selected.external_key}. {tr('Review the exact text above first.')}</small></div><button className="approve-button" onClick={publishTicketReply} disabled={decisionBusy}><MessageSquareText size={13} /> {tr('Publish public reply')}</button></div>}
                        {run.ticket_comment_published && <div className="decision-banner approved"><CheckCheck size={15} />{tr('Public reply published to')} {selected.external_key}<span>{run.ticket_comment_id ? `${tr('· comment')} ${run.ticket_comment_id}` : ''}</span></div>}
                      </div>
                      <aside className="review-column">
                        <div className={`score-card ${run.score >= 9 ? 'pass' : 'revise'}`}><div className="score-card-head"><span>{tr('QUALITY GATE')}</span><Target size={15} /></div><div className="score-value">{run.score.toFixed(1)}<small>/10</small></div><div className="score-track"><span style={{ width: `${run.score * 10}%` }} /></div><p>{run.score >= 9 ? tr('Threshold reached · human review required') : tr('Below threshold · inspect findings')}</p></div>
                        <div className="review-list"><div className="section-label">{tr('REVIEW LANES')}</div>{run.reviewer_findings.map((item) => <div className="review-item" key={item.lane}><div className="review-item-top"><b>{item.lane}</b><span className={item.score >= 9 ? 'review-pass' : 'review-warn'}>{item.score.toFixed(1)}</span></div><ul>{item.findings.map((finding, index) => <li key={`${index}-${finding}`}>{finding}</li>)}</ul></div>)}</div>
                        {run.council_options.length > 0 && <div className="council-card"><div className="section-label"><Layers3 size={13} /> {run.council_options.every((option) => option.label.startsWith('Option ')) ? tr('BLIND THREE-MODEL COUNCIL') : tr('MODEL COUNCIL · SIMULATED OR UNAVAILABLE')}</div>{run.council_options.map((option) => <div className="council-option" key={option.label}><div><b>{option.label}</b><span>{option.score.toFixed(1)}</span></div><p>{option.proposal}</p><small>{option.rationale}</small><small>{option.criteria.join(' · ')}</small></div>)}<small className="council-disclaimer">{run.council_options.every((option) => option.label.startsWith('Option ')) ? tr('The independent judge sees anonymous proposals. Scores are advisory; no model can approve or send.') : tr('This is a simulation or incomplete configuration; no blind model judging occurred.')}</small></div>}
                        <div className="triage-note"><div className="section-label"><Activity size={14} /> {tr('INFERRED TRIAGE')}</div><div><span>{tr('Support tier')}</span><b>{run.support_level}</b></div><div><span>{tr('Category')}</span><b>{run.category}</b></div><div><span>{tr('Urgency')}</span><b>{run.urgency}</b></div><div><span>{tr('Confidence')}</span><b>{Math.round(run.confidence * 100)}%</b></div><small>{run.support_route_reason} · {tr('verify before action')}</small></div>
                      </aside>
                    </div>
                  </>}
                </div>
              </>}
            </section>
          </div>
          <footer className="page-footer"><span><ShieldCheck size={14} />{integrations?.jira_configured ? tr('Jira Service Management connected · human-reviewed') : tr('Synthetic data · local prototype')}</span><span>Support Workflow Studio <span className="footer-separator">/</span> v0.1</span></footer>
        </div>
      </main>

      {showIntake && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowIntake(false) }}><form className="intake-modal" onSubmit={submitIntake} lang={locale}><div className="modal-head"><div><span className="section-label">{tr('SYNTHETIC CASE INTAKE')}</span><h2>{tr('Add a lab request')}</h2></div><button className="icon-button" type="button" onClick={() => setShowIntake(false)} aria-label={tr('Close')}><X size={18} /></button></div><p>{tr('Use fictional details only. This local prototype is not approved for customer data.')}</p><label>{tr('Request title')}<input required minLength={4} value={intake.title} onChange={(event) => setIntake({ ...intake, title: event.target.value })} placeholder={tr('Example: Calendar sync stopped')} /></label><label>{tr('Fictional requester')}<input required minLength={2} value={intake.requester} onChange={(event) => setIntake({ ...intake, requester: event.target.value })} placeholder={locale === 'fr' ? 'Camille Martin' : 'Alex Morgan'} /></label><label>{tr('What happened?')}<textarea required minLength={15} value={intake.body} onChange={(event) => setIntake({ ...intake, body: event.target.value })} placeholder={tr('Describe the synthetic support issue...')} /></label><label>{tr('Reported impact')}<input value={intake.impact} onChange={(event) => setIntake({ ...intake, impact: event.target.value })} /></label><div className="modal-actions"><button className="quiet-button" type="button" onClick={() => setShowIntake(false)}>{tr('Cancel')}</button><button className="primary-button" type="submit"><Plus size={15} /> {tr('Add to lab')}</button></div></form></div>}
      {showSetup && <SetupPanel locale={locale} onClose={() => setShowSetup(false)} onSaved={() => void refreshSetupStatus()} />}
    </div>
  )
}
