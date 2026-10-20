import { useEffect, useRef, useState } from 'react'
import { DataSet } from 'vis-data/peer'
import { Network } from 'vis-network/peer'
import 'vis-network/styles/vis-network.css'
import type { Subgraph } from './api'

// Muted colours that stay distinct in lightness as well as hue, one set per theme.
const PALETTE = {
  light: {
    Disease: '#0f766e',
    Drug: '#3b5b92',
    Gene: '#7a5aa6',
    Phenotype: '#a8601b',
    Exposure: '#9a3b5c',
    Anatomy: '#4d7c0f',
    Pathway: '#64748b',
    other: '#94a3b8',
    text: '#1a2530',
    edge: '#cbd5e1',
  },
  dark: {
    Disease: '#2dd4bf',
    Drug: '#8fb3e8',
    Gene: '#c4b2e8',
    Phenotype: '#f0b46b',
    Exposure: '#e79ab5',
    Anatomy: '#a3c76d',
    Pathway: '#9ca3af',
    other: '#64748b',
    text: '#e6edf3',
    edge: '#2f3e55',
  },
}

type Palette = (typeof PALETTE)['light']

function useDark(): boolean {
  const query = '(prefers-color-scheme: dark)'
  const [dark, setDark] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const onChange = () => setDark(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return dark
}

const colorFor = (p: Palette, label: string) => (p as Record<string, string>)[label] ?? p.other

export function GraphView({ graph }: { graph: Subgraph }) {
  const ref = useRef<HTMLDivElement>(null)
  const dark = useDark()
  const palette = dark ? PALETTE.dark : PALETTE.light

  useEffect(() => {
    if (!ref.current || !graph.nodes.length) return
    const nodes = new DataSet(
      graph.nodes.map((n) => ({
        id: n.id,
        label: n.name.length > 28 ? n.name.slice(0, 26) + '…' : n.name,
        title: `${n.label}: ${n.name}`,
        color: { background: colorFor(palette, n.label), border: colorFor(palette, n.label) },
        size: n.seed ? 16 : 8,
        borderWidth: 0,
        font: { color: palette.text, size: n.seed ? 14 : 12, face: 'Inter, system-ui, sans-serif' },
      })),
    )
    const edges = new DataSet(
      graph.edges.map((e, i) => ({
        id: i,
        from: e.source,
        to: e.target,
        title: e.type,
        arrows: { to: { enabled: true, scaleFactor: 0.4 } },
        color: { color: palette.edge, highlight: palette.text, hover: palette.text },
      })),
    )
    const network = new Network(
      ref.current,
      { nodes, edges },
      {
        nodes: { shape: 'dot' },
        edges: { smooth: false, width: 1 },
        physics: { solver: 'forceAtlas2Based', stabilization: { iterations: 150 } },
        interaction: { hover: true, tooltipDelay: 80 },
      },
    )
    return () => network.destroy()
  }, [graph, palette])

  if (!graph.nodes.length) return <p className="panel-empty">No graph to show for this answer.</p>
  const labels = [...new Set(graph.nodes.map((n) => n.label))]
  return (
    <div className="graph-wrap">
      <div ref={ref} className="graph" role="img" aria-label={`Graph of ${graph.nodes.length} entities and their relationships`} />
      <div className="legend">
        {labels.map((l) => (
          <span key={l}>
            <i style={{ background: colorFor(palette, l) }} />
            {l}
          </span>
        ))}
      </div>
    </div>
  )
}
