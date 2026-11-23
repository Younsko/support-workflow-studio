import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDown, ArrowDownUp, ArrowLeft, ArrowRight, ArrowUp, Bot, Check, ChevronDown,
  CircleAlert, ClipboardCheck, Clock3, FileCheck2, FileSearch,
  GripVertical, History, Inbox, Languages, LockKeyhole, Minus, Play, Plus, Search, Settings2, ShieldCheck,
  Save, Trash2, UserRoundCheck, Waypoints, X,
} from 'lucide-react'
import './WorkflowBuilder.css'

export type WorkflowBuilderLocale = 'en' | 'fr'

type NodeKind = 'trigger' | 'triage' | 'knowledge' | 'agent' | 'verify' | 'approval' | 'publish'
type FlowNode = { id: string; kind: NodeKind; title: string; subtitle: string; localeGenerated?: boolean; definitionType?: string; role?: string; provider?: string; modelRef?: string; allowedTools?: string[]; active?: boolean }
type DefinitionNode = { id: string; type: string; name: string; config: Record<string, unknown> }
type WorkflowDefinition = {
  schema_version: 1; id: string; name: string; version: number; status: 'draft'
  trigger_node_id: string; nodes: DefinitionNode[]; connections: { id: string; source: string; target: string }[]
}
type WorkflowBundle = {
  draft: { definition: WorkflowDefinition; revision: number; updated_at: string } | null
  published: { definition: Omit<WorkflowDefinition, 'status'> & { status: 'published' }; published_at: string; content_hash: string }[]
}
type PublishedVersion = WorkflowBundle['published'][number]

type Props = { locale?: WorkflowBuilderLocale; onLocaleChange?: (locale: WorkflowBuilderLocale) => void; onOpenConnections?: () => void; onRunPublishedWorkflow?: (workflowId: string, version: number, contentHash: string) => Promise<void>; onOpenRuns?: () => void; canRunPublishedWorkflow?: boolean; runningPublishedWorkflow?: boolean; executionError?: string; connectionsRevision?: number }
type ConnectionBindings = {
  jiraReady: boolean; knowledgeReady: boolean; knowledgeMode: string
  modelReady: boolean; provider: string; model: string; modelRef: string
}

const copy = {
  en: {
    eyebrow: 'WORKFLOW STUDIO', title: 'Support intake', crumb: 'Workflows', draft: 'Draft · local',
    subtitle: 'Build a reviewable support flow, step by step.', trigger: 'Trigger', action: 'Action', ai: 'AI agent', review: 'Review',
    addNode: 'Add a step', palette: 'Step library', search: 'Search steps', canvas: 'Workflow canvas',
    zoomOut: 'Zoom out', zoomIn: 'Zoom in', fit: 'Fit workflow', inspector: 'Step settings',
    moveEarlier: 'Move step earlier', moveLater: 'Move step later', stepOrder: 'Step order',
    orderHint: 'The ticket trigger stays first. Reordering edits the graph; only the supported starter sequence can run.',
    selectNode: 'Select a step to view its settings.', name: 'Name', description: 'Description', model: 'Model provider',
    tools: 'Approved tools', toolKnowledge: 'Search approved knowledge', toolTicket: 'Read ticket details', role: 'Agent role', approvalGate: 'Approval gate', on: 'Required', requiredForWrite: 'Required before any external reply',
    localNotice: 'Published starter flows can run locally. Team access and hosted runner are not connected yet.',
    save: 'Save draft', publishAction: 'Publish version', saved: 'Saved locally', saving: 'Saving…', publishing: 'Publishing…',
    publishBlocked: 'This flow is not ready to publish. Configure its connections and model first.',
    unsupportedGraph: 'This version has branches or nodes this editor cannot preserve. It is read-only here; saving is blocked to prevent data loss.',
    concurrentDraft: 'A draft was created in another tab. Reload the workflow before saving to avoid overwriting those changes.',
    validate: 'Validate flow', add: 'Add step', close: 'Close', remove: 'Remove step', output: 'Output',
    ticket: 'Ticket received', ticketSub: 'New request · source connector', triage: 'Normalize & triage', triageSub: 'Classify, prioritize, route',
    knowledge: 'Search knowledge', knowledgeSub: 'Approved local support sources', agent: 'Draft response', agentSub: 'Support agent · BYOK model',
    verify: 'Independent check', verifySub: 'Evidence, policy & completeness', approval: 'Human approval', approvalSub: 'Review draft before release',
    publish: 'Publish response', publishSub: 'Send reply to source ticket',
    nodeTypes: ['Ticket trigger', 'Normalize & triage', 'Knowledge search', 'AI agent', 'Independent verifier', 'Human approval', 'Publish reply'],
    validationTitle: 'Flow validation', valid: 'The flow has one entry point and an approval gate before publishing.',
    invalid: 'Check the flow before using it.', triggerMissing: 'Add a ticket trigger.', approvalMissing: 'Put a human approval step before publishing.', disconnected: 'Every step must be connected to the flow.',
    removeConfirm: 'Remove this step?', noTools: 'Execution currently allows approved knowledge search only.', modelLocal: 'Choose a configured model',
    stepCount: 'steps', runTab: 'Runs', workflowTab: 'Workflow', connections: 'Connections', status: 'Not published',
    configure: 'Configure', jiraConnected: 'Jira configured', jiraMissing: 'Jira setup needed',
    knowledgeConnected: 'Knowledge ready', knowledgeDemo: 'Demo knowledge', knowledgeMissing: 'Knowledge setup needed',
    modelConfigured: 'Settings saved · connection not tested', modelDemo: 'Synthetic demo', modelMissing: 'Model setup needed',
    history: 'History', publishedHistory: 'Published versions', noPublishedVersions: 'No published versions yet.',
    version: 'Version', publishedAt: 'Published', contentHash: 'Content hash', latestVersion: 'Latest version',
    forkLatest: 'Fork latest as draft', forking: 'Creating draft…', historyLoading: 'Loading published versions…',
    historyLoadError: 'Could not load published versions. Try again.', closeHistory: 'Close version history',
    forkConfirm: 'Create a new editable draft from the latest published version? Unsaved editor changes will be replaced.',
    forkCreated: 'A new draft was created from the latest published version.',
    draftAlreadyExists: 'A draft already exists on the server. Reload the workflow before continuing to avoid overwriting it.',
    forkFailed: 'Could not create a draft from the latest published version.',
    executionDisclosure: 'Only the exact linear starter flow can execute. Runs create a reviewed draft; external publication remains a separate human action.',
    runLatest: 'Run on selected ticket', runningFlow: 'Starting run…', runUnavailable: 'Publish a supported flow and configure Jira, knowledge, and a model before running.',
  },
  fr: {
    eyebrow: 'STUDIO DE WORKFLOWS', title: 'Entrée du support', crumb: 'Workflows', draft: 'Brouillon · local',
    subtitle: 'Construisez un flux support vérifiable, étape par étape.', trigger: 'Déclencheur', action: 'Action', ai: 'Agent IA', review: 'Revue',
    addNode: 'Ajouter une étape', palette: 'Bibliothèque d’étapes', search: 'Rechercher une étape', canvas: 'Canvas du workflow',
    zoomOut: 'Dézoomer', zoomIn: 'Zoomer', fit: 'Ajuster le workflow', inspector: 'Paramètres de l’étape',
    moveEarlier: 'Déplacer l’étape plus tôt', moveLater: 'Déplacer l’étape plus tard', stepOrder: 'Ordre des étapes',
    orderHint: 'Le déclencheur reste en premier. Le réordonnancement modifie le graphe ; seul le flux de démarrage pris en charge peut être exécuté.',
    selectNode: 'Sélectionnez une étape pour afficher ses paramètres.', name: 'Nom', description: 'Description', model: 'Fournisseur du modèle',
    tools: 'Outils autorisés', toolKnowledge: 'Rechercher dans les sources approuvées', toolTicket: 'Lire les détails du ticket', role: 'Rôle de l’agent', approvalGate: 'Validation humaine', on: 'Requise', requiredForWrite: 'Requise avant toute réponse externe',
    localNotice: 'Les flux de démarrage publiés peuvent être exécutés en local. Les accès d’équipe et le runner hébergé ne sont pas encore connectés.',
    save: 'Enregistrer le brouillon', publishAction: 'Publier la version', saved: 'Enregistré localement', saving: 'Enregistrement…', publishing: 'Publication…',
    publishBlocked: 'Ce flux ne peut pas être publié. Configurez ses connexions et son modèle.',
    unsupportedGraph: 'Cette version contient des branches ou des étapes que cet éditeur ne sait pas préserver. Elle est en lecture seule ici; l’enregistrement est bloqué.',
    concurrentDraft: 'Un brouillon a été créé dans un autre onglet. Rechargez le workflow avant d’enregistrer pour éviter d’écraser ses modifications.',
    validate: 'Valider le flux', add: 'Ajouter', close: 'Fermer', remove: 'Supprimer l’étape', output: 'Résultat',
    ticket: 'Ticket reçu', ticketSub: 'Nouvelle demande · connecteur ITSM', triage: 'Normalisation et triage', triageSub: 'Catégoriser, prioriser, router',
    knowledge: 'Recherche documentaire', knowledgeSub: 'Sources locales de support approuvées', agent: 'Rédiger une réponse', agentSub: 'Agent support · modèle BYOK',
    verify: 'Vérification indépendante', verifySub: 'Preuves, règles et complétude', approval: 'Validation humaine', approvalSub: 'Relire le brouillon avant envoi',
    publish: 'Publier la réponse', publishSub: 'Répondre sur le ticket source',
    nodeTypes: ['Déclencheur ticket', 'Normalisation et triage', 'Recherche documentaire', 'Agent IA', 'Vérificateur indépendant', 'Validation humaine', 'Publier la réponse'],
    validationTitle: 'Validation du flux', valid: 'Le flux a un point d’entrée et une validation avant publication.',
    invalid: 'Corrigez le flux avant de l’utiliser.', triggerMissing: 'Ajoutez un déclencheur de ticket.', approvalMissing: 'Placez une validation humaine avant la publication.', disconnected: 'Toutes les étapes doivent être reliées au flux.',
    removeConfirm: 'Supprimer cette étape ?', noTools: 'Seule la recherche documentaire approuvée est actuellement exécutable.', modelLocal: 'Choisissez un modèle configuré',
    stepCount: 'étapes', runTab: 'Exécutions', workflowTab: 'Workflow', connections: 'Connexions', status: 'Non publié',
    configure: 'Configurer', jiraConnected: 'Jira configuré', jiraMissing: 'Jira à configurer',
    knowledgeConnected: 'Documentation prête', knowledgeDemo: 'Documentation démo', knowledgeMissing: 'Documentation à configurer',
    modelConfigured: 'Paramètres enregistrés · connexion non testée', modelDemo: 'Démo synthétique', modelMissing: 'Modèle à configurer',
    history: 'Historique', publishedHistory: 'Versions publiées', noPublishedVersions: 'Aucune version publiée pour le moment.',
    version: 'Version', publishedAt: 'Publiée', contentHash: 'Empreinte du contenu', latestVersion: 'Dernière version',
    forkLatest: 'Créer un brouillon depuis la dernière version', forking: 'Création du brouillon…', historyLoading: 'Chargement des versions publiées…',
    historyLoadError: 'Impossible de charger les versions publiées. Réessayez.', closeHistory: 'Fermer l’historique des versions',
    forkConfirm: 'Créer un brouillon modifiable depuis la dernière version publiée ? Les modifications non enregistrées seront remplacées.',
    forkCreated: 'Un nouveau brouillon a été créé depuis la dernière version publiée.',
    draftAlreadyExists: 'Un brouillon existe déjà sur le serveur. Rechargez le workflow avant de continuer pour éviter de l’écraser.',
    forkFailed: 'Impossible de créer un brouillon depuis la dernière version publiée.',
    executionDisclosure: 'Seul le flux de démarrage linéaire exact est exécutable. Une exécution crée un brouillon à relire ; l’envoi externe reste une action humaine séparée.',
    runLatest: 'Exécuter sur le ticket sélectionné', runningFlow: 'Démarrage…', runUnavailable: 'Publiez un flux pris en charge et configurez Jira, la documentation et un modèle.',
  },
} as const

function defaultNodeText(kind: NodeKind, t: (typeof copy)[WorkflowBuilderLocale]) {
  const text = {
    trigger: { title: t.ticket, subtitle: t.ticketSub }, triage: { title: t.triage, subtitle: t.triageSub },
    knowledge: { title: t.knowledge, subtitle: t.knowledgeSub }, agent: { title: t.agent, subtitle: t.agentSub },
    verify: { title: t.verify, subtitle: t.verifySub }, approval: { title: t.approval, subtitle: t.approvalSub },
    publish: { title: t.publish, subtitle: t.publishSub },
  }
  return text[kind]
}

const makeStarter = (t: (typeof copy)[WorkflowBuilderLocale]): FlowNode[] => (['trigger', 'triage', 'knowledge', 'agent', 'verify', 'approval', 'publish'] as NodeKind[]).map((kind) => ({
  id: ({ trigger: 'ticket', triage: 'triage', knowledge: 'knowledge', agent: 'agent', verify: 'verify', approval: 'approval', publish: 'publish' })[kind],
  kind, ...defaultNodeText(kind, t), localeGenerated: true,
  ...(kind === 'agent' ? { role: 'Support analyst', provider: '', modelRef: 'unconfigured-model', allowedTools: ['knowledge.search'] } : {}),
}))

const icons = {
  trigger: Inbox, triage: ArrowDownUp, knowledge: FileSearch, agent: Bot,
  verify: ShieldCheck, approval: UserRoundCheck, publish: ArrowRight,
} satisfies Record<NodeKind, typeof Inbox>

const typeColors: Record<NodeKind, string> = {
  trigger: 'blue', triage: 'slate', knowledge: 'violet', agent: 'amber',
  verify: 'green', approval: 'rose', publish: 'green',
}

const workflowId = 'support-intake'
const nodeTypeByKind: Record<NodeKind, string> = {
  trigger: 'ticket.trigger', triage: 'support.triage', knowledge: 'knowledge.search',
  agent: 'ai.agent', verify: 'review.verify', approval: 'review.human_approval',
  publish: 'itsm.publish',
}

function isEditableLinear(definition: Pick<WorkflowDefinition, 'nodes' | 'connections' | 'trigger_node_id'>): boolean {
  if (definition.nodes.length < 1 || definition.connections.length !== definition.nodes.length - 1) return false
  const triggerNodes = definition.nodes.filter((node) => node.type === 'ticket.trigger')
  if (triggerNodes.length !== 1 || definition.trigger_node_id !== triggerNodes[0].id) return false
  if (definition.nodes.some((node) => !Object.values(nodeTypeByKind).includes(node.type) && node.type !== 'ticket.normalize')) return false
  return definition.connections.every((edge, index) =>
    edge.source === definition.nodes[index].id && edge.target === definition.nodes[index + 1].id,
  )
}

function toDefinition(nodes: FlowNode[], version: number, bindings: ConnectionBindings | null): WorkflowDefinition {
  const definitionNodes: DefinitionNode[] = nodes.map((node) => {
    let config: Record<string, unknown> = {}
    if (node.kind === 'trigger') config = { connection_ref: bindings?.jiraReady ? 'jira-service-management' : 'unconfigured-jsm-connection' }
    if (node.kind === 'knowledge') config = { source_ref: bindings?.knowledgeReady ? bindings.knowledgeMode === 'local' ? 'local-approved-knowledge' : bindings.knowledgeMode === 'confluence' ? 'confluence-approved-knowledge' : 'unconfigured-knowledge-source' : 'unconfigured-knowledge-source' }
    if (node.kind === 'agent') {
      const selectedProviderMatches = !node.provider || node.provider === bindings?.provider
      config = {
        role: node.role || '', instructions: node.subtitle || 'Draft a response grounded in approved sources.',
        model_ref: bindings?.modelReady && selectedProviderMatches ? bindings.modelRef : 'unconfigured-model',
        provider: selectedProviderMatches ? node.provider || bindings?.provider || '' : node.provider || '',
        allowed_tools: node.allowedTools ?? [],
      }
    }
    if (node.kind === 'approval') config = { required_role: 'Approver' }
    if (node.kind === 'publish') config = { connection_ref: bindings?.jiraReady ? 'jira-service-management' : 'unconfigured-jsm-connection', operation: 'public_reply' }
    return { id: node.id, type: node.definitionType ?? nodeTypeByKind[node.kind], name: node.title, config }
  })
  return {
    schema_version: 1, id: workflowId, name: 'Support intake', version, status: 'draft',
    trigger_node_id: nodes.find((node) => node.kind === 'trigger')?.id ?? 'missing-trigger',
    nodes: definitionNodes,
    connections: nodes.slice(1).map((node, index) => ({
      id: `edge-${index + 1}`, source: nodes[index].id, target: node.id,
    })),
  }
}

function fromDefinition(definition: Pick<WorkflowDefinition, 'nodes'>): FlowNode[] {
  const kindByNodeType: Record<string, NodeKind> = Object.fromEntries(
    Object.entries(nodeTypeByKind).map(([kind, nodeType]) => [nodeType, kind]),
  ) as Record<string, NodeKind>
  return definition.nodes.flatMap((node) => {
    const kind = node.type === 'ticket.normalize' ? 'triage' : kindByNodeType[node.type]
    if (!kind) return []
    const subtitle = typeof node.config.instructions === 'string' ? node.config.instructions : node.type
    const labelsEn = defaultNodeText(kind, copy.en)
    const labelsFr = defaultNodeText(kind, copy.fr)
    const isDefaultTitle = node.name === labelsEn.title || node.name === labelsFr.title
    const localeGenerated = kind === 'agent'
      ? (node.name === labelsEn.title && subtitle === labelsEn.subtitle) || (node.name === labelsFr.title && subtitle === labelsFr.subtitle)
      : isDefaultTitle
    return [{
      id: node.id, kind, title: node.name, subtitle, definitionType: node.type,
      localeGenerated,
      role: typeof node.config.role === 'string' ? node.config.role : undefined,
      provider: typeof node.config.provider === 'string' ? node.config.provider : undefined,
      modelRef: typeof node.config.model_ref === 'string' ? node.config.model_ref : undefined,
      allowedTools: Array.isArray(node.config.allowed_tools) ? node.config.allowed_tools.filter((tool): tool is string => typeof tool === 'string') : undefined,
    }]
  })
}

export default function WorkflowBuilder({ locale: initialLocale = 'en', onLocaleChange, onOpenConnections, onRunPublishedWorkflow, onOpenRuns, canRunPublishedWorkflow = false, runningPublishedWorkflow = false, executionError = '', connectionsRevision = 0 }: Props) {
  const [localLocale, setLocalLocale] = useState<WorkflowBuilderLocale>(initialLocale)
  const locale = onLocaleChange ? initialLocale : localLocale
  const t = copy[locale]
  const [nodes, setNodes] = useState<FlowNode[]>(() => makeStarter(t))
  const [selectedId, setSelectedId] = useState('agent')
  const [search, setSearch] = useState('')
  const [zoom, setZoom] = useState(0.9)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [validation, setValidation] = useState<'valid' | 'invalid' | null>(null)
  const [validationErrors, setValidationErrors] = useState<string[]>([])
  const [showValidation, setShowValidation] = useState(false)
  const [draftRevision, setDraftRevision] = useState<number | null>(null)
  const [workflowVersion, setWorkflowVersion] = useState(1)
  const [hasPublishedVersion, setHasPublishedVersion] = useState(false)
  const [unsupportedGraph, setUnsupportedGraph] = useState(false)
  const [saveState, setSaveState] = useState<'local' | 'saved' | 'saving' | 'publishing' | 'error'>('local')
  const [saveMessage, setSaveMessage] = useState('')
  const [bindings, setBindings] = useState<ConnectionBindings | null>(null)
  const [publishedHistory, setPublishedHistory] = useState<PublishedVersion[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [historyNotice, setHistoryNotice] = useState('')
  const [forking, setForking] = useState(false)
  const canvasRef = useRef<HTMLDivElement>(null)
  const copyRef = useRef(t)
  const localeRef = useRef(locale)
  copyRef.current = t
  localeRef.current = locale

  useEffect(() => {
    const translated = makeStarter(copy[locale]).reduce((result, node) => {
      result[node.kind] = { title: node.title, subtitle: node.subtitle }
      return result
    }, {} as Record<NodeKind, { title: string; subtitle: string }>)
    setNodes((current) => current.map((node) => node.localeGenerated ? { ...node, ...translated[node.kind] } : node))
  }, [locale])

  useEffect(() => {
    if (!historyOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setHistoryOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [historyOpen])

  useEffect(() => {
    let cancelled = false
    async function loadBindings() {
      try {
        const [integrationResponse, healthResponse, setupResponse] = await Promise.all([
          fetch('/api/integrations/status'), fetch('/api/health'), fetch('/api/setup'),
        ])
        if (!integrationResponse.ok || !healthResponse.ok || !setupResponse.ok) throw new Error('Setup state is unavailable.')
        const [integrations, health, setup] = await Promise.all([
          integrationResponse.json(), healthResponse.json(), setupResponse.json(),
        ]) as [
          { jira_configured?: boolean; knowledge?: { available?: boolean; mode?: string }; external_ticket_inference_allowed?: boolean },
          { provider_mode?: string; provider?: string; model?: string; credentials_configured?: boolean },
          { external_model_disclosure_accepted?: boolean },
        ]
        const provider = health.provider || 'demo'
        const model = health.model || 'Synthetic workflow model'
        const externalConsent = integrations.external_ticket_inference_allowed ?? setup.external_model_disclosure_accepted ?? false
        const modelReady = provider === 'demo' || Boolean(health.credentials_configured && (provider === 'ollama-local' || externalConsent))
        if (!cancelled) setBindings({
          jiraReady: Boolean(integrations.jira_configured),
          knowledgeReady: Boolean(integrations.knowledge?.available),
          knowledgeMode: integrations.knowledge?.mode || 'synthetic',
          modelReady,
          provider,
          model,
          modelRef: modelReady ? `${provider}:${model}` : 'unconfigured-model',
        })
      } catch {
        if (!cancelled) setBindings({ jiraReady: false, knowledgeReady: false, knowledgeMode: 'local', modelReady: false, provider: '', model: '', modelRef: 'unconfigured-model' })
      }
    }
    void loadBindings()
    return () => { cancelled = true }
  }, [connectionsRevision])

  useEffect(() => {
    let cancelled = false
    async function loadWorkflow() {
      try {
        const response = await fetch(`/api/v1/workflows/${workflowId}`)
        if (response.status === 404) return
        if (!response.ok) throw new Error('Could not load the saved workflow.')
        const bundle = await response.json() as WorkflowBundle
        if (!cancelled) {
          setHasPublishedVersion(bundle.published.length > 0)
          setPublishedHistory(bundle.published)
        }
        if (!cancelled && bundle.draft) {
          if (!isEditableLinear(bundle.draft.definition)) {
            setUnsupportedGraph(true)
            setDraftRevision(bundle.draft.revision)
            setWorkflowVersion(bundle.draft.definition.version)
            setSaveState('error')
            setSaveMessage(copyRef.current.unsupportedGraph)
            return
          }
          const restored = fromDefinition(bundle.draft.definition)
          if (restored.length > 0) {
            setNodes(restored)
            setSelectedId(restored.find((node) => node.kind === 'agent')?.id ?? restored[0].id)
          }
          setDraftRevision(bundle.draft.revision)
          setWorkflowVersion(bundle.draft.definition.version)
          setSaveState('saved')
          setSaveMessage(copyRef.current.saved)
        } else if (!cancelled && bundle.published.length > 0) {
          const latest = bundle.published[0].definition
          if (!isEditableLinear(latest)) {
            setUnsupportedGraph(true)
            setWorkflowVersion(latest.version + 1)
            setSaveState('error')
            setSaveMessage(copyRef.current.unsupportedGraph)
            return
          }
          const restored = fromDefinition(latest)
          if (restored.length > 0) {
            setNodes(restored)
            setSelectedId(restored.find((node) => node.kind === 'agent')?.id ?? restored[0].id)
          }
          setWorkflowVersion(latest.version + 1)
          setSaveState('saved')
          setSaveMessage(localeRef.current === 'fr' ? 'Version publiée chargée · enregistrez pour créer un nouveau brouillon' : 'Published version loaded · save to create a new draft')
        }
      } catch {
        if (!cancelled) setSaveMessage(copyRef.current.localNotice)
      }
    }
    void loadWorkflow()
    return () => { cancelled = true }
  }, [workflowId])

  const selected = nodes.find((node) => node.id === selectedId)
  const paletteItems = useMemo(() => {
    const kinds: NodeKind[] = ['trigger', 'triage', 'knowledge', 'agent', 'verify', 'approval', 'publish']
    return kinds.map((kind, index) => ({ kind, label: t.nodeTypes[index] }))
      .filter((item) => item.label.toLowerCase().includes(search.toLowerCase()))
  }, [search, t.nodeTypes])

  async function validateFlow() {
    const errors: string[] = []
    if (!nodes.some((node) => node.kind === 'trigger')) errors.push(t.triggerMissing)
    if (nodes.some((node) => node.kind === 'publish')) {
      const publishIndex = nodes.findIndex((node) => node.kind === 'publish')
      if (!nodes.slice(0, publishIndex).some((node) => node.kind === 'approval')) errors.push(t.approvalMissing)
    }
    if (nodes.length < 2) errors.push(t.disconnected)
    let finalErrors = errors
    if (errors.length === 0) {
      try {
        const response = await fetch(`/api/v1/workflows/${workflowId}/validate`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(toDefinition(nodes, workflowVersion, bindings)),
        })
        if (response.ok) {
          const result = await response.json() as { valid: boolean; issues: { message: string }[] }
          finalErrors = result.issues.map((issue) => issue.message)
        }
      } catch { /* the local structural check remains available offline */ }
    }
    setValidationErrors(finalErrors)
    setValidation(finalErrors.length === 0 ? 'valid' : 'invalid')
    setShowValidation(true)
  }

  async function saveDefinition(candidate: WorkflowDefinition): Promise<{ definition: WorkflowDefinition; revision: number } | null> {
    if (unsupportedGraph) {
      setSaveState('error')
      setSaveMessage(t.unsupportedGraph)
      return null
    }
    setSaveState('saving')
    setSaveMessage('')
    let definition = candidate
    let currentRevision = draftRevision
    try {
      if (currentRevision === null && hasPublishedVersion) {
        const forkResponse = await fetch(`/api/v1/workflows/${workflowId}/fork`, { method: 'POST' })
        const fork = await forkResponse.json().catch(() => ({})) as { definition?: WorkflowDefinition; revision?: number; created?: boolean; detail?: string | { code?: string; message?: string; revision?: number } }
        if (forkResponse.status === 409 && typeof fork.detail === 'object' && fork.detail?.code === 'draft_already_exists') {
          setSaveState('error')
          setSaveMessage(t.concurrentDraft)
          return null
        }
        if (!forkResponse.ok || !fork.definition || typeof fork.revision !== 'number') {
          const message = typeof fork.detail === 'string' ? fork.detail : fork.detail?.message
          throw new Error(message || 'Could not create a draft from the published version.')
        }
        definition = { ...candidate, version: fork.definition.version }
        currentRevision = fork.revision
        setWorkflowVersion(fork.definition.version)
        setDraftRevision(fork.revision)
      }
      const isNew = currentRevision === null
      const response = await fetch(isNew ? '/api/v1/workflows' : `/api/v1/workflows/${workflowId}/draft`, {
        method: isNew ? 'POST' : 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ definition, ...(isNew ? {} : { expected_revision: currentRevision }) }),
      })
      const result = await response.json().catch(() => ({})) as { revision?: number; detail?: string }
      if (!response.ok || typeof result.revision !== 'number') throw new Error(result.detail || 'Could not save the workflow draft.')
      setDraftRevision(result.revision)
      setSaveState('saved')
      setSaveMessage(t.saved)
      return { definition, revision: result.revision }
    } catch (error) {
      setSaveState('error')
      setSaveMessage(error instanceof Error ? error.message : 'Could not save the workflow draft.')
      return null
    }
  }

  async function saveDraft() {
    const result = await saveDefinition(toDefinition(nodes, workflowVersion, bindings))
    if (result) setWorkflowVersion(result.definition.version)
  }

  async function publishDraft() {
    const persisted = await saveDefinition(toDefinition(nodes, workflowVersion, bindings))
    if (!persisted) return
    setSaveState('publishing')
    try {
      const response = await fetch(`/api/v1/workflows/${workflowId}/publish`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ definition: persisted.definition, expected_revision: persisted.revision }),
      })
      const result = await response.json().catch(() => ({})) as { detail?: string }
      if (!response.ok) throw new Error(result.detail || t.publishBlocked)
      setHasPublishedVersion(true)
      setDraftRevision(null)
      setWorkflowVersion(persisted.definition.version + 1)
      setSaveState('saved')
      setSaveMessage(locale === 'fr' ? `Version ${persisted.definition.version} publiée · enregistrez pour créer le brouillon suivant` : `Version ${persisted.definition.version} published · save to create the next draft`)
    } catch (error) {
      setSaveState('error')
      setSaveMessage(error instanceof Error ? error.message : t.publishBlocked)
    }
  }

  function addNode(kind: NodeKind) {
    if (unsupportedGraph || saveState === 'saving' || saveState === 'publishing') return
    const index = ['trigger', 'triage', 'knowledge', 'agent', 'verify', 'approval', 'publish'].indexOf(kind)
    const title = t.nodeTypes[index]
    const defaults: Record<NodeKind, string> = {
      trigger: t.ticketSub, triage: t.triageSub, knowledge: t.knowledgeSub, agent: t.agentSub,
      verify: t.verifySub, approval: t.approvalSub, publish: t.publishSub,
    }
    const node: FlowNode = {
      id: `${kind}-${crypto.randomUUID()}`, kind, title, subtitle: defaults[kind], localeGenerated: true,
      ...(kind === 'agent' ? { role: 'Support analyst', provider: '', modelRef: 'unconfigured-model', allowedTools: ['knowledge.search'] } : {}),
    }
    setNodes((current) => {
      if (kind === 'trigger') return [node, ...current]
      const at = current.findIndex((item) => item.id === selectedId)
      const next = [...current]
      next.splice(at < 0 ? current.length : at + 1, 0, node)
      return next
    })
    setSelectedId(node.id)
    setSaveState('local')
    setSaveMessage('')
    setValidation(null)
    setPaletteOpen(false)
    window.setTimeout(() => canvasRef.current?.scrollTo({ left: canvasRef.current.scrollWidth, behavior: 'smooth' }), 20)
  }

  function updateSelected(field: 'title' | 'subtitle' | 'role' | 'provider', value: string) {
    if (!selected || unsupportedGraph || saveState === 'saving' || saveState === 'publishing') return
    setNodes((current) => current.map((node) => node.id === selected.id ? { ...node, [field]: value, localeGenerated: false } : node))
    setSaveState('local')
    setSaveMessage('')
  }

  function moveSelected(direction: -1 | 1) {
    if (!selected || selected.kind === 'trigger' || unsupportedGraph || saveState === 'saving' || saveState === 'publishing') return
    const currentIndex = nodes.findIndex((node) => node.id === selected.id)
    const nextIndex = currentIndex + direction
    if (currentIndex < 1 || nextIndex < 1 || nextIndex >= nodes.length) return
    setNodes((current) => {
      const from = current.findIndex((node) => node.id === selected.id)
      const to = from + direction
      if (from < 1 || to < 1 || to >= current.length) return current
      const reordered = [...current]
      const [moving] = reordered.splice(from, 1)
      reordered.splice(to, 0, moving)
      return reordered
    })
    setSelectedId(selected.id)
    setSaveState('local')
    setSaveMessage('')
    setValidation(null)
  }

  function toggleSelectedTool(tool: string) {
    if (!selected || selected.kind !== 'agent' || unsupportedGraph || saveState === 'saving' || saveState === 'publishing') return
    const current = selected.allowedTools ?? []
    const allowedTools = current.includes(tool) ? current.filter((item) => item !== tool) : [...current, tool]
    setNodes((items) => items.map((node) => node.id === selected.id ? { ...node, allowedTools } : node))
    setSaveState('local')
    setSaveMessage('')
  }

  function removeSelected() {
    if (!selected || selected.kind === 'trigger' || unsupportedGraph || saveState === 'saving' || saveState === 'publishing') return
    if (!window.confirm(t.removeConfirm)) return
    const remaining = nodes.filter((node) => node.id !== selected.id)
    setNodes(remaining)
    setSaveState('local')
    setSaveMessage('')
    setSelectedId(remaining[0]?.id ?? '')
    setValidation(null)
  }

  function fitCanvas() {
    setZoom(0.9)
    canvasRef.current?.scrollTo({ left: 0, top: 0, behavior: 'smooth' })
  }

  async function openHistory() {
    setHistoryOpen(true)
    setHistoryLoading(true)
    setHistoryError('')
    setHistoryNotice('')
    try {
      const response = await fetch(`/api/v1/workflows/${workflowId}`)
      if (!response.ok) throw new Error(t.historyLoadError)
      const bundle = await response.json() as WorkflowBundle
      setPublishedHistory(bundle.published)
      setHasPublishedVersion(bundle.published.length > 0)
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : t.historyLoadError)
    } finally {
      setHistoryLoading(false)
    }
  }

  async function forkLatestPublished() {
    if (publishedHistory.length === 0 || forking) return
    if ((saveState === 'local' || saveState === 'error') && !window.confirm(t.forkConfirm)) return
    setForking(true)
    setHistoryError('')
    setHistoryNotice('')
    try {
      const response = await fetch(`/api/v1/workflows/${workflowId}/fork`, { method: 'POST' })
      const result = await response.json().catch(() => ({})) as {
        definition?: WorkflowDefinition; revision?: number
        detail?: string | { code?: string; message?: string; revision?: number }
      }
      if (response.status === 409) {
        setHistoryError(typeof result.detail === 'object' && result.detail?.code === 'draft_already_exists'
          ? t.draftAlreadyExists
          : typeof result.detail === 'object' ? result.detail?.message || t.forkFailed : result.detail || t.forkFailed)
        const currentResponse = await fetch(`/api/v1/workflows/${workflowId}`)
        if (currentResponse.ok) {
          const currentBundle = await currentResponse.json() as WorkflowBundle
          setPublishedHistory(currentBundle.published)
          setHasPublishedVersion(currentBundle.published.length > 0)
        }
        return
      }
      if (!response.ok || !result.definition || typeof result.revision !== 'number') {
        throw new Error(typeof result.detail === 'string' ? result.detail : result.detail?.message || t.forkFailed)
      }
      const latestVersion = publishedHistory[0]?.definition.version
      if (latestVersion !== undefined && result.definition.version <= latestVersion) {
        throw new Error(t.forkFailed)
      }
      const restored = fromDefinition(result.definition)
      if (restored.length === 0) throw new Error(t.forkFailed)
      setNodes(restored)
      setSelectedId(restored.find((node) => node.kind === 'agent')?.id ?? restored[0].id)
      setWorkflowVersion(result.definition.version)
      setDraftRevision(result.revision)
      setUnsupportedGraph(false)
      setSaveState('saved')
      setSaveMessage(t.saved)
      setHistoryNotice(t.forkCreated)
      setHistoryOpen(false)
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : t.forkFailed)
    } finally {
      setForking(false)
    }
  }

  return (
    <section className="sws-builder" lang={locale} aria-label={t.title}>
      <header className="sws-topbar">
        <div className="sws-crumb"><Waypoints size={16} /><span>{t.crumb}</span><ArrowRight size={13} /><b>{t.title}</b></div>
        <div className="sws-top-actions">
          <span className={`sws-draft-pill ${saveState === 'error' ? 'error' : ''}`}><span />{saveState === 'saving' ? t.saving : saveState === 'publishing' ? t.publishing : saveState === 'saved' ? t.saved : t.draft}</span>
          <button className="sws-language-toggle" type="button" onClick={() => { const nextLocale = locale === 'en' ? 'fr' : 'en'; setLocalLocale(nextLocale); onLocaleChange?.(nextLocale) }} aria-label={locale === 'en' ? 'Switch language to French' : 'Changer la langue en anglais'}><Languages size={14} />{locale.toUpperCase()}</button>
          <button className="sws-validate" type="button" onClick={validateFlow}><Check size={15} />{t.validate}</button>
          <button className="sws-save" type="button" onClick={() => void saveDraft()} disabled={saveState === 'saving' || saveState === 'publishing'}><Save size={14} />{t.save}</button>
          <button className="sws-publish-button" type="button" onClick={() => void publishDraft()} disabled={saveState === 'saving' || saveState === 'publishing'}><ShieldCheck size={14} />{t.publishAction}</button>
        </div>
      </header>

      {saveMessage && <div className={`sws-save-message ${saveState}`} role={saveState === 'error' ? 'alert' : 'status'}>{saveMessage}</div>}
      {executionError && <div className="sws-save-message error" role="alert">{executionError}</div>}

      <section className="sws-heading">
        <div><p className="sws-eyebrow">{t.eyebrow}</p><h1>{t.title}</h1><p>{t.subtitle}</p></div>
        <div className="sws-heading-actions">
          <div className="sws-tabs" role="tablist" aria-label={t.title}>
            <button className="active" type="button" role="tab" aria-selected="true"><Waypoints size={14} />{t.workflowTab}</button>
            <button type="button" role="tab" aria-selected="false" onClick={onOpenRuns} disabled={!onOpenRuns}><Clock3 size={14} />{t.runTab}</button>
            <button type="button" role="tab" aria-selected="false" aria-haspopup="dialog" onClick={() => void openHistory()}><History size={14} />{t.history}</button>
            <button type="button" role="tab" aria-selected="false" onClick={onOpenConnections}><Settings2 size={14} />{t.connections}</button>
          </div>
          <div className="sws-connection-status" role="status">
            <span className={bindings?.jiraReady ? 'ready' : ''}><i />{bindings?.jiraReady ? t.jiraConnected : t.jiraMissing}</span>
            <span className={bindings?.knowledgeReady ? 'ready' : ''}><i />{bindings?.knowledgeReady ? bindings.knowledgeMode === 'local' ? t.knowledgeConnected : t.knowledgeDemo : t.knowledgeMissing}</span>
            <span className={bindings?.modelReady ? 'ready' : ''}><i />{bindings?.modelReady ? bindings.provider === 'demo' ? t.modelDemo : `${t.modelConfigured} · ${bindings.provider}` : t.modelMissing}</span>
            <button type="button" onClick={onOpenConnections}>{t.configure}</button>
          </div>
          <button
            type="button"
            className="sws-publish-button"
            title={canRunPublishedWorkflow ? t.runLatest : t.runUnavailable}
            disabled={!canRunPublishedWorkflow || runningPublishedWorkflow || publishedHistory.length === 0 || !onRunPublishedWorkflow}
            onClick={() => {
              const latest = publishedHistory[0]
              if (latest) void onRunPublishedWorkflow?.(workflowId, latest.definition.version, latest.content_hash)
            }}
          ><Play size={14} fill="currentColor" />{runningPublishedWorkflow ? t.runningFlow : t.runLatest}</button>
        </div>
      </section>

      <div className="sws-editor-shell">
        <aside className="sws-palette">
          <div className="sws-side-heading"><div><strong>{t.palette}</strong><small>{nodes.length} {t.stepCount}</small></div><button type="button" className="sws-add-square" aria-label={t.addNode} onClick={() => setPaletteOpen((open) => !open)}><Plus size={16} /></button></div>
          <label className="sws-search"><Search size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t.search} /></label>
          <div className="sws-library-list">
            {paletteItems.map(({ kind, label }) => {
              const Icon = icons[kind]
              return <button key={kind} type="button" className="sws-library-item" onClick={() => addNode(kind)}><span className={`sws-icon-box ${typeColors[kind]}`}><Icon size={15} /></span><span>{label}</span><Plus size={13} /></button>
            })}
          </div>
          <div className="sws-palette-note"><LockKeyhole size={14} /><span>{t.localNotice}</span></div>
        </aside>

        <section className="sws-canvas-column" aria-label={t.canvas}>
          <div className="sws-canvas-toolbar">
            <span><span className="sws-canvas-live-dot" />{t.status}</span>
            <div className="sws-zoom-controls">
              <button type="button" onClick={() => setZoom((value) => Math.max(0.55, value - 0.1))} aria-label={t.zoomOut}><Minus size={14} /></button>
              <span>{Math.round(zoom * 100)}%</span>
              <button type="button" onClick={() => setZoom((value) => Math.min(1.2, value + 0.1))} aria-label={t.zoomIn}><Plus size={14} /></button>
              <i />
              <button type="button" onClick={fitCanvas} aria-label={t.fit}><ArrowDownUp size={14} /></button>
            </div>
          </div>
          <div ref={canvasRef} className="sws-canvas-scroll">
            <div className="sws-canvas" style={{ transform: `scale(${zoom})` }}>
              <div className="sws-flow-track">
                {nodes.map((node, index) => {
                  const Icon = icons[node.kind]
                  const selectedNode = node.id === selectedId
                  return <div className="sws-flow-item" key={node.id}>
                    <button type="button" className={`sws-node ${selectedNode ? 'selected' : ''}`} onClick={() => setSelectedId(node.id)} aria-pressed={selectedNode}>
                      <div className={`sws-node-icon ${typeColors[node.kind]}`}><Icon size={17} /></div>
                      <div className="sws-node-content"><small>{node.kind === 'trigger' ? t.trigger : node.kind === 'agent' ? t.ai : node.kind === 'approval' || node.kind === 'verify' ? t.review : t.action}</small><b>{node.title}</b><span>{node.subtitle}</span></div>
                      <GripVertical className="sws-grip" size={14} />
                      {node.kind === 'publish' && <span className="sws-node-lock"><LockKeyhole size={11} /></span>}
                    </button>
                    {index < nodes.length - 1 && <div className="sws-connector" aria-hidden="true"><span /><ArrowRight size={14} /></div>}
                  </div>
                })}
                <button type="button" className="sws-add-node-card" onClick={() => setPaletteOpen(true)}><span><Plus size={17} /></span>{t.addNode}</button>
              </div>
            </div>
            {paletteOpen && <div className="sws-add-popover" role="dialog" aria-label={t.addNode}>
              <div className="sws-popover-head"><b>{t.addNode}</b><button type="button" onClick={() => setPaletteOpen(false)} aria-label={t.close}><X size={15} /></button></div>
              {paletteItems.slice(0, 8).map(({ kind, label }) => { const Icon = icons[kind]; return <button key={kind} type="button" onClick={() => addNode(kind)}><Icon size={15} />{label}<Plus size={13} /></button> })}
            </div>}
            <div className="sws-canvas-hint"><ArrowLeft size={12} /> {locale === 'fr' ? 'Faites défiler pour voir le flux' : 'Scroll horizontally to explore the flow'} <ArrowRight size={12} /></div>
          </div>
          <footer className="sws-canvas-footer"><span><span className="sws-footer-dot" />{nodes.length} {t.stepCount}</span><span><LockKeyhole size={12} />{locale === 'fr' ? 'Les réponses externes nécessitent une validation humaine' : 'External replies require human approval'}</span></footer>
        </section>

        <aside className="sws-inspector">
          <div className="sws-inspector-head"><div><p>{t.inspector}</p><small>{selected ? t.nodeTypes[['trigger','triage','knowledge','agent','verify','approval','publish'].indexOf(selected.kind)] : ''}</small></div><button type="button" className="sws-inspector-menu" aria-label={t.close} disabled><ChevronDown size={16} /></button></div>
          {selected ? <div className="sws-inspector-body">
            <div className="sws-inspector-type"><span className={`sws-icon-box ${typeColors[selected.kind]}`}>{(() => { const Icon = icons[selected.kind]; return <Icon size={16} /> })()}</span><div><b>{selected.title}</b><small>{selected.id.slice(0, 16)}</small></div></div>
            {selected.kind !== 'trigger' && <section className="sws-step-order" aria-label={t.stepOrder}>
              <p>{t.orderHint}</p>
              <div><button type="button" onClick={() => moveSelected(-1)} disabled={nodes.findIndex((node) => node.id === selected.id) <= 1 || unsupportedGraph || saveState === 'saving' || saveState === 'publishing'}><ArrowUp size={14} />{t.moveEarlier}</button><button type="button" onClick={() => moveSelected(1)} disabled={nodes.findIndex((node) => node.id === selected.id) >= nodes.length - 1 || unsupportedGraph || saveState === 'saving' || saveState === 'publishing'}><ArrowDown size={14} />{t.moveLater}</button></div>
            </section>}
            <label>{t.name}<input value={selected.title} onChange={(event) => updateSelected('title', event.target.value)} maxLength={80} /></label>
            <label>{t.description}<textarea value={selected.subtitle} onChange={(event) => updateSelected('subtitle', event.target.value)} rows={3} maxLength={240} /></label>
            {selected.kind === 'agent' && <>
              <label>{t.role}<input value={selected.role ?? ''} onChange={(event) => updateSelected('role', event.target.value)} maxLength={80} /></label>
              <label>{t.model}<select value={selected.provider || bindings?.provider || ''} onChange={(event) => updateSelected('provider', event.target.value)}><option value="">{t.modelLocal}</option>{bindings?.provider && <option value={bindings.provider}>{bindings.provider} · {bindings.model}</option>}<option value="ollama-local">Ollama · local</option><option value="openai-compatible">OpenAI-compatible · BYOK</option><option value="anthropic">Anthropic · BYOK</option></select></label>
              <div className="sws-tool-list"><span>{t.tools}</span><label><input type="checkbox" checked={(selected.allowedTools ?? []).includes('knowledge.search')} onChange={() => toggleSelectedTool('knowledge.search')} />{t.toolKnowledge}</label><label><input type="checkbox" checked={(selected.allowedTools ?? []).includes('ticket.read')} onChange={() => toggleSelectedTool('ticket.read')} />{t.toolTicket}</label></div>
              <div className="sws-inspector-callout"><Bot size={15} /><span>{t.noTools}</span></div>
            </>}
            {selected.kind === 'publish' && <div className="sws-approval-card"><div><UserRoundCheck size={15} /><b>{t.approvalGate}</b></div><strong><Check size={12} />{t.on}</strong><small>{t.requiredForWrite}</small></div>}
            {selected.kind !== 'trigger' && <button className="sws-remove-node" type="button" onClick={removeSelected}><Trash2 size={14} />{t.remove}</button>}
            <div className="sws-inspector-bottom"><FileCheck2 size={14} /><span>{t.localNotice}</span></div>
          </div> : <div className="sws-empty-inspector">{t.selectNode}</div>}
        </aside>
      </div>

      {historyNotice && <div className="sws-history-feedback" role="status" aria-live="polite">{historyNotice}</div>}

      {historyOpen && <div className="sws-history-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setHistoryOpen(false) }}>
        <section className="sws-history-dialog" role="dialog" aria-modal="true" aria-labelledby="sws-history-title" aria-describedby="sws-history-disclosure">
          <header className="sws-history-header"><div><p className="sws-eyebrow">{t.workflowTab}</p><h2 id="sws-history-title">{t.publishedHistory}</h2></div><button type="button" autoFocus className="sws-history-close" aria-label={t.closeHistory} onClick={() => setHistoryOpen(false)}><X size={17} /></button></header>
          <p id="sws-history-disclosure" className="sws-history-disclosure"><LockKeyhole size={14} />{t.executionDisclosure}</p>
          {historyError && <div className="sws-history-error" role="alert">{historyError}</div>}
          <div className="sws-history-list" aria-live="polite">
            {historyLoading ? <p className="sws-history-empty" role="status">{t.historyLoading}</p> : publishedHistory.length === 0
              ? <p className="sws-history-empty">{t.noPublishedVersions}</p>
              : publishedHistory.map((item, index) => <article className="sws-history-version" key={`${item.definition.version}-${item.content_hash}`}>
                <div className="sws-history-version-heading"><h3>{t.version} {item.definition.version}</h3>{index === 0 && <span>{t.latestVersion}</span>}</div>
                <dl>
                  <div><dt>{t.publishedAt}</dt><dd><time dateTime={item.published_at}>{new Date(item.published_at).toLocaleString(locale === 'fr' ? 'fr-FR' : 'en-US', { dateStyle: 'medium', timeStyle: 'short' })}</time></dd></div>
                  <div><dt>{t.contentHash}</dt><dd><code title={item.content_hash}>{item.content_hash}</code></dd></div>
                </dl>
              </article>)}
          </div>
          <footer className="sws-history-footer"><button type="button" className="sws-history-secondary" onClick={() => setHistoryOpen(false)}>{t.close}</button><button type="button" className="sws-history-primary" disabled={publishedHistory.length === 0 || forking || historyLoading} onClick={() => void forkLatestPublished()}>{forking ? t.forking : t.forkLatest}</button></footer>
        </section>
      </div>}

      {showValidation && <div className={`sws-validation-toast ${validation}`} role="status" aria-live="polite">
        <span className="sws-validation-icon">{validation === 'valid' ? <ClipboardCheck size={18} /> : <CircleAlert size={18} />}</span>
        <div><b>{t.validationTitle}</b>{validation === 'valid' ? <p>{t.valid}</p> : <><p>{t.invalid}</p><ul>{validationErrors.map((error) => <li key={error}>{error}</li>)}</ul></>}</div>
        <button type="button" aria-label={t.close} onClick={() => setShowValidation(false)}><X size={15} /></button>
      </div>}
    </section>
  )
}
