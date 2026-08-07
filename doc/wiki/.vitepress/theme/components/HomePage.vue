<script setup lang="ts">
import { computed } from 'vue'
import { useData, withBase } from 'vitepress'
import {
  ArrowRight,
  BookOpen,
  Boxes,
  CircleHelp,
  Download,
  ExternalLink,
  FileJson,
  Globe2,
  ImageDown,
  Languages,
  Monitor,
  PanelTop,
  PenTool,
  Settings2,
  TerminalSquare,
  Wrench,
} from '@lucide/vue'

const { lang } = useData()
const isZh = computed(() => lang.value.startsWith('zh'))
const locale = computed(() => isZh.value ? 'zh' : 'en')
const sourceImage = withBase('/images/showcase/before.png')
const translatedImage = withBase('/images/showcase/after.png')

const docLink = (path: string) => withBase(`/${locale.value}/${path}`)

const copy = computed(() => isZh.value ? {
  label: '项目文档',
  title: 'Manga Translator 文档',
  intro: '从安装、第一次翻译到精修和批处理，按手头的任务直接进入对应文档。',
  start: '完成第一次翻译',
  install: '选择安装方式',
  tasksTitle: '现在要做什么？',
  tasksIntro: '不用从目录第一项开始读，选一个目标就行。',
  modesTitle: '选择使用方式',
  modesIntro: '桌面端、Web 和命令行使用同一套翻译能力，但适合的工作场景不同。',
  resultTitle: '先看实际结果',
  resultIntro: '原图经过文本检测、OCR、翻译、修复与嵌字，输出可继续在编辑器中调整。',
  before: '翻译前',
  after: '翻译后',
  problemsTitle: '遇到问题',
  problemsIntro: '按现象定位，比在全部设置里逐项尝试更快。',
  allTroubleshooting: '打开故障排查',
  contribute: '参与维护',
  contributeText: '发现步骤过期或界面文案不一致，可以提交 Issue 或 Pull Request。',
  github: '查看 GitHub',
} : {
  label: 'Project documentation',
  title: 'Manga Translator Docs',
  intro: 'Go straight to the task at hand, from installation and a first translation to editing and batch processing.',
  start: 'Complete a first translation',
  install: 'Choose an installation',
  tasksTitle: 'What are you doing?',
  tasksIntro: 'Pick a goal instead of reading the documentation from the beginning.',
  modesTitle: 'Choose how you work',
  modesIntro: 'Desktop, Web, and CLI use the same translation pipeline but fit different workflows.',
  resultTitle: 'See the actual output',
  resultIntro: 'Text detection, OCR, translation, inpainting, and typesetting produce an image you can continue editing.',
  before: 'Before',
  after: 'After',
  problemsTitle: 'Something went wrong',
  problemsIntro: 'Start from the symptom instead of changing every setting.',
  allTroubleshooting: 'Open troubleshooting',
  contribute: 'Contribute',
  contributeText: 'Found an outdated step or mismatched UI label? Open an issue or pull request.',
  github: 'View on GitHub',
})

const tasks = computed(() => isZh.value ? [
  { icon: Download, title: '安装桌面端', detail: 'Windows 便携版、发行版和源码安装', link: 'install/windows-portable' },
  { icon: Languages, title: '翻译第一张图', detail: '从导入图片到检查输出的完整流程', link: 'introduction/first-translation' },
  { icon: Settings2, title: '调整翻译设置', detail: '检测、OCR、翻译、修复和排版', link: 'desktop/settings/' },
  { icon: PenTool, title: '修改译文与画面', detail: '文本框、蒙版、样式和快捷键', link: 'desktop/editor/layout-and-file-list' },
  { icon: Boxes, title: '批量处理文件', detail: '工作流、输入输出和任务管理', link: 'workflows/normal' },
  { icon: FileJson, title: '接入程序或脚本', detail: 'CLI、HTTP API 和 WebSocket', link: 'developer/http-api/translation-endpoints' },
] : [
  { icon: Download, title: 'Install the desktop app', detail: 'Windows portable, releases, and source installs', link: 'install/windows-portable' },
  { icon: Languages, title: 'Translate the first image', detail: 'A complete run from import to output review', link: 'introduction/first-translation' },
  { icon: Settings2, title: 'Tune translation settings', detail: 'Detection, OCR, translation, inpainting, and rendering', link: 'desktop/settings/' },
  { icon: PenTool, title: 'Edit text and artwork', detail: 'Text regions, masks, styles, and shortcuts', link: 'desktop/editor/layout-and-file-list' },
  { icon: Boxes, title: 'Process files in batches', detail: 'Workflows, input, output, and task management', link: 'workflows/normal' },
  { icon: FileJson, title: 'Integrate an app or script', detail: 'CLI, HTTP API, and WebSocket', link: 'developer/http-api/translation-endpoints' },
])

const modes = computed(() => isZh.value ? [
  { icon: Monitor, name: '桌面端', detail: '本机翻译、逐张检查并在可视化编辑器中精修。', link: 'desktop/navigation-and-language', action: '查看桌面端文档' },
  { icon: Globe2, name: 'Web 界面', detail: '在浏览器上传图片、管理任务、账号和翻译历史。', link: 'web/launch-and-access', action: '查看 Web 文档' },
  { icon: TerminalSquare, name: '命令行与 API', detail: '适合批处理、自动化脚本和服务集成。', link: 'cli/command-structure', action: '查看开发文档' },
] : [
  { icon: Monitor, name: 'Desktop app', detail: 'Translate locally, review each image, and refine it in the visual editor.', link: 'desktop/navigation-and-language', action: 'Desktop documentation' },
  { icon: Globe2, name: 'Web UI', detail: 'Upload images and manage tasks, accounts, and history in a browser.', link: 'web/launch-and-access', action: 'Web documentation' },
  { icon: TerminalSquare, name: 'CLI and APIs', detail: 'Built for batches, automation scripts, and service integrations.', link: 'cli/command-structure', action: 'Developer documentation' },
])

const problems = computed(() => isZh.value ? [
  { icon: PanelTop, label: '安装失败、无法启动或端口冲突', link: 'troubleshooting/installation-and-startup' },
  { icon: Wrench, label: '模型下载、GPU 或显存问题', link: 'troubleshooting/model-gpu-and-memory' },
  { icon: CircleHelp, label: 'API 鉴权、限流或请求超时', link: 'troubleshooting/api-auth-rate-limit-and-timeout' },
  { icon: ImageDown, label: '输出图片、JSON 或排版异常', link: 'troubleshooting/output-json-and-rendering' },
] : [
  { icon: PanelTop, label: 'Install failure, startup crash, or port conflict', link: 'troubleshooting/installation-and-startup' },
  { icon: Wrench, label: 'Model download, GPU, or memory issue', link: 'troubleshooting/model-gpu-and-memory' },
  { icon: CircleHelp, label: 'API authentication, rate limit, or timeout', link: 'troubleshooting/api-auth-rate-limit-and-timeout' },
  { icon: ImageDown, label: 'Broken output image, JSON, or typesetting', link: 'troubleshooting/output-json-and-rendering' },
])
</script>

<template>
  <main class="wiki-vue-home">
    <section class="home-intro">
      <div class="home-shell intro-inner">
        <img
          v-motion
          :initial="{ opacity: 0, y: 18, scale: 0.96 }"
          :enter="{ opacity: 1, y: 0, scale: 1, transition: { duration: 520 } }"
          class="intro-logo"
          :src="withBase('/home.png')"
          alt="Manga Translator"
          width="178"
          height="214"
        >
        <div
          v-motion
          :initial="{ opacity: 0, y: 22 }"
          :enter="{ opacity: 1, y: 0, transition: { duration: 480, delay: 90 } }"
          class="intro-copy"
        >
          <p class="intro-label"><BookOpen :size="15" />{{ copy.label }}</p>
          <h1>{{ copy.title }}</h1>
          <p class="intro-text">{{ copy.intro }}</p>
          <div class="intro-actions">
            <a class="home-button home-button-primary" :href="docLink('introduction/first-translation')">
              {{ copy.start }}<ArrowRight :size="17" />
            </a>
            <a class="home-button home-button-secondary" :href="docLink('install/release-download')">
              <Download :size="17" />{{ copy.install }}
            </a>
          </div>
        </div>
      </div>
    </section>

    <section class="home-section home-tasks">
      <div class="home-shell">
        <header class="section-heading">
          <h2>{{ copy.tasksTitle }}</h2>
          <p>{{ copy.tasksIntro }}</p>
        </header>
        <div class="task-grid">
          <a
            v-for="(task, index) in tasks"
            :key="task.link"
            v-motion
            :initial="{ opacity: 0, y: 18 }"
            :visible-once="{ opacity: 1, y: 0, transition: { duration: 380, delay: index * 45 } }"
            class="task-card"
            :href="docLink(task.link)"
          >
            <span class="task-icon"><component :is="task.icon" :size="21" /></span>
            <span class="task-copy"><strong>{{ task.title }}</strong><small>{{ task.detail }}</small></span>
            <ArrowRight class="task-arrow" :size="18" />
          </a>
        </div>
      </div>
    </section>

    <section class="home-section home-modes">
      <div class="home-shell">
        <header class="section-heading section-heading-light">
          <h2>{{ copy.modesTitle }}</h2>
          <p>{{ copy.modesIntro }}</p>
        </header>
        <div class="mode-grid">
          <article v-for="(mode, index) in modes" :key="mode.link" class="mode-item">
            <div class="mode-number">0{{ index + 1 }}</div>
            <component :is="mode.icon" class="mode-icon" :size="25" />
            <h3>{{ mode.name }}</h3>
            <p>{{ mode.detail }}</p>
            <a :href="docLink(mode.link)">{{ mode.action }}<ArrowRight :size="16" /></a>
          </article>
        </div>
      </div>
    </section>

    <section class="home-section home-result">
      <div class="home-shell">
        <header class="section-heading">
          <h2>{{ copy.resultTitle }}</h2>
          <p>{{ copy.resultIntro }}</p>
        </header>
        <div class="result-grid">
          <figure v-motion :initial="{ opacity: 0, x: -22 }" :visible-once="{ opacity: 1, x: 0, transition: { duration: 480 } }">
            <figcaption>{{ copy.before }}</figcaption>
            <img :src="sourceImage" :alt="copy.before" loading="lazy">
          </figure>
          <figure v-motion :initial="{ opacity: 0, x: 22 }" :visible-once="{ opacity: 1, x: 0, transition: { duration: 480, delay: 90 } }">
            <figcaption>{{ copy.after }}</figcaption>
            <img :src="translatedImage" :alt="copy.after" loading="lazy">
          </figure>
        </div>
      </div>
    </section>

    <section class="home-section home-problems">
      <div class="home-shell problem-layout">
        <header class="section-heading">
          <h2>{{ copy.problemsTitle }}</h2>
          <p>{{ copy.problemsIntro }}</p>
          <a class="text-link" :href="docLink('troubleshooting/installation-and-startup')">
            {{ copy.allTroubleshooting }}<ArrowRight :size="16" />
          </a>
        </header>
        <nav class="problem-list" :aria-label="copy.problemsTitle">
          <a v-for="problem in problems" :key="problem.link" :href="docLink(problem.link)">
            <component :is="problem.icon" :size="19" />
            <span>{{ problem.label }}</span>
            <ArrowRight :size="16" />
          </a>
        </nav>
      </div>
    </section>

    <footer class="home-footer">
      <div class="home-shell footer-inner">
        <div><strong>{{ copy.contribute }}</strong><p>{{ copy.contributeText }}</p></div>
        <a href="https://github.com/hgmzhn/manga-translator-ui" target="_blank" rel="noreferrer">
          <ExternalLink :size="18" />{{ copy.github }}
        </a>
      </div>
    </footer>
  </main>
</template>

<style scoped>
.wiki-vue-home {
  --home-ink: #172126;
  --home-muted: #5c696d;
  --home-line: #dce2e2;
  --home-paper: #ffffff;
  --home-soft: #f4f7f6;
  --home-teal: #0d6a70;
  --home-teal-deep: #084e53;
  --home-coral: #b85f3b;
  min-height: calc(100vh - var(--vp-nav-height));
  color: var(--home-ink);
  background: var(--home-paper);
}

.dark .wiki-vue-home {
  --home-ink: #edf2f1;
  --home-muted: #aab6b5;
  --home-line: #344241;
  --home-paper: #151b1c;
  --home-soft: #1c2525;
  --home-teal: #65c8cb;
  --home-teal-deep: #91dcde;
  --home-coral: #e69772;
}

.home-shell {
  width: min(1160px, calc(100% - 48px));
  margin: 0 auto;
}

.home-intro {
  border-bottom: 1px solid var(--home-line);
  background: var(--home-paper);
}

.intro-inner {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: min(570px, calc(100vh - var(--vp-nav-height)));
  padding: 58px 0 52px;
}

.intro-logo {
  flex: 0 0 auto;
  width: 178px;
  height: auto;
  margin-right: 64px;
  object-fit: contain;
}

.intro-copy {
  max-width: 670px;
}

.intro-label {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0 0 16px;
  color: var(--home-teal);
  font-size: 14px;
  font-weight: 700;
}

.intro-copy h1 {
  margin: 0;
  color: var(--home-ink);
  font-size: clamp(40px, 6vw, 68px);
  font-weight: 760;
  line-height: 1.08;
  letter-spacing: 0;
}

.intro-text {
  max-width: 620px;
  margin: 24px 0 0;
  color: var(--home-muted);
  font-size: 19px;
  line-height: 1.8;
}

.intro-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 32px;
}

.home-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  min-height: 46px;
  padding: 0 18px;
  border: 1px solid transparent;
  border-radius: 7px;
  font-size: 15px;
  font-weight: 700;
  text-decoration: none;
  transition: transform 180ms ease, background-color 180ms ease, border-color 180ms ease, color 180ms ease;
}

.home-button:hover {
  transform: translateY(-2px);
}

.home-button-primary {
  color: #fff;
  background: #0d6a70;
}

.home-button-primary:hover {
  color: #fff;
  background: #084e53;
}

.home-button-secondary {
  border-color: var(--home-line);
  color: var(--home-ink);
  background: var(--home-paper);
}

.home-button-secondary:hover {
  border-color: var(--home-teal);
  color: var(--home-teal-deep);
}

.home-section {
  padding: 82px 0;
}

.section-heading {
  max-width: 680px;
  margin-bottom: 34px;
}

.section-heading h2 {
  margin: 0;
  color: var(--home-ink);
  font-size: 30px;
  line-height: 1.25;
  letter-spacing: 0;
}

.section-heading p {
  margin: 10px 0 0;
  color: var(--home-muted);
  font-size: 16px;
  line-height: 1.7;
}

.task-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.task-card {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr) 18px;
  align-items: center;
  gap: 13px;
  min-height: 104px;
  padding: 18px;
  border: 1px solid var(--home-line);
  border-radius: 8px;
  color: var(--home-ink);
  background: var(--home-paper);
  text-decoration: none;
  transition: transform 180ms ease, border-color 180ms ease, box-shadow 180ms ease;
}

.task-card:hover {
  border-color: color-mix(in srgb, var(--home-teal) 62%, var(--home-line));
  color: var(--home-ink);
  transform: translateY(-3px);
  box-shadow: 0 12px 30px rgba(17, 43, 45, 0.09);
}

.task-icon {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border-radius: 7px;
  color: var(--home-teal-deep);
  background: color-mix(in srgb, var(--home-teal) 11%, transparent);
}

.task-copy {
  min-width: 0;
}

.task-copy strong,
.task-copy small {
  display: block;
}

.task-copy strong {
  font-size: 15px;
  line-height: 1.4;
}

.task-copy small {
  margin-top: 5px;
  color: var(--home-muted);
  font-size: 13px;
  line-height: 1.45;
}

.task-arrow {
  color: var(--home-muted);
  transition: transform 180ms ease, color 180ms ease;
}

.task-card:hover .task-arrow {
  color: var(--home-teal);
  transform: translateX(3px);
}

.home-modes {
  color: #f2f6f5;
  background: #172729;
}

.section-heading-light h2 {
  color: #f2f6f5;
}

.section-heading-light p {
  color: #adbcba;
}

.mode-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border-top: 1px solid #405153;
  border-bottom: 1px solid #405153;
}

.mode-item {
  position: relative;
  min-height: 300px;
  padding: 34px 34px 32px;
}

.mode-item + .mode-item {
  border-left: 1px solid #405153;
}

.mode-number {
  position: absolute;
  top: 24px;
  right: 28px;
  color: #728381;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.mode-icon {
  color: #74ced1;
}

.mode-item h3 {
  margin: 48px 0 0;
  color: #fff;
  font-size: 22px;
  letter-spacing: 0;
}

.mode-item p {
  min-height: 74px;
  margin: 13px 0 0;
  color: #adbcba;
  font-size: 15px;
  line-height: 1.65;
}

.mode-item a,
.text-link {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-top: 23px;
  color: #8bdbdd;
  font-size: 14px;
  font-weight: 700;
  text-decoration: none;
}

.mode-item a:hover {
  color: #fff;
}

.mode-item a svg,
.text-link svg {
  transition: transform 180ms ease;
}

.mode-item a:hover svg,
.text-link:hover svg {
  transform: translateX(3px);
}

.home-result {
  background: var(--home-soft);
}

.result-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.result-grid figure {
  position: relative;
  height: 620px;
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--home-line);
  border-radius: 8px;
  background: var(--home-paper);
}

.result-grid figcaption {
  position: absolute;
  z-index: 1;
  top: 14px;
  left: 14px;
  padding: 7px 11px;
  border-radius: 6px;
  color: #fff;
  background: rgba(15, 25, 27, 0.82);
  font-size: 13px;
  font-weight: 700;
}

.result-grid img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: top center;
  transition: transform 420ms cubic-bezier(0.22, 1, 0.36, 1);
}

.result-grid figure:hover img {
  transform: scale(1.015);
}

.problem-layout {
  display: grid;
  grid-template-columns: minmax(240px, 0.7fr) minmax(0, 1.3fr);
  gap: 80px;
  align-items: start;
}

.problem-layout .section-heading {
  margin-bottom: 0;
}

.text-link {
  color: var(--home-teal-deep);
}

.problem-list {
  border-top: 1px solid var(--home-line);
}

.problem-list a {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) 18px;
  align-items: center;
  gap: 12px;
  min-height: 62px;
  border-bottom: 1px solid var(--home-line);
  color: var(--home-ink);
  font-size: 15px;
  text-decoration: none;
  transition: color 180ms ease, padding-left 180ms ease;
}

.problem-list a > svg:first-child {
  color: var(--home-coral);
}

.problem-list a > svg:last-child {
  color: var(--home-muted);
}

.problem-list a:hover {
  padding-left: 8px;
  color: var(--home-teal-deep);
}

.home-footer {
  border-top: 1px solid var(--home-line);
  background: var(--home-soft);
}

.footer-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 32px;
  min-height: 140px;
  padding: 28px 0;
}

.footer-inner strong {
  font-size: 16px;
}

.footer-inner p {
  margin: 5px 0 0;
  color: var(--home-muted);
  font-size: 14px;
}

.footer-inner a {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  color: var(--home-ink);
  font-size: 14px;
  font-weight: 700;
  text-decoration: none;
}

.footer-inner a:hover {
  color: var(--home-teal-deep);
}

@media (max-width: 900px) {
  .intro-inner {
    min-height: auto;
    padding: 64px 0;
  }

  .intro-logo {
    width: 136px;
    margin-right: 38px;
  }

  .intro-copy h1 {
    font-size: 46px;
  }

  .task-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .mode-item {
    padding: 30px 24px;
  }

  .problem-layout {
    grid-template-columns: 1fr;
    gap: 34px;
  }
}

@media (max-width: 640px) {
  .home-shell {
    width: min(100% - 32px, 1160px);
  }

  .intro-inner {
    display: block;
    padding: 40px 0 48px;
  }

  .intro-logo {
    width: 112px;
    margin: 0 0 26px;
  }

  .intro-copy h1 {
    max-width: 100%;
    font-size: 38px;
    overflow-wrap: anywhere;
  }

  .intro-text {
    margin-top: 18px;
    font-size: 16px;
    line-height: 1.7;
  }

  .intro-actions {
    display: grid;
    margin-top: 26px;
  }

  .home-button {
    width: 100%;
  }

  .home-section {
    padding: 58px 0;
  }

  .section-heading h2 {
    font-size: 26px;
  }

  .task-grid,
  .mode-grid,
  .result-grid {
    grid-template-columns: 1fr;
  }

  .task-card {
    min-height: 96px;
  }

  .mode-grid {
    border-bottom: 0;
  }

  .mode-item {
    min-height: 245px;
    border-bottom: 1px solid #405153;
  }

  .mode-item + .mode-item {
    border-left: 0;
  }

  .mode-item p {
    min-height: 0;
  }

  .result-grid figure {
    height: min(112vw, 560px);
  }

  .footer-inner {
    display: block;
    min-height: 0;
    padding: 32px 0;
  }

  .footer-inner a {
    margin-top: 22px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .wiki-vue-home *,
  .wiki-vue-home *::before,
  .wiki-vue-home *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
  }
}
</style>
