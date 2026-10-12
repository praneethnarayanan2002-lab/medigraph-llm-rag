import { useEffect, useRef } from 'react'
import { DataSet } from 'vis-data/peer'
import { Network } from 'vis-network/peer'
import 'vis-network/styles/vis-network.css'
import type { Subgraph } from './api'

const LABEL_COLORS: Record<string, string> = {
  Disease: '#e5484d',
  Drug: '#3e63dd',
  Gene: '#30a46c',
  Phenotype: '#f76b15',
  Exposure: '#8e4ec6',
  Anatomy: '#12a594',
  Pathway: '#d6409f',
  BiologicalProcess: '#978365',
  MolecularFunction: '#978365',
  CellularComponent: '#978365',
}

export function GraphView({ graph }: { graph: Subgraph }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current || !graph.nodes.length) return
    const dark = window.matchMedia('(prefers-color-scheme: dark)').matches
    const text = dark ? '#e8e8ec' : '#1c2024'
    const nodes = new DataSet(
      graph.nodes.map((n) => ({
        id: n.id,
        label: n.name.length > 28 ? n.name.slice(0, 26) + '…' : n.name,
        title: `${n.label}: ${n.name}`,
        color: LABEL_COLORS[n.label] ?? '#8b8d98',
        size: n.seed ? 22 : 12,
        borderWidth: n.seed ? 3 : 1,
        font: { color: text, size: n.seed ? 15 : 12 },
      })),
    )
    const edges = new DataSet(
      graph.edges.map((e, i) => ({
        id: i,
        from: e.source,
        to: e.target,
        title: e.type,
        arrows: 'to',
        color: { color: dark ? '#5a5d66' : '#c1c3cb' },
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
  }, [graph])

  if (!graph.nodes.length) return <p className="muted">No graph to show for this answer.</p>
  const labels = [...new Set(graph.nodes.map((n) => n.label))]
  return (
    <div className="graph-wrap">
      <div ref={ref} className="graph" />
      <div className="legend">
        {labels.map((l) => (
          <span key={l}>
            <i style={{ background: LABEL_COLORS[l] ?? '#8b8d98' }} />
            {l}
          </span>
        ))}
      </div>
    </div>
  )
}
