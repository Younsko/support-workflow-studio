import { type ChangeEvent, type FormEvent, useEffect, useState } from 'react'
import { CheckCircle2, CircleAlert, PlugZap, ShieldCheck, X } from 'lucide-react'
import { translate, type AppLocale } from './i18n'
import { apiFetch } from './runnerAuth'

type SetupValues = {
  jira_base_url: string
  jira_email: string
  jira_service_desk_id: string
  jira_queue_id: string
  jira_token_configured: boolean
  knowledge_dir: string
  knowledge_provider: 'local' | 'confluence'
  confluence_base_url: string
  confluence_email: string
  confluence_space_keys: string
  confluence_api_token_configured: boolean
  confluence_ticket_search_allowed: boolean
  auto_triage_new_tickets: boolean
  poll_interval_seconds: number
  max_auto_triage_per_poll: number
  n1_keywords: string
  n3_plus_keywords: string
  n3_keywords: string
  model_provider: string
  model_name: string
  model_base_url: string
  model_api_key_configured: boolean
  external_model_disclosure_accepted: boolean
  private_local_mode: boolean
}
type JiraOption = { id: string; name: string; key?: string }

const emptySetup: SetupValues = {
  jira_base_url: '', jira_email: '', jira_service_desk_id: '', jira_queue_id: '',
  jira_token_configured: false, knowledge_dir: '', knowledge_provider: 'local',
  confluence_base_url: '', confluence_email: '', confluence_space_keys: '',
  confluence_api_token_configured: false, confluence_ticket_search_allowed: false,
  auto_triage_new_tickets: false,
  poll_interval_seconds: 60, max_auto_triage_per_poll: 5,
  n1_keywords: '', n3_plus_keywords: '', n3_keywords: '', model_provider: 'demo',
  model_name: 'Synthetic workflow model', model_base_url: '', model_api_key_configured: false,
  external_model_disclosure_accepted: false, private_local_mode: false,
}

function modelKeyTarget(provider: string, baseUrl: string) {
  const destination = provider === 'anthropic'
    ? 'https://api.anthropic.com/v1/messages'
    : baseUrl.trim().replace(/\/+$/, '')
  return `${provider}\0${destination}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`/api${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) } })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export default function SetupPanel({ onClose, onSaved, locale = 'en' }: { onClose: () => void; onSaved: () => void; locale?: AppLocale }) {
  const t = (value: string) => translate(locale, value)
  const [values, setValues] = useState<SetupValues>(emptySetup)
  const [savedModelTarget, setSavedModelTarget] = useState('')
  const [token, setToken] = useState('')
  const [modelKey, setModelKey] = useState('')
  const [confluenceToken, setConfluenceToken] = useState('')
  const [checkingConfluence, setCheckingConfluence] = useState(false)
  const [confluenceCheckMessage, setConfluenceCheckMessage] = useState('')
  const [clearModelKey, setClearModelKey] = useState(false)
  const [workspaces, setWorkspaces] = useState<JiraOption[]>([])
  const [queues, setQueues] = useState<JiraOption[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [discoveringWorkspaces, setDiscoveringWorkspaces] = useState(false)
  const [discoveringQueues, setDiscoveringQueues] = useState(false)
  const [manualIds, setManualIds] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState('')
  const [currentStep, setCurrentStep] = useState(0)
  const stepTitles = [t('Ticket source'), t('Approved knowledge'), t('Model provider'), t('Triage and escalation rules'), t('Review settings')]

  useEffect(() => {
    request<SetupValues>('/setup')
      .then((loaded) => {
        setValues(loaded)
        setSavedModelTarget(modelKeyTarget(loaded.model_provider, loaded.model_base_url))
      })
      .catch((reason: Error) => setError(t(reason.message)))
      .finally(() => setLoading(false))
  }, [])

  function update<K extends keyof SetupValues>(key: K, value: SetupValues[K]) {
    setValues((current) => ({ ...current, [key]: value }))
    setResult('')
  }

  function discoveryBody(serviceDeskId = values.jira_service_desk_id) {
    return {
      jira_base_url: values.jira_base_url,
      jira_email: values.jira_email,
      jira_api_token: token,
      service_desk_id: serviceDeskId,
    }
  }

  async function discoverWorkspaces() {
    setDiscoveringWorkspaces(true); setError(''); setQueues([])
    try {
      const found = await request<JiraOption[]>('/setup/jira/workspaces', {
        method: 'POST', body: JSON.stringify(discoveryBody('')),
      })
      setWorkspaces(found)
      if (values.jira_service_desk_id && found.some((item) => item.id === values.jira_service_desk_id)) {
        setDiscoveringQueues(true)
        try {
          const availableQueues = await request<JiraOption[]>('/setup/jira/queues', {
            method: 'POST', body: JSON.stringify(discoveryBody(values.jira_service_desk_id)),
          })
          setQueues(availableQueues)
        } catch (reason) {
          setError(reason instanceof Error ? t(reason.message) : t('Jira queues could not be loaded'))
        } finally { setDiscoveringQueues(false) }
      }
    } catch (reason) {
      setError(reason instanceof Error ? t(reason.message) : t('Jira service desks could not be loaded'))
    } finally { setDiscoveringWorkspaces(false) }
  }

  async function selectWorkspace(serviceDeskId: string) {
    update('jira_service_desk_id', serviceDeskId)
    update('jira_queue_id', '')
    setQueues([]); setError('')
    if (!serviceDeskId) return
    setDiscoveringQueues(true)
    try {
      const found = await request<JiraOption[]>('/setup/jira/queues', {
        method: 'POST', body: JSON.stringify(discoveryBody(serviceDeskId)),
      })
      setQueues(found)
    } catch (reason) {
      setError(reason instanceof Error ? t(reason.message) : t('Jira queues could not be loaded'))
    } finally { setDiscoveringQueues(false) }
  }

  async function checkConfluenceConnection() {
    setCheckingConfluence(true); setError(''); setConfluenceCheckMessage('')
    try {
      const check = await request<{ site_reachable: boolean; result_count: number }>('/setup/confluence/check', {
        method: 'POST', body: JSON.stringify({
          confluence_base_url: values.confluence_base_url,
          confluence_email: values.confluence_email,
          confluence_api_token: confluenceToken,
          confluence_space_keys: values.confluence_space_keys,
        }),
      })
      setConfluenceCheckMessage(check.site_reachable
        ? `${t('Confluence site reachable')} · ${t('No ticket search was sent')} · ${t('Selected spaces are not individually verified')}`
        : t('Confluence site could not be reached'))
    } catch (reason) {
      setError(reason instanceof Error ? t(reason.message) : t('Confluence connection check failed'))
    } finally { setCheckingConfluence(false) }
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (currentStep < 4) return
    setSaving(true); setError(''); setResult('')
    try {
      const outcome = await request<{ jira_connected: boolean; jira_queue_sample_count: number; knowledge: { mode: string; available: boolean; source_count: number }; model_provider: string; model_name: string }>('/setup', {
        method: 'PUT',
        body: JSON.stringify({ ...values, jira_api_token: token, confluence_api_token: confluenceToken, model_api_key: modelKey, clear_model_api_key: clearModelKey }),
      })
      setValues((current) => ({
        ...current,
        jira_token_configured: current.jira_token_configured || Boolean(token),
        model_api_key_configured: clearModelKey ? false : current.model_api_key_configured || Boolean(modelKey),
      }))
      setSavedModelTarget(modelKeyTarget(outcome.model_provider, values.model_base_url))
      setToken('')
      setModelKey('')
      setConfluenceToken('')
      setClearModelKey(false)
      const modelSummary = locale === 'fr'
        ? `Paramètres du modèle enregistrés pour ${outcome.model_provider} · ${outcome.model_name}. Aucun appel au modèle n’a été effectué.`
        : `Model settings saved for ${outcome.model_provider} · ${outcome.model_name}. No model request was sent.`
      setResult(outcome.jira_connected
        ? locale === 'fr'
          ? `File Jira vérifiée (${outcome.jira_queue_sample_count} ticket${outcome.jira_queue_sample_count === 1 ? '' : 's'} d’exemple). ${outcome.knowledge.mode === 'confluence' && outcome.knowledge.available ? 'Confluence configuré ; les termes minimisés seront envoyés lors du triage.' : outcome.knowledge.mode === 'local' && outcome.knowledge.available ? `${outcome.knowledge.source_count} sources locales approuvées prêtes.` : 'Configurez une source documentaire locale approuvée ou Confluence avant le triage Jira.'} ${modelSummary}`
          : `Jira queue verified (${outcome.jira_queue_sample_count} sample ticket${outcome.jira_queue_sample_count === 1 ? '' : 's'}). ${outcome.knowledge.mode === 'confluence' && outcome.knowledge.available ? 'Confluence configured; minimized terms will be sent during triage.' : outcome.knowledge.mode === 'local' && outcome.knowledge.available ? `${outcome.knowledge.source_count} approved local sources ready.` : 'Configure an approved local source or Confluence before Jira triage.'} ${modelSummary}`
        : `${t('Local settings saved.') } ${modelSummary}`)
      onSaved()
    } catch (reason) {
      setError(reason instanceof Error ? t(reason.message) : t('Settings could not be saved'))
    } finally { setSaving(false) }
  }

  const setField = (key: keyof SetupValues) => (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const raw = event.currentTarget.type === 'checkbox' ? (event.currentTarget as HTMLInputElement).checked : event.currentTarget.value
    if (key === 'poll_interval_seconds' || key === 'max_auto_triage_per_poll') update(key, Number(raw) as never)
    else update(key, raw as never)
  }
  const savedKeyMatchesTarget = savedModelTarget === modelKeyTarget(values.model_provider, values.model_base_url)
  const configuredRuleCount = [values.n1_keywords, values.n3_keywords, values.n3_plus_keywords]
    .flatMap((keywords) => keywords.split(',')).filter((keyword) => keyword.trim()).length
  const ticketSummary = values.private_local_mode
    ? t('Private local mode is active.')
    : values.jira_base_url
      ? `${values.jira_base_url}${values.jira_queue_id ? ` · ${t('Queue')} ${values.jira_queue_id}` : ''} · ${t('Configured · not tested')}`
      : t('Synthetic ticket queue · local demo')
  const knowledgeSummary = values.knowledge_provider === 'confluence'
    ? `${values.confluence_base_url || t('Confluence site not configured')} · ${values.confluence_space_keys || t('No spaces selected')} · ${values.confluence_ticket_search_allowed ? t('Ticket search disclosure accepted') : t('Ticket search disclosure required')} · ${confluenceCheckMessage || t('Connection not checked in this session')}`
    : values.knowledge_dir
      ? `${values.knowledge_dir} · ${t('Will be checked on save')}`
      : t('No custom folder · demo knowledge only')
  const modelConnectionSummary = values.model_provider === 'demo'
    ? t('Demo provider · no external model call')
    : values.model_provider === 'ollama-local'
      ? t('Local model · not tested')
      : values.model_api_key_configured && savedKeyMatchesTarget && !modelKey
        ? t('Model saved · not tested')
        : modelKey
          ? t('Key entered · will be saved; connection not tested')
        : t('Not configured')
  const modelNameSummary = `${values.model_provider} · ${values.model_name}`
  const automaticIntakeSummary = values.private_local_mode
    ? t('Disabled in private mode')
    : values.auto_triage_new_tickets
      ? values.jira_base_url ? t('Enabled') : t('Enabled after Jira connection')
      : t('Disabled')

  return (
    <div className="modal-backdrop setup-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <form className="setup-modal setup-wizard" onSubmit={save} lang={locale}>
      <div className="setup-header"><div><span className="section-label"><PlugZap size={13} /> {t('WORKSPACE SETUP')}</span><h2>{t('Connect your support workflow')}</h2><p>{t('Configure a ticket source, approved knowledge, triage rules, and a model provider.')}</p></div><button className="icon-button" type="button" onClick={onClose} aria-label={t('Close setup')}><X size={18} /></button></div>
        {loading ? <div className="setup-loading">{t('Loading local settings…')}</div> : <>
          <nav className="setup-wizard-progress" aria-label={t('Setup progress')}>
            <div className="setup-wizard-steps">{stepTitles.map((title, index) => <button type="button" key={title} className={currentStep === index ? 'active' : currentStep > index ? 'complete' : ''} aria-current={currentStep === index ? 'step' : undefined} onClick={() => { if (index <= currentStep) setCurrentStep(index) }}><span>{index + 1}</span>{title}</button>)}</div>
            <div className="setup-wizard-progress-track"><span style={{ width: `${(currentStep / (stepTitles.length - 1)) * 100}%` }} /></div>
            <small>{t('Step')} {currentStep + 1} {t('of')} {stepTitles.length} · {stepTitles[currentStep]}</small>
          </nav>
          {values.private_local_mode && <div className="setup-notice"><ShieldCheck size={16} /> {t('Private local mode is active.')} {t('External Jira connections are disabled by the server.')}</div>}
          <div className="setup-grid">
            {currentStep === 0 && <section className="setup-card"><div className="setup-card-title"><span>01</span><div><h3>{t('Ticket source')}</h3><p>{t('Jira Service Management Cloud · the app can find desks and queues you can access.')}</p></div></div>
              <label>{t('Jira site URL')}<input disabled={values.private_local_mode} value={values.jira_base_url} onChange={(event) => { if (event.target.value !== values.jira_base_url) { setWorkspaces([]); setQueues([]); update('jira_service_desk_id', ''); update('jira_queue_id', '') } setField('jira_base_url')(event) }} placeholder="https://example.atlassian.net" autoComplete="url" /></label>
              <div className="setup-two"><label>{t('Account email')}<input disabled={values.private_local_mode} value={values.jira_email} onChange={setField('jira_email')} placeholder="agent@example.com" autoComplete="username" /></label><label>{t('API token')}<input disabled={values.private_local_mode} type="password" value={token} onChange={(e) => { setToken(e.target.value); setResult('') }} placeholder={values.jira_token_configured ? t('Saved locally — leave blank to keep') : t('Paste Jira API token')} autoComplete="new-password" /></label></div>
              <div className="setup-discovery-actions"><button className="setup-discover-button" type="button" onClick={() => void discoverWorkspaces()} disabled={values.private_local_mode || discoveringWorkspaces || !values.jira_base_url || !values.jira_email || (!token && !values.jira_token_configured)}>{discoveringWorkspaces ? t('Loading service desks…') : t('Find service desks')}</button><button className="setup-manual-toggle" type="button" disabled={values.private_local_mode} onClick={() => setManualIds((current) => !current)}>{manualIds ? t('Choose from Jira instead') : t('Enter IDs manually')}</button></div>
              {manualIds
                ? <div className="setup-two"><label>{t('Service desk ID')}<input disabled={values.private_local_mode} inputMode="numeric" value={values.jira_service_desk_id} onChange={setField('jira_service_desk_id')} placeholder="1" /></label><label>{t('Queue ID')}<input disabled={values.private_local_mode} inputMode="numeric" value={values.jira_queue_id} onChange={setField('jira_queue_id')} placeholder="5" /></label></div>
                : <div className="setup-two"><label>{t('Service desk')}<select value={values.jira_service_desk_id} onChange={(event) => void selectWorkspace(event.target.value)} disabled={values.private_local_mode || !workspaces.length || discoveringQueues}><option value="">{discoveringQueues ? t('Loading queues…') : workspaces.length ? t('Choose a service desk') : t('Find service desks first')}</option>{values.jira_service_desk_id && !workspaces.some((item) => item.id === values.jira_service_desk_id) && <option value={values.jira_service_desk_id}>{t('Saved selection · ')}{values.jira_service_desk_id}</option>}{workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}{item.key ? ` · ${item.key}` : ''}</option>)}</select></label><label>{t('Queue')}<select value={values.jira_queue_id} onChange={(event) => update('jira_queue_id', event.target.value)} disabled={values.private_local_mode || !queues.length}><option value="">{queues.length ? t('Choose a queue') : t('Choose a service desk first')}</option>{values.jira_queue_id && !queues.some((item) => item.id === values.jira_queue_id) && <option value={values.jira_queue_id}>{t('Saved selection · ')}{values.jira_queue_id}</option>}{queues.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div>}
              <small className="setup-help">{t('The token is written only to this machine’s ignored .env file. It is never sent back to the browser.')}</small>
            </section>}
            {currentStep === 1 && <section className="setup-card"><div className="setup-card-title"><span>02</span><div><h3>{t('Approved knowledge')}</h3><p>{t('Choose a local allowlisted folder or a read-only Confluence Cloud source.')}</p></div></div>
              <label>{t('Knowledge source')}<select value={values.knowledge_provider} onChange={(event) => { const provider = event.target.value as 'local' | 'confluence'; update('knowledge_provider', provider); if (provider === 'local') update('confluence_ticket_search_allowed', false); setConfluenceCheckMessage('') }}><option value="local">{t('Local approved folder')}</option><option value="confluence" disabled={values.private_local_mode}>{t('Confluence Cloud · read-only')}</option></select></label>
              {values.knowledge_provider === 'local' ? <>
                <label>{t('Knowledge directory')}<input value={values.knowledge_dir} onChange={setField('knowledge_dir')} placeholder="C:\\support\\approved-runbooks" /></label>
                <div className="setup-notice subtle-notice"><ShieldCheck size={15} /> {t('Only explicitly allowlisted Markdown and text sources are used for ticket evidence.')}</div>
              </> : <>
                <div className="setup-two"><label>{t('Confluence Cloud site')}<input value={values.confluence_base_url} onChange={(event) => { update('confluence_base_url', event.target.value); update('confluence_ticket_search_allowed', false); setConfluenceCheckMessage('') }} placeholder="https://example.atlassian.net" autoComplete="url" /></label><label>{t('Account email')}<input value={values.confluence_email} onChange={(event) => { update('confluence_email', event.target.value); update('confluence_ticket_search_allowed', false); setConfluenceCheckMessage('') }} placeholder="agent@example.com" autoComplete="username" /></label></div>
                <label>{t('Confluence API token')}<input type="password" value={confluenceToken} onChange={(event) => { setConfluenceToken(event.target.value); update('confluence_ticket_search_allowed', false); setConfluenceCheckMessage('') }} placeholder={values.confluence_api_token_configured ? t('Saved locally — leave blank to keep') : t('Paste a Confluence API token')} autoComplete="new-password" /></label>
                <label>{t('Allowed space keys')}<input value={values.confluence_space_keys} onChange={(event) => { update('confluence_space_keys', event.target.value.toUpperCase()); update('confluence_ticket_search_allowed', false); setConfluenceCheckMessage('') }} placeholder="HELP, ITOPS" /><small className="setup-help">{t('Comma-separated keys limit searches to the selected spaces.')}</small></label>
                <div className="setup-discovery-actions"><button className="setup-discover-button" type="button" onClick={() => void checkConfluenceConnection()} disabled={values.private_local_mode || checkingConfluence || !values.confluence_base_url || !values.confluence_email || !values.confluence_space_keys || (!confluenceToken && !values.confluence_api_token_configured)}>{checkingConfluence ? t('Checking Confluence…') : t('Check Confluence connection')}</button>{confluenceCheckMessage && <span className="setup-inline-status" role="status">{confluenceCheckMessage}</span>}</div>
                <div className="setup-notice subtle-notice"><ShieldCheck size={15} /> {t('Connection check sends no ticket text. During triage, up to eight minimized search terms derived from the ticket are sent to Confluence. These terms are not anonymized and may include names or sensitive words. Page content is treated as untrusted evidence.')}</div>
                <label className="setup-checkbox"><input type="checkbox" checked={values.confluence_ticket_search_allowed} onChange={(event) => update('confluence_ticket_search_allowed', event.target.checked)} /> {t('I approve sending minimized ticket search terms to this Confluence site and the selected spaces')}</label>
                <small className="setup-help">{t('The API token stays in this machine’s ignored .env file. Saving does not contact Confluence; the connection check runs only when clicked.')}</small>
              </>}
              <h4>{t('Automatic intake')}</h4>
              <label className="setup-checkbox"><input type="checkbox" checked={values.auto_triage_new_tickets} onChange={setField('auto_triage_new_tickets')} /> {t('Automatically draft triage for tickets arriving after setup is enabled')}</label>
              <div className="setup-two"><label>{t('Poll interval (seconds)')}<input type="number" min={15} max={900} value={values.poll_interval_seconds} onChange={setField('poll_interval_seconds')} /></label><label>{t('Draft limit per poll')}<input type="number" min={1} max={10} value={values.max_auto_triage_per_poll} onChange={setField('max_auto_triage_per_poll')} /></label></div>
              <p className="setup-help">{t('Automatic intake creates drafts only. It never approves or publishes a reply.')}</p>
            </section>}
            {currentStep === 2 && <section className="setup-card"><div className="setup-card-title"><span>03</span><div><h3>{t('Model provider')}</h3><p>{t('Choose a local model or bring a provider key. Saving settings does not call the model.')}</p></div></div>
              <label>{t('Provider')}<select value={values.model_provider} onChange={(event) => { update('model_provider', event.target.value); update('external_model_disclosure_accepted', false); setModelKey(''); setResult('') }}>
                <option value="demo">{t('Demo · synthetic responses')}</option>
                <option value="openai-compatible" disabled={values.private_local_mode}>{t('OpenAI-compatible API · includes Groq')}</option>
                <option value="anthropic" disabled={values.private_local_mode}>{t('Anthropic API')}</option>
                <option value="ollama-local">{t('Ollama · this machine')}</option>
              </select></label>
              <label>{t('Model name')}<input value={values.model_name} onChange={setField('model_name')} placeholder={values.model_provider === 'demo' ? t('Synthetic workflow model') : t('Provider model identifier')} /></label>
              {(values.model_provider === 'openai-compatible' || values.model_provider === 'ollama-local') && <label>{t('API base URL')}<input value={values.model_base_url} onChange={(event) => { if (event.target.value !== values.model_base_url) { update('external_model_disclosure_accepted', false); setModelKey('') } setField('model_base_url')(event) }} placeholder={values.model_provider === 'ollama-local' ? 'http://127.0.0.1:11434' : 'https://api.groq.com/openai/v1'} /></label>}
              {(values.model_provider === 'openai-compatible' || values.model_provider === 'anthropic') && <>
                <label>{t('Model API key')}<input type="password" value={modelKey} onChange={(event) => { setModelKey(event.target.value); setClearModelKey(false); setResult('') }} placeholder={values.model_api_key_configured && savedKeyMatchesTarget ? t('Saved locally — leave blank to keep') : t('Enter a key for this provider and endpoint')} autoComplete="new-password" /></label>
                {values.model_api_key_configured && <>
                  <label className="setup-checkbox"><input type="checkbox" checked={clearModelKey} disabled={values.model_provider === 'openai-compatible' || values.model_provider === 'anthropic'} onChange={(event) => { setClearModelKey(event.target.checked); setModelKey('') }} /> {t('Remove saved model API key')}</label>
                  {(values.model_provider === 'openai-compatible' || values.model_provider === 'anthropic') && <small className="setup-help">{t('Switch to Demo or Ollama before removing the key used by the selected API provider.')}</small>}
                </>}
                <label className="setup-checkbox"><input type="checkbox" checked={values.external_model_disclosure_accepted} onChange={(event) => update('external_model_disclosure_accepted', event.target.checked)} /> {t('I understand that the selected external model provider will receive ticket content needed for a run')}</label>
                <div className="setup-notice"><CircleAlert size={15} /> {t('Live requests are not sent while saving. The external model receives ticket text only when a workflow run is started; review your provider terms and data policy first.')}</div>
              </>}
              {values.model_provider === 'ollama-local' && <div className="setup-notice subtle-notice"><ShieldCheck size={15} /> {t('Only a loopback Ollama URL is accepted. Ticket content stays on this machine when the runtime uses this local provider.')}</div>}
              {values.model_provider === 'demo' && <div className="setup-notice subtle-notice"><ShieldCheck size={15} /> {t('Demo mode uses synthetic responses and does not call an external model.')}</div>}
              <small className="setup-help">{t('API keys are written to this machine’s ignored .env file and are never returned to the browser. This local setup is not the hosted runner secret store.')}</small>
            </section>}
            {currentStep === 3 && <section className="setup-card setup-rules"><div className="setup-card-title"><span>04</span><div><h3>{t('Triage and escalation rules')}</h3><p>{t('Add comma-separated phrases to the built-in signals; matches set a provisional tier. Other tickets default to N2 investigation.')}</p></div></div>
              <label>{t('N1 · documented how-to or routine support')}<textarea rows={2} value={values.n1_keywords} onChange={setField('n1_keywords')} placeholder="how do i, how to, password reset, setup instructions, where can i" /></label>
              <label>{t('N3+ · immediate specialist review')}<textarea rows={3} value={values.n3_plus_keywords} onChange={setField('n3_plus_keywords')} placeholder="security incident, data loss, production outage, phishing" /></label>
              <label>{t('N3 · recurring or engineering review')}<textarea rows={3} value={values.n3_keywords} onChange={setField('n3_keywords')} placeholder="repeated, recurring, multiple users, regression, crash, bug" /></label>
              <div className="setup-notice subtle-notice"><CircleAlert size={15} /> {t('Rules are advisory classification. They do not trigger remediation or skip human review.')}</div>
            </section>}
            {currentStep === 4 && <section className="setup-card setup-review-card"><div className="setup-card-title"><span>✓</span><div><h3>{t('Configuration summary')}</h3><p>{t('Review these choices before saving. Saving stores local settings; it does not publish or send a support reply.')}</p></div></div>
              <dl className="setup-review-list">
                <div><dt>{t('Ticket source')}</dt><dd>{ticketSummary}</dd></div>
                <div><dt>{t('Approved knowledge')}</dt><dd>{knowledgeSummary}</dd></div>
                <div><dt>{t('Model provider')}</dt><dd>{modelNameSummary}<small>{modelConnectionSummary}</small></dd></div>
                <div><dt>{t('Automatic intake summary')}</dt><dd>{automaticIntakeSummary} · {values.poll_interval_seconds}s · {values.max_auto_triage_per_poll} {t('tickets per poll')}<small>{t('Automatic intake creates drafts only. It never approves or publishes a reply.')}</small></dd></div>
                <div><dt>{t('Triage and escalation rules')}</dt><dd>{configuredRuleCount ? `${configuredRuleCount} ${t('Rules configured')}` : t('No custom phrases')}</dd></div>
              </dl>
              <div className="setup-notice"><ShieldCheck size={15} /> {t('This local prototype saves setup values to its local .env file. External model calls happen only when you start a workflow.')}</div>
            </section>}
          </div>
          {error && <div className="setup-feedback error" role="alert">{t(error)}</div>}
          {result && <div className="setup-feedback success" role="status"><CheckCircle2 size={16} /> {result}</div>}
          <div className="setup-actions"><span>{currentStep === 4 ? t('Review settings') : `${t('Step')} ${currentStep + 1} ${t('of')} ${stepTitles.length}`}</span><div className="setup-wizard-buttons"><button className="quiet-button" type="button" onClick={onClose}>{t('Cancel')}</button>{currentStep > 0 && <button className="quiet-button" type="button" onClick={() => setCurrentStep((step) => Math.max(0, step - 1))}>{t('Back')}</button>}{currentStep < 4 ? <button className="primary-button" type="button" onClick={() => setCurrentStep((step) => Math.min(4, step + 1))}>{t('Continue')}</button> : <button className="primary-button" type="submit" disabled={saving}>{saving ? t('Saving settings…') : t('Review & save')}</button>}</div></div>
        </>}
      </form>
    </div>
  )
}
