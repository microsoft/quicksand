<script setup>
import DefaultTheme from 'vitepress/theme'
import { useData } from 'vitepress'
import { ref, onMounted, onUnmounted } from 'vue'

const { frontmatter, isDark } = useData()
const blurOpacity = ref(0)
const bannerRef = ref(null)

function onScroll() {
  const banner = bannerRef.value
  if (!banner) return
  const bannerHeight = banner.offsetHeight
  blurOpacity.value = Math.min(window.scrollY / bannerHeight, 1)
}

onMounted(() => {
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onScroll, { passive: true })
  onScroll()
})

onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onScroll)
})
</script>

<template>
  <div :class="{ 'home-page': frontmatter.layout === 'home' }">
    <div v-if="frontmatter.layout === 'home'" ref="bannerRef" class="home-banner" />
    <div
      v-if="frontmatter.layout === 'home'"
      class="home-blur-overlay"
      :style="{ opacity: blurOpacity }"
    />
    <DefaultTheme.Layout>
      <template #home-hero-image>
        <div class="hero-install">
          <pre><code><span class="prompt">$ </span>pip install quick-sandbox</code></pre>
        </div>
      </template>
    </DefaultTheme.Layout>
  </div>
</template>
