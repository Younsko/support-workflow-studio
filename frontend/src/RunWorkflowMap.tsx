import {
  ArrowRight, Check, CircleDot, ClipboardCheck, FileSearch, GitBranch,
  Inbox, LockKeyhole, MessageSquareText, RotateCcw, ShieldCheck, Sparkles, UserRound,
} from 'lucide-react'

type Stage = { id: string; label: string; status: string; detail: string; actor: string }
type ReviewRound = { round: number; score: number; critic_score: number; verdict: string; origin: string }
type WorkLane = { lane: string; scope: string }
type Reviewer = { lane: string; score: number }

type RunWorkflowMapProps = {
  locale: 'en' | 'fr'
  providerMode: string
  stages: Stage[]
  planReviews: ReviewRound[]
  workLanes: WorkLane[]
  reviewers: Reviewer[]
  status: string
  ticketPublished: boolean
  publishAvailable: boolean
}

const labels = {
  en: {
    title: 'Run path', subtitle: 'Observed stages and review gates for this ticket',
    intake: 'Ticket received', triage: 'Normalize & triage', knowledge: 'Approved knowledge',
    investigation: 'Independent investigation', technical: 'Technical context', access: 'Access & privacy', impact: 'Customer impact',
    design: 'Architect → critic → revise', loop: 'Revise only below 9.0 · bounded by the run budget',
    drafting: 'Response draft', verifier: 'Independent verifier', reviews: 'Specialist review lanes',
    quality: 'Quality gate', human: 'Human decision', publish: 'Publish reply',
    separate: 'Separate, explicit action', awaiting: 'Awaiting review', approved: 'Approved', rejected: 'Rejected',
    sent: 'Published', notSent: 'Not sent', unavailable: 'ITSM publication unavailable', awaitingApproval: 'Waiting for approval',
    demo: 'Simulated run · no model calls', live: 'Live model run', notReached: 'Not reached',
    result: 'Run result', rounds: 'rounds', noRounds: 'No critic round recorded',
    verifyNote: 'Read-only findings; cannot edit or approve', reviewNote: 'Correctness · security · customer clarity',
    gateNote: '9.0/10 minimum before approval',
  },
  fr: {
    title: 'Parcours de l’exécution', subtitle: 'Étapes observées et contrôles de cette demande',
    intake: 'Ticket reçu', triage: 'Normalisation et triage', knowledge: 'Documentation approuvée',
    investigation: 'Analyses indépendantes', technical: 'Contexte technique', access: 'Accès et confidentialité', impact: 'Impact client',
    design: 'Architecte → critique → révision', loop: 'Révision si score < 9,0 · limitée par le budget',
    drafting: 'Brouillon de réponse', verifier: 'Vérificateur indépendant', reviews: 'Relectures spécialisées',
    quality: 'Seuil qualité', human: 'Décision humaine', publish: 'Publier la réponse',
    separate: 'Action séparée et explicite', awaiting: 'En attente de validation', approved: 'Approuvé', rejected: 'Refusé',
    sent: 'Publié', notSent: 'Non envoyé', unavailable: 'Publication ITSM indisponible', awaitingApproval: 'En attente de validation',
    demo: 'Exécution simulée · aucun appel modèle', live: 'Exécution avec modèle', notReached: 'Non atteint',
    result: 'Résultat', rounds: 'tours', noRounds: 'Aucun tour de critique enregistré',
    verifyNote: 'Constats en lecture seule ; sans droit de modification ni d’approbation', reviewNote: 'Exactitude · sécurité · clarté client',
    gateNote: 'Minimum de 9,0/10 avant approbation',
  },
} as const

function stateOf(stages: Stage[], ids: string[]): 'complete' | 'warning' | 'pending' | 'unseen' {
  const matching = ids.map((id) => stages.find((stage) => stage.id === id)).filter((stage): stage is Stage => Boolean(stage))
  if (matching.length === 0) return 'unseen'
  if (matching.some((stage) => stage.status === 'warning')) return 'warning'
  if (matching.some((stage) => stage.status === 'pending')) return 'pending'
  return 'complete'
}

function Node({
  icon, title, detail, state = 'complete', score,
}: {
  icon: typeof Inbox; title: string; detail: string; state?: string; score?: string
}) {
  const Icon = icon
  return <div className={`agent-map-node ${state}`}>
    <span className="agent-map-icon"><Icon size={16} strokeWidth={1.8} /></span>
    <span className="agent-map-copy"><b>{title}</b><small>{detail}</small></span>
    {score && <strong className="agent-map-score">{score}</strong>}
    <span className="agent-map-state" aria-hidden="true">{state === 'complete' ? <Check size={13} /> : state === 'pending' ? <CircleDot size={13} /> : state === 'unseen' ? '·' : '!'}</span>
  </div>
}

function Connector() { return <span className="agent-map-connector" aria-hidden="true"><ArrowRight size={14} /></span> }

export default function RunWorkflowMap(props: RunWorkflowMapProps) {
  const copy = labels[props.locale]
  const intake = stateOf(props.stages, ['intake'])
  const knowledge = stateOf(props.stages, ['evidence'])
  const investigation = stateOf(props.stages, ['investigation-lanes'])
  const design = stateOf(props.stages, ['design', 'gate'])
  const verifier = stateOf(props.stages, ['verify', 'verify-revision'])
  const review = stateOf(props.stages, ['review', 'review-revision'])
  const gate = stateOf(props.stages, ['gate'])
  const human = props.status === 'awaiting_human' ? 'pending' : props.status === 'rejected' ? 'warning' : props.status === 'approved' ? 'complete' : 'unseen'
  const publishState = props.ticketPublished ? 'complete' : props.status === 'approved' && props.publishAvailable ? 'pending' : 'unseen'

  return <section className="agent-map" aria-labelledby="agent-map-title">
    <header className="agent-map-header">
      <div><h4 id="agent-map-title">{copy.title}</h4><p>{copy.subtitle}</p></div>
      <span className={`agent-map-run-mode ${props.stages.length > 0 && props.status ? '' : 'muted'}`}>
        <span aria-hidden="true" />{props.status && props.stages.length > 0 ? (props.providerMode === 'live' ? copy.live : copy.demo) : copy.notReached}
      </span>
    </header>

    <div className="agent-map-topline">
      <Node icon={Inbox} title={copy.intake} detail={props.stages.find((stage) => stage.id === 'intake')?.detail ?? copy.notReached} state={intake} />
      <Connector />
      <Node icon={Sparkles} title={copy.triage} detail={props.stages.find((stage) => stage.id === 'intake')?.actor ?? copy.notReached} state={intake} />
      <Connector />
      <Node icon={FileSearch} title={copy.knowledge} detail={props.stages.find((stage) => stage.id === 'evidence')?.detail ?? copy.notReached} state={knowledge} />
    </div>

    <div className="agent-map-branch-wrap">
      <div className="agent-map-branch-rail" aria-hidden="true"><GitBranch size={14} /></div>
      <div className="agent-map-branch">
        <div className="agent-map-group-heading"><span>{copy.investigation}</span><small>{props.workLanes.length} {props.locale === 'fr' ? 'voies en lecture seule' : 'read-only lanes'}</small></div>
        <div className="agent-map-lanes">
          {props.workLanes.map((lane, index) => <article className={`agent-map-lane ${investigation}`} key={lane.lane}>
            <span className="agent-map-lane-index">0{index + 1}</span><span><b>{lane.lane}</b><small>{lane.scope}</small></span>
            {investigation === 'complete' && <Check size={13} aria-label="Complete" />}
          </article>)}
        </div>
      </div>
    </div>

    <div className="agent-map-route">
      <div className="agent-map-route-spacer" aria-hidden="true" />
      <div className="agent-map-loop-column">
        <Node icon={RotateCcw} title={copy.design} detail={copy.loop} state={design}
          score={props.planReviews.length ? `${props.planReviews[props.planReviews.length - 1].score.toFixed(1)}/10` : undefined} />
        <div className="agent-map-loop">
          {props.planReviews.length ? props.planReviews.map((round) => <div className={`agent-map-round ${round.verdict}`} key={round.round}>
            <span>{copy.result} {round.round}</span><b>{round.score.toFixed(1)}<small>/10</small></b><em>{round.verdict === 'pass' ? '✓' : '↻'} {round.verdict === 'pass' ? (props.locale === 'fr' ? 'seuil atteint' : 'gate passed') : (props.locale === 'fr' ? 'à réviser' : 'revise')}</em>
          </div>) : <div className="agent-map-empty-round">{copy.noRounds}</div>}
        </div>
      </div>
      <div className="agent-map-route-arrow" aria-hidden="true"><ArrowRight size={15} /></div>
      <div className="agent-map-review-package">
        <div className="agent-map-group-heading"><span>{copy.drafting}</span><small>{props.locale === 'fr' ? 'après le plan accepté' : 'after accepted plan'}</small></div>
        <div className="agent-map-review-lanes">
          <article className={`agent-map-review-lane ${verifier}`}><span className="agent-map-review-symbol"><ShieldCheck size={15} /></span><span><b>{copy.verifier}</b><small>{copy.verifyNote}</small></span></article>
          {props.reviewers.map((item) => <article className={`agent-map-review-lane ${review}`} key={item.lane}>
            <span className="agent-map-review-symbol"><ClipboardCheck size={15} /></span><span><b>{item.lane}</b><small>{copy.reviewNote}</small></span><strong>{item.score.toFixed(1)}</strong>
          </article>)}
        </div>
      </div>
    </div>

    <div className="agent-map-endpoint">
      <div className={`agent-map-gate-note ${gate}`}><span><LockKeyhole size={14} /></span><small>{copy.gateNote}</small></div>
      <Connector />
      <Node icon={UserRound} title={copy.human} detail={props.status === 'awaiting_human' ? copy.awaiting : props.status === 'rejected' ? copy.rejected : props.status === 'approved' ? copy.approved : copy.notReached} state={human} score={props.status === 'awaiting_human' ? undefined : (props.status === 'approved' || props.status === 'rejected' ? (props.locale === 'fr' ? 'décision' : 'decision') : undefined)} />
      <Connector />
      <Node icon={MessageSquareText} title={copy.publish} detail={props.ticketPublished ? copy.sent : props.status === 'awaiting_human' ? copy.awaitingApproval : props.status === 'rejected' ? copy.notSent : props.status === 'approved' && props.publishAvailable ? copy.separate : props.status === 'approved' ? copy.unavailable : copy.notReached} state={publishState} />
    </div>
  </section>
}
