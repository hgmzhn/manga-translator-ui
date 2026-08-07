import Teek from 'vitepress-theme-teek'
import { MotionPlugin } from '@vueuse/motion'
import Layout from './Layout.vue'
import { bindSidebarHover } from './sidebarHover'
import 'vitepress-theme-teek/index.css'
import './styles.css'
import './docs.css'
import './sidebar.css'

const openZoomModal = (block: HTMLElement) => {
  const output = block.querySelector<HTMLElement>('.wiki-mermaid-output')
  const svg = output?.querySelector<SVGElement>('svg')
  if (!svg) return

  const modal = document.createElement('div')
  modal.className = 'wiki-mermaid-modal'
  modal.setAttribute('role', 'dialog')
  modal.setAttribute('aria-modal', 'true')

  const card = document.createElement('div')
  card.className = 'wiki-mermaid-modal-card'

  const toolbar = document.createElement('div')
  toolbar.className = 'wiki-mermaid-modal-toolbar'

  const makeToolButton = (label: string, text: string) => {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'wiki-mermaid-modal-tool'
    button.setAttribute('aria-label', label)
    button.textContent = text
    return button
  }

  const zoomOut = makeToolButton('Zoom out', '−')
  const reset = makeToolButton('Reset zoom', '100%')
  const zoomIn = makeToolButton('Zoom in', '+')

  const close = document.createElement('button')
  close.type = 'button'
  close.className = 'wiki-mermaid-modal-close'
  close.setAttribute('aria-label', 'Close')
  close.textContent = '✕'

  const viewport = document.createElement('div')
  viewport.className = 'wiki-mermaid-modal-viewport'
  viewport.setAttribute('aria-label', 'Diagram viewport')

  const stage = document.createElement('div')
  stage.className = 'wiki-mermaid-modal-stage'
  const cloned = svg.cloneNode(true) as SVGElement
  stage.appendChild(cloned)
  viewport.appendChild(stage)
  toolbar.append(zoomOut, reset, zoomIn)
  card.append(toolbar, close, viewport)
  modal.appendChild(card)
  document.body.appendChild(modal)

  let scale = 1
  let offsetX = 0
  let offsetY = 0
  let dragStartX = 0
  let dragStartY = 0
  let startOffsetX = 0
  let startOffsetY = 0
  let dragging = false

  const updateTransform = () => {
    stage.style.transform = `translate(calc(-50% + ${offsetX}px), calc(-50% + ${offsetY}px)) scale(${scale})`
    reset.textContent = `${Math.round(scale * 100)}%`
    reset.setAttribute('aria-label', `Reset zoom, ${Math.round(scale * 100)} percent`)
  }

  const zoomTo = (nextScale: number, pointerX = viewport.clientWidth / 2, pointerY = viewport.clientHeight / 2) => {
    const clamped = Math.min(4, Math.max(0.25, nextScale))
    const ratio = clamped / scale
    const centerX = pointerX - viewport.clientWidth / 2
    const centerY = pointerY - viewport.clientHeight / 2
    offsetX = centerX - ratio * (centerX - offsetX)
    offsetY = centerY - ratio * (centerY - offsetY)
    scale = clamped
    updateTransform()
  }

  const resetView = () => {
    scale = 1
    offsetX = 0
    offsetY = 0
    updateTransform()
  }

  zoomOut.addEventListener('click', () => zoomTo(scale - 0.25))
  zoomIn.addEventListener('click', () => zoomTo(scale + 0.25))
  reset.addEventListener('click', resetView)
  viewport.addEventListener(
    'wheel',
    (event) => {
      event.preventDefault()
      const rect = viewport.getBoundingClientRect()
      zoomTo(scale + (event.deltaY < 0 ? 0.2 : -0.2), event.clientX - rect.left, event.clientY - rect.top)
    },
    { passive: false },
  )
  viewport.addEventListener('pointerdown', (event) => {
    dragging = true
    dragStartX = event.clientX
    dragStartY = event.clientY
    startOffsetX = offsetX
    startOffsetY = offsetY
    viewport.setPointerCapture(event.pointerId)
    viewport.classList.add('is-dragging')
  })
  viewport.addEventListener('pointermove', (event) => {
    if (!dragging) return
    offsetX = startOffsetX + event.clientX - dragStartX
    offsetY = startOffsetY + event.clientY - dragStartY
    updateTransform()
  })
  const stopDragging = (event: PointerEvent) => {
    if (!dragging) return
    dragging = false
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId)
    viewport.classList.remove('is-dragging')
  }
  viewport.addEventListener('pointerup', stopDragging)
  viewport.addEventListener('pointercancel', stopDragging)
  updateTransform()

  const destroy = () => {
    modal.remove()
    document.removeEventListener('keydown', onKey)
    document.body.style.overflow = ''
  }
  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') destroy()
  }
  close.addEventListener('click', destroy)
  modal.addEventListener('click', (event) => {
    if (event.target === modal) destroy()
  })
  document.addEventListener('keydown', onKey)
  document.body.style.overflow = 'hidden'
}

const bindZoom = (block: HTMLElement) => {
  const btn = block.querySelector<HTMLElement>('.wiki-mermaid-zoom')
  if (!btn || btn.dataset.bound === 'true') return
  btn.dataset.bound = 'true'
  btn.addEventListener('click', () => openZoomModal(block))
}

const renderMermaid = async () => {
  if (typeof document === 'undefined') return
  const blocks = Array.from(document.querySelectorAll<HTMLElement>('.wiki-mermaid'))
  if (!blocks.length) return

  const { renderMermaidSVG } = await import('beautiful-mermaid')
  for (const block of blocks) {
    const source = block.querySelector<HTMLElement>('.wiki-mermaid-source')
    const output = block.querySelector<HTMLElement>('.wiki-mermaid-output')
    if (!source || !output || output.dataset.rendered === 'true') continue
    try {
      let svg = renderMermaidSVG(source.textContent || '', {
        bg: 'var(--vp-c-bg)',
        fg: 'var(--vp-c-text-1)',
        line: 'var(--vp-c-divider)',
        accent: 'var(--vp-c-brand-1)',
        muted: 'var(--vp-c-text-2)',
        surface: 'var(--vp-c-bg-soft)',
        border: 'var(--vp-c-divider)',
        font: 'Inter, "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI", sans-serif',
        nodeSpacing: 40,
        layerSpacing: 55,
      })
      // Strip external Google Fonts @import (offline / mainland-China friendly)
      svg = svg.replace(/@import\s+url\([^)]*\)\s*;?/g, '')
      output.innerHTML = svg
      output.dataset.rendered = 'true'
    } catch (error) {
      console.error('Failed to render Mermaid diagram', error)
    }
    bindZoom(block)
  }
}

export default {
  extends: Teek,
  Layout,
  async enhanceApp(context) {
    context.app.use(MotionPlugin)
    if (typeof window === 'undefined') return

    bindSidebarHover()
    window.setTimeout(() => void renderMermaid(), 0)
    const previousAfterRouteChange = context.router.onAfterRouteChange
    context.router.onAfterRouteChange = async (to) => {
      await previousAfterRouteChange?.(to)
      window.setTimeout(() => void renderMermaid(), 0)
    }
  },
}






