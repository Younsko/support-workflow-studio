import { type ChangeEvent, type FormEvent, useEffect, useState } from 'react'
import { CheckCircle2, CircleAlert, PlugZap, ShieldCheck, X } from 'lucide-react'

type SetupValues = {
  jira_base_url: string
  jira_email: string
  jira_service_desk_id: string
  jira_queue_id: string
  jira_token_configured: boolean
  knowledge_dir: string
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
  jira_token_configured: false, knowledge_dir: '', auto_triage_new_tickets: false,
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
  const response = await fetch(`/api${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) } })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export default function SetupPanel({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [values, setValues] = useState<SetupValues>(emptySetup)
  const [savedModelTarget, setSavedModelTarget] = useState('')
  const [token, setToken] = useState('')
  const [modelKey, setModelKey] = useState('')
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

  useEffect(() => {
    request<SetupValues>('/setup')
      .then((loaded) => {
        setValues(loaded)
        setSavedModelTarget(modelKeyTarget(loaded.model_provider, loaded.model_base_url))
      })
      .catch((reason: Error) => setError(reason.message))
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
          setError(reason instanceof Error ? reason.message : 'Jira queues could not be loaded')
        } finally { setDiscoveringQueues(false) }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Jira service desks could not be loaded')
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
      setError(reason instanceof Error ? reason.message : 'Jira queues could not be loaded')
    } finally { setDiscoveringQueues(false) }
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    setSaving(true); setError(''); setResult('')
    try {
      const outcome = await request<{ jira_connected: boolean; jira_queue_sample_count: number; knowledge: { mode: string; available: boolean; source_count: number }; model_provider: string; model_name: string }>('/setup', {
        method: 'PUT',
        body: JSON.stringify({ ...values, jira_api_token: token, model_api_key: modelKey, clear_model_api_key: clearModelKey }),
      })
      setValues((current) => ({
        ...current,
        jira_token_configured: current.jira_token_configured || Boolean(token),
        model_api_key_configured: clearModelKey ? false : current.model_api_key_configured || Boolean(modelKey),
      }))
      setSavedModelTarget(modelKeyTarget(outcome.model_provider, values.model_base_url))
      setToken('')
      setModelKey('')
      setClearModelKey(false)
      const modelSummary = `Model settings saved for ${outcome.model_provider} · ${outcome.model_name}. No model request was sent.`
      setResult(outcome.jira_connected
        ? `Jira queue verified (${outcome.jira_queue_sample_count} sample ticket${outcome.jira_queue_sample_count === 1 ? '' : 's'}). ${outcome.knowledge.mode === 'local' && outcome.knowledge.available ? `${outcome.knowledge.source_count} approved local sources ready.` : 'Select an approved local knowledge folder before Jira triage.'} ${modelSummary}`
        : `Local settings saved. ${modelSummary}`)
      onSaved()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Settings could not be saved')
    } finally { setSaving(false) }
  }

  const setField = (key: keyof SetupValues) => (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const raw = event.currentTarget.type === 'checkbox' ? (event.currentTarget as HTMLInputElement).checked : event.currentTarget.value
    if (key === 'poll_interval_seconds' || key === 'max_auto_triage_per_poll') update(key, Number(raw) as never)
    else update(key, raw as never)
  }
  const savedKeyMatchesTarget = savedModelTarget === modelKeyTarget(values.model_provider, values.model_base_url)

  return (
    <div className="modal-backdrop setup-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <form className="setup-modal" onSubmit={save}>
      <div className="setup-header"><div><span className="section-label"><PlugZap size={13} /> WORKSPACE SETUP</span><h2>Connect your support workflow</h2><p>Configure a ticket source, approved knowledge, triage rules, and a model provider.</p></div><button className="icon-button" type="button" onClick={onClose} aria-label="Close setup"><X size={18} /></button></div>
        {loading ? <div className="setup-loading">Loading local settings…</div> : <>
          {values.private_local_mode && <div className="setup-notice"><ShieldCheck size={16} /> Private local mode is active. External Jira connections are disabled by the server.</div>}
          <div className="setup-grid">
            <section className="setup-card"><div className="setup-card-title"><span>01</span><div><h3>Ticket source</h3><p>Jira Service Management Cloud · the app can find desks and queues you can access.</p></div></div>
              <label>Jira site URL<input disabled={values.private_local_mode} value={values.jira_base_url} onChange={(event) => { if (event.target.value !== values.jira_base_url) { setWorkspaces([]); setQueues([]); update('jira_service_desk_id', ''); update('jira_queue_id', '') } setField('jira_base_url')(event) }} placeholder="https://example.atlassian.net" autoComplete="url" /></label>
              <div className="setup-two"><label>Account email<input disabled={values.private_local_mode} value={values.jira_email} onChange={setField('jira_email')} placeholder="agent@example.com" autoComplete="username" /></label><label>API token<input disabled={values.private_local_mode} type="password" value={token} onChange={(e) => { setToken(e.target.value); setResult('') }} placeholder={values.jira_token_configured ? 'Saved locally — leave blank to keep' : 'Paste Jira API token'} autoComplete="new-password" /></label></div>
              <div className="setup-discovery-actions"><button className="setup-discover-button" type="button" onClick={() => void discoverWorkspaces()} disabled={values.private_local_mode || discoveringWorkspaces || !values.jira_base_url || !values.jira_email || (!token && !values.jira_token_configured)}>{discoveringWorkspaces ? 'Loading service desks…' : 'Find service desks'}</button><button className="setup-manual-toggle" type="button" disabled={values.private_local_mode} onClick={() => setManualIds((current) => !current)}>{manualIds ? 'Choose from Jira instead' : 'Enter IDs manually'}</button></div>
              {manualIds
                ? <div className="setup-two"><label>Service desk ID<input disabled={values.private_local_mode} inputMode="numeric" value={values.jira_service_desk_id} onChange={setField('jira_service_desk_id')} placeholder="1" /></label><label>Queue ID<input disabled={values.private_local_mode} inputMode="numeric" value={values.jira_queue_id} onChange={setField('jira_queue_id')} placeholder="5" /></label></div>
                : <div className="setup-two"><label>Service desk<select value={values.jira_service_desk_id} onChange={(event) => void selectWorkspace(event.target.value)} disabled={values.private_local_mode || !workspaces.length || discoveringQueues}><option value="">{discoveringQueues ? 'Loading queues…' : workspaces.length ? 'Choose a service desk' : 'Find service desks first'}</option>{values.jira_service_desk_id && !workspaces.some((item) => item.id === values.jira_service_desk_id) && <option value={values.jira_service_desk_id}>Saved selection · {values.jira_service_desk_id}</option>}{workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}{item.key ? ` · ${item.key}` : ''}</option>)}</select></label><label>Queue<select value={values.jira_queue_id} onChange={(event) => update('jira_queue_id', event.target.value)} disabled={values.private_local_mode || !queues.length}><option value="">{queues.length ? 'Choose a queue' : 'Choose a service desk first'}</option>{values.jira_queue_id && !queues.some((item) => item.id === values.jira_queue_id) && <option value={values.jira_queue_id}>Saved selection · {values.jira_queue_id}</option>}{queues.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div>}
              <small className="setup-help">The token is written only to this machine’s ignored .env file. It is never sent back to the browser.</small>
            </section>
            <section className="setup-card"><div className="setup-card-title"><span>02</span><div><h3>Approved knowledge</h3><p>Use a local folder with the source allowlist file.</p></div></div>
              <label>Knowledge directory<input value={values.knowledge_dir} onChange={setField('knowledge_dir')} placeholder="C:\\support\\approved-runbooks" /></label>
              <div className="setup-notice subtle-notice"><ShieldCheck size={15} /> Only explicitly allowlisted Markdown and text sources are used for ticket evidence.</div>
              <h4>Automatic intake</h4>
              <label className="setup-checkbox"><input type="checkbox" checked={values.auto_triage_new_tickets} onChange={setField('auto_triage_new_tickets')} /> Automatically draft triage for tickets arriving after setup is enabled</label>
              <div className="setup-two"><label>Poll interval (seconds)<input type="number" min={15} max={900} value={values.poll_interval_seconds} onChange={setField('poll_interval_seconds')} /></label><label>Draft limit per poll<input type="number" min={1} max={10} value={values.max_auto_triage_per_poll} onChange={setField('max_auto_triage_per_poll')} /></label></div>
              <p className="setup-help">Automatic intake creates drafts only. It never approves or publishes a reply.</p>
            </section>
            <section className="setup-card setup-rules"><div className="setup-card-title"><span>03</span><div><h3>Triage and escalation rules</h3><p>Add comma-separated phrases to the built-in signals; matches set a provisional tier. Other tickets default to N2 investigation.</p></div></div>
              <label>N1 · documented how-to or routine support<textarea rows={2} value={values.n1_keywords} onChange={setField('n1_keywords')} placeholder="how do i, how to, password reset, setup instructions, where can i" /></label>
              <label>N3+ · immediate specialist review<textarea rows={3} value={values.n3_plus_keywords} onChange={setField('n3_plus_keywords')} placeholder="security incident, data loss, production outage, phishing" /></label>
              <label>N3 · recurring or engineering review<textarea rows={3} value={values.n3_keywords} onChange={setField('n3_keywords')} placeholder="repeated, recurring, multiple users, regression, crash, bug" /></label>
              <div className="setup-notice subtle-notice"><CircleAlert size={15} /> Rules are advisory classification. They do not trigger remediation or skip human review.</div>
            </section>
            <section className="setup-card"><div className="setup-card-title"><span>04</span><div><h3>Model provider</h3><p>Choose a local model or bring a provider key. Saving settings does not call the model.</p></div></div>
              <label>Provider<select value={values.model_provider} onChange={(event) => { update('model_provider', event.target.value); update('external_model_disclosure_accepted', false); setModelKey(''); setResult('') }}>
                <option value="demo">Demo · synthetic responses</option>
                <option value="openai-compatible" disabled={values.private_local_mode}>OpenAI-compatible API · includes Groq</option>
                <option value="anthropic" disabled={values.private_local_mode}>Anthropic API</option>
                <option value="ollama-local">Ollama · this machine</option>
              </select></label>
              <label>Model name<input value={values.model_name} onChange={setField('model_name')} placeholder={values.model_provider === 'demo' ? 'Synthetic workflow model' : 'Provider model identifier'} /></label>
              {(values.model_provider === 'openai-compatible' || values.model_provider === 'ollama-local') && <label>API base URL<input value={values.model_base_url} onChange={(event) => { if (event.target.value !== values.model_base_url) { update('external_model_disclosure_accepted', false); setModelKey('') } setField('model_base_url')(event) }} placeholder={values.model_provider === 'ollama-local' ? 'http://127.0.0.1:11434' : 'https://api.groq.com/openai/v1'} /></label>}
              {(values.model_provider === 'openai-compatible' || values.model_provider === 'anthropic') && <>
                <label>Model API key<input type="password" value={modelKey} onChange={(event) => { setModelKey(event.target.value); setClearModelKey(false); setResult('') }} placeholder={values.model_api_key_configured && savedKeyMatchesTarget ? 'Saved locally — leave blank to keep' : 'Enter a key for this provider and endpoint'} autoComplete="new-password" /></label>
                {values.model_api_key_configured && <>
                  <label className="setup-checkbox"><input type="checkbox" checked={clearModelKey} disabled={values.model_provider === 'openai-compatible' || values.model_provider === 'anthropic'} onChange={(event) => { setClearModelKey(event.target.checked); setModelKey('') }} /> Remove saved model API key</label>
                  {(values.model_provider === 'openai-compatible' || values.model_provider === 'anthropic') && <small className="setup-help">Switch to Demo or Ollama before removing the key used by the selected API provider.</small>}
                </>}
                <label className="setup-checkbox"><input type="checkbox" checked={values.external_model_disclosure_accepted} onChange={(event) => update('external_model_disclosure_accepted', event.target.checked)} /> I understand that the selected external model provider will receive ticket content needed for a run</label>
                <div className="setup-notice"><CircleAlert size={15} /> Live requests are not sent while saving. The external model receives ticket text only when a workflow run is started; review your provider terms and data policy first.</div>
              </>}
              {values.model_provider === 'ollama-local' && <div className="setup-notice subtle-notice"><ShieldCheck size={15} /> Only a loopback Ollama URL is accepted. Ticket content stays on this machine when the runtime uses this local provider.</div>}
              {values.model_provider === 'demo' && <div className="setup-notice subtle-notice"><ShieldCheck size={15} /> Demo mode uses synthetic responses and does not call an external model.</div>}
              <small className="setup-help">API keys are written to this machine’s ignored .env file and are never returned to the browser. This local setup is not the hosted runner secret store.</small>
            </section>
          </div>
          {error && <div className="setup-feedback error" role="alert">{error}</div>}
          {result && <div className="setup-feedback success" role="status"><CheckCircle2 size={16} /> {result}</div>}
          <div className="setup-actions"><span>Changes are stored in the local ignored <code>.env</code> file.</span><button className="quiet-button" type="button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={saving}>{saving ? 'Saving settings…' : 'Save settings'}</button></div>
        </>}
      </form>
    </div>
  )
}
