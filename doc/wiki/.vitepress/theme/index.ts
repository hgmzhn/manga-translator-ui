import DefaultTheme from 'vitepress/theme'
import Layout from './Layout.vue'
import './styles.css'

const renderMermaid = async () => {
  if (typeof document === 'undefined') return
  const blocks = Array.from(document.querySelectorAll<HTMLElement>('.wiki-mermaid'))
  if (!blocks.length) return

  const mermaid = (await import('mermaid')).default
  mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'default' })
  for (const [index, block] of blocks.entries()) {
    const source = block.querySelector<HTMLElement>('.wiki-mermaid-source')
    const output = block.querySelector<HTMLElement>('.wiki-mermaid-output')
    if (!source || !output || output.dataset.rendered === 'true') continue
    try {
      const { svg } = await mermaid.render(`wiki-mermaid-${index}`, source.textContent || '')
      output.innerHTML = svg
      output.dataset.rendered = 'true'
    } catch (error) {
      console.error('Failed to render Mermaid diagram', error)
    }
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
