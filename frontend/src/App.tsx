import { useEffect, useRef, useState } from 'react'
import { streamChat, type Step, type Subgraph, type Turn } from './api'
import { GraphView } from './GraphView'

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

const STEP_NAMES: Record<string, string> = {
  contextualize: 'Rewrite follow-up',
  understand: 'Find entities',
  generate_cypher: 'Write Cypher',
  validate: 'Validate query',
  execute: 'Run on Neo4j',
  fallback: 'Fallback: neighbourhood lookup',
}

const STATUS_TEXT: Record<string, string> = {
  answered: 'Grounded in graph query',
  fallback: 'Exact query found nothing · neighbourhood used',
  no_answer: 'Not found in graph',
  out_of_scope: 'Out of scope',
}

const emptyEvidence = (): Evidence => ({ steps: [], cypher: '', rows: [], subgraph: { nodes: [], edges: [] } })

export default function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const [tab, setTab] = useState<'steps' | 'cypher' | 'graph' | 'rows'>('steps')
  const [examples, setExamples] = useState<string[]>([])
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch('/api/examples')
      .then((r) => r.json())
      .then(setExamples)
      .catch(() => setExamples([]))
  }, [])
  useEffect(() => bottom.current?.scrollIntoView({ behavior: 'smooth' }), [messages])

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
          if (final.subgraph.nodes.length) setTab('graph')
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

  const evidence = selected !== null ? messages[selected]?.evidence : undefined

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">⬡</span>
          <div>
            <h1>MediGraph</h1>
            <p>Medical Q&amp;A grounded in a knowledge graph · Qwen3.5-9B (local) + Neo4j PrimeKG</p>
          </div>
        </div>
        {messages.length > 0 && (
          <button className="ghost" onClick={() => (setMessages([]), setSelected(null))} disabled={busy}>
            New chat
          </button>
        )}
      </header>

      <main>
        <section className="chat">
          <div className="messages">
            {messages.length === 0 && (
              <div className="empty">
                <h2>Ask about diseases, drugs, genes and symptoms</h2>
                <p className="muted">
                  Every answer comes from a Cypher query over 129k nodes and 4M relationships. Follow-up
                  questions work too.
                </p>
                <div className="chips">
                  {examples.map((q) => (
                    <button key={q} className="chip" onClick={() => ask(q)}>
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <div
                key={i}
                className={`msg ${m.role} ${selected === i ? 'selected' : ''}`}
                onClick={() => m.evidence && setSelected(i)}
              >
                {m.role === 'assistant' && m.evidence?.status && (
                  <span className={`badge ${m.evidence.status}`}>
                    {STATUS_TEXT[m.evidence.status] ?? m.evidence.status}
                    {m.evidence.ms ? ` · ${(m.evidence.ms / 1000).toFixed(1)}s` : ''}
                  </span>
                )}
                {m.error ? (
                  <p className="error">{m.error}</p>
                ) : m.pending && !m.content ? (
                  <p className="muted thinking">
                    {STEP_NAMES[m.evidence?.steps[m.evidence.steps.length - 1]?.step ?? ''] ?? 'Starting'}…
                  </p>
                ) : (
                  <Markdown text={m.content} />
                )}
              </div>
            ))}
            <div ref={bottom} />
          </div>
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault()
              ask(input)
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={messages.length ? 'Ask a follow-up…' : 'e.g. What drugs treat a heart attack?'}
              disabled={busy}
              aria-label="Question"
            />
            <button type="submit" disabled={busy || !input.trim()}>
              {busy ? 'Thinking…' : 'Ask'}
            </button>
          </form>
          <p className="disclaimer">Research demo, not medical advice. Answers only reflect the PrimeKG dataset.</p>
        </section>

        <aside className="evidence">
          <nav>
            {(['steps', 'cypher', 'graph', 'rows'] as const).map((t) => (
              <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
                {{ steps: 'Pipeline', cypher: 'Cypher', graph: 'Graph', rows: 'Rows' }[t]}
              </button>
            ))}
          </nav>
          <div className="panel">
            {!evidence ? (
              <p className="muted">Ask a question to see how the answer was retrieved.</p>
            ) : tab === 'steps' ? (
              <Steps steps={evidence.steps} />
            ) : tab === 'cypher' ? (
              evidence.cypher ? <pre className="code">{evidence.cypher}</pre> : <p className="muted">No query.</p>
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

function Steps({ steps }: { steps: Step[] }) {
  if (!steps.length) return <p className="muted">Waiting…</p>
  return (
    <ol className="steps">
      {steps.map((s, i) => (
        <li key={i} className={s.ok === false || (s.errors as string[] | undefined)?.length ? 'bad' : ''}>
          <div className="step-head">
            <strong>
              {STEP_NAMES[s.step] ?? s.step}
              {s.step === 'generate_cypher' && Number(s.attempt) > 1 ? ` (retry ${Number(s.attempt) - 1})` : ''}
            </strong>
            <span className="muted">{s.ms} ms</span>
          </div>
          <StepDetail step={s} />
        </li>
      ))}
    </ol>
  )
}

function StepDetail({ step }: { step: Step }) {
  if (step.step === 'contextualize' && step.rewritten) return <p>→ {String(step.question)}</p>
  if (step.step === 'understand') {
    const ents = (step.entities as { mention: string; name: string; label: string; method: string; score: number }[]) ?? []
    return (
      <ul className="ents">
        {ents.map((e, i) => (
          <li key={i}>
            “{e.mention}” → <b>{e.name}</b> <span className="tag">{e.label}</span>{' '}
            <span className="muted">
              {e.method} {e.score.toFixed(2)}
            </span>
          </li>
        ))}
        {step.in_scope === false && <li className="error">Judged out of scope</li>}
        {!ents.length && step.in_scope !== false && <li className="muted">No entities linked</li>}
      </ul>
    )
  }
  if (step.step === 'generate_cypher') return <pre className="code small">{String(step.cypher)}</pre>
  if (step.step === 'validate')
    return step.ok ? (
      <p className="ok">Read-only, schema-valid, entity names exist</p>
    ) : (
      <ul className="errors">{(step.errors as string[]).map((e, i) => <li key={i}>{e}</li>)}</ul>
    )
  if (step.step === 'execute')
    return step.errors ? (
      <ul className="errors">{(step.errors as string[]).map((e, i) => <li key={i}>{e}</li>)}</ul>
    ) : (
      <p>
        {String(step.n_rows)} rows{step.truncated ? ' (truncated)' : ''}
      </p>
    )
  if (step.step === 'fallback') return <p>{String(step.n_rows)} related facts</p>
  return null
}

function Rows({ rows, truncated, fallback }: { rows: Record<string, unknown>[]; truncated?: boolean; fallback?: boolean }) {
  if (!rows.length) return <p className="muted">No rows.</p>
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))]
  const show = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v))
  return (
    <>
      {(truncated || fallback) && (
        <p className="muted">{fallback ? 'Neighbourhood facts (fallback).' : 'First 50 rows shown.'}</p>
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
