import { defineConfig } from 'vitepress'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const wikiDir = fileURLToPath(new URL('..', import.meta.url))

type SidebarItem = { text: string; link?: string; items?: SidebarItem[] }

const TOP_LEVEL_ORDER = ['introduction', 'install', 'desktop', 'workflows', 'web', 'cli', 'developer', 'reference', 'troubleshooting']
const DESKTOP_ORDER = ['translation', 'settings', 'translator', 'api-management', 'prompts', 'replacement-rules', 'rich-text-rules', 'batch-management', 'editor']

function titleOf(file: string, fallback: string): string {
  const raw = readFileSync(file, 'utf8')
  const m = raw.match(/^---\r?\n([\s\S]*?)\r?\n---/)
  if (m) {
    const t = m[1].match(/^title:\s*(.+)$/m)
    if (t) return t[1].trim().replace(/（待补充）\s*$/, '').trim()
  }
  return fallback
}

function orderFor(dir: string): Record<string, number> {
  if (dir.endsWith('desktop')) {
    return Object.fromEntries(DESKTOP_ORDER.map((name, i) => [name, i]))
  }
  return Object.fromEntries(TOP_LEVEL_ORDER.map((name, i) => [name, i]))
}

function buildTree(dir: string, base: string, prefix: string): SidebarItem[] {
  const entries = readdirSync(dir, { withFileTypes: true })
    .filter((e) => !e.name.startsWith('.') && e.name !== 'public')
    .sort((a, b) => {
      const order = orderFor(dir)
      const ao = order[a.name] ?? 999
      const bo = order[b.name] ?? 999
      if (ao !== bo) return ao - bo
      if (a.isDirectory() !== b.isDirectory()) return a.isDirectory() ? -1 : 1
      return a.name.localeCompare(b.name)
    })
  const items: SidebarItem[] = []
  for (const e of entries) {
    const full = join(dir, e.name)
    if (e.isDirectory()) {
      const children = buildTree(full, base, prefix)
      if (children.length) items.push({ text: e.name, items: children })
    } else if (e.name.endsWith('.md')) {
      const rel = relative(base, full).replaceAll(sep, '/').replace(/\.md$/, '')
      const link = rel === 'index' ? `/${prefix}` : `/${prefix}${rel}`
      items.push({ text: titleOf(full, e.name.replace(/\.md$/, '')), link })
    }
  }
  return items
}

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
    sidebar: {
      '/zh/': buildTree(join(wikiDir, 'zh'), join(wikiDir, 'zh'), 'zh/'),
      '/en/': buildTree(join(wikiDir, 'en'), join(wikiDir, 'en'), 'en/'),
    },
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
        return `\n<div class="wiki-mermaid"><pre class="wiki-mermaid-source">${md.utils.escapeHtml(token.content)}</pre><div class="wiki-mermaid-output" aria-label="Mermaid diagram"></div><button type="button" class="wiki-mermaid-zoom" aria-label="Zoom diagram"></button></div>\n`
      }
    },
  },
})


