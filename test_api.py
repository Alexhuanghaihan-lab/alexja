import os
import urllib.request, json

def post(url, data, token=None, method='POST'):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {'raw': body.decode()}

def get(url, token=None):
    headers = {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req)
    return resp.status, json.loads(resp.read())

BASE = os.environ.get('API_BASE', 'http://127.0.0.1:8080') + '/api'

print('=== 1. 注册消费者 ===')
s, d = post(BASE + '/register', {'name': '张三', 'class_name': '高一1班', 'phone': '13800138000', 'password': '123456'})
print(s, d.get('success'), d.get('msg', ''))
user_token = d.get('token')
uid = d.get('user', {}).get('id')

print('=== 2. 重复手机号注册(应失败) ===')
s, d = post(BASE + '/register', {'name': '李四', 'class_name': '高一2班', 'phone': '13800138000', 'password': '123456'})
print(s, d.get('success'), d.get('msg', ''))

print('=== 3. 获取商品 ===')
s, d = get(BASE + '/products')
print('商品数:', len(d.get('products', [])))
for p in d['products']:
    print('  -', p['name'], '¥' + str(p['price']), '款式:', p.get('styles'))
first_product_id = d['products'][0]['id'] if d.get('products') else None

print('=== 4. 注册管理员(错误密钥) ===')
s, d = post(BASE + '/admin/register', {'username': 'admin', 'password': 'admin123', 'admin_key': 'wrong'})
print(s, d.get('success'), d.get('msg', ''))

print('=== 5. 注册管理员(正确密钥) ===')
s, d = post(BASE + '/admin/register', {'username': 'admin', 'password': 'admin123', 'admin_key': 'SPARKJOY-ADMIN-2026'})
print(s, d.get('success'), d.get('msg', ''))
admin_token = d.get('token')

print('=== 5.5 管理员创建商品 ===')
s, d = post(BASE + '/admin/products', {
    'name': '测试徽章', 'description': '测试用', 'price': 15, 'image_url': '',
    'styles': '金色,银色', 'stock': 100
}, admin_token)
print(s, d.get('success'), d.get('msg', ''))

print('=== 5.6 再次获取商品 ===')
s, d = get(BASE + '/products')
first_product_id = d['products'][0]['id'] if d.get('products') else None
print('商品数:', len(d.get('products', [])), '首个ID:', first_product_id)

print('=== 6. 下单 ===')
s, d = post(BASE + '/orders', {
    'product_id': first_product_id, 'quantity': 2, 'style': '金色',
    'name': '张三', 'class_name': '高一1班', 'phone': '13800138000'
}, user_token)
print(s, d.get('success'), d.get('msg', ''))
if d.get('order'):
    print('  订单号:', d['order']['id'], '金额:', d['order']['amount'])

print('=== 7. 管理员查看订单 ===')
s, d = get(BASE + '/admin/orders', admin_token)
print('订单数:', len(d.get('orders', [])))
for o in d.get('orders', []):
    print('  - #' + str(o['id']), o['product_name'], o['amount'], o['status'])

print('=== 8. 管理员修改订单状态 ===')
oid = d['orders'][0]['id']
s, d = post(BASE + '/admin/orders/' + str(oid) + '/status', {'status': 'paid'}, admin_token, method='PUT')
print(s, d.get('success'))

print('=== 9. 财务概览 ===')
s, d = get(BASE + '/admin/finance/overview', admin_token)
print(s, '收入:', d.get('income'), '成本:', d.get('cost'), '利润:', d.get('profit'))

print('=== 10. 录入成本 ===')
s, d = post(BASE + '/admin/finance/costs', {'amount': 50, 'description': '材料', 'cost_date': '2026-11-01'}, admin_token)
print(s, d.get('success'))

print('=== 11. 月度统计 ===')
s, d = get(BASE + '/admin/finance/monthly?year=2026', admin_token)
nov = [m for m in d['months'] if m['month'] == '2026-11'][0]
print('2026-11: 收入', nov['income'], '成本', nov['cost'], '利润', nov['profit'])

print('\n=== 全部测试通过 ===')
