import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sparkjoy.db')

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

c.execute("DELETE FROM reviews")
c.execute("DELETE FROM messages")
c.execute("DELETE FROM orders")
c.execute("DELETE FROM costs")

c.execute("DELETE FROM sqlite_sequence WHERE name IN ('reviews','messages','orders','costs')")

conn.commit()

c.execute("SELECT COUNT(*) FROM users")
user_count = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM admins")
admin_count = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM products")
product_count = c.fetchone()[0]
conn.close()

print("Test business data cleared (orders/messages/reviews/costs).")
print(f"Kept: users={user_count}, admins={admin_count}, products={product_count}")
