const API = '/api';
let currentRole = localStorage.getItem('sj_role') || null;
let currentUser = JSON.parse(localStorage.getItem('sj_user') || 'null');
let currentToken = localStorage.getItem('sj_token') || null;
let socket = null;
let currentProduct = null;
let currentOrderQty = 1;
let currentReviewOrderId = null;
let currentReviewRating = 0;
let adminChatUserId = null;
let adminChatUser = null;          // 当前对话的顾客信息
let unreadConvs = new Set();       // 有未读消息的顾客 id 集合
let currentOrderFilter = '';

function saveSession(role, token, user) {
  currentRole = role; currentToken = token; currentUser = user;
  localStorage.setItem('sj_role', role);
  localStorage.setItem('sj_token', token);
  localStorage.setItem('sj_user', JSON.stringify(user));
}
function clearSession() {
  currentRole = null; currentToken = null; currentUser = null;
  localStorage.removeItem('sj_role');
  localStorage.removeItem('sj_token');
  localStorage.removeItem('sj_user');
}

// ---------- Utils ----------
function $(sel, el = document) { return el.querySelector(sel); }
function $$(sel, el = document) { return Array.from(el.querySelectorAll(sel)); }

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2200);
}

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (currentToken) opts.headers['Authorization'] = 'Bearer ' + currentToken;
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API + path, opts);
  const data = await res.json().catch(() => ({ success: false, msg: '网络错误' }));
  // 令牌失效：自动退回登录页，避免界面看似已登录但实际无权限
  if (res.status === 401 && currentToken) {
    clearSession();
    $('#consumer-view').classList.remove('active');
    $('#admin-view').classList.remove('active');
    $('#auth-view').classList.add('active');
    toast('登录已过期，请重新登录');
    return { success: false, msg: '登录已过期' };
  }
  return data;
}

function openModal(id) { $('#' + id).classList.add('active'); }
function closeModal(id) { $('#' + id).classList.remove('active'); }

function showDot(sel) { const el = $(sel); if (el) el.style.display = 'block'; }
function hideDot(sel) { const el = $(sel); if (el) el.style.display = 'none'; }

function statusText(s) {
  return { pending_payment: '待支付', paid: '已支付', delivered: '已交付', received: '已收货', cancelled: '已取消' }[s] || s;
}

// 数据库统一以 UTC 存储 "YYYY-MM-DD HH:MM:SS"，展示时转成本地时间，避免时区错误
function toLocalDate(s) {
  if (!s) return null;
  const d = new Date(String(s).replace(' ', 'T') + 'Z');
  return isNaN(d.getTime()) ? null : d;
}
function pad2(n) { return n.toString().padStart(2, '0'); }
function fmtTime(s) {
  const d = toLocalDate(s);
  return d ? `${pad2(d.getHours())}:${pad2(d.getMinutes())}` : '';
}
function fmtDateTime(s) {
  const d = toLocalDate(s);
  if (!d) return s || '';
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

// ---------- Auth ----------
$$('.role-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('.role-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const role = btn.dataset.role;
    currentRole = role;
    $$('.auth-tab').forEach(t => {
      const mode = t.dataset.mode;
      const form = document.getElementById(`${role}-${mode}-form`);
      if (form) form.classList.toggle('active', t.classList.contains('active'));
    });
    $$('.auth-form').forEach(f => f.classList.remove('active'));
    const activeMode = $('.auth-tab.active').dataset.mode;
    $(`#${role}-${activeMode}-form`).classList.add('active');
  });
});

$$('.auth-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    $$('.auth-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const role = $('.role-btn.active').dataset.role;
    $$('.auth-form').forEach(f => f.classList.remove('active'));
    $(`#${role}-${tab.dataset.mode}-form`).classList.add('active');
  });
});

$('#user-login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const data = await api('POST', '/login', Object.fromEntries(fd));
  if (data.success) {
    saveSession('user', data.token, data.user);
    enterConsumer();
  } else toast(data.msg);
});

$('#user-register-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const data = await api('POST', '/register', Object.fromEntries(fd));
  if (data.success) {
    saveSession('user', data.token, data.user);
    enterConsumer();
  } else toast(data.msg);
});

$('#admin-login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const key = (e.target.super_key.value || '').trim();
  const body = {
    username: e.target.username.value.trim(),
    password: e.target.password.value,
  };
  // 只有填写了主管理员密钥时才走主账号设定流程；平时留空即为普通登录
  let path = '/admin/login';
  if (key) { body.super_key = key; path = '/admin/super-login'; }
  const data = await api('POST', path, body);
  if (data.success) {
    saveSession('admin', data.token, data.admin);
    enterAdmin();
  } else toast(data.msg);
});

$('#admin-register-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const data = await api('POST', '/admin/register', Object.fromEntries(fd));
  if (data.success) {
    saveSession('admin', data.token, data.admin);
    enterAdmin();
  } else toast(data.msg);
});

// ---------- Consumer ----------
function enterConsumer() {
  $('#auth-view').classList.remove('active');
  $('#consumer-view').classList.add('active');
  $('#consumer-name').textContent = currentUser.name;
  connectSocket();
  loadProducts();
  loadOrders();
}

function enterAdmin() {
  $('#auth-view').classList.remove('active');
  $('#admin-view').classList.add('active');
  $('#admin-name').textContent = currentUser.username + (currentUser.is_super ? '（主账号）' : '');
  $('#super-nav-btn').style.display = currentUser.is_super ? 'block' : 'none';
  connectSocket();
  loadAdminProducts();
  loadAdminOrders();
  loadFinance();
  loadConvList();
  loadReviews();
}

$('#consumer-logout').addEventListener('click', logout);
$('#admin-logout').addEventListener('click', logout);

function logout() {
  clearSession();
  if (socket) socket.disconnect();
  $('#consumer-view').classList.remove('active');
  $('#admin-view').classList.remove('active');
  $('#auth-view').classList.add('active');
}

// Bottom nav
$$('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('.nav-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    $$('.tab-pane').forEach(p => p.classList.remove('active'));
    $('#' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'c-records') loadOrders();
    if (btn.dataset.tab === 'c-service') { loadMessages(); hideDot('#service-dot'); }
  });
});

// ---------- Products ----------
async function loadProducts() {
  const data = await api('GET', '/products');
  if (!data.success) return;
  const list = $('#product-list');
  list.innerHTML = data.products.map(p => `
    <div class="product-card">
      <img src="${p.image_url || ''}" alt="${p.name}" onerror="this.style.background='#eee'">
      <div class="product-info">
        <h4>${p.name}</h4>
        <p class="desc">${p.description || ''}</p>
        <div class="price">¥${p.price}</div>
      </div>
      <div class="product-actions">
        <button class="btn-primary" onclick="openOrderModal(${p.id})">立即购买</button>
      </div>
    </div>
  `).join('');
}

window.openOrderModal = function(pid) {
  api('GET', '/products').then(data => {
    if (!data.success) return;
    currentProduct = data.products.find(p => p.id === pid);
    if (!currentProduct) return;
    currentOrderQty = 1;
    $('#modal-product-img').src = currentProduct.image_url || '';
    $('#modal-product-name').textContent = currentProduct.name;
    $('#modal-product-price').textContent = '¥' + currentProduct.price;
    const styles = currentProduct.styles || [];
    $('#modal-style').innerHTML = styles.length ? styles.map(s => `<option value="${s}">${s}</option>`).join('') : '<option value="">默认</option>';
    $('#modal-qty').textContent = '1';
    updateTotal();
    const of = $('#order-form');
    of.name.value = currentUser.name;
    of.class_name.value = currentUser.class_name;
    of.phone.value = currentUser.phone;
    openModal('order-modal');
  });
};

$$('.qty-btn').forEach(b => b.addEventListener('click', () => {
  if (b.dataset.act === 'inc') currentOrderQty++;
  else if (currentOrderQty > 1) currentOrderQty--;
  $('#modal-qty').textContent = currentOrderQty;
  updateTotal();
}));

function updateTotal() {
  $('#modal-total').textContent = '¥' + (currentProduct.price * currentOrderQty).toFixed(2);
}

$('#order-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd);
  body.product_id = currentProduct.id;
  body.quantity = currentOrderQty;
  body.style = $('#modal-style').value;
  const data = await api('POST', '/orders', body);
  if (data.success) {
    toast('下单成功！');
    closeModal('order-modal');
    loadOrders();
    $$('.nav-btn').forEach(b => b.classList.remove('active'));
    $$('.tab-pane').forEach(p => p.classList.remove('active'));
    $('.nav-btn[data-tab="c-records"]').classList.add('active');
    $('#c-records').classList.add('active');
  } else toast(data.msg);
});

// ---------- Orders ----------
let orderStatusFilter = 'pending';

$$('.record-tab').forEach(t => t.addEventListener('click', () => {
  $$('.record-tab').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  orderStatusFilter = t.dataset.status;
  renderOrders();
}));

let userOrders = [];
async function loadOrders() {
  const data = await api('GET', '/orders');
  if (data.success) { userOrders = data.orders; renderOrders(); }
}

function renderOrders() {
  const list = $('#order-list');
  let filtered = userOrders;
  if (orderStatusFilter === 'pending') {
    filtered = userOrders.filter(o => ['pending_payment', 'paid', 'delivered'].includes(o.status));
  } else {
    filtered = userOrders.filter(o => o.status === 'received');
  }
  if (!filtered.length) { list.innerHTML = '<p style="text-align:center;color:var(--text-light);padding:40px">暂无订单</p>'; return; }
  list.innerHTML = filtered.map(o => `
    <div class="order-card">
      <div class="order-head">
        <span class="pname">${o.product_name}</span>
        <span class="status-badge status-${o.status}">${statusText(o.status)}</span>
      </div>
      <div class="order-body">
        <img src="${o.product_image || ''}" alt="" onerror="this.style.background='#eee'">
        <div class="order-meta">
          <div>款式：${o.style || '默认'}</div>
          <div>数量：${o.quantity}</div>
          <div>收货人：${o.name} ${o.class_name}</div>
          <div class="amount">¥${o.amount}</div>
        </div>
      </div>
      <div class="order-foot">
        ${o.status === 'pending_payment' ? `<button class="btn-outline danger" onclick="cancelOrder(${o.id})">取消订单</button>` : ''}
        ${o.status === 'received' && !o.reviewed ? `<button class="btn-primary" onclick="openReviewModal(${o.id})">评价</button>` : ''}
      </div>
    </div>
  `).join('');
}

window.cancelOrder = async function(oid) {
  if (!confirm('确认取消该订单？')) return;
  const data = await api('POST', `/orders/${oid}/cancel`);
  if (data.success) { toast('已取消'); loadOrders(); }
  else toast(data.msg);
};

window.openReviewModal = function(oid) {
  currentReviewOrderId = oid;
  currentReviewRating = 0;
  $$('.star').forEach(s => s.classList.remove('active'));
  $('#review-form').content.value = '';
  openModal('review-modal');
};

$$('.star').forEach(s => s.addEventListener('click', () => {
  currentReviewRating = parseInt(s.dataset.v);
  $$('.star').forEach(x => x.classList.toggle('active', parseInt(x.dataset.v) <= currentReviewRating));
}));

$('#review-form').addEventListener('submit', async e => {
  e.preventDefault();
  if (!currentReviewRating) { toast('请选择评分'); return; }
  const data = await api('POST', '/reviews', { order_id: currentReviewOrderId, rating: currentReviewRating, content: $('#review-form').content.value });
  if (data.success) { toast('评价已提交'); closeModal('review-modal'); loadOrders(); }
  else toast(data.msg);
});

// ---------- Chat ----------
let chatMessages = [];
async function loadMessages() {
  const data = await api('GET', '/messages');
  if (data.success) { chatMessages = data.messages; renderChat(); }
}

function renderChat() {
  const box = $('#chat-messages');
  box.innerHTML = chatMessages.map(m => {
    const isSelf = m.sender_type === 'user';   // 消费者端：自己的消息靠右
    return `
    <div class="msg ${isSelf ? 'm-self' : 'm-other'}">
      <div class="msg-text">${m.content}</div>
      <div class="msg-meta">
        ${!isSelf && m.sender_name ? `<span class="sender-tag">客服 ${m.sender_name}</span>` : ''}
        <span class="time">${fmtTime(m.created_at)}</span>
      </div>
    </div>`;
  }).join('');
  box.scrollTop = box.scrollHeight;
}

$('#chat-send').addEventListener('click', sendUserMsg);
$('#chat-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendUserMsg(); });

async function sendUserMsg() {
  const input = $('#chat-input');
  const content = input.value.trim();
  if (!content) return;
  input.value = '';
  const data = await api('POST', '/messages', { content });
  if (data.success) { chatMessages.push(data.message); renderChat(); }
}

// ---------- Socket.IO ----------
function connectSocket() {
  if (socket) socket.disconnect();
  socket = io({ auth: { token: currentToken, role: currentRole } });

  socket.on('new_message', msg => {
    if (currentRole === 'user') {
      if (msg.sender_type === 'admin') {
        chatMessages.push(msg);
        if ($('#c-service').classList.contains('active')) {
          renderChat();
        } else {
          showDot('#service-dot');   // 不在客服页时，底部「客服」入口亮红点
        }
      }
    } else if (currentRole === 'admin') {
      if (adminChatUserId && msg.user_id === adminChatUserId) {
        adminChatMessages.push(msg);
        renderAdminChat();
      } else if (msg.sender_type === 'user') {
        unreadConvs.add(msg.user_id);          // 标记该顾客有未读消息
        showDot('#admin-service-dot');         // 侧栏「客服与评价」入口亮红点
      }
      loadConvList();
    }
  });

  socket.on('order_update', data => {
    if (currentRole === 'user') {
      loadOrders();
    }
  });

  socket.on('new_order', () => {
    if (currentRole === 'admin' && $('#a-orders').classList.contains('active')) loadAdminOrders();
  });

  socket.on('order_status_changed', () => {
    if (currentRole === 'admin') { loadAdminOrders(); loadFinance(); }
  });

  socket.on('product_update', () => {
    if (currentRole === 'user') loadProducts();
    else loadAdminProducts();
  });

  socket.on('new_review', () => {
    if (currentRole === 'admin') loadReviews();
  });
}

// ---------- Admin: Sidebar ----------
$$('.sidebar-btn').forEach(btn => btn.addEventListener('click', () => {
  $$('.sidebar-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  $$('.admin-pane').forEach(p => p.classList.remove('active'));
  $('#' + btn.dataset.tab).classList.add('active');
  const tab = btn.dataset.tab;
  if (tab === 'a-products') loadAdminProducts();
  if (tab === 'a-orders') loadAdminOrders();
  if (tab === 'a-finance') { loadFinance(); loadMonthly(); loadCosts(); }
  if (tab === 'a-service') { loadConvList(); loadReviews(); hideDot('#admin-service-dot'); }
  if (tab === 'a-super') { loadSuperUsers(); loadSuperAdmins(); }
}));

// ---------- Admin: 主管理员账号管理 ----------
$$('.filter-btn[data-super]').forEach(btn => btn.addEventListener('click', () => {
  $$('.filter-btn[data-super]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  $$('#a-super .sub-pane').forEach(p => p.classList.remove('active'));
  $('#super-' + btn.dataset.super + '-sub').classList.add('active');
}));

async function loadSuperUsers() {
  const data = await api('GET', '/admin/super/users');
  if (!data.success) { toast(data.msg); return; }
  const body = $('#super-users-body');
  if (!data.users.length) { body.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-light)">暂无账号</td></tr>'; return; }
  body.innerHTML = data.users.map(u => `
    <tr>
      <td>${u.id}</td>
      <td>${u.name}</td>
      <td>${u.class_name}</td>
      <td>${u.phone}</td>
      <td>${u.order_count}</td>
      <td>¥${u.total_amount}</td>
      <td>${u.status ? '<span class="status-ok">正常</span>' : '<span class="status-off">已停用</span>'}</td>
      <td>
        <button class="btn-outline" onclick="viewUserOrders(${u.id})">查看订单</button>
        <button class="${u.status ? 'btn-danger' : 'btn-primary'}" onclick="toggleUserStatus(${u.id}, ${u.status ? 0 : 1})">${u.status ? '停用' : '启用'}</button>
      </td>
    </tr>
  `).join('');
}

window.toggleUserStatus = async function(uid, status) {
  const data = await api('PUT', `/admin/super/users/${uid}/status`, { status });
  if (data.success) { toast(status ? '账号已启用' : '账号已停用'); loadSuperUsers(); }
  else toast(data.msg);
};

window.viewUserOrders = async function(uid) {
  const data = await api('GET', `/admin/super/users/${uid}/orders`);
  if (!data.success) { toast(data.msg); return; }
  if (!data.orders.length) { toast('该账号暂无订单'); return; }
  const lines = data.orders.map(o =>
    `#${o.id} ${o.product_name} ×${o.quantity} ¥${o.amount} ${statusText(o.status)} (${fmtDateTime(o.created_at)})`
  ).join('\n');
  alert('该账号的订单：\n' + lines);
};

async function loadSuperAdmins() {
  const data = await api('GET', '/admin/super/admins');
  if (!data.success) { toast(data.msg); return; }
  const body = $('#super-admins-body');
  body.innerHTML = data.admins.map(a => `
    <tr>
      <td>${a.id}</td>
      <td>${a.username}</td>
      <td>${a.is_super ? '<span class="status-ok">主账号</span>' : '普通管理员'}</td>
      <td>${a.status ? '<span class="status-ok">正常</span>' : '<span class="status-off">已停用</span>'}</td>
      <td>
        ${a.is_super ? '—' : `<button class="${a.status ? 'btn-danger' : 'btn-primary'}" onclick="toggleAdminStatus(${a.id}, ${a.status ? 0 : 1})">${a.status ? '停用' : '启用'}</button>`}
      </td>
    </tr>
  `).join('');
}

window.toggleAdminStatus = async function(aid, status) {
  const data = await api('PUT', `/admin/super/admins/${aid}/status`, { status });
  if (data.success) { toast(status ? '账号已启用' : '账号已停用'); loadSuperAdmins(); }
  else toast(data.msg);
};

// ---------- Admin: Products ----------
async function loadAdminProducts() {
  const data = await api('GET', '/admin/products');
  if (!data.success) return;
  const list = $('#admin-product-list');
  list.innerHTML = data.products.map(p => `
    <div class="product-card">
      <img src="${p.image_url || ''}" alt="${p.name}" onerror="this.style.background='#eee'">
      <div class="product-info">
        <h4>${p.name}</h4>
        <p class="desc">${p.description || ''}</p>
        <div class="price">¥${p.price} · 库存${p.stock}</div>
      </div>
      <div class="product-actions">
        <button class="btn-outline" onclick='editProduct(${JSON.stringify(p)})'>编辑</button>
        <button class="btn-danger" onclick="delProduct(${p.id})">下架</button>
      </div>
    </div>
  `).join('');
}

$('#add-product-btn').addEventListener('click', () => {
  $('#product-modal-title').textContent = '添加商品';
  $('#product-form').reset();
  $('#product-form').dataset.editId = '';
  $('#product-image-preview').removeAttribute('src');
  openModal('product-modal');
});

window.editProduct = function(p) {
  $('#product-modal-title').textContent = '编辑商品';
  const f = $('#product-form');
  f.name.value = p.name;
  f.description.value = p.description || '';
  f.price.value = p.price;
  f.image_url.value = p.image_url || '';
  f.styles.value = (p.styles || []).join(',');
  f.stock.value = p.stock;
  f.dataset.editId = p.id;
  const preview = $('#product-image-preview');
  if (p.image_url) preview.src = p.image_url; else preview.removeAttribute('src');
  openModal('product-modal');
};

// 从图库/本地选择图片，转为 base64 存入 image_url
$('#product-image-file').addEventListener('change', e => {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  if (!file.type.startsWith('image/')) { toast('请选择图片文件'); return; }
  const reader = new FileReader();
  reader.onload = ev => {
    const f = $('#product-form');
    f.image_url.value = ev.target.result;
    $('#product-image-preview').src = ev.target.result;
  };
  reader.readAsDataURL(file);
});

window.delProduct = async function(pid) {
  if (!confirm('确认下架该商品？')) return;
  const data = await api('DELETE', `/admin/products/${pid}`);
  if (data.success) { toast('已下架'); loadAdminProducts(); }
};

$('#product-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd);
  const editId = e.target.dataset.editId;
  let data;
  if (editId) {
    data = await api('PUT', `/admin/products/${editId}`, body);
  } else {
    data = await api('POST', '/admin/products', body);
  }
  if (data.success) { toast(editId ? '已更新' : '已添加'); closeModal('product-modal'); loadAdminProducts(); }
  else toast(data.msg);
});

// ---------- Admin: Orders ----------
$$('.filter-btn[data-status]').forEach(btn => btn.addEventListener('click', () => {
  $$('.filter-btn[data-status]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  currentOrderFilter = btn.dataset.status;
  loadAdminOrders();
}));

async function loadAdminOrders() {
  const url = currentOrderFilter ? `/admin/orders?status=${currentOrderFilter}` : '/admin/orders';
  const data = await api('GET', url);
  if (!data.success) return;
  const list = $('#admin-order-list');
  list.innerHTML = data.orders.map(o => `
    <div class="admin-order-card">
      <div class="admin-order-head">
        <b>#${o.id} ${o.product_name}</b>
        <span class="status-badge status-${o.status}">${statusText(o.status)}</span>
      </div>
      <div class="admin-order-body">
        <span><b>买家：</b>${o.name}</span>
        <span><b>班级：</b>${o.class_name}</span>
        <span><b>手机：</b>${o.phone}</span>
        <span><b>款式：</b>${o.style || '默认'}</span>
        <span><b>数量：</b>${o.quantity}</span>
        <span><b>下单：</b>${fmtDateTime(o.created_at)}</span>
        <span><b>金额：</b><span class="price" style="color:var(--accent)">¥${o.amount}</span></span>
      </div>
      <div class="admin-order-actions">
        <select class="select-sm" onchange="updateOrderStatus(${o.id}, this.value)">
          <option value="pending_payment" ${o.status==='pending_payment'?'selected':''}>待支付</option>
          <option value="paid" ${o.status==='paid'?'selected':''}>已支付</option>
          <option value="delivered" ${o.status==='delivered'?'selected':''}>已交付</option>
          <option value="received" ${o.status==='received'?'selected':''}>已收货</option>
          <option value="cancelled" ${o.status==='cancelled'?'selected':''}>已取消</option>
        </select>
        <input type="number" step="0.01" class="amount-input" value="${o.amount}" onchange="updateOrderAmount(${o.id}, this.value)">
        <button class="btn-outline" onclick="updateOrderAmount(${o.id}, this.previousElementSibling.value)">改金额</button>
        <button class="btn-danger" onclick="deleteOrder(${o.id})">删除</button>
      </div>
    </div>
  `).join('');
}

window.updateOrderStatus = async function(oid, status) {
  const data = await api('PUT', `/admin/orders/${oid}/status`, { status });
  if (data.success) toast('状态已更新');
  else toast(data.msg);
};

window.updateOrderAmount = async function(oid, amount) {
  const data = await api('PUT', `/admin/orders/${oid}/amount`, { amount: parseFloat(amount) });
  if (data.success) { toast('金额已修改'); loadFinance(); }
  else toast(data.msg);
};

window.deleteOrder = async function(oid) {
  if (!confirm('确认删除该订单？删除后不可恢复。')) return;
  const data = await api('DELETE', `/admin/orders/${oid}`);
  if (data.success) { toast('订单已删除'); loadAdminOrders(); loadFinance(); }
  else toast(data.msg);
};

// ---------- Admin: 手动添加订单 ----------
$('#manual-order-btn').addEventListener('click', async () => {
  const data = await api('GET', '/admin/products');
  if (!data.success) return;
  const sel = $('#manual-product-select');
  sel.innerHTML = data.products.map(p => `<option value="${p.id}" data-price="${p.price}">${p.name}（¥${p.price}）</option>`).join('');
  $('#manual-order-form').reset();
  openModal('manual-order-modal');
});

$('#manual-order-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd);
  const data = await api('POST', '/admin/orders/manual', body);
  if (data.success) { toast('订单已录入'); closeModal('manual-order-modal'); loadAdminOrders(); loadFinance(); }
  else toast(data.msg);
});

// ---------- Admin: Finance ----------
async function loadFinance() {
  const data = await api('GET', '/admin/finance/overview');
  if (data.success) {
    $('#fin-income').textContent = '¥' + data.income;
    $('#fin-cost').textContent = '¥' + data.cost;
    $('#fin-profit').textContent = '¥' + data.profit;
    $('#fin-orders').textContent = data.order_count;
  }
  initYearSelect();
}

function initYearSelect() {
  const sel = $('#year-select');
  if (sel.options.length) return;
  const cy = new Date().getFullYear();
  for (let y = cy; y >= cy - 3; y--) {
    const opt = document.createElement('option');
    opt.value = y; opt.textContent = y + '年';
    sel.appendChild(opt);
  }
  sel.addEventListener('change', loadMonthly);
  loadMonthly();
}

async function loadMonthly() {
  const year = $('#year-select').value;
  const data = await api('GET', `/admin/finance/monthly?year=${year}`);
  if (!data.success) return;
  $('#monthly-body').innerHTML = data.months.map(m => `
    <tr>
      <td>${m.month}</td>
      <td>¥${m.income}</td>
      <td>¥${m.cost}</td>
      <td style="color:${m.profit>=0?'var(--success)':'var(--danger)'}">¥${m.profit}</td>
      <td>${m.order_count}</td>
    </tr>
  `).join('');
}

$('#cost-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd);
  const editId = e.target.dataset.editId;
  let data;
  if (editId) data = await api('PUT', `/admin/finance/costs/${editId}`, body);
  else data = await api('POST', '/admin/finance/costs', body);
  if (data.success) {
    toast(editId ? '成本已更新' : '成本已录入');
    resetCostForm();
    loadFinance();
    loadMonthly();
    loadCosts();
  } else toast(data.msg);
});

function resetCostForm() {
  const f = $('#cost-form');
  f.reset();
  f.dataset.editId = '';
  $('#cost-submit-btn').textContent = '录入成本';
}

$('#cost-cancel-btn').addEventListener('click', resetCostForm);

async function loadCosts() {
  const data = await api('GET', '/admin/finance/costs');
  if (!data.success) return;
  const list = $('#cost-list');
  if (!data.costs.length) { list.innerHTML = '<p style="color:var(--text-light);padding:8px">暂无成本记录</p>'; return; }
  list.innerHTML = data.costs.map(c => `
    <div class="cost-item">
      <span class="cost-date">${c.cost_date}</span>
      <span class="cost-desc">${c.description || '（无描述）'}</span>
      <span class="cost-amount">¥${c.amount}</span>
      <button class="btn-outline" onclick='editCost(${JSON.stringify(c)})'>编辑</button>
      <button class="btn-danger" onclick="deleteCost(${c.id})">删除</button>
    </div>
  `).join('');
}

window.editCost = function(c) {
  const f = $('#cost-form');
  f.amount.value = c.amount;
  f.description.value = c.description || '';
  f.cost_date.value = c.cost_date || '';
  f.dataset.editId = c.id;
  $('#cost-submit-btn').textContent = '保存修改';
  window.scrollTo({ top: 0, behavior: 'smooth' });
  toast('正在编辑成本，修改后点「保存修改」');
};

window.deleteCost = async function(cid) {
  if (!confirm('确认删除该成本记录？')) return;
  const data = await api('DELETE', `/admin/finance/costs/${cid}`);
  if (data.success) { toast('已删除'); loadFinance(); loadMonthly(); loadCosts(); }
  else toast(data.msg);
};

$('#export-excel-btn').addEventListener('click', () => {
  const year = $('#year-select').value || new Date().getFullYear();
  window.open(`${API}/admin/finance/export?year=${year}&token=${currentToken}`, '_blank');
});

// ---------- Admin: Service ----------
$$('.filter-btn[data-sub]').forEach(btn => btn.addEventListener('click', () => {
  $$('.filter-btn[data-sub]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  $$('.sub-pane').forEach(p => p.classList.remove('active'));
  $('#admin-' + btn.dataset.sub + '-sub').classList.add('active');
}));

let convUsers = [];
async function loadConvList() {
  const data = await api('GET', '/admin/messages/users');
  if (!data.success) return;
  convUsers = data.users;
  const list = $('#user-conv-list');
  if (!data.users.length) { list.innerHTML = '<p style="padding:16px;color:var(--text-light)">暂无对话</p>'; return; }
  list.innerHTML = data.users.map(u => `
    <div class="conv-item ${adminChatUserId===u.id?'active':''}" onclick="selectConv(${u.id})">
      <div class="cname">${u.name}（${u.class_name}）</div>
      <div class="clast">${u.last_msg || ''}</div>
      ${unreadConvs.has(u.id) && adminChatUserId!==u.id ? '<span class="conv-dot"></span>' : ''}
    </div>
  `).join('');
}

function setAdminChatHead(u) {
  $('#admin-chat-title').textContent = u ? `${u.name}（${u.class_name}）` : '请选择左侧对话';
  $('#admin-chat-subtitle').textContent = u ? '顾客 · 实时对话' : '';
}

let adminChatMessages = [];
window.selectConv = async function(uid) {
  adminChatUserId = uid;
  unreadConvs.delete(uid);
  if (!unreadConvs.size) hideDot('#admin-service-dot');
  const u = convUsers.find(x => x.id === uid);
  setAdminChatHead(u);
  $('#admin-chat-input').disabled = false;
  $('#admin-chat-send').disabled = false;
  loadConvList();
  const data = await api('GET', `/admin/messages/${uid}`);
  if (data.success) { adminChatMessages = data.messages; renderAdminChat(); }
};

function renderAdminChat() {
  const box = $('#admin-chat-messages');
  box.innerHTML = adminChatMessages.map(m => {
    const isSelf = m.sender_type === 'admin';   // 管理端：管理员自己的回复靠右
    return `
    <div class="msg ${isSelf ? 'm-self' : 'm-other'}">
      <div class="msg-text">${m.content}</div>
      <div class="msg-meta">
        ${isSelf && m.sender_name ? `<span class="sender-tag">${m.sender_name}</span>` : ''}
        <span class="time">${fmtTime(m.created_at)}</span>
      </div>
    </div>`;
  }).join('');
  box.scrollTop = box.scrollHeight;
}

$('#admin-chat-send').addEventListener('click', sendAdminMsg);
$('#admin-chat-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendAdminMsg(); });

async function sendAdminMsg() {
  if (!adminChatUserId) return;
  const input = $('#admin-chat-input');
  const content = input.value.trim();
  if (!content) return;
  input.value = '';
  const data = await api('POST', `/admin/messages/${adminChatUserId}`, { content });
  if (data.success) { adminChatMessages.push(data.message); renderAdminChat(); }
}

async function loadReviews() {
  const data = await api('GET', '/admin/reviews');
  if (!data.success) return;
  const list = $('#reviews-list');
  if (!data.reviews.length) { list.innerHTML = '<p style="color:var(--text-light);padding:40px;text-align:center">暂无评价</p>'; return; }
  list.innerHTML = data.reviews.map(r => `
    <div class="review-card">
      <div class="review-head">
        <b>${r.buyer_name}（${r.class_name}）</b>
        <span class="review-stars">${'★'.repeat(r.rating)}${'☆'.repeat(5-r.rating)}</span>
      </div>
      <div style="font-size:12px;color:var(--text-light);margin-bottom:6px">商品：${r.product_name} · ${fmtDateTime(r.created_at)}</div>
      <div class="review-content">${r.content || '（无文字评价）'}</div>
    </div>
  `).join('');
}

// ---------- Modals close ----------
$$('.modal-close').forEach(btn => btn.addEventListener('click', () => {
  btn.closest('.modal').classList.remove('active');
}));
$$('.modal').forEach(m => m.addEventListener('click', e => {
  if (e.target === m) m.classList.remove('active');
}));

// init - restore session
if (currentToken && currentRole && currentUser) {
  if (currentRole === 'user') enterConsumer();
  else if (currentRole === 'admin') enterAdmin();
}
