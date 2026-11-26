// Runtime site configuration. Build/dev: TTROS_STUDIO_SITE_ID and TTROS_SITE_DOMAIN are injected by vite.config.js;
// node tests read them from the environment. Defaults are this installation's registered values.
const env = (typeof process !== 'undefined' && process.env) || {}
const injected = (typeof __TTROS_SITE__ !== 'undefined' && __TTROS_SITE__) || {}
export const SITE_ID = injected.id || env.TTROS_STUDIO_SITE_ID || 'default-site'
export const SITE_DOMAIN = injected.domain || env.TTROS_SITE_DOMAIN || 'core-site.example'
export const SITE_URL = `https://www.${SITE_DOMAIN}`
