import os
import json
import uuid
import hashlib
from datetime import datetime
from io import BytesIO

from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, join_room

from models import get_db, init_db, row_to_dict, rows_to_list

ADMIN_KEY = "SPARKJOY-ADMIN-2026"
# 主管理员（主账号）登录所需的额外密钥
SUPER_KEY = "SPARKJOY-OWNER-2026"

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = 'sparkjoy-secret-key-2026'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# 商品图片以 base64 形式存库，放宽请求体上限
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

tokens = {}


def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()


def generate_token(role, uid, is_super=False):
    token = uuid.uuid4().hex
    tokens[token] = {'role': role, 'id': uid, 'super': is_super}
    return token


def get_current_user():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not token:
        token = request.args.get('token', '')
    info = tokens.get(token)
    if not info:
        return None
    return info


def require_user():
    info = get_current_user()
    if not info or info['role'] != 'user':
        return None
    return info


def require_admin():
    info = get_current_user()
    if not info or info['role'] != 'admin':
        return None
    return info


def require_super():
    info = get_current_user()
    if not info or info['role'] != 'admin' or not info.get('super'):
        return None
    return info


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/health')
def health():
    """保活探针：供外部定时任务访问，减少 Render 冷启动。"""
    return jsonify({'ok': True})


# ==================== AUTH ====================

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    name = (data.get('name') or '').strip()
    class_name = (data.get('class_name') or '').strip()
    phone = (data.get('phone') or '').strip()
    password = data.get('password') or ''

    if not all([name, class_name, phone, password]):
        return jsonify({'success': False, 'msg': '请填写完整信息'}), 400
    if not (phone.isdigit() and len(phone) == 11):
        return jsonify({'success': False, 'msg': '请输入 11 位手机号'}), 400

    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
    if existing:
        conn.close()
        return jsonify({'success': False, 'msg': '该手机号已注册，一个手机号只能注册一次'}), 400

    conn.execute(
        "INSERT INTO users (name, class_name, phone, password) VALUES (?,?,?,?)",
        (name, class_name, phone, hash_password(password))
    )
    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
    conn.close()
    user_dict = row_to_dict(user)
    token = generate_token('user', user_dict['id'])
    user_dict.pop('password', None)
    return jsonify({'success': True, 'token': token, 'user': user_dict})


@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    phone = (data.get('phone') or '').strip()
    password = data.get('password') or ''
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
    conn.close()
    if not user:
        return jsonify({'success': False, 'msg': '该手机号未注册'}), 400
    if user['password'] != hash_password(password):
        return jsonify({'success': False, 'msg': '密码错误'}), 400
    if not user['status']:
        return jsonify({'success': False, 'msg': '该账号已被停用，请联系管理员'}), 403
    user_dict = row_to_dict(user)
    token = generate_token('user', user_dict['id'])
    user_dict.pop('password', None)
    return jsonify({'success': True, 'token': token, 'user': user_dict})


@app.route('/api/admin/register', methods=['POST'])
def admin_register():
    data = request.get_json()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    admin_key = (data.get('admin_key') or '').strip()

    if admin_key != ADMIN_KEY:
        return jsonify({'success': False, 'msg': '管理者密钥错误'}), 400
    if not username or not password:
        return jsonify({'success': False, 'msg': '请填写账号和密码'}), 400

    conn = get_db()
    existing = conn.execute("SELECT id FROM admins WHERE username=?", (username,)).fetchone()
    if existing:
        conn.close()
        return jsonify({'success': False, 'msg': '该账号已存在'}), 400
    conn.execute(
        "INSERT INTO admins (username, password) VALUES (?,?)",
        (username, hash_password(password))
    )
    conn.commit()
    admin = conn.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
    conn.close()
    admin_dict = row_to_dict(admin)
    token = generate_token('admin', admin_dict['id'])
    admin_dict.pop('password', None)
    return jsonify({'success': True, 'token': token, 'admin': admin_dict})


@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    conn = get_db()
    admin = conn.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
    conn.close()
    if not admin:
        return jsonify({'success': False, 'msg': '账号不存在'}), 400
    if admin['password'] != hash_password(password):
        return jsonify({'success': False, 'msg': '密码错误'}), 400
    if not admin['status']:
        return jsonify({'success': False, 'msg': '该账号已被停用'}), 403
    admin_dict = row_to_dict(admin)
    token = generate_token('admin', admin_dict['id'], is_super=bool(admin_dict.get('is_super')))
    admin_dict.pop('password', None)
    return jsonify({'success': True, 'token': token, 'admin': admin_dict})


@app.route('/api/admin/super-login', methods=['POST'])
def admin_super_login():
    """主管理员登录：除账号密码外，还需额外的主管理员密钥。"""
    data = request.get_json()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    super_key = (data.get('super_key') or '').strip()
    if super_key != SUPER_KEY:
        return jsonify({'success': False, 'msg': '主管理员密钥错误'}), 400
    conn = get_db()
    admin = conn.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
    if not admin:
        conn.close()
        return jsonify({'success': False, 'msg': '账号不存在'}), 400
    if admin['password'] != hash_password(password):
        conn.close()
        return jsonify({'success': False, 'msg': '密码错误'}), 400
    conn.execute("UPDATE admins SET is_super=1 WHERE id=?", (admin['id'],))
    conn.commit()
    admin = conn.execute("SELECT * FROM admins WHERE id=?", (admin['id'],)).fetchone()
    conn.close()
    admin_dict = row_to_dict(admin)
    token = generate_token('admin', admin_dict['id'], is_super=True)
    admin_dict.pop('password', None)
    return jsonify({'success': True, 'token': token, 'admin': admin_dict})


# ==================== PRODUCTS ====================

@app.route('/api/products')
def list_products():
    conn = get_db()
    products = conn.execute("SELECT * FROM products WHERE active=1 ORDER BY created_at DESC").fetchall()
    conn.close()
    result = []
    for p in rows_to_list(products):
        try:
            p['styles'] = json.loads(p['styles']) if p.get('styles') else []
        except Exception:
            p['styles'] = []
        result.append(p)
    return jsonify({'success': True, 'products': result})


@app.route('/api/admin/products', methods=['GET', 'POST'])
def admin_products():
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    conn = get_db()
    if request.method == 'POST':
        data = request.get_json()
        styles = data.get('styles') or []
        if isinstance(styles, str):
            styles = [s.strip() for s in styles.split(',') if s.strip()]
        conn.execute(
            "INSERT INTO products (name, description, price, image_url, styles, stock, active) VALUES (?,?,?,?,?,?,1)",
            (data.get('name'), data.get('description'), float(data.get('price') or 0),
             data.get('image_url'), json.dumps(styles, ensure_ascii=False), int(data.get('stock') or 0))
        )
        conn.commit()
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        product = {'id': pid}
        socketio.emit('product_update', {'action': 'add', 'product': product}, room='admin')
        socketio.emit('product_update', {'action': 'refresh'}, room='users')
        return jsonify({'success': True})
    else:
        products = conn.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
        conn.close()
        result = []
        for p in rows_to_list(products):
            try:
                p['styles'] = json.loads(p['styles']) if p.get('styles') else []
            except Exception:
                p['styles'] = []
            result.append(p)
        return jsonify({'success': True, 'products': result})


@app.route('/api/admin/products/<int:pid>', methods=['PUT', 'DELETE'])
def admin_product_detail(pid):
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    conn = get_db()
    if request.method == 'DELETE':
        conn.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        socketio.emit('product_update', {'action': 'refresh'}, room='users')
        return jsonify({'success': True})
    data = request.get_json()
    styles = data.get('styles')
    if styles is not None:
        if isinstance(styles, str):
            styles = [s.strip() for s in styles.split(',') if s.strip()]
        styles = json.dumps(styles, ensure_ascii=False)
    fields = []
    values = []
    for f in ['name', 'description', 'price', 'image_url', 'stock']:
        if f in data:
            fields.append(f"{f}=?")
            values.append(data[f])
    if styles is not None:
        fields.append("styles=?")
        values.append(styles)
    if fields:
        values.append(pid)
        conn.execute(f"UPDATE products SET {','.join(fields)} WHERE id=?", values)
        conn.commit()
    conn.close()
    socketio.emit('product_update', {'action': 'refresh'}, room='users')
    return jsonify({'success': True})


# ==================== ORDERS ====================

@app.route('/api/orders', methods=['GET', 'POST'])
def orders():
    if request.method == 'POST':
        user = require_user()
        if not user:
            return jsonify({'success': False, 'msg': '请先登录'}), 401
        data = request.get_json()
        product_id = data.get('product_id')
        quantity = int(data.get('quantity') or 1)
        style = data.get('style') or ''
        name = (data.get('name') or '').strip()
        class_name = (data.get('class_name') or '').strip()
        phone = (data.get('phone') or '').strip()

        if not all([product_id, name, class_name, phone]):
            return jsonify({'success': False, 'msg': '请填写完整收货信息'}), 400

        conn = get_db()
        product = conn.execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
        if not product:
            conn.close()
            return jsonify({'success': False, 'msg': '商品不存在'}), 400
        amount = product['price'] * quantity
        conn.execute(
            "INSERT INTO orders (user_id, product_id, quantity, style, name, class_name, phone, amount, status) VALUES (?,?,?,?,?,?,?,?, 'pending_payment')",
            (user['id'], product_id, quantity, style, name, class_name, phone, amount)
        )
        conn.commit()
        oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        order = conn.execute("""
            SELECT o.*, p.name as product_name, p.image_url as product_image
            FROM orders o JOIN products p ON o.product_id=p.id WHERE o.id=?
        """, (oid,)).fetchone()
        conn.close()
        order_dict = row_to_dict(order)
        socketio.emit('new_order', order_dict, room='admin')
        socketio.emit('order_update', {'order': order_dict}, room=f"user_{user['id']}")
        return jsonify({'success': True, 'order': order_dict})
    else:
        user = require_user()
        if not user:
            return jsonify({'success': False, 'msg': '请先登录'}), 401
        conn = get_db()
        orders_list = conn.execute("""
            SELECT o.*, p.name as product_name, p.image_url as product_image,
                   (SELECT COUNT(*) FROM reviews r WHERE r.order_id=o.id) as reviewed
            FROM orders o JOIN products p ON o.product_id=p.id
            WHERE o.user_id=? ORDER BY o.created_at DESC
        """, (user['id'],)).fetchall()
        conn.close()
        return jsonify({'success': True, 'orders': rows_to_list(orders_list)})


@app.route('/api/orders/<int:oid>/cancel', methods=['POST'])
def cancel_order(oid):
    user = require_user()
    if not user:
        return jsonify({'success': False, 'msg': '请先登录'}), 401
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, user['id'])).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'msg': '订单不存在'}), 404
    if order['status'] not in ('pending_payment',):
        conn.close()
        return jsonify({'success': False, 'msg': '当前状态不可取消'}), 400
    conn.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
    conn.commit()
    conn.close()
    socketio.emit('order_update', {'order_id': oid, 'status': 'cancelled'}, room=f"user_{user['id']}")
    socketio.emit('order_status_changed', {'order_id': oid, 'status': 'cancelled'}, room='admin')
    return jsonify({'success': True})


@app.route('/api/admin/orders')
def admin_orders():
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    status = request.args.get('status', '')
    conn = get_db()
    if status:
        orders_list = conn.execute("""
            SELECT o.*, p.name as product_name, p.image_url as product_image
            FROM orders o JOIN products p ON o.product_id=p.id
            WHERE o.status=? ORDER BY o.created_at DESC
        """, (status,)).fetchall()
    else:
        orders_list = conn.execute("""
            SELECT o.*, p.name as product_name, p.image_url as product_image
            FROM orders o JOIN products p ON o.product_id=p.id
            ORDER BY o.created_at DESC
        """).fetchall()
    conn.close()
    return jsonify({'success': True, 'orders': rows_to_list(orders_list)})


@app.route('/api/admin/orders/manual', methods=['POST'])
def admin_order_manual():
    """管理员手动录入线下订单，金额可自定义，直接计入营业额统计。"""
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    data = request.get_json()
    name = (data.get('name') or '').strip()
    class_name = (data.get('class_name') or '').strip()
    phone = (data.get('phone') or '').strip()
    style = data.get('style') or ''
    quantity = int(data.get('quantity') or 1)
    product_id = data.get('product_id')
    status = data.get('status') or 'paid'
    if status not in ('pending_payment', 'paid', 'delivered', 'received', 'cancelled'):
        return jsonify({'success': False, 'msg': '状态无效'}), 400
    if not all([name, class_name, phone, product_id]):
        return jsonify({'success': False, 'msg': '请填写姓名、班级、手机号和商品'}), 400

    conn = get_db()
    product = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not product:
        conn.close()
        return jsonify({'success': False, 'msg': '商品不存在'}), 400
    if data.get('amount') not in (None, ''):
        amount = float(data.get('amount'))
    else:
        amount = product['price'] * quantity

    # 若该手机号已有账号，关联到该账号；否则记为线下散客
    matched = conn.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
    user_id = matched['id'] if matched else 0

    conn.execute(
        "INSERT INTO orders (user_id, product_id, quantity, style, name, class_name, phone, amount, status) VALUES (?,?,?,?,?,?,?,?,?)",
        (user_id, product_id, quantity, style, name, class_name, phone, amount, status)
    )
    conn.commit()
    oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    order = conn.execute("""
        SELECT o.*, p.name as product_name, p.image_url as product_image
        FROM orders o JOIN products p ON o.product_id=p.id WHERE o.id=?
    """, (oid,)).fetchone()
    conn.close()
    order_dict = row_to_dict(order)
    socketio.emit('order_status_changed', {'order_id': oid}, room='admin')
    if user_id:
        socketio.emit('order_update', {'order': order_dict}, room=f"user_{user_id}")
    return jsonify({'success': True, 'order': order_dict})


@app.route('/api/admin/orders/<int:oid>', methods=['DELETE'])
def admin_order_delete(oid):
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'msg': '订单不存在'}), 404
    conn.execute("DELETE FROM reviews WHERE order_id=?", (oid,))
    conn.execute("DELETE FROM orders WHERE id=?", (oid,))
    conn.commit()
    conn.close()
    socketio.emit('order_status_changed', {'order_id': oid, 'deleted': True}, room='admin')
    return jsonify({'success': True})


@app.route('/api/admin/orders/<int:oid>/status', methods=['PUT'])
def admin_order_status(oid):
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    data = request.get_json()
    status = data.get('status')
    if status not in ('pending_payment', 'paid', 'delivered', 'received', 'cancelled'):
        return jsonify({'success': False, 'msg': '状态无效'}), 400
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'msg': '订单不存在'}), 404
    conn.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    conn.commit()
    conn.close()
    if order['user_id']:
        socketio.emit('order_update', {'order_id': oid, 'status': status}, room=f"user_{order['user_id']}")
    socketio.emit('order_status_changed', {'order_id': oid, 'status': status}, room='admin')
    return jsonify({'success': True})


@app.route('/api/admin/orders/<int:oid>/amount', methods=['PUT'])
def admin_order_amount(oid):
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    data = request.get_json()
    amount = float(data.get('amount') or 0)
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'msg': '订单不存在'}), 404
    conn.execute("UPDATE orders SET amount=? WHERE id=?", (amount, oid))
    conn.commit()
    conn.close()
    if order['user_id']:
        socketio.emit('order_update', {'order_id': oid, 'amount': amount}, room=f"user_{order['user_id']}")
    socketio.emit('order_status_changed', {'order_id': oid, 'amount': amount}, room='admin')
    return jsonify({'success': True})


# ==================== MESSAGES ====================

@app.route('/api/messages', methods=['GET', 'POST'])
def messages():
    user = require_user()
    if not user:
        return jsonify({'success': False, 'msg': '请先登录'}), 401
    if request.method == 'POST':
        data = request.get_json()
        content = (data.get('content') or '').strip()
        if not content:
            return jsonify({'success': False, 'msg': '内容不能为空'}), 400
        conn = get_db()
        conn.execute(
            "INSERT INTO messages (user_id, sender_type, sender_id, content) VALUES (?,?,?,?)",
            (user['id'], 'user', user['id'], content)
        )
        conn.commit()
        mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        msg = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        conn.close()
        msg_dict = row_to_dict(msg)
        socketio.emit('new_message', msg_dict, room='admin')
        socketio.emit('new_message', msg_dict, room=f"user_{user['id']}")
        return jsonify({'success': True, 'message': msg_dict})
    else:
        conn = get_db()
        msgs = conn.execute(
            "SELECT * FROM messages WHERE user_id=? ORDER BY created_at ASC", (user['id'],)
        ).fetchall()
        conn.close()
        return jsonify({'success': True, 'messages': rows_to_list(msgs)})


@app.route('/api/admin/messages/users')
def admin_message_users():
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    conn = get_db()
    users = conn.execute("""
        SELECT DISTINCT u.id, u.name, u.class_name,
               (SELECT content FROM messages m WHERE m.user_id=u.id ORDER BY created_at DESC LIMIT 1) as last_msg,
               (SELECT created_at FROM messages m WHERE m.user_id=u.id ORDER BY created_at DESC LIMIT 1) as last_time
        FROM messages m JOIN users u ON m.user_id=u.id
        ORDER BY last_time DESC
    """).fetchall()
    conn.close()
    return jsonify({'success': True, 'users': rows_to_list(users)})


@app.route('/api/admin/messages/<int:uid>', methods=['GET', 'POST'])
def admin_messages_with_user(uid):
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    if request.method == 'POST':
        data = request.get_json()
        content = (data.get('content') or '').strip()
        if not content:
            return jsonify({'success': False, 'msg': '内容不能为空'}), 400
        conn = get_db()
        conn.execute(
            "INSERT INTO messages (user_id, sender_type, sender_id, content) VALUES (?,?,?,?)",
            (uid, 'admin', admin['id'], content)
        )
        conn.commit()
        mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        msg = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        conn.close()
        msg_dict = row_to_dict(msg)
        socketio.emit('new_message', msg_dict, room=f"user_{uid}")
        socketio.emit('new_message', msg_dict, room='admin')
        return jsonify({'success': True, 'message': msg_dict})
    else:
        conn = get_db()
        msgs = conn.execute(
            "SELECT * FROM messages WHERE user_id=? ORDER BY created_at ASC", (uid,)
        ).fetchall()
        conn.close()
        return jsonify({'success': True, 'messages': rows_to_list(msgs)})


# ==================== REVIEWS ====================

@app.route('/api/reviews', methods=['POST'])
def add_review():
    user = require_user()
    if not user:
        return jsonify({'success': False, 'msg': '请先登录'}), 401
    data = request.get_json()
    order_id = data.get('order_id')
    rating = int(data.get('rating') or 0)
    content = data.get('content') or ''
    if not order_id or rating < 1 or rating > 5:
        return jsonify({'success': False, 'msg': '请选择评分(1-5)'}), 400
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user['id'])).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'msg': '订单不存在'}), 404
    if order['status'] != 'received':
        conn.close()
        return jsonify({'success': False, 'msg': '仅已收货订单可评价'}), 400
    existing = conn.execute("SELECT id FROM reviews WHERE order_id=?", (order_id,)).fetchone()
    if existing:
        conn.close()
        return jsonify({'success': False, 'msg': '该订单已评价'}), 400
    conn.execute(
        "INSERT INTO reviews (order_id, user_id, rating, content) VALUES (?,?,?,?)",
        (order_id, user['id'], rating, content)
    )
    conn.commit()
    conn.close()
    socketio.emit('new_review', {'order_id': order_id}, room='admin')
    return jsonify({'success': True})


@app.route('/api/admin/reviews')
def admin_reviews():
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    conn = get_db()
    reviews = conn.execute("""
        SELECT r.*, o.name as buyer_name, o.class_name, p.name as product_name
        FROM reviews r
        JOIN orders o ON r.order_id=o.id
        JOIN products p ON o.product_id=p.id
        ORDER BY r.created_at DESC
    """).fetchall()
    conn.close()
    return jsonify({'success': True, 'reviews': rows_to_list(reviews)})


# ==================== FINANCE ====================

@app.route('/api/admin/finance/overview')
def finance_overview():
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    conn = get_db()
    income = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM orders WHERE status IN ('paid','delivered','received')"
    ).fetchone()[0]
    total_cost = conn.execute("SELECT COALESCE(SUM(amount),0) FROM costs").fetchone()[0]
    order_count = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE status IN ('paid','delivered','received')"
    ).fetchone()[0]
    conn.close()
    return jsonify({
        'success': True,
        'income': round(income, 2),
        'cost': round(total_cost, 2),
        'profit': round(income - total_cost, 2),
        'order_count': order_count
    })


@app.route('/api/admin/finance/costs', methods=['GET', 'POST'])
def finance_costs():
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    conn = get_db()
    if request.method == 'POST':
        data = request.get_json()
        amount = float(data.get('amount') or 0)
        description = data.get('description') or ''
        cost_date = data.get('cost_date') or datetime.now().strftime('%Y-%m-%d')
        conn.execute(
            "INSERT INTO costs (amount, description, cost_date) VALUES (?,?,?)",
            (amount, description, cost_date)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    costs = conn.execute("SELECT * FROM costs ORDER BY cost_date DESC").fetchall()
    conn.close()
    return jsonify({'success': True, 'costs': rows_to_list(costs)})


@app.route('/api/admin/finance/costs/<int:cid>', methods=['PUT', 'DELETE'])
def finance_cost_detail(cid):
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    conn = get_db()
    cost = conn.execute("SELECT * FROM costs WHERE id=?", (cid,)).fetchone()
    if not cost:
        conn.close()
        return jsonify({'success': False, 'msg': '该成本记录不存在'}), 404
    if request.method == 'DELETE':
        conn.execute("DELETE FROM costs WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    data = request.get_json()
    amount = data.get('amount')
    description = data.get('description')
    cost_date = data.get('cost_date')
    fields = []
    values = []
    if amount not in (None, ''):
        fields.append("amount=?")
        values.append(float(amount))
    if description is not None:
        fields.append("description=?")
        values.append(description)
    if cost_date:
        fields.append("cost_date=?")
        values.append(cost_date)
    if fields:
        values.append(cid)
        conn.execute(f"UPDATE costs SET {','.join(fields)} WHERE id=?", values)
        conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/admin/finance/monthly')
def finance_monthly():
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    year = request.args.get('year', datetime.now().year, type=int)
    conn = get_db()
    months = []
    for m in range(1, 13):
        ym = f"{year}-{m:02d}"
        income = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM orders WHERE status IN ('paid','delivered','received') AND strftime('%Y-%m', created_at)=?",
            (ym,)
        ).fetchone()[0]
        cost = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM costs WHERE strftime('%Y-%m', cost_date)=?", (ym,)
        ).fetchone()[0]
        count = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status IN ('paid','delivered','received') AND strftime('%Y-%m', created_at)=?",
            (ym,)
        ).fetchone()[0]
        months.append({
            'month': ym,
            'income': round(income, 2),
            'cost': round(cost, 2),
            'profit': round(income - cost, 2),
            'order_count': count
        })
    conn.close()
    return jsonify({'success': True, 'months': months})


@app.route('/api/admin/finance/export')
def finance_export():
    admin = require_admin()
    if not admin:
        return jsonify({'success': False, 'msg': '未授权'}), 401
    year = request.args.get('year', datetime.now().year, type=int)
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = f"{year}年月度业绩"
    ws.append(['月份', '收入', '成本', '利润', '订单数'])
    conn = get_db()
    for m in range(1, 13):
        ym = f"{year}-{m:02d}"
        income = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM orders WHERE status IN ('paid','delivered','received') AND strftime('%Y-%m', created_at)=?",
            (ym,)
        ).fetchone()[0]
        cost = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM costs WHERE strftime('%Y-%m', cost_date)=?", (ym,)
        ).fetchone()[0]
        count = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status IN ('paid','delivered','received') AND strftime('%Y-%m', created_at)=?",
            (ym,)
        ).fetchone()[0]
        ws.append([ym, round(income, 2), round(cost, 2), round(income - cost, 2), count])
    ws2 = wb.create_sheet("订单明细")
    ws2.append(['订单号', '商品', '买家', '班级', '手机号', '数量', '金额', '状态', '下单时间'])
    orders = conn.execute("""
        SELECT o.id, p.name, o.name, o.class_name, o.phone, o.quantity, o.amount, o.status, o.created_at
        FROM orders o JOIN products p ON o.product_id=p.id
        ORDER BY o.created_at DESC
    """).fetchall()
    for o in orders:
        ws2.append(list(o))
    ws3 = wb.create_sheet("成本明细")
    ws3.append(['日期', '金额', '描述'])
    costs = conn.execute("SELECT cost_date, amount, description FROM costs ORDER BY cost_date DESC").fetchall()
    for c in costs:
        ws3.append(list(c))
    conn.close()

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"sparkjoy_finance_{year}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ==================== SUPER ADMIN（主管理员）====================

@app.route('/api/admin/super/users')
def super_users():
    info = require_super()
    if not info:
        return jsonify({'success': False, 'msg': '需要主管理员权限'}), 403
    conn = get_db()
    users = conn.execute("""
        SELECT u.id, u.name, u.class_name, u.phone, u.status, u.created_at,
               (SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id) as order_count,
               (SELECT COALESCE(SUM(o.amount),0) FROM orders o
                 WHERE o.user_id=u.id AND o.status IN ('paid','delivered','received')) as total_amount
        FROM users u ORDER BY u.created_at DESC
    """).fetchall()
    conn.close()
    return jsonify({'success': True, 'users': rows_to_list(users)})


@app.route('/api/admin/super/users/<int:uid>/orders')
def super_user_orders(uid):
    info = require_super()
    if not info:
        return jsonify({'success': False, 'msg': '需要主管理员权限'}), 403
    conn = get_db()
    orders_list = conn.execute("""
        SELECT o.*, p.name as product_name, p.image_url as product_image
        FROM orders o JOIN products p ON o.product_id=p.id
        WHERE o.user_id=? ORDER BY o.created_at DESC
    """, (uid,)).fetchall()
    conn.close()
    return jsonify({'success': True, 'orders': rows_to_list(orders_list)})


@app.route('/api/admin/super/users/<int:uid>/status', methods=['PUT'])
def super_user_status(uid):
    info = require_super()
    if not info:
        return jsonify({'success': False, 'msg': '需要主管理员权限'}), 403
    status = 1 if request.get_json().get('status') else 0
    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        conn.close()
        return jsonify({'success': False, 'msg': '账号不存在'}), 404
    conn.execute("UPDATE users SET status=? WHERE id=?", (status, uid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'status': status})


@app.route('/api/admin/super/admins')
def super_admins():
    info = require_super()
    if not info:
        return jsonify({'success': False, 'msg': '需要主管理员权限'}), 403
    conn = get_db()
    admins = conn.execute(
        "SELECT id, username, is_super, status, created_at FROM admins ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return jsonify({'success': True, 'admins': rows_to_list(admins)})


@app.route('/api/admin/super/admins/<int:aid>/status', methods=['PUT'])
def super_admin_status(aid):
    info = require_super()
    if not info:
        return jsonify({'success': False, 'msg': '需要主管理员权限'}), 403
    if aid == info['id']:
        return jsonify({'success': False, 'msg': '不能停用当前登录的主账号'}), 400
    status = 1 if request.get_json().get('status') else 0
    conn = get_db()
    admin = conn.execute("SELECT id FROM admins WHERE id=?", (aid,)).fetchone()
    if not admin:
        conn.close()
        return jsonify({'success': False, 'msg': '账号不存在'}), 404
    conn.execute("UPDATE admins SET status=? WHERE id=?", (status, aid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'status': status})


# ==================== SOCKET.IO ====================

@socketio.on('connect')
def on_connect():
    auth = request.args.get('auth') or ''
    try:
        auth_data = json.loads(auth)
    except Exception:
        return False
    token = auth_data.get('token')
    role = auth_data.get('role')
    info = tokens.get(token)
    if not info or info['role'] != role:
        return False
    if role == 'admin':
        join_room('admin')
    else:
        join_room(f"user_{info['id']}")
        join_room('users')


@socketio.on('disconnect')
def on_disconnect():
    pass


# 模块级初始化数据库：本地直接运行时执行，部署到 gunicorn（app:app）时同样会执行
init_db()


if __name__ == '__main__':
    import socket as _socket
    import subprocess as _sp
    try:
        _s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        _lan_ip = _s.getsockname()[0]
        _s.close()
    except Exception:
        _lan_ip = "127.0.0.1"
    _port = int(os.environ.get('PORT', 8080))
    try:
        _hn = _sp.check_output(["scutil", "--get", "LocalHostName"], stderr=_sp.DEVNULL).decode().strip()
    except Exception:
        _hn = _socket.gethostname().split('.')[0]
    _local_url = f"http://{_hn}.local:{_port}"
    _db_mode = "Postgres (Neon)" if os.environ.get('DATABASE_URL', '').startswith('postgres') else "本地 SQLite"
    print("=" * 60)
    print(f"  GQ SparkJoy 徽章工坊 服务已启动")
    print(f"  ─────────────────────────────────────────────")
    print(f"  数据库:         {_db_mode}")
    print(f"  固定地址(推荐): {_local_url}")
    print(f"  本机地址:       http://127.0.0.1:{_port}")
    print(f"  手机/局域网:    http://{_lan_ip}:{_port}")
    print(f"  管理者密钥:     SPARKJOY-ADMIN-2026")
    print(f"  主管理员密钥:   SPARKJOY-OWNER-2026")
    print(f"  ─────────────────────────────────────────────")
    print(f"  提示: 固定地址不随WiFi切换变化，手机和电脑")
    print(f"        连接同一WiFi即可用同一地址访问。")
    print("=" * 60)
    socketio.run(app, host='0.0.0.0', port=_port, debug=False, allow_unsafe_werkzeug=True)
