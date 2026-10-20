import { useEffect, useRef, useState } from 'react'
import { streamChat, type Step, type Subgraph, type Turn } from './api'
import { GraphView } from './GraphView'
import {
  ArrowUp,
  ArrowUpRight,
  CircleAlert,
  CircleCheck,
  Close,
  Copy,
  Info,
  NewChat,
  ShieldCheck,
} from './icons'

type Evidence = {
  steps: Step[]
  cypher: string
  rows: Record<string, unknown>[]
  truncated?: boolean
  fallback?: boolean
  subgraph: Subgraph
  status?: string
  ms?: number
}

type Message = Turn & { evidence?: Evidence; error?: string; pending?: boolean }
type Tab = 'steps' | 'query' | 'graph' | 'rows'
type Health = { state: 'checking' | 'live' | 'down'; nodes?: number }

const STEP_NAMES: Record<string, string> = {
  contextualize: 'Rewrite follow-up',
  understand: 'Find entities',
  generate_cypher: 'Write query',
  validate: 'Check query',
  execute: 'Run on the graph',
  fallback: 'Look up related facts',
}

const PENDING_TEXT: Record<string, string> = {
  contextualize: 'Finding the entities in your question',
  understand: 'Writing a query',
  generate_cypher: 'Checking the query',
  validate: 'Running it on the graph',
  execute: 'Writing the answer',
  fallback: 'Writing the answer',
}

function pendingText(steps: Step[]): string {
  const last = steps[steps.length - 1]
  if (!last) return 'Reading your question'
  const failed = last.ok === false || ((last.errors as string[] | undefined)?.length ?? 0) > 0
  if (failed && (last.step === 'validate' || last.step === 'execute')) return 'Fixing the query'
  return PENDING_TEXT[last.step] ?? 'Working'
}

const TABS: { id: Tab; label: string }[] = [
  { id: 'steps', label: 'Steps' },
  { id: 'query', label: 'Query' },
  { id: 'graph', label: 'Graph' },
  { id: 'rows', label: 'Rows' },
]

const emptyEvidence = (): Evidence => ({ steps: [], cypher: '', rows: [], subgraph: { nodes: [], edges: [] } })

export default function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const [tab, setTab] = useState<Tab>('steps')
  const [sheetOpen, setSheetOpen] = useState(false)
  const [examples, setExamples] = useState<string[]>([])
  const [health, setHealth] = useState<Health>({ state: 'checking' })
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch('/api/examples')
      .then((r) => r.json())
      .then(setExamples)
      .catch(() => setExamples([]))
    fetch('/api/health')
      .then((r) => r.json())
      .then((h) => setHealth(h.neo4j ? { state: 'live', nodes: h.graph?.nodes } : { state: 'down' }))
      .catch(() => setHealth({ state: 'down' }))
  }, [])
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const ask = async (question: string) => {
    if (!question.trim() || busy) return
    const history: Turn[] = messages
      .filter((m) => !m.error && !m.pending)
      .map(({ role, content }) => ({ role, content }))
    const idx = messages.length + 1
    setMessages((m) => [
      ...m,
      { role: 'user', content: question },
      { role: 'assistant', content: '', pending: true, evidence: emptyEvidence() },
    ])
    setSelected(idx)
    setTab('steps')
    setInput('')
    setBusy(true)

    const update = (fn: (msg: Message) => Message) =>
      setMessages((all) => all.map((msg, i) => (i === idx ? fn(msg) : msg)))
    const updateEvidence = (fn: (e: Evidence) => Evidence) =>
      update((msg) => ({ ...msg, evidence: fn(msg.evidence ?? emptyEvidence()) }))

    try {
      await streamChat(question, history, {
        onStep: (step) =>
          updateEvidence((e) => ({
            ...e,
            steps: [...e.steps, step],
            cypher: step.step === 'generate_cypher' ? String(step.cypher ?? '') : e.cypher,
          })),
        onRows: (rows, meta) =>
          updateEvidence((e) => ({ ...e, rows, truncated: meta.truncated, fallback: meta.fallback })),
        onToken: (text) => update((msg) => ({ ...msg, content: msg.content + text })),
        onFinal: (final) => {
          update((msg) => ({ ...msg, content: final.answer, pending: false }))
          updateEvidence((e) => ({ ...e, subgraph: final.subgraph, status: final.status, ms: final.ms }))
        },
        onError: (message) => update((msg) => ({ ...msg, pending: false, error: message })),
      })
    } catch (err) {
      update((msg) => ({ ...msg, pending: false, error: String(err) }))
    } finally {
      update((msg) => ({ ...msg, pending: false }))
      setBusy(false)
    }
  }

  const showEvidence = (i: number) => {
    setSelected(i)
    setTab('steps')
    setSheetOpen(true)
  }

  const newChat = () => {
    setMessages([])
    setSelected(null)
    setSheetOpen(false)
  }

  const evidence = selected !== null ? messages[selected]?.evidence : undefined

  return (
    <div className="app">
      <div className="topbar">
        <header className="topbar-row">
          <ShieldCheck style={{ color: 'var(--accent-text)' }} />
          <div className="brand">
            <span className="brand-name">MediGraph</span>
            <span className="status">
              <span className={`dot ${health.state === 'live' ? 'live' : health.state === 'down' ? 'down' : ''}`} />
              {health.state === 'live' && health.nodes ? (
                <>
                  Connected to PrimeKG<span className="hide-narrow"> · {health.nodes.toLocaleString()} entities</span>
                </>
              ) : health.state === 'live'
                ? 'Connected to PrimeKG'
                : health.state === 'down'
                  ? 'Knowledge graph offline'
                  : 'Connecting…'}
            </span>
          </div>
          {messages.length > 0 && (
            <button type="button" className="pill-button" onClick={newChat} disabled={busy}>
              <NewChat />
              <span className="hide-narrow">New chat</span>
              <span className="sr-only show-narrow">New chat</span>
            </button>
          )}
        </header>
        <div className="notice">
          <Info />
          <p>
            Answers come only from the PrimeKG knowledge graph and are for general information, not
            medical advice. In an emergency, call <strong className="urgent">911</strong>.
          </p>
        </div>
      </div>

      <main>
        <section className="chat" aria-label="Conversation">
          <div className="thread-scroll">
            <div className="thread">
              {messages.length === 0 && (
                <>
                  <div className="bubble assistant">
                    Hello. Ask me about diseases, drugs, genes and symptoms. I answer only from a curated
                    medical knowledge graph, and you can see the evidence behind every answer.
                  </div>
                  <div className="chips">
                    {examples.map((q) => (
                      <button key={q} type="button" className="chip" onClick={() => ask(q)}>
                        {q}
                      </button>
                    ))}
                  </div>
                </>
              )}

              {messages.map((m, i) =>
                m.role === 'user' ? (
                  <div key={i} className="bubble user">
                    {m.content}
                  </div>
                ) : (
                  <div key={i} className={`answer${selected === i ? ' selected' : ''}`}>
                    <div className="bubble assistant">
                      {m.error ? (
                        <p className="error-text">{m.error}</p>
                      ) : m.pending && !m.content ? (
                        <p className="thinking">
                          <span className="dot live" />
                          {pendingText(m.evidence?.steps ?? [])}…
                        </p>
                      ) : (
                        <Markdown text={m.content} />
                      )}
                    </div>
                    {!m.pending && !m.error && m.evidence && (
                      <div className="meta">
                        <AnswerStatus evidence={m.evidence} />
                        {m.evidence.steps.length > 0 && (
                          <button type="button" className="source" onClick={() => showEvidence(i)}>
                            <span>
                              Source: PrimeKG query
                              <ArrowUpRight />
                            </span>
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                ),
              )}
              <div ref={bottom} />
            </div>
          </div>

          <div className="composer-wrap">
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault()
                ask(input)
              }}
            >
              <label htmlFor="question" className="sr-only">
                Question
              </label>
              <input
                id="question"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={messages.length ? 'Ask a follow-up…' : 'Ask about a disease, drug, gene or symptom…'}
                disabled={busy}
                autoComplete="off"
              />
              <button type="submit" className="send" aria-label="Send question" disabled={busy || !input.trim()}>
                <ArrowUp />
              </button>
            </form>
          </div>
        </section>

        <aside className={`evidence${sheetOpen ? ' open' : ''}`} aria-label="Evidence">
          <div className="evidence-head">
            <h2>How this answer was found</h2>
            <button type="button" className="icon-button" aria-label="Close evidence" onClick={() => setSheetOpen(false)}>
              <Close />
            </button>
          </div>
          <div className="tabs" role="tablist" aria-label="Evidence views">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                id={`tab-${t.id}`}
                aria-selected={tab === t.id}
                aria-controls="evidence-panel"
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="panel" id="evidence-panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
            {!evidence ? (
              <p className="panel-empty">
                Ask a question to see each step: the entities found, the query written and checked, and the
                facts it returned.
              </p>
            ) : tab === 'steps' ? (
              <Steps steps={evidence.steps} />
            ) : tab === 'query' ? (
              <Query cypher={evidence.cypher} />
            ) : tab === 'graph' ? (
              <GraphView graph={evidence.subgraph} />
            ) : (
              <Rows rows={evidence.rows} truncated={evidence.truncated} fallback={evidence.fallback} />
            )}
          </div>
        </aside>
      </main>
    </div>
  )
}

function AnswerStatus({ evidence }: { evidence: Evidence }) {
  const secs = evidence.ms ? ` · ${(evidence.ms / 1000).toFixed(1)} s` : ''
  const facts = evidence.rows.length
  if (evidence.status === 'answered')
    return (
      <span className="meta-status ok">
        <CircleCheck size={14} />
        Grounded in {facts} {facts === 1 ? 'fact' : 'facts'}
        {secs}
      </span>
    )
  const text: Record<string, string> = {
    fallback: 'Exact answer not in the graph · showing related facts',
    no_answer: 'Not found in the knowledge graph',
    out_of_scope: 'Outside what the knowledge graph covers',
  }
  return (
    <span className="meta-status">
      <Info size={14} />
      {text[evidence.status ?? ''] ?? 'Answered'}
      {secs}
    </span>
  )
}

function Steps({ steps }: { steps: Step[] }) {
  if (!steps.length) return <p className="panel-empty">Working…</p>
  return (
    <ol className="steps">
      {steps.map((s, i) => {
        const bad = s.ok === false || ((s.errors as string[] | undefined)?.length ?? 0) > 0
        return (
          <li key={i} className={bad ? 'bad' : ''}>
            <span className="step-icon">{bad ? <CircleAlert /> : <CircleCheck />}</span>
            <div>
              <div className="step-head">
                <strong>
                  {STEP_NAMES[s.step] ?? s.step}
                  {s.step === 'generate_cypher' && Number(s.attempt) > 1 ? ` · attempt ${Number(s.attempt)}` : ''}
                </strong>
                <span className="step-time">{(s.ms / 1000).toFixed(1)} s</span>
              </div>
              <div className="step-body">
                <StepDetail step={s} />
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function StepDetail({ step }: { step: Step }) {
  if (step.step === 'contextualize')
    return <p>{step.rewritten ? `Rewritten as: ${String(step.question)}` : 'No rewrite needed'}</p>
  if (step.step === 'understand') {
    const ents = (step.entities as { mention: string; name: string; label: string }[]) ?? []
    if (step.in_scope === false) return <p>Judged outside what the graph covers</p>
    if (!ents.length) return <p>No entities matched; the query relies on the schema</p>
    return (
      <ul>
        {ents.map((e, i) => (
          <li key={i}>
            {e.mention === e.name ? (
              <b>{e.name}</b>
            ) : (
              <>
                {e.mention} → <b>{e.name}</b>
              </>
            )}
            <span className="tag">{e.label}</span>
          </li>
        ))}
      </ul>
    )
  }
  if (step.step === 'generate_cypher') return <pre className="code">{String(step.cypher)}</pre>
  if (step.step === 'validate')
    return step.ok ? (
      <p>Read-only, matches the schema, every name exists</p>
    ) : (
      <ul className="errors">{(step.errors as string[]).map((e, i) => <li key={i}>{e}</li>)}</ul>
    )
  if (step.step === 'execute')
    return step.errors ? (
      <ul className="errors">{(step.errors as string[]).map((e, i) => <li key={i}>{e}</li>)}</ul>
    ) : (
      <p>
        {String(step.n_rows)} {Number(step.n_rows) === 1 ? 'row' : 'rows'}
        {step.truncated ? ', first 50 shown' : ''}
      </p>
    )
  if (step.step === 'fallback') return <p>{String(step.n_rows)} related facts found</p>
  return null
}

function Query({ cypher }: { cypher: string }) {
  const [copied, setCopied] = useState(false)
  if (!cypher) return <p className="panel-empty">No query was written for this question.</p>
  const copy = () => {
    navigator.clipboard
      ?.writeText(cypher)
      .then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1600)
      })
      .catch(() => setCopied(false))
  }
  return (
    <>
      <pre className="code">{cypher}</pre>
      <div className="code-actions">
        <button type="button" className="pill-button" onClick={copy}>
          <Copy />
          {copied ? 'Copied' : 'Copy query'}
        </button>
      </div>
    </>
  )
}

function Rows({ rows, truncated, fallback }: { rows: Record<string, unknown>[]; truncated?: boolean; fallback?: boolean }) {
  if (!rows.length) return <p className="panel-empty">The query returned no rows.</p>
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))]
  const show = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v))
  return (
    <>
      {(truncated || fallback) && (
        <p className="note">{fallback ? 'Related facts about the entities in your question.' : 'First 50 rows shown.'}</p>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{cols.map((c) => <td key={c}>{show(r[c] ?? '')}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

/** Minimal markdown: paragraphs, bullet lists and **bold**; enough for the model's answers. */
function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = []
  let list: string[] = []
  const inline = (s: string) =>
    s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith('**') && part.endsWith('**') ? <b key={i}>{part.slice(2, -2)}</b> : part,
    )
  const flush = () => {
    if (list.length) blocks.push(<ul key={blocks.length}>{list.map((l, i) => <li key={i}>{inline(l)}</li>)}</ul>)
    list = []
  }
  for (const line of text.split('\n')) {
    const bullet = line.match(/^\s*[*-]\s+(.*)/)
    if (bullet) list.push(bullet[1])
    else {
      flush()
      if (line.trim()) blocks.push(<p key={blocks.length}>{inline(line.replace(/^#+\s*/, ''))}</p>)
    }
  }
  flush()
  return <div className="md">{blocks}</div>
}
