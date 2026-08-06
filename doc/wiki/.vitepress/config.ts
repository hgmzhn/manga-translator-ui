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
})
