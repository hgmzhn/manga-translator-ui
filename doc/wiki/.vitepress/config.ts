import { defineConfig } from 'vitepress'

const sharedTheme = {
  outline: [2, 4],
}

export default defineConfig({
  base: '/manga-translator-ui/',
  lang: 'zh-CN',
  locales: {
    root: { label: '简体中文', lang: 'zh-CN' },
    zh: { label: '简体中文', lang: 'zh-CN', ...sharedTheme },
    en: { label: 'English', lang: 'en-US', ...sharedTheme },
  },
  themeConfig: {
    nav: [
      { text: '中文', link: '/zh/' },
      { text: 'English', link: '/en/' },
    ],
  },
  markdown: {
    config(md) {
      const defaultFence = md.renderer.rules.fence
      md.renderer.rules.fence = (tokens, idx, options, env, self) => {
        const token = tokens[idx]
        const language = token.info.trim().split(/\s+/, 1)[0]
        if (language !== 'mermaid') {
          return defaultFence
            ? defaultFence(tokens, idx, options, env, self)
            : self.renderToken(tokens, idx, options)
        }
        return `\n<div class="wiki-mermaid"><pre class="wiki-mermaid-source">${md.utils.escapeHtml(token.content)}</pre><div class="wiki-mermaid-output" aria-label="Mermaid diagram"></div></div>\n`
      }
    },
  },
})
