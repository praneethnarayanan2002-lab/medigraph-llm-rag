export type Turn = { role: 'user' | 'assistant'; content: string }

export type Step = { step: string; ms: number; [key: string]: unknown }

export type GraphNode = { id: string; name: string; label: string; seed: boolean }
export type GraphEdge = { source: string; target: string; type: string }
export type Subgraph = { nodes: GraphNode[]; edges: GraphEdge[] }

export type Final = { answer: string; status: string; subgraph: Subgraph; ms: number }

export type Handlers = {
  onStep: (step: Step) => void
  onRows: (rows: Record<string, unknown>[], meta: { truncated?: boolean; fallback?: boolean }) => void
  onToken: (text: string) => void
  onFinal: (final: Final) => void
  onError: (message: string) => void
}

/** POST a question and consume the Server-Sent Events stream from the pipeline. */
export async function streamChat(question: string, history: Turn[], h: Handlers, signal?: AbortSignal) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, history }),
    signal,
  })
  if (!res.ok || !res.body) {
    h.onError(`Request failed (${res.status})`)
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const raw = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      let event = 'message'
      const data: string[] = []
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
      }
      if (!data.length) continue
      const payload = JSON.parse(data.join('\n'))
      if (event === 'step') h.onStep(payload)
      else if (event === 'rows') h.onRows(payload.rows, payload)
      else if (event === 'token') h.onToken(payload.text)
      else if (event === 'final') h.onFinal(payload)
      else if (event === 'error') h.onError(payload.message)
    }
  }
}
