/* ─── 登录页版本号动态加载 ─── */
(function loadVersion() {
  fetch('/version.json')
    .then(r => r.json())
    .then(d => {
      const v = (d && d.version) || 'v2.5.1';
      const el1 = document.getElementById('login-version');
      const el2 = document.getElementById('footer-version');
      if (el1) el1.textContent = v;
      if (el2) el2.textContent = v;
    })
    .catch(() => {});
})();
