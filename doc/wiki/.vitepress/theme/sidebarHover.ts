const HOVER_OPEN_DELAY = 180
const HOVER_CLOSE_DELAY = 90
const DESKTOP_HOVER_QUERY = '(min-width: 960px) and (hover: hover) and (pointer: fine)'

type SidebarGroup = HTMLElement

interface HoverState {
  autoOpened: boolean
  closeTimer?: number
  openTimer?: number
  suppressUntilLeave: boolean
}

declare global {
  interface Window {
    __wikiSidebarHoverCleanup?: () => void
  }
}

const states = new Map<SidebarGroup, HoverState>()
const clickStartState = new WeakMap<SidebarGroup, boolean>()

const getGroup = (target: EventTarget | null) => {
  if (!(target instanceof Element)) return null
  // Match nested groups as well as top-level sections. The closest match keeps
  // pointer events scoped to the group currently under the cursor.
  return target.closest<SidebarGroup>('.VPSidebarItem.collapsible')
}

const getHeader = (group: SidebarGroup) =>
  Array.from(group.children).find(
    (child): child is HTMLElement => child instanceof HTMLElement && child.classList.contains('item'),
  )

const getItems = (group: SidebarGroup) =>
  Array.from(group.children).find(
    (child): child is HTMLElement => child instanceof HTMLElement && child.classList.contains('items'),
  )

const containsActivePage = (group: SidebarGroup) =>
  group.classList.contains('has-active') ||
  group.classList.contains('is-active') ||
  group.querySelector('.VPSidebarItem.is-active') !== null

const clearTimers = (state: HoverState) => {
  if (state.openTimer !== undefined) window.clearTimeout(state.openTimer)
  if (state.closeTimer !== undefined) window.clearTimeout(state.closeTimer)
  state.openTimer = undefined
  state.closeTimer = undefined
}

const getState = (group: SidebarGroup) => {
  let state = states.get(group)
  if (!state) {
    state = { autoOpened: false, suppressUntilLeave: false }
    states.set(group, state)
  }
  return state
}

const toggleGroup = (group: SidebarGroup) => {
  const header = getHeader(group)
  if (!header) return false

  const target = header.querySelector<HTMLElement>(':scope > .caret') ?? header
  target.click()
  return true
}

const animateToggle = (group: SidebarGroup, wasCollapsed: boolean) => {
  if (!group.isConnected) return

  const isCollapsed = group.classList.contains('collapsed')
  const items = getItems(group)
  if (!items || wasCollapsed === isCollapsed) return
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

  items.getAnimations().forEach((animation) => animation.cancel())
  const expandedHeight = `${items.scrollHeight}px`
  items.animate(
    wasCollapsed
      ? [{ height: '0px', opacity: 0 }, { height: expandedHeight, opacity: 1 }]
      : [{ height: expandedHeight, opacity: 1 }, { height: '0px', opacity: 0 }],
    {
      duration: wasCollapsed ? 240 : 190,
      easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
    },
  )
}

const isTrueEnterOrLeave = (group: SidebarGroup, relatedTarget: EventTarget | null) =>
  !(relatedTarget instanceof Node && group.contains(relatedTarget))

export const bindSidebarHover = () => {
  if (typeof window === 'undefined') return

  window.__wikiSidebarHoverCleanup?.()

  const hoverMedia = window.matchMedia(DESKTOP_HOVER_QUERY)
  let activeCheckFrame = 0

  const ensureActiveGroupIsOpen = () => {
    window.cancelAnimationFrame(activeCheckFrame)
    activeCheckFrame = window.requestAnimationFrame(() => {
      document
        .querySelectorAll<SidebarGroup>('.VPSidebarItem.collapsible.has-active.collapsed')
        .forEach((group) => toggleGroup(group))
    })
  }

  const onPointerOver = (event: PointerEvent) => {
    if (!hoverMedia.matches) return
    const group = getGroup(event.target)
    if (!group || !isTrueEnterOrLeave(group, event.relatedTarget)) return

    const state = getState(group)
    if (state.closeTimer !== undefined) window.clearTimeout(state.closeTimer)
    state.closeTimer = undefined
    if (state.suppressUntilLeave || !group.classList.contains('collapsed')) return

    state.openTimer = window.setTimeout(() => {
      state.openTimer = undefined
      if (!group.matches(':hover') || !group.classList.contains('collapsed')) return
      if (toggleGroup(group)) {
        state.autoOpened = true
        group.classList.add('wiki-hover-open')
      }
    }, HOVER_OPEN_DELAY)
  }

  const onPointerOut = (event: PointerEvent) => {
    const group = getGroup(event.target)
    if (!group || !isTrueEnterOrLeave(group, event.relatedTarget)) return

    const state = getState(group)
    if (state.openTimer !== undefined) window.clearTimeout(state.openTimer)
    state.openTimer = undefined
    state.suppressUntilLeave = false

    if (!hoverMedia.matches || !state.autoOpened || containsActivePage(group)) {
      state.autoOpened = false
      group.classList.remove('wiki-hover-open')
      states.delete(group)
      return
    }

    state.closeTimer = window.setTimeout(() => {
      state.closeTimer = undefined
      if (group.matches(':hover')) return
      if (!group.classList.contains('collapsed')) toggleGroup(group)
      state.autoOpened = false
      group.classList.remove('wiki-hover-open')
      states.delete(group)
    }, HOVER_CLOSE_DELAY)
  }

  const onToggleInteraction = (event: MouseEvent | KeyboardEvent) => {
    if (event instanceof KeyboardEvent && event.key !== 'Enter') return
    const group = getGroup(event.target)
    if (!group) return
    const header = getHeader(group)
    if (!header || !(event.target instanceof Node) || !header.contains(event.target)) return

    clickStartState.set(group, group.classList.contains('collapsed'))
    window.requestAnimationFrame(() => {
      const wasCollapsed = clickStartState.get(group)
      clickStartState.delete(group)
      if (wasCollapsed !== undefined) animateToggle(group, wasCollapsed)
    })

    if (event instanceof MouseEvent && !event.isTrusted) return
    const state = getState(group)
    clearTimers(state)
    state.autoOpened = false
    state.suppressUntilLeave = group.matches(':hover')
    group.classList.remove('wiki-hover-open')
  }

  const onMediaChange = () => {
    if (hoverMedia.matches) return
    for (const [group, state] of states) {
      clearTimers(state)
      if (state.autoOpened && !containsActivePage(group) && !group.classList.contains('collapsed')) {
        toggleGroup(group)
      }
      group.classList.remove('wiki-hover-open')
    }
    states.clear()
  }

  const observer = new MutationObserver(ensureActiveGroupIsOpen)
  observer.observe(document.body, {
    attributeFilter: ['class'],
    attributes: true,
    childList: true,
    subtree: true,
  })

  document.addEventListener('pointerover', onPointerOver)
  document.addEventListener('pointerout', onPointerOut)
  document.addEventListener('click', onToggleInteraction, true)
  document.addEventListener('keydown', onToggleInteraction, true)
  hoverMedia.addEventListener('change', onMediaChange)
  ensureActiveGroupIsOpen()

  window.__wikiSidebarHoverCleanup = () => {
    document.removeEventListener('pointerover', onPointerOver)
    document.removeEventListener('pointerout', onPointerOut)
    document.removeEventListener('click', onToggleInteraction, true)
    document.removeEventListener('keydown', onToggleInteraction, true)
    hoverMedia.removeEventListener('change', onMediaChange)
    observer.disconnect()
    window.cancelAnimationFrame(activeCheckFrame)
    for (const [group, state] of states) {
      clearTimers(state)
      group.classList.remove('wiki-hover-open')
    }
    states.clear()
    delete window.__wikiSidebarHoverCleanup
  }
}
