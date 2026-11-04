'use strict';
// Translate only application copy. Recorded messages, IDs and backend evidence stay verbatim.
window.YanI18n = (() => {
  let language = 'zh';
  try { if (localStorage.getItem('yan-console-language') === 'en') language = 'en'; } catch (_) {}
  const tr = (zh, en, values={}) => (language === 'en' ? en : zh).replace(/\{(\w+)\}/g, (m,k) => String(values[k] ?? m));
  function apply() {
    document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN';
    for (const el of document.querySelectorAll('[data-en]')) {
      if (!el.hasAttribute('data-zh')) el.dataset.zh = el.textContent;
      el.textContent = language === 'en' ? el.dataset.en : el.dataset.zh;
    }
    for (const attr of ['placeholder','aria-label','title']) {
      for (const el of document.querySelectorAll(`[data-en-${attr}]`)) {
        if (!el.hasAttribute(`data-zh-${attr}`)) el.setAttribute(`data-zh-${attr}`,el.getAttribute(attr) || '');
        el.setAttribute(attr,el.getAttribute(`data-${language}-${attr}`));
      }
    }
  }
  return {tr, apply, get language(){return language;}, get locale(){return language==='en' ? 'en-GB':'zh-CN';}, set(value){
    language = value === 'en' ? 'en' : 'zh';
    try {localStorage.setItem('yan-console-language',language);} catch (_) {}
    apply();
  }};
})();
YanI18n.apply();
