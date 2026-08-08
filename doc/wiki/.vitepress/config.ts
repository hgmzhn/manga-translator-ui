import { defineConfig } from 'vitepress'
import type { TeekConfig } from 'vitepress-theme-teek'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const wikiDir = fileURLToPath(new URL('..', import.meta.url))

const teekTheme: TeekConfig = {
  teekTheme: true,
  teekHome: false,
  vpHome: true,
  pageStyle: 'default',
  sidebarTrigger: true,
  windowTransition: true,
  viewTransition: { enabled: true, mode: 'out-in', duration: 420 },
  themeEnhance: { enabled: false },
  articleAnalyze: { showInfo: false, imageViewer: { enabled: true } },
  articleUpdate: { enabled: false },
  breadcrumb: { enabled: true, showCurrentName: false },
  codeBlock: { enabled: true, collapseHeight: 720, langTextTransform: 'none' },
  footerInfo: {
    theme: { show: false },
    copyright: { show: false },
  },
}

type SidebarItem = { text: string; link?: string; items?: SidebarItem[]; collapsed?: boolean }

const TOP_LEVEL_ORDER = ['introduction', 'install', 'desktop', 'workflows', 'web', 'cli', 'developer', 'community', 'reference', 'troubleshooting']

// 侧边栏目录名多语言：zh 用中文目录名，en 用英文原名
const DIR_LABELS: Record<string, Record<string, string>> = {
  zh: {
    introduction: '简介',
    install: '安装',
    desktop: '桌面端',
    workflows: '工作流',
    web: 'Web 端',
    cli: '命令行',
    developer: '开发者',
    community: '社区维护',
    reference: '参考',
    troubleshooting: '故障排查',
    debugging: '调试',
    translation: '翻译',
    settings: '设置',
    translator: '翻译器',
    'api-management': 'API 管理',
    prompts: '提示词',
    'replacement-rules': '替换规则',
    'rich-text-rules': '富文本规则',
    'batch-management': '批量管理',
    editor: '编辑器',
    'http-api': 'HTTP API',
  },
}

function dirLabel(prefix: string, name: string): string {
  return DIR_LABELS[prefix.slice(0, 2)]?.[name] ?? name
}
const DESKTOP_ORDER = ['translation', 'settings', 'translator', 'api-management', 'prompts', 'replacement-rules', 'rich-text-rules', 'batch-management', 'editor']
const INSTALL_ORDER = ['windows-portable.md', 'linux-and-macos.md', 'release-download.md', 'docker.md', 'source-windows.md', 'requirements.md', 'update-and-version-switching.md', 'uninstall-and-data-cleanup.md']

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
  if (dir.endsWith('install')) {
    return Object.fromEntries(INSTALL_ORDER.map((name, i) => [name, i]))
  }
  return Object.fromEntries(TOP_LEVEL_ORDER.map((name, i) => [name, i]))
}

function buildTree(dir: string, base: string, prefix: string): SidebarItem[] {
  const entries = readdirSync(dir, { withFileTypes: true })
    .filter((e) => !e.name.startsWith('.') && e.name !== 'public')
    .sort((a, b) => {
      if (a.name === 'index.md') return -1
      if (b.name === 'index.md') return 1
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
      if (children.length) items.push({ text: dirLabel(prefix, e.name), items: children, collapsed: true })
    } else if (e.name.endsWith('.md')) {
      const rel = relative(base, full).replaceAll(sep, '/').replace(/\.md$/, '')
      const link = rel === 'index' ? `/${prefix}` : `/${prefix}${rel}`
      items.push({ text: titleOf(full, e.name.replace(/\.md$/, '')), link })
    }
  }
  return items
}

export default defineConfig({
  base: '/manga-translator-ui/',
  lang: 'zh-CN',
  title: 'Manga Translator Wiki',
  srcExclude: [
    'BLUEPRINT.md',
    'PAGE_GUIDELINES.md',
    'TODO.md',
    'data/**',
    'research/**',
    'public/**/*.md',
  ],
  head: [
    ['link', { rel: 'icon', type: 'image/png', href: '/manga-translator-ui/favicon.png' }],
  ],
  locales: {
    root: { label: '简体中文', lang: 'zh-CN' },
    zh: {
      label: '简体中文',
      lang: 'zh-CN',
      themeConfig: {
        nav: [
          { text: '快速开始', link: '/zh/introduction/first-translation' },
          { text: '设置', link: '/zh/reference/settings-index' },
          { text: '排障', link: '/zh/troubleshooting/installation-and-startup' },
        ],
        outline: { label: '本页内容', level: [2, 4] },
        docFooter: { prev: '上一篇', next: '下一篇' },
        lastUpdated: { text: '最后更新' },
        returnToTopLabel: '返回顶部',
        sidebarMenuLabel: '目录',
        darkModeSwitchLabel: '外观',
        lightModeSwitchTitle: '切换到浅色模式',
        darkModeSwitchTitle: '切换到深色模式',
      },
    },
    en: {
      label: 'English',
      lang: 'en-US',
      themeConfig: {
        nav: [
          { text: 'Quick start', link: '/en/introduction/first-translation' },
          { text: 'Settings', link: '/en/reference/settings-index' },
          { text: 'Troubleshooting', link: '/en/troubleshooting/installation-and-startup' },
        ],
        outline: { label: 'On this page', level: [2, 4] },
        docFooter: { prev: 'Previous', next: 'Next' },
        lastUpdated: { text: 'Last updated' },
        returnToTopLabel: 'Back to top',
        sidebarMenuLabel: 'Menu',
        darkModeSwitchLabel: 'Appearance',
        lightModeSwitchTitle: 'Switch to light theme',
        darkModeSwitchTitle: 'Switch to dark theme',
      },
    },
  },
  themeConfig: {
    ...teekTheme,
    logo: '/logo.png',
    search: { provider: 'local' },
    socialLinks: [
      { icon: 'github', link: 'https://github.com/hgmzhn/manga-translator-ui' },
    ],
    sidebar: {
      '/zh/': buildTree(join(wikiDir, 'zh'), join(wikiDir, 'zh'), 'zh/'),
      '/en/': buildTree(join(wikiDir, 'en'), join(wikiDir, 'en'), 'en/'),
    },
  },
  vite: {
    ssr: {
      noExternal: ['vitepress-theme-teek'],
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
