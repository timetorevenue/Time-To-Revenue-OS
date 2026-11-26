// Read-only SSR snapshot of actual source components.
import { build } from 'esbuild'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
const require=createRequire(import.meta.url)
const source='/home/operator/ttros-projects/default-site'
const entry=`import React from 'react';import {renderToStaticMarkup} from 'react-dom/server';import Header from '${source}/components/Header.tsx';import Footer from '${source}/components/Footer.tsx';export const templates={header:renderToStaticMarkup(React.createElement(Header)),footer:renderToStaticMarkup(React.createElement(Footer))};`
const result = await build({ stdin: { contents: entry, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic', plugins: [{ name: 'snapshot-shims', setup(b) {
  b.onResolve({ filter: /^react($|\/)|^react-dom\// }, a => ({ path: require.resolve(a.path), external: true }))
  b.onResolve({ filter: /^@\// }, a => ({ path: path.join(source, a.path.slice(2)) + (fs.existsSync(path.join(source, a.path.slice(2)) + '.tsx') ? '.tsx' : '.ts') }))
  b.onResolve({ filter: /^next\// }, a => ({ path: a.path, namespace: 'shim' }))
  b.onLoad({ filter: /.*/, namespace: 'shim' }, a => ({ loader: 'jsx', contents: a.path === 'next/navigation' ? `export const usePathname=()=>'/';` : `import React from 'react'; export default function Shim({children,fill,priority,sizes,unoptimized,...p}){return React.createElement('${a.path === 'next/image' ? 'img' : 'a'}',p,children)}` }))
} }] })
const module = { exports: {} }; new Function('require', 'module', 'exports', result.outputFiles[0].text)(require, module, module.exports)
fs.writeFileSync('src/studio/shared-templates.json',JSON.stringify(module.exports.templates,null,2)+'\n')
