/* TW-165: pure scroll-math for the StoryArc story arc.
 * Extracted from StoryArc.jsx so it can be unit-tested with node.
 * stagePos: 0..3 (stage i centered at i + 0.5).
 */

export function clamp(v, min, max) {
  return Math.min(max, Math.max(min, v))
}

/**
 * Style values for a foreground panel at `index`.
 * Panel fades 0.3 -> 0.5 off-center; pointerEvents mirrors interactivity
 * so invisible panels are also non-interactive (keyboard trap fix: the
 * caller sets el.inert = !pointerEvents).
 */
export function stageStyles(stagePos, index, isCoarse) {
  const rel = stagePos - (index + 0.5)
  const abs = Math.abs(rel)
  const opacity = clamp(1 - (abs - 0.3) / 0.2, 0, 1)
  const rise = isCoarse ? 40 : 90
  const y = clamp(rel, -0.6, 0.6) * rise
  const scale = isCoarse ? 1 : 1 - Math.min(abs, 0.5) * 0.05
  return {
    opacity,
    transform: `translate3d(0, ${y.toFixed(1)}px, 0) scale(${scale.toFixed(4)})`,
    pointerEvents: opacity > 0.4,
  }
}

/**
 * Background-layer opacity — wider overlap than panels so the world never
 * goes blank between stages.
 */
export function bgStyles(stagePos, index) {
  const rel = stagePos - (index + 0.5)
  return { opacity: clamp(1 - (Math.abs(rel) - 0.4) / 0.2, 0, 1) }
}
