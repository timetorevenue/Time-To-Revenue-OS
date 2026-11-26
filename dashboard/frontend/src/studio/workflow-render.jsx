// Shared by the canvas and the real server-tree adapter. The figure's markup stays source-owned:
// only its text destinations and the bounded design properties (workflow-graphic.js) change.
import React from 'react'
import {localStyle} from './design.js'
import {nestedRules} from './nested.js'
import { graphicStyle,graphicAttributes } from './workflow-graphic.js'

// Apply a native child to the original, verified source component (also used in the canvas).
export function bindWorkflowFigure(original,props,extra={}) {
 const names=Object.values(nestedRules.slots.Section0.workflow.legacy.fields)
 let index=0
 function visit(node) {
  if(typeof node==='string'&&node.trim()) {
   if(index>=names.length)throw Error('WorkflowFigure text structure drift.')
   return props[names[index++]]
  }
  if(!React.isValidElement(node))return node
  return node.props.children===undefined?node:React.cloneElement(node,{},React.Children.map(node.props.children,visit))
 }
 const figure=visit(original)
 if(index!==names.length)throw Error('WorkflowFigure text structure drift.')
 return React.cloneElement(figure,{'data-studio-component':'WorkflowFigure','data-studio-component-id':props.id,'data-studio-element':'section',...graphicAttributes(props.graphic),...extra,style:{...original.props.style,...graphicStyle(props.graphic),...localStyle(props.styles?.section)}})
}

// Source renderers are registered separately from the reusable native slot tree.
export const nestedRenderers={WorkflowFigure:bindWorkflowFigure}
