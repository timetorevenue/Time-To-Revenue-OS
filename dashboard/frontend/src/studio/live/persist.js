// Browser persistence for crash/reopen: unsaved documents and each page's undo history live in
// IndexedDB (large), with a localStorage fallback for the small autosave record. Best effort: a
// private window or blocked storage simply falls back to the server autosave slot.
const DB = 'ttros-studio-live', STORE = 'kv'
let opening = null
function db() {
 if (!opening) opening = new Promise((resolve, reject) => {
  try {
   const req = indexedDB.open(DB, 1)
   req.onupgradeneeded = () => req.result.createObjectStore(STORE)
   req.onsuccess = () => resolve(req.result)
   req.onerror = () => reject(req.error)
  } catch (e) { reject(e) }
 })
 return opening
}
export async function idbGet(key) {
 try { const d = await db(); return await new Promise((res, rej) => { const r = d.transaction(STORE).objectStore(STORE).get(key); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error) }) } catch { return undefined }
}
export async function idbSet(key, value) {
 try { const d = await db(); await new Promise((res, rej) => { const t = d.transaction(STORE, 'readwrite'); t.objectStore(STORE).put(value, key); t.oncomplete = res; t.onerror = () => rej(t.error) }); return true } catch { return false }
}
export async function idbDel(key) {
 try { const d = await db(); await new Promise((res, rej) => { const t = d.transaction(STORE, 'readwrite'); t.objectStore(STORE).delete(key); t.oncomplete = res; t.onerror = () => rej(t.error) }) } catch { /* best effort */ }
}
export const keys = {
 autosave: site => `autosave:${site}`,
 history: (site, page) => `history:${site}:${page}`,
 page: site => `ttros-studio-live:page:${site}`,
}
export function debounce(fn, ms) {
 let t = null
 const run = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms) }
 run.flush = (...args) => { clearTimeout(t); fn(...args) }
 run.cancel = () => clearTimeout(t)
 return run
}
