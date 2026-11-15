// Tour guiado (mesmo padrão do Cérebro Pro): balões apontando para a tela real, resto escurecido. Roteiro em window.TOUR = {chave, passos}.
// Abre sozinho 1x (localStorage) e por window.SBTour.abrir(). Passo sem elemento visível vira balão no centro.
;(() => {
  const css = `.sb-tour{position:fixed;inset:0;z-index:9000}
.sb-tour-furo{position:fixed;border-radius:14px;box-shadow:0 0 0 9999px rgba(5,6,9,.74);outline:2px solid var(--ac);outline-offset:2px;transition:all .28s cubic-bezier(.2,.8,.2,1);pointer-events:none}
.sb-tour-b{position:fixed;width:min(360px,calc(100vw - 24px));background:var(--card);color:var(--fg);border:1px solid var(--bd);border-radius:16px;padding:16px 18px 14px;box-shadow:0 18px 50px rgba(0,0,0,.5);transition:left .28s,top .28s;outline:none;animation:sbtIn .25s ease;font:15px/1.5 system-ui,sans-serif}
.sb-tour-b.centro{left:50%!important;top:50%!important;transform:translate(-50%,-50%)}
@keyframes sbtIn{from{opacity:0;translate:0 6px}to{opacity:1;translate:0 0}}
.sb-tour-b b.t{display:block;font-family:"Display",system-ui,sans-serif;font-size:17px;margin:0 28px 6px 0}
.sb-tour-b p{margin:0 0 12px;color:var(--fg)}
.sb-tour-x{position:absolute;top:10px;right:10px;background:none;border:0;color:var(--mut);font-size:20px;cursor:pointer}
.sb-tour-pe{display:flex;align-items:center;justify-content:space-between;gap:8px}
.sb-tour-pt{display:flex;gap:4px}.sb-tour-pt i{width:6px;height:6px;border-radius:50%;background:var(--bd)}.sb-tour-pt i.on{background:var(--ac);width:16px;border-radius:3px}
.sb-tour-bt{display:flex;gap:6px}.sb-tour-bt button{border-radius:10px;padding:8px 12px;font:700 13px system-ui;cursor:pointer;border:1px solid var(--bd);background:transparent;color:var(--fg)}
.sb-tour-bt .pri{background:var(--ac);border-color:var(--ac);color:var(--ac-txt,#fff)}`
  const est = document.createElement('style')
  est.textContent = css
  document.head.appendChild(est)
  const cfg = window.TOUR || { chave: 'tour', passos: [] }
  const P = cfg.passos
  let i = 0, raiz = null
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
  const vis = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 4 && r.height > 4 && getComputedStyle(el).visibility !== 'hidden' }
  function desenhar() {
    const p = P[i], b = raiz.querySelector('.sb-tour-b'), ult = i === P.length - 1
    b.innerHTML = `<button class="sb-tour-x" data-t="fim" aria-label="Fechar">×</button><b class="t">${esc(p.titulo)}</b><p>${esc(p.texto)}</p>
      <div class="sb-tour-pe"><span class="sb-tour-pt">${P.map((_, k) => `<i class="${k === i ? 'on' : ''}"></i>`).join('')}</span>
      <span class="sb-tour-bt">${i ? '<button data-t="volt">Voltar</button>' : '<button data-t="fim">Pular</button>'}<button class="pri" data-t="${ult ? 'fim' : 'prox'}">${ult ? 'Começar' : 'Próximo'}</button></span></div>`
    // centraliza o elemento na tela e só depois posiciona o balão (antes, a rolagem acontecia depois do cálculo e o destaque sumia)
    const alvo = p.alvo ? document.querySelector(p.alvo) : null
    if (alvo && vis(alvo)) { alvo.scrollIntoView({ block: 'center' }); requestAnimationFrame(() => requestAnimationFrame(posicionar)); setTimeout(posicionar, 300) }
    posicionar()
  }
  function posicionar() {
    if (!raiz) return
    const p = P[i], furo = raiz.querySelector('.sb-tour-furo'), b = raiz.querySelector('.sb-tour-b')
    const el = p.alvo ? document.querySelector(p.alvo) : null
    if (!vis(el)) { furo.style.cssText = 'left:50%;top:50%;width:0;height:0'; b.classList.add('centro'); b.style.left = b.style.top = ''; return }
    b.classList.remove('centro')
    const r = el.getBoundingClientRect(), m = 8
    furo.style.cssText = `left:${r.left - m}px;top:${r.top - m}px;width:${r.width + 2 * m}px;height:${r.height + 2 * m}px`
    const bb = b.getBoundingClientRect()
    const op = [[r.left + r.width / 2 - bb.width / 2, r.bottom + 18], [r.left + r.width / 2 - bb.width / 2, r.top - 18 - bb.height], [r.right + 18, r.top], [r.left - 18 - bb.width, r.top]]
    let [x, y] = op.find(([ox, oy]) => oy >= 8 && oy + bb.height <= innerHeight - 8) || op[1]
    b.style.left = Math.round(Math.max(12, Math.min(x, innerWidth - bb.width - 12))) + 'px'
    b.style.top = Math.round(Math.max(12, Math.min(y, innerHeight - bb.height - 12))) + 'px'
  }
  function fechar() {
    try { localStorage.setItem(cfg.chave, '1') } catch {}
    if (raiz) { raiz.remove(); raiz = null; removeEventListener('resize', posicionar) }
  }
  function abrir() {
    if (raiz || !P.length) return
    i = 0
    raiz = document.createElement('div')
    raiz.className = 'sb-tour'
    raiz.innerHTML = '<div class="sb-tour-furo"></div><div class="sb-tour-b" role="dialog" aria-modal="true"></div>'
    document.body.appendChild(raiz)
    raiz.addEventListener('click', (e) => { const t = e.target.closest('[data-t]')?.dataset.t; if (t === 'prox') { i++; desenhar() } else if (t === 'volt') { i--; desenhar() } else if (t === 'fim') fechar() })
    addEventListener('resize', posicionar)
    desenhar()
  }
  addEventListener('keydown', (e) => { if (raiz && e.key === 'Escape') fechar() })
  window.SBTour = { abrir, fechar }
  let visto = true
  try { visto = !!localStorage.getItem(cfg.chave) } catch {}
  if (!visto) setTimeout(abrir, cfg.atraso || 1500)
})()
