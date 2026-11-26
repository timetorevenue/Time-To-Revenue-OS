// Website-specific API extension; existing session/proxy authentication is preserved.
import axios from 'axios'
const api = axios.create({ baseURL: '/api/website/preparations', timeout: 20000 })
export const getWebsitePreparations = () => api.get('').then(r => r.data)
export const postWebsitePreparation = body => api.post('', body).then(r => r.data)
export const planWebsiteSeo = body => api.post('/seo-plan', body).then(r => r.data)
export const queueWebsitePreparation = id => api.post(`/${encodeURIComponent(id)}/queue`).then(r => r.data)
