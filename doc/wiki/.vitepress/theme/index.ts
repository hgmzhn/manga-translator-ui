import DefaultTheme from 'vitepress/theme'
import Layout from './Layout.vue'
import './styles.css'

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

  const close = document.createElement('button')
  close.type = 'button'
  close.className = 'wiki-mermaid-modal-close'
  close.setAttribute('aria-label', 'Close')
  close.textContent = '✕'

  const cloned = svg.cloneNode(true) as SVGElement
  card.appendChild(close)
  card.appendChild(cloned)
  modal.appendChild(card)
  document.body.appendChild(modal)

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
  extends: DefaultTheme,
  Layout,
  enhanceApp({ router }) {
    if (typeof window === 'undefined') return
    window.setTimeout(() => void renderMermaid(), 0)
    router.onAfterRouteChange = () => {
      window.setTimeout(() => void renderMermaid(), 0)
    }
  },
}






