/* ═════════════════════════════════════
   DBCheck 登录页 JavaScript - 2026-06-06
   ═════════════════════════════════════ */

/* ─── 主题切换 ─── */
function toggleLoginTheme() {
  const html = document.documentElement;
  const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
}

/* ─── 密码显示切换 ─── */
function toggleLoginPwd() {
  const inp = document.getElementById('login-pass');
  const icon = document.getElementById('login-eye-icon');
  if (!inp || !icon) return;
  if (inp.type === 'password') {
    inp.type = 'text';
    icon.innerHTML = '<path d="M17.94 17.94A10.06 10.06 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.08 9.08 0 0 1 12 4c7 0 11 8 11 8a18.45 18.45 0 0 1-2.28 3.28M1 1l22 22"/>';
  } else {
    inp.type = 'password';
    icon.innerHTML = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
  }
}

/* ─── 卡片抖动 ─── */
(function(){
  const s = document.createElement('style');
  s.textContent = '@keyframes cardShake{0%,100%{transform:translateX(0)}20%{transform:translateX(-8px)}40%{transform:translateX(8px)}60%{transform:translateX(-5px)}80%{transform:translateX(5px)}}';
  document.head.appendChild(s);
})();

function shakeLoginCard(){
  const card = document.querySelector('#login-overlay .login-card');
  if (!card) return;
  card.style.animation = 'none';
  void card.offsetWidth;
  card.style.animation = 'cardShake .4s ease';
}

/* ─── 按钮涟漪 ─── */
document.addEventListener('click', function(e){
  const btn = e.target.closest('#btn-login');
  if (!btn) return;
  const rect = btn.getBoundingClientRect();
  const ripple = document.createElement('span');
  ripple.className = 'ripple';
  const size = Math.max(rect.width, rect.height);
  ripple.style.width = ripple.style.height = size + 'px';
  ripple.style.left = (e.clientX - rect.left - size/2) + 'px';
  ripple.style.top  = (e.clientY - rect.top  - size/2) + 'px';
  btn.appendChild(ripple);
  setTimeout(() => ripple.remove(), 500);
});

/* ─── 版本号动态加载 ─── */
function loadVersion(){
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
}

/* ─── 登录提交 ─── */
function doLogin(e) {
  if (e) e.preventDefault();
  const btn = document.getElementById('btn-login');
  const err = document.getElementById('login-error');
  const user = document.getElementById('login-user').value.trim();
  const pass = document.getElementById('login-pass').value;

  err.classList.remove('show');
  if (!user || !pass) {
    err.textContent = '请输入用户名和密码';
    err.classList.add('show');
    shakeLoginCard();
    return;
  }

  btn.classList.add('loading');
  document.getElementById('btn-text').style.visibility = 'hidden';

  fetch('/api/auth/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username: user, password: pass})
  })
  .then(r => r.json())
  .then(d => {
    btn.classList.remove('loading');
    document.getElementById('btn-text').style.visibility = '';
    if (d.ok) {
      document.getElementById('login-overlay').style.display = 'none';
      location.reload();
    } else {
      err.textContent = d.error || '登录失败';
      err.classList.add('show');
      shakeLoginCard();
    }
  })
  .catch(() => {
    btn.classList.remove('loading');
    document.getElementById('btn-text').style.visibility = '';
    err.textContent = '网络错误，请重试';
    err.classList.add('show');
    shakeLoginCard();
  });
}

/* ─── 初始化 ─── */
document.addEventListener('DOMContentLoaded', function() {
  loadVersion();

  /* 回车提交 */
  const passInput = document.getElementById('login-pass');
  if (passInput) {
    passInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') doLogin(e);
    });
  }
});
