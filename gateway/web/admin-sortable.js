"use strict";
(() => {
  let drag=null;
  const bindings=new WeakMap();
  function announce(message) {
    const live=document.getElementById('workspace-order-announcement');
    if(live){live.textContent='';requestAnimationFrame(()=>{live.textContent=message;});}
  }
  function handle({id,label,index,total,axis='y',disabled=false}) {
    const button=document.createElement('button');button.type='button';button.className='order-handle';
    button.dataset.orderHandle=id;button.dataset.orderLabel=label;button.disabled=disabled || total<2;
    button.setAttribute('aria-label','调整 '+label+' 顺序，第 '+(index+1)+' 项，共 '+total+' 项');
    button.setAttribute('aria-describedby','workspace-order-instructions');
    button.setAttribute('aria-keyshortcuts',axis==='x'?'ArrowLeft ArrowRight Home End':'ArrowUp ArrowDown Home End');
    button.title=axis==='x'?'拖动左右排序，或使用左右方向键':'拖动上下排序，或使用上下方向键';
    button.append(icon('GripVertical'));return button;
  }
  function moved(ids,id,index) {const next=ids.filter(value=>value!==id);next.splice(index,0,id);return next;}
  function bind(container,{axis='y',onReorder,disabled=()=>false,focusRoot=container}) {
    if(drag?.container===container)drag.cancel();
    bindings.get(container)?.abort();
    const controller=new AbortController();bindings.set(container,controller);
    const listen=(event,handler)=>container.addEventListener(event,handler,{signal:controller.signal});
    const items=()=>[...container.querySelectorAll('[data-order-id]')];
    let busy=false;
    const restore=id=>{
      const root=typeof focusRoot==='function'?focusRoot():focusRoot;
      const button=root?.querySelector('[data-order-handle="'+CSS.escape(id)+'"]');
      if(button){button.focus({preventScroll:true});button.scrollIntoView({block:'nearest',inline:'nearest'});}
    };
    async function commit(ids,id,index) {
      if(busy || disabled())return;
      busy=true;
      try {const accepted=await onReorder(ids,{id,index});if(accepted!==false)announce('已将 '+id+' 移动到第 '+(index+1)+' 项。');}
      catch(error){announce(error.message || '排序失败，请重试。');}
      finally {busy=false;restore(id);}
    }
    listen('keydown',event=>{
      const button=event.target.closest('.order-handle');
      if(!button || !container.contains(button) || button.disabled || busy || disabled())return;
      const ids=items().map(item=>item.dataset.orderId), id=button.dataset.orderHandle, current=ids.indexOf(id);
      const previous=axis==='x'?'ArrowLeft':'ArrowUp', next=axis==='x'?'ArrowRight':'ArrowDown';
      let index=current;
      if(event.key===previous)index--;else if(event.key===next)index++;else if(event.key==='Home')index=0;else if(event.key==='End')index=ids.length-1;else return;
      event.preventDefault();event.stopPropagation();index=Math.max(0,Math.min(ids.length-1,index));
      if(index!==current)void commit(moved(ids,id,index),id,index);
    });
    listen('pointerdown',event=>{
      const button=event.target.closest('.order-handle');
      if(!button || !container.contains(button) || button.disabled || busy || disabled() || event.button!==0 || event.isPrimary===false)return;
      if(drag)drag.cancel();
      const list=items(), ids=list.map(item=>item.dataset.orderId), id=button.dataset.orderHandle;
      if(ids.length<2)return;
      const source=list.find(item=>item.dataset.orderId===id);if(!source)return;
      let active=false, index=ids.indexOf(id), x=event.clientX,y=event.clientY,frame=0,ghost=null;
      const startX=x,startY=y;
      let scrollHost=container;
      while(scrollHost) {
        const style=getComputedStyle(scrollHost), overflow=axis==='x'?style.overflowX:style.overflowY;
        const scrollable=axis==='x'?scrollHost.scrollWidth>scrollHost.clientWidth+2:scrollHost.scrollHeight>scrollHost.clientHeight+2;
        if(/auto|scroll/.test(overflow) && scrollable)break;
        scrollHost=scrollHost.parentElement;
      }
      const rootScroll=!scrollHost || scrollHost===document.body || scrollHost===document.documentElement;
      const clearMarks=()=>list.forEach(item=>item.classList.remove('order-drop-before','order-drop-after'));
      function place(){
        if(!active)return;
        const remaining=list.filter(item=>item!==source),point=axis==='x'?x:y;
        index=remaining.filter(item=>{const box=item.getBoundingClientRect();return point>(axis==='x'?box.left+box.width/2:box.top+box.height/2);}).length;
        clearMarks();const target=remaining[Math.min(index,remaining.length-1)];
        target?.classList.add(index===remaining.length?'order-drop-after':'order-drop-before');
        if(ghost){ghost.style.left=Math.min(innerWidth-ghost.offsetWidth-8,Math.max(8,x+14))+'px';ghost.style.top=Math.min(innerHeight-ghost.offsetHeight-8,Math.max(8,y+14))+'px';}
      }
      function scroll(){
        if(!active || !container.isConnected)return cancel();
        const box=rootScroll?{top:0,left:0,right:innerWidth,bottom:innerHeight}:scrollHost.getBoundingClientRect();
        const point=axis==='x'?x:y,lo=axis==='x'?box.left:box.top,hi=axis==='x'?box.right:box.bottom;
        const speed=point<lo+30?-Math.min(14,(lo+30-point)/2):point>hi-30?Math.min(14,(point-hi+30)/2):0;
        if(speed){if(rootScroll)window.scrollBy(axis==='x'?speed:0,axis==='y'?speed:0);else scrollHost.scrollBy(axis==='x'?speed:0,axis==='y'?speed:0);place();}
        frame=requestAnimationFrame(scroll);
      }
      function start(){
        active=true;source.classList.add('is-order-dragging');container.classList.add('order-axis-'+axis);button.setAttribute('aria-pressed','true');
        document.documentElement.classList.add('workspace-is-sorting');ghost=document.createElement('div');ghost.className='order-drag-ghost';ghost.textContent=button.dataset.orderLabel;document.body.append(ghost);
        frame=requestAnimationFrame(scroll);
      }
      function move(moveEvent){
        if(moveEvent.pointerId!==event.pointerId)return;
        x=moveEvent.clientX;y=moveEvent.clientY;
        if(!active && Math.hypot(x-startX,y-startY)>=5)start();
        if(active){moveEvent.preventDefault();place();}
      }
      function cleanup(){
        cancelAnimationFrame(frame);clearMarks();source.classList.remove('is-order-dragging');button.removeAttribute('aria-pressed');
        document.documentElement.classList.remove('workspace-is-sorting');ghost?.remove();
        button.removeEventListener('pointermove',move);button.removeEventListener('pointerup',up);button.removeEventListener('pointercancel',cancel);button.removeEventListener('lostpointercapture',cancel);
        document.removeEventListener('keydown',escape,true);if(button.hasPointerCapture?.(event.pointerId))button.releasePointerCapture(event.pointerId);drag=null;
      }
      function cancel(){cleanup();if(active)announce('已取消排序。');}
      function up(upEvent){if(upEvent.pointerId!==event.pointerId)return;const changed=active&&index!==ids.indexOf(id);cleanup();if(changed)void commit(moved(ids,id,index),id,index);}
      function escape(keyEvent){if(keyEvent.key==='Escape'){keyEvent.preventDefault();keyEvent.stopPropagation();cancel();restore(id);}}
      drag={cancel,container};button.focus({preventScroll:true});button.setPointerCapture(event.pointerId);
      button.addEventListener('pointermove',move);button.addEventListener('pointerup',up);button.addEventListener('pointercancel',cancel);button.addEventListener('lostpointercapture',cancel);document.addEventListener('keydown',escape,true);
    });
    listen('click',event=>{if(event.target.closest('.order-handle'))event.preventDefault();});
  }
  window.MirraSortable={handle,bind,announce,cancel:()=>drag?.cancel()};
  window.addEventListener('hashchange',()=>drag?.cancel());
})();
