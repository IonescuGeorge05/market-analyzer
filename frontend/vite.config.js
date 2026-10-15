import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // GitHub Pages serves a project site under /<repo-name>/, not the domain
  // root, so every asset URL in the demo build needs that prefix. Local dev
  // and the normal build stay at "/".
  base: mode === 'demo' ? '/market-analyzer/' : '/',
}))
